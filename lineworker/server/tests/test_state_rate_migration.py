"""Story 3.1 AC 1 — `state_rate_schedule`, from the prototype to the database.

Four separable claims, and the file is organised along them:

1. The **rows** are the prototype's, in cents. Asserted against
   `docs/Workers_Comp_Prototype.html` itself rather than against
   `state_rates.json`, so the extractor is under test too — a comparison with
   its own output would pass however wrong it was (Story 2.5's rule for
   `path_required_form`, and 2.6's for `photo`).
2. **Every claim's jurisdiction is covered.** This is the assertion the story
   is really about: the prototype answers ``STATE_WC_RATES[c.state] ||
   {max:1200, min:250}``, so an unlisted state silently clamps a worker's
   weekly benefit to two numbers belonging to no jurisdiction. Migration 0023
   refuses to complete while any seeded claim is uncovered; this checks the
   property directly, against the rows, so a re-cut portfolio fails here with
   a reason rather than in a handler's benefit card.
3. **The bounds are ordered**, in the file, in the migration and in the
   database — three statements of one rule, because an inverted pair pins
   every claim in the state to the lower bound and the card still renders a
   confident figure.
4. The **grants** are SELECT and nothing else. Nothing in Epics 1–8 writes a
   statutory rate, so the seed migration is the table's only writer (AD-12).

Only the last two need a database, so the first two run everywhere.
"""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from tests import seed_fixture
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = SERVER_ROOT / "data" / "seed" / "state_rates.json"
PROTOTYPE = SERVER_ROOT.parents[1] / "docs" / "Workers_Comp_Prototype.html"

#: The seventeen jurisdictions the fifteen plants sit in. Written down because
#: it is what makes "a state fell out of the extractor" a failure rather than a
#: quieter portfolio.
#: [Source: docs/Workers_Comp_Prototype.html lines 638-645]
EXPECTED_STATES = 17

#: The prototype's whole dollars against this schema's integer cents.
CENTS_PER_DOLLAR = 100

#: The date migration 0023 supplies, since the prototype's literal carries
#: none. Restated here rather than imported, for `seed_fixture.HIGH_RISK_MIN`'s
#: reason — and because the benefit card cites this date as the schedule's
#: provenance, which makes it a fact a reader should be able to check without
#: opening the migration.
EFFECTIVE_FROM = "2026-01-01"

#: One `XX:{max:…,min:…,name:"…"}` entry, read independently of the extractor's
#: own pattern — same object, different reading, so the two are unlikely to be
#: wrong in the same way. This one is keyed on the *name* first and recovers
#: the numbers by lookup, where the extractor's is strictly positional.
_PROTOTYPE_RATE = re.compile(r"([A-Z]{2})\s*:\s*\{([^}]*)\}")
_PROTOTYPE_FIELD = re.compile(r'(\w+)\s*:\s*(\d+|"(?:[^"\\]|\\.)*")')


def seeded_rates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return rows


def prototype_rates() -> dict[str, dict[str, Any]]:
    """`{state code: {max, min, name}}`, parsed from `STATE_WC_RATES`.

    Field-keyed rather than positional: the extractor refuses a literal whose
    keys have moved (which is the safe failure), and this reading would still
    get it right — so the pair covers both "the shape changed" and "the values
    are wrong" rather than failing together.
    """
    html = PROTOTYPE.read_text(encoding="utf-8")
    literal = re.search(r"const STATE_WC_RATES\s*=\s*(\{.*?\n\}\;)", html, re.S)
    assert literal, "STATE_WC_RATES not found in the prototype"

    found: dict[str, dict[str, Any]] = {}
    for code, body in _PROTOTYPE_RATE.findall(literal.group(1)):
        fields = {key: json.loads(value) for key, value in _PROTOTYPE_FIELD.findall(body)}
        assert {"max", "min", "name"} <= set(fields), f"{code} is missing a field: {fields}"
        found[code] = fields
    return found


# --- AC 1: the rows are the prototype's ---------------------------------


def test_the_seed_file_is_the_prototypes_seventeen_jurisdictions() -> None:
    assert {rate["state_code"] for rate in seeded_rates()} == set(prototype_rates())
    assert len(seeded_rates()) == EXPECTED_STATES


def test_every_bound_is_the_prototypes_dollars_times_one_hundred() -> None:
    """The ×100 the extractor does, checked against the source it read.

    This is the classic bug the story's Dev Notes name: the prototype's AWW is
    dollars and the seeded `aww` is already cents, so a schedule left in
    dollars would clamp every claim in the portfolio to a maximum of about
    eleven dollars a week and nothing would look obviously broken.
    """
    expected = prototype_rates()
    for rate in seeded_rates():
        source = expected[rate["state_code"]]
        assert rate["weekly_min_cents"] == source["min"] * CENTS_PER_DOLLAR
        assert rate["weekly_max_cents"] == source["max"] * CENTS_PER_DOLLAR
        assert rate["state_name"] == source["name"]


