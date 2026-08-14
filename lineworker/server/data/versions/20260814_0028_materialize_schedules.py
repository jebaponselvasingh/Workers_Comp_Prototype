"""Materialize the indemnity payment schedules (Story 3.3, AC 2).

Writes `payment_schedule_week` rows for all 100 seeded claims by running the
one generator — `services/financials/schedule.py::project_payments` — over the
one benefit calculation, `compute_benefit`.

## This migration imports live code, and it is the only one that does

Every other seed migration loads a JSON file an extractor produced, and 0013
and 0014 write their vocabularies out as literals under an explicit rule: *a
migration is a frozen historical record, and one that read a live constant
would silently change what it did when somebody reordered that constant.* This
one deliberately does the opposite, so the exception needs its reason stated.

The rule protects **declarations**. `BODY_KEYS` decides the member order of a
native enum type; freezing a copy is right, because the migration's job there
is to record what the schema became on that day, and a copy is the only way to
keep saying it.

This migration declares nothing. It seeds **derived data whose defining
property is that exactly one function in the system computes it** (AD-2), and
Story 3.3 states the constraint in as many words — "one generator (the same
pure projection referenced by 3.2 — extend it, never fork it)". A frozen copy
of the projection here would not preserve history; it would create the second
generator that AD-2 and the story both forbid, free to drift from the real one
with nothing anywhere to notice. The same argument covers `compute_benefit`:
restating the statutory formula to seed a weekly amount would put a second
answer to "what does this claim pay" in the tree.

Two things make the exception cheap as well as correct:

- **These rows are refreshed, not frozen.** `services/financials/materialize.py`
  runs on every financial read and rewrites any undecided week whose projection
  has moved, so a generator change reaches existing databases on the next
  request rather than being trapped in whatever this migration wrote.
- **`tests/test_financial_tables_migration.py` re-derives the whole book** from
  the live generator and compares week counts claim by claim, so a drift
  between this seed and the service fails a test rather than showing up as a
  wrong figure on a card.

## The statuses are a snapshot, and the refresh is what makes that alright

Three of the five statuses answer "where does this week sit relative to today",
so they are true as of the moment `alembic upgrade head` ran and no longer. The
reference date is therefore `utc_today()` — the same clock
`services/financials` resolves the case file against — and the rows are
correct on the day they are written and corrected by the first read after that.
Seeding them at all is worth it because the alternative is a fresh database
whose Bills tab is empty until somebody opens it, and because the e2e reset is
`alembic upgrade head` and nothing else (see `e2e/fixtures/reset.ts`): a boot
-time materialization would not run after a reset, and every spec would open on
an empty schedule.

## Weeks, not payments

`project_payments` clamps every schedule to between 4 and 20 weeks, so this
inserts at most 2,000 rows and in practice about 1,100. No claim is skipped: a
missing state rate raises `MissingStateRate` and takes the migration down,
which is migration 0023's ruling applied one story later — a claim whose
jurisdiction has no statutory schedule is a data error, not a claim to seed
with a default.

Revision ID: 0028_materialize_schedules
Revises: 0027_seed_line_items
Create Date: 2026-08-14

"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from alembic import op

from data.models.core import StateRateSchedule
from data.models.enums import Disability, RecoveryWindow, ReturnStatus, Stage
from rules.engine import LoadedDocument, evaluate, utc_today
from rules.parameters import (
    BENEFIT_PARAMS_KEY,
    DERIVATION_THRESHOLDS_KEY,
    BenefitParams,
    DerivationThresholds,
)
from services.financials.benefit import compute_benefit
from services.financials.schedule import project_payments

revision: str = "0028_materialize_schedules"
down_revision: str | None = "0027_seed_line_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPECTED_CLAIMS = 100

#: The clamp `project_payments` applies. Restated here only as a sanity bound
#: on what this migration is allowed to insert — if a generator change ever
#: made a schedule unbounded, this is the line that stops a seed migration
#: writing a million rows before anybody notices.
MIN_WEEKS_PER_CLAIM = 4
MAX_WEEKS_PER_CLAIM = 20

#: The claim columns the benefit and the projection read, together.
CLAIM_COLUMNS = (
    "id",
    "claim_id",
    "doi",
    "stage",
    "recovery",
    "state",
    "aww",
    "reserve",
    "severity_score",
    "injury_type",
    "disability",
    "surgery_required",
    "litigation_flag",
    "return_status",
    "comp_rate_override_bp",
)


@dataclass(frozen=True)
class _SeedClaim:
    """A claim, shaped for `BenefitClaim` and `ScheduleClaim` at once.

    Both protocols are structural, so a frozen dataclass satisfies them without
    the ORM. Built explicitly rather than by handing the raw row through
    because the enum columns arrive from a reflected table as **strings**, and
    `project_payments` keys a dict on `RecoveryWindow` — a string key is a
    `KeyError` at seed time, which is a poor way to find out.
    """

    doi: date
    stage: Stage
    recovery: RecoveryWindow
    state: str
    aww: int
    reserve: int
    severity_score: int
    injury_type: str
    disability: Disability
    surgery_required: bool
    litigation_flag: bool
    return_status: ReturnStatus
    comp_rate_override_bp: int | None


def _document(bind: sa.Connection, key: str, as_of: date) -> LoadedDocument:
    """`rules.engine.load`, over a synchronous connection.

    The same query and the same ordering — highest version whose effective date
    has arrived — restated because `load` takes an `AsyncSession` and Alembic
    has no event loop. Only the *transport* differs; the document, its
    evaluation and its typed block are all the live ones, which is the half
    that could otherwise drift.
    """
    row = bind.execute(
        sa.text(
            "SELECT version, content FROM rule_document "
            "WHERE key = :key AND effective_from <= :as_of "
            "ORDER BY version DESC LIMIT 1"
        ),
        {"key": key, "as_of": as_of},
    ).first()
    if row is None:
        raise ValueError(
            f"no rule document {key!r} effective on {as_of} — the rules "
            "migrations and this seed have gone out of order"
        )
    return LoadedDocument(key=key, version=int(row.version), content=dict(row.content))


def upgrade() -> None:
    bind = op.get_bind()
    as_of = utc_today()

    params = BenefitParams.of(doc := _document(bind, BENEFIT_PARAMS_KEY, as_of), evaluate(doc))
    thresholds = DerivationThresholds.of(
        doc := _document(bind, DERIVATION_THRESHOLDS_KEY, as_of), evaluate(doc)
    )

    rates = {
        row.state_code: StateRateSchedule(
            state_code=row.state_code,
            state_name=row.state_name,
            weekly_min_cents=row.weekly_min_cents,
            weekly_max_cents=row.weekly_max_cents,
            effective_date=row.effective_date,
        )
        for row in bind.execute(sa.text("SELECT * FROM state_rate_schedule"))
    }

    claims = bind.execute(
        sa.text(f"SELECT {', '.join(CLAIM_COLUMNS)} FROM claim ORDER BY id")
    ).all()
    if len(claims) != EXPECTED_CLAIMS:
        raise ValueError(
            f"expected {EXPECTED_CLAIMS} seeded claims, found {len(claims)} — "
            "this seed and 0004 have drifted apart"
        )

    rows: list[dict[str, object]] = []
    for row in claims:
        rate = rates.get(row.state)
        if rate is None:
            # 0023's ruling, one story on: no default, and the failure is loud.
            raise ValueError(
                f"claim {row.claim_id} is in state {row.state!r}, which has no "
                "statutory rate schedule — 0023 should have refused first"
            )
        claim = _SeedClaim(
            doi=row.doi,
            stage=Stage(row.stage),
            recovery=RecoveryWindow(row.recovery),
            state=row.state,
            aww=row.aww,
            reserve=row.reserve,
            severity_score=row.severity_score,
            injury_type=row.injury_type,
            disability=Disability(row.disability),
            surgery_required=row.surgery_required,
            litigation_flag=row.litigation_flag,
            return_status=ReturnStatus(row.return_status),
            comp_rate_override_bp=row.comp_rate_override_bp,
        )
        benefit = compute_benefit(claim, rate, params, thresholds)
        projection = project_payments(
            claim,
            weekly_cents=benefit.weekly_cents,
            waiting_days=benefit.waiting_days,
            as_of=as_of,
        )
        if not MIN_WEEKS_PER_CLAIM <= projection.week_count <= MAX_WEEKS_PER_CLAIM:
            raise ValueError(
                f"claim {row.claim_id} projected {projection.week_count} weeks, outside "
                f"the {MIN_WEEKS_PER_CLAIM}-{MAX_WEEKS_PER_CLAIM} clamp — the generator "
                "and this migration's bound disagree"
            )
        rows.extend(
            {
                "claim_id": row.id,
                "week_no": week.week,
                "period_start": week.start,
                "period_end": week.end,
                "amount_cents": week.amount_cents,
                "status": week.status.value,
            }
            for week in projection.weeks
        )

    meta = sa.MetaData()
    schedule = sa.Table("payment_schedule_week", meta, autoload_with=bind)
    bind.execute(schedule.insert(), rows)


def downgrade() -> None:
    op.execute("DELETE FROM payment_schedule_week")
