"""The two retention floors, compared at build time instead of at the first sweep.

`AUDIT_RETENTION_YEARS` is read in two places that cannot see each other.
Migration 0003 interpolates it into `audit_redactor_delete`'s
`USING (at < now() - interval '<n> years')`, once, at the moment the migration
runs; `services/audit/retention.py` reads `Settings.audit_retention_years` from
the environment on every sweep. The first is a property of the database as it
was built; the second is a property of the process as it is running, and nothing
has ever held them together.

Story 8.1 closed the *consequence* — `purge_expired_audit_events` counts the
rows past its own cutoff, notices when the policy allowed fewer, and raises
`RetentionFloorMismatch` rather than under-deleting in silence. It said so, and
said what was left, in `deferred-work.md:925`: "nothing in CI compares the two
numbers at deploy time, so the divergence is detected on the first sweep after
the change rather than at the change. Story 8.4's gate is the place for a check
that reads the policy's `USING` clause and the setting together." This is that
check.

**Why the timing is the whole point.** A lowered `AUDIT_RETENTION_YEARS` is a
one-line diff in an env file, and the failure it causes is a daily job that
raises — *after* deployment, in an operator's inbox, on a system with no
alerting stack (deliberately: see `deploy/DEPLOYMENT.md § 8`). Raised in a
migration's absence rather than at the change, it is a compliance number that
two artifacts disagree about for however long it takes somebody to read a log.
Compared here, it is a red build naming both numbers.

**Compared by the database, not by parsing.** The literal is lifted out of the
policy's rendered qualifier — which Postgres normalises to `'7 years'::interval`
regardless of how the migration spelled it — and handed straight back as
`:lit::interval = make_interval(years => :configured)`. Parsing "7" out of the
string and comparing integers would be this test inventing an interval
arithmetic that Postgres already has, and would quietly disagree with the server
the day the policy is expressed in months.

`tests/test_audit_grants.py` deliberately asserts only that the qualifier still
*carries* an interval, and says why: pinning the literal there would fail a
deployment that legitimately configured a different floor and migrated to match.
That is the right call for that file and it is exactly the gap this one fills —
here both halves are read, so a legitimately-different floor passes and a
divergent one does not.
"""

import re
from collections.abc import Iterator
from typing import Final

import pytest
import sqlalchemy as sa

from config import get_settings
from tests.conftest import requires_db

pytestmark = requires_db

TABLE: Final[str] = "audit_event"
POLICY: Final[str] = "audit_redactor_delete"

#: The interval literal inside a rendered policy qualifier.
#:
#: Postgres does not store the source text of a `USING` clause; it stores a
#: parse tree and renders it back, which normalises `interval '7 years'` to
#: `'7 years'::interval`. Both spellings are accepted anyway — the cast form is
#: what every server this project targets produces, and the prefix form is what
#: migration 0003 is written in, so a reader comparing the two files should not
#: have to know which one the catalog happens to return.
_INTERVAL: Final[re.Pattern[str]] = re.compile(
    r"(?:'(?P<cast>[^']+)'::interval|interval\s+'(?P<prefix>[^']+)')"
)


@pytest.fixture(scope="module")
def catalog(seeded_db_url: str) -> Iterator[sa.Engine]:
    """A connection to the fresh-migration database.

    `seeded_db_url` rather than a bare URL, for `tests/test_audit_grants.py`'s
    reason: the floor this file compares is the one a *fresh* `alembic upgrade
    head` bakes in, which is what CI runs and what a new deployment gets. A
    database that happened to be lying around would be a test about history.
    """
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _policy_interval(engine: sa.Engine) -> str:
    """The interval literal `audit_redactor_delete` bounds its DELETE with.

    Fails loudly on a qualifier with no interval in it rather than returning
    `None` for the caller to compare against something. A policy that lost its
    floor is an unbounded DELETE on the audit log — the failure this whole
    arrangement exists to prevent — so it must not reach an equality check that
    could be satisfied by a second absence.
    """
    with engine.connect() as conn:
        qual = conn.execute(
            sa.text("SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = :p"),
            {"t": TABLE, "p": POLICY},
        ).scalar_one_or_none()

    assert qual is not None, (
        f"there is no {POLICY} policy on {TABLE}. Without it the audit_redactor "
        "role's DELETE grant is unbounded and the retention job could take the "
        "whole table."
    )
    match = _INTERVAL.search(str(qual))
    assert match is not None, (
        f"{POLICY}'s qualifier is {qual!r} and carries no interval literal — the "
        "role's DELETE is no longer bounded by an age at all."
    )
    return match["cast"] or match["prefix"]