def test_no_seeded_schedule_has_its_bounds_reversed() -> None:
    """Checked in the extractor, in the migration, and by a database CHECK.

    Three statements of one rule, because the clamp is
    `max(min, min(max, weekly))`: reversed, it pins every claim in the state
    to the lower of the two and the benefit card renders a confident figure
    for all of them.
    """
    for rate in seeded_rates():
        assert rate["weekly_min_cents"] <= rate["weekly_max_cents"], rate["state_code"]


def test_every_seeded_claims_state_has_a_schedule_in_the_seed_file() -> None:
    """AC 1's real assertion — the prototype's silent fallback is gone.

    Read from `seed_data.json` (Story 1.2's portfolio) against
    `state_rates.json`, so it fails on a laptop with no database as well as in
    CI. The database-backed twin below asserts the same property of the rows
    that actually exist.
    """
    filed_in = {str(claim["state"]) for claim in seed_fixture.seed()["claims"]}
    covered = {rate["state_code"] for rate in seeded_rates()}

    assert filed_in - covered == set(), (
        "claims are filed in these states and no rate schedule covers them, so no "
        f"weekly benefit could be computed for them: {sorted(filed_in - covered)}"
    )


# --- the rows in the database -------------------------------------------


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


@requires_db
def test_the_migration_loaded_every_jurisdiction(engine: Any) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT state_code, state_name, weekly_min_cents, weekly_max_cents, "
                "effective_date FROM state_rate_schedule ORDER BY state_code"
            )
        ).all()

    expected = sorted(seeded_rates(), key=lambda rate: str(rate["state_code"]))
    assert len(rows) == EXPECTED_STATES
    for row, rate in zip(rows, expected, strict=True):
        assert row.state_code == rate["state_code"]
        assert row.state_name == rate["state_name"]
        # The columns are `BigInteger`; psycopg hands back `int` for those and
        # `Decimal` for numerics, so the comparison is written to be explicit
        # about which this is rather than relying on coercion.
        assert int(row.weekly_min_cents) == rate["weekly_min_cents"]
        assert int(row.weekly_max_cents) == rate["weekly_max_cents"]
        assert row.effective_date.isoformat() == EFFECTIVE_FROM
        assert not isinstance(row.weekly_min_cents, Decimal), "money must arrive as an integer"


@requires_db
def test_every_claim_in_the_database_has_a_schedule(engine: Any) -> None:
    """The property migration 0023 refuses to complete without, asked of the
    rows rather than of the seed file — the database is what a request reads."""
    with engine.connect() as conn:
        uncovered = (
            conn.execute(
                sa.text(
                    "SELECT DISTINCT c.state FROM claim c "
                    "LEFT JOIN state_rate_schedule s ON s.state_code = c.state "
                    "WHERE s.id IS NULL ORDER BY c.state"
                )
            )
            .scalars()
            .all()
        )

    assert list(uncovered) == []


@requires_db
def test_the_database_refuses_a_schedule_with_reversed_bounds(engine: Any) -> None:
    """The CHECK constraint, exercised — the half of the rule that holds for
    rows nobody reviewed. Rolled back, so the seeded book is unchanged."""
    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError, match="ck_state_rate_schedule"):
            conn.execute(
                sa.text(
                    "INSERT INTO state_rate_schedule "
                    "(state_code, state_name, weekly_min_cents, weekly_max_cents, "
                    "effective_date) VALUES ('ZZ', 'Nowhere', 500000, 1000, '2026-01-01')"
                )
            )
        conn.rollback()


@requires_db
def test_two_schedules_for_one_state_are_refused(engine: Any) -> None:
    """`state_code` is unique, which is what makes "the schedule for WA" a
    question with one answer today.

    The model docstring records what changes when a refresh process adds next
    year's figures — the constraint moves to `(state_code, effective_date)` and
    the read picks the latest effective row, exactly as `rule_document` already
    does. Until then a second row would make `rate_for_state` raise
    `MultipleResultsFound` inside a request, and this is what stops one
    arriving quietly.
    """
    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError, match="uq_state_rate_schedule_state_code"):
            conn.execute(
                sa.text(
                    "INSERT INTO state_rate_schedule "
                    "(state_code, state_name, weekly_min_cents, weekly_max_cents, "
                    "effective_date) VALUES ('WA', 'Washington', 1000, 2000, '2027-01-01')"
                )
            )
        conn.rollback()


# --- AD-4 grants ---------------------------------------------------------


@requires_db
def test_the_app_role_may_read_the_schedule_and_nothing_more(engine: Any) -> None:
    """`glossary_term`'s and `path_required_form`'s grant, for the same reason.

    An app role that could rewrite a statutory weekly maximum would be able to
    change what every injured worker in a state is paid, from inside a request,
    through an endpoint that does not exist.
    """
    with engine.connect() as conn:
        granted = set(
            conn.execute(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'state_rate_schedule' AND grantee = 'lineworker_app'"
                )
            ).scalars()
        )

    assert granted == {"SELECT"}
