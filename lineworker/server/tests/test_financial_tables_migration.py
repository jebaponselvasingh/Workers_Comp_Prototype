"""Story 3.3 AC 2 and 3 — the three tables, their seed, and their grants.

Four separable claims:

1. The **enum tuples in migration 0026** are the Python enums. They are written
   out as literals under the rule 0013 and 0014 set — a migration is a frozen
   record and must not import a live constant — and separate statements of one
   list need a test that they agree. Order included, because it decides the
   type's label order in PostgreSQL.
2. The **line items** are the prototype's. Asserted against
   `docs/Workers_Comp_Prototype.html` by re-evaluating its own expression
   rather than against `bills.json`, so the extractor is under test too — a
   comparison with its own output would pass however wrong it was (Story 2.5's
   rule).
3. The **schedules** are the live generator's, re-derived claim by claim, so a
   drift between migration 0028's seed and `services/financials` fails here.
4. The **grants** are full CRUD, unlike `photo`'s: these tables have runtime
   writers (the materialization command now, Story 3.4's approvals next).

Only 3 and 4 need a database; 1 and 2 run everywhere.
"""

import importlib.util
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from data.models.enums import (
    BillCategory,
    ExpenseCategory,
    LineItemStatus,
    RecoveryWindow,
    ScheduleWeekStatus,
)
from services.financials import scheduled_weeks
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = SERVER_ROOT / "data" / "seed"
PROTOTYPE = SERVER_ROOT.parents[1] / "docs" / "Workers_Comp_Prototype.html"

#: Loaded by path rather than imported, `test_injury_validation.py`'s device:
#: `data/versions/` is Alembic's script directory, not a package, and the
#: module names begin with a digit.
_MIGRATION = SERVER_ROOT / "data" / "versions" / "20260814_0026_financial_tables.py"
_spec = importlib.util.spec_from_file_location("_m0026", _MIGRATION)
assert _spec and _spec.loader
m0026 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0026)

EXPECTED_BILLS = 520
EXPECTED_EXPENSES = 340
EXPECTED_CLAIMS = 100

#: The clamp `services/financials/schedule.py` applies. Restated so a change to
#: it is a decision here as well as there.
WEEK_BOUNDS = (4, 20)