def test_the_policys_floor_and_the_configured_floor_are_the_same_interval(
    catalog: sa.Engine,
) -> None:
    """The comparison `deferred-work.md:925` assigns to this story's gate.

    Both numbers are named in the failure because the fix depends on which one
    is wrong, and they are fixed in different places by different mechanisms: the
    setting moves with an environment variable, and the policy moves **only with
    a migration** — migration 0049's docstring spends four paragraphs refusing a
    `current_setting()` GUC precisely so that a session cannot lower the floor
    that guards it. So "make them agree" is never "edit the policy in place"; it
    is either restore the setting or write the migration.
    """
    configured = get_settings().audit_retention_years
    literal = _policy_interval(catalog)

    with catalog.connect() as conn:
        agrees, rendered = conn.execute(
            sa.text(
                "SELECT CAST(:lit AS interval) = make_interval(years => :years), "
                "CAST(CAST(:lit AS interval) AS text)"
            ),
            {"lit": literal, "years": configured},
        ).one()

    assert agrees, (
        f"AUDIT_RETENTION_YEARS is {configured} and {POLICY} bounds the redactor's "
        f"DELETE at {rendered!r}. The two are the same compliance number held in two "
        "places: the setting decides which rows the sweep selects, the policy decides "
        "which rows the database will let it delete. While they disagree, "
        "`purge_expired_audit_events` raises RetentionFloorMismatch on every run "
        "(services/audit/retention.py) — either restore the setting, or write the "
        "migration that moves the policy, which is the only thing that can."
    )


def test_the_comparison_would_notice_a_floor_that_had_moved(catalog: sa.Engine) -> None:
    """The mandatory positive control: this test can fail.

    An equality between two things read out of the same system is the classic
    shape of an assertion that cannot fail — and the story this test ships with
    says the vacuity rule applies to its own additions first. So the policy's
    interval is compared against a floor one year short of the configured one and
    must **not** match. Without this, a `_policy_interval` that had started
    returning the configured value by some accident of parsing would leave the
    test above green forever.

    One year rather than zero, because zero is a special case Postgres would
    reject on other grounds and would prove less: a year is the smallest change
    an operator would actually make.
    """
    configured = get_settings().audit_retention_years
    literal = _policy_interval(catalog)

    with catalog.connect() as conn:
        matches_a_shorter_floor = conn.execute(
            sa.text("SELECT CAST(:lit AS interval) = make_interval(years => :years)"),
            {"lit": literal, "years": configured - 1},
        ).scalar_one()

    assert not matches_a_shorter_floor, (
        f"{POLICY}'s interval matches both {configured} and {configured - 1} years, "
        "which no interval does — this comparison is not reading what it thinks it is."
    )


def test_the_floor_is_a_real_bound_rather_than_zero(catalog: sa.Engine) -> None:
    """A policy of `interval '0 years'` is a policy that permits everything.

    `Settings.audit_retention_years` carries `gt=0` for exactly this reason —
    `config.py:341`'s comment says a zero in the environment a migration runs
    from produces "a redactor role entitled to delete the whole audit log,
    including the row written a second ago". The bound is enforced on the
    setting; this asserts it on the artifact the setting produced, which is the
    one an operator's DELETE actually meets.
    """
    literal = _policy_interval(catalog)
    with catalog.connect() as conn:
        positive = conn.execute(
            sa.text("SELECT CAST(:lit AS interval) > CAST('0' AS interval)"), {"lit": literal}
        ).scalar_one()
    assert positive, (
        f"{POLICY} bounds the redactor's DELETE at {literal!r}, which is not a floor: "
        "every row in the audit log is past it, including the one written a second ago."
    )