def seed(name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads((SEED_DIR / name).read_text(encoding="utf-8"))
    return rows


# --- 1. the migration's literals against the enums ------------------------


@pytest.mark.parametrize(
    ("literal", "enum"),
    [
        (m0026.SCHEDULE_WEEK_STATUSES, ScheduleWeekStatus),
        (m0026.LINE_ITEM_STATUSES, LineItemStatus),
        (m0026.BILL_CATEGORIES, BillCategory),
        (m0026.EXPENSE_CATEGORIES, ExpenseCategory),
    ],
)
def test_the_migrations_enum_tuples_are_the_enums(
    literal: tuple[str, ...], enum: type[StrEnum]
) -> None:
    """Two statements of one vocabulary, pinned to agree.

    The migration cannot import the enum — 0014's rule — so the copy has to be
    checked instead. **Order too, not just membership**: the tuple decides the
    native type's label order, which is what `ORDER BY status` sorts by.
    """
    assert literal == tuple(member.value for member in enum)


def test_the_two_line_item_tables_share_one_status_vocabulary() -> None:
    """One enum, one type, and the migration says so once.

    `bill` and `expense` are separate tables because their *categories* are
    different lists; their statuses are identical, and the prototype agrees by
    rendering both through one `billStatusLabel`. This is the assertion that
    keeps a later story from widening one and not the other.
    """
    assert m0026.LINE_ITEM_STATUS_ENUM == "line_item_status"
    assert set(m0026.BILL_CATEGORIES).isdisjoint(m0026.EXPENSE_CATEGORIES)


def test_pending_submission_and_payment_scheduled_are_distinct() -> None:
    """The prototype's label bug, refused explicitly.

    `BILL_STATUS_LABEL` maps `PendingSubmission` to the string "Payment
    Scheduled" (line 806), conflating a bill nobody has filed with one a
    handler has approved into a batch. They are opposite facts about what a
    claim is about to cost, so they are two members here — and Story 3.4's
    approval is the transition between them.
    """
    # Asserted as *membership* rather than as an inequality: mypy rightly
    # points out that comparing two distinct enum members can never be false,
    # which would make the obvious spelling a tautology. What is worth checking
    # is that both tokens reached the database type — the prototype's bug is a
    # shared *label*, and that the two labels differ is asserted where labels
    # live (`BillsTab.test.tsx`).
    assert {"pending_submission", "payment_scheduled"} <= set(m0026.LINE_ITEM_STATUSES)
    assert len({member.value for member in LineItemStatus}) == len(LineItemStatus)


# --- 2. the seed files against the prototype ------------------------------


def _prototype_claims() -> list[dict[str, Any]]:
    html = PROTOTYPE.read_text(encoding="utf-8")
    match = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    assert match, "ALL_CLAIMS not found in the prototype"
    claims: list[dict[str, Any]] = json.loads(match.group(1))
    return claims


def test_the_seed_covers_every_claim_in_both_tables() -> None:
    bills, expenses = seed("bills.json"), seed("expenses.json")

    assert len(bills) == EXPECTED_BILLS
    assert len(expenses) == EXPECTED_EXPENSES
    assert len({row["claim_id"] for row in bills}) == EXPECTED_CLAIMS
    assert len({row["claim_id"] for row in expenses}) == EXPECTED_CLAIMS


def test_every_seeded_status_and_category_is_in_its_enum() -> None:
    """No row carries a token the column cannot hold.

    The migration would refuse such a row, so this is not about the database —
    it is about the *extractor*, whose one deliberate divergence from the
    prototype exists precisely because JavaScript's signed shift produced a
    status outside every declared vocabulary on 26 of 860 rows.
    """
    for row in seed("bills.json"):
        assert row["status"] in {member.value for member in LineItemStatus}
        assert row["category"] in {member.value for member in BillCategory}
    for row in seed("expenses.json"):
        assert row["status"] in {member.value for member in LineItemStatus}
        assert row["category"] in {member.value for member in ExpenseCategory}


def test_the_seeded_amounts_are_the_prototypes_own_arithmetic() -> None:
    """The amounts, re-derived from the HTML by a second implementation.

    Written out here rather than imported from the extractor, which is Story
    3.1's rule for the state-rate table: a comparison against the extractor's
    own output proves only that it is deterministic. This evaluates the
    prototype's expression independently — the unsigned rolling hash, the
    multiplier from three bits, `Math.round` — over the first unconditional
    line item of every claim, which is the one whose `idx` is 0 and therefore
    the one whose value is unambiguous regardless of which optional items the
    claim qualifies for.
    """
    bills = {
        row["claim_id"]: row for row in seed("bills.json") if row["category"] == "initial_treatment"
    }
    assert len(bills) == EXPECTED_CLAIMS

    for claim in _prototype_claims():
        digest = 0
        for char in claim["claimId"]:
            digest = (digest * 31 + ord(char)) & 0xFFFFFFFF
        # `0.8 + ((h >> 0) & 7) / 10` over a base of $850, rounded half up.
        multiplier = 0.8 + (digest & 7) / 10
        expected = int(850 * multiplier + 0.5) * 100

        assert bills[claim["claimId"]]["amount_cents"] == expected, claim["claimId"]


def test_every_amount_is_a_whole_number_of_dollars() -> None:
    """The prototype computes in dollars and this seed multiplies once.

    Load-bearing for the UI: `lib/money.ts` formats whole dollars, so a row
    carrying stray cents would render as a figure that does not add up to the
    total beside it. `format_dollars` and `formatCents` both round half up, so
    the disagreement would be small and permanent.
    """
    for name in ("bills.json", "expenses.json"):
        for row in seed(name):
            assert row["amount_cents"] % 100 == 0, (name, row)


def test_a_settled_claim_has_nothing_outstanding() -> None:
    """The prototype's own branch, and the settled payout breakdown needs it.

    Every status pool on a settled claim is `["Paid"]`. This matters beyond the
    Bills tab: Story 2.2's settled Overview renders an expense line that had
    nothing behind it until these rows landed, and it is only correct if a
    settled claim's expenses are all paid.
    """
    settled = {claim["claimId"] for claim in _prototype_claims() if claim["stage"] == "settled"}
    assert settled, "no settled claims in the prototype — the fixture has moved"

    for name in ("bills.json", "expenses.json"):
        for row in seed(name):
            if row["claim_id"] in settled:
                assert row["status"] == "paid", (name, row)


# --- 3 and 4. the database ------------------------------------------------


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


@requires_db
def test_the_seed_lands_every_row_in_every_table(engine: Any) -> None:
    """AC 2 and AC 3's "for all 100 claims", asserted against the database.

    The schedule's row count is not a literal, because it depends on each
    claim's recovery window — what is fixed is that every claim has one and
    that every one of them is inside the generator's clamp.
    """
    with engine.connect() as conn:
        counts = {
            table: conn.execute(
                sa.text(f"SELECT count(*), count(DISTINCT claim_id) FROM {table}")
            ).one()
            for table in ("payment_schedule_week", "bill", "expense")
        }
        per_claim = conn.execute(
            sa.text(
                "SELECT min(n), max(n) FROM ("
                "SELECT count(*) n FROM payment_schedule_week GROUP BY claim_id) s"
            )
        ).one()

    assert counts["bill"] == (EXPECTED_BILLS, EXPECTED_CLAIMS)
    assert counts["expense"] == (EXPECTED_EXPENSES, EXPECTED_CLAIMS)
    assert counts["payment_schedule_week"][1] == EXPECTED_CLAIMS
    assert tuple(per_claim) == WEEK_BOUNDS


@requires_db
def test_the_seeded_schedule_is_the_live_generators_answer(engine: Any) -> None:
    """Migration 0028 imports the generator; this is what makes that safe.

    The migration is the one place in `data/versions/` that reaches into
    `services/` — deliberately, because a frozen copy of the projection would
    be the second generator AD-2 forbids. The cost of that exception is that a
    generator change could silently invalidate the seed, so the seed is
    re-derived here from the live code and compared row for row.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT c.claim_id, c.recovery, count(*) AS weeks "
                "FROM payment_schedule_week w JOIN claim c ON c.id = w.claim_id "
                "GROUP BY c.claim_id, c.recovery"
            )
        ).mappings()

        for row in rows:
            assert row["weeks"] == scheduled_weeks(RecoveryWindow(row["recovery"])), row["claim_id"]


@requires_db
def test_one_claim_cannot_hold_two_of_the_same_week(engine: Any) -> None:
    """The unique constraint, asserted by asking for the failure.

    Load-bearing rather than tidy: the rows *are* a projection keyed by week,
    so the materialization command is an upsert. Without this a regeneration
    bug appends a second Wk 3 and the table renders two of them under a total
    that counts it once.
    """
    with engine.connect() as conn:
        existing = (
            conn.execute(
                sa.text(
                    "SELECT claim_id, week_no, period_start, period_end, amount_cents, status "
                    "FROM payment_schedule_week LIMIT 1"
                )
            )
            .mappings()
            .one()
        )

        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO payment_schedule_week "
                    "(claim_id, week_no, period_start, period_end, amount_cents, status) "
                    "VALUES (:claim_id, :week_no, :period_start, :period_end, "
                    ":amount_cents, CAST(:status AS schedule_week_status))"
                ),
                dict(existing),
            )
        conn.rollback()


@requires_db
def test_a_financial_row_cannot_outlive_its_claim(engine: Any) -> None:
    """The FK on all three, asserted by asking for the failure.

    Epic 8's purge cascade depends on these constraints existing; until then
    their job is to make an orphan impossible rather than to make one silently
    disappear (`photo`'s ruling).
    """
    statements = {
        "bill": "INSERT INTO bill (claim_id, category, label, amount_cents, status) VALUES "
        "(0, 'imaging', 'x', 1, 'paid')",
        "expense": "INSERT INTO expense (claim_id, category, label, amount_cents, status) "
        "VALUES (0, 'misc', 'x', 1, 'paid')",
        "payment_schedule_week": "INSERT INTO payment_schedule_week "
        "(claim_id, week_no, period_start, period_end, amount_cents, status) VALUES "
        "(0, 1, '2026-01-01', '2026-01-07', 1, 'paid')",
    }
    with engine.connect() as conn:
        for table, statement in statements.items():
            with pytest.raises(sa.exc.IntegrityError, match="foreign key"):
                conn.execute(sa.text(statement))
            conn.rollback()
            assert table  # names the failing table in the traceback


@requires_db
def test_the_app_role_can_write_these_tables_unlike_the_photos(engine: Any) -> None:
    """AD-4's grant statement, and the reason it differs from `photo`'s.

    `photo` holds SELECT and nothing else because no story in Epics 1-8 writes
    one. These three have runtime writers from this story on: the
    materialization command inserts, updates and deletes schedule weeks, and
    Story 3.4's approval updates a bill's status. The restriction that
    `services/financials` is the *only* writer is enforced by where the code
    lives, not by the grant (0010's argument for `timeline_event`) — and Story
    8.1's PHI purge has to be able to delete these rows.
    """
    with engine.connect() as conn:
        for table in ("payment_schedule_week", "bill", "expense"):
            granted = conn.execute(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = :table AND grantee = 'lineworker_app'"
                ),
                {"table": table},
            ).scalars()

            assert set(granted) == {"SELECT", "INSERT", "UPDATE", "DELETE"}, table
