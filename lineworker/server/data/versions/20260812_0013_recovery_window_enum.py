"""`claim.recovery`: the dataset's display text becomes a snake_case enum
(Story 2.3, code review).

Story 1.2 seeded this column as `Text` holding the prototype's own strings —
`"4-6 Weeks"`, `"Greater than 1 Year"` — which was the honest shape at the
time: nothing constrained the value and nothing but a regex read it. Story
2.3's inline-edit command closed the vocabulary to five options and refuses
anything else, so the Enums convention now attaches ("enum values snake_case
lowercase in DB/API; UI owns display labels").

**Why this is worth a migration rather than a note.** While the stored value
was the display string, `services/derivations/treatment_progress.py` had to
recover a claim's expected duration by running a regex over prose. That made
the *label* load-bearing: rewording it changed a derived value, and a
lowercase `w` dropped the claim onto a default window. Tokens make the
duration a lookup keyed by a member and hand the wording back to the browser,
where a label belongs.

**The mapping is written out here, and asserted before it is applied.**
`upgrade()` refuses to run if the table holds a value this migration does not
know, rather than silently mapping it to a default — a claim whose recovery
window this build cannot name is a data question, not something a migration
should answer on its own. `tests/test_recovery_window_migration.py` asserts
the mapping covers exactly the seeded distinct values.

**`data/seed/seed_data.json` is deliberately left holding the display
strings.** It is the extraction artifact — the prototype's data as the
prototype records it — and Story 1.2's tests compare the database against it
on that understanding. Migration 0004 still inserts the text; this revision
converts it immediately afterwards on a fresh database, so the seed file
stays a faithful record of the source and the conversion stays reviewable in
one place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_recovery_window_enum"
down_revision: str | None = "0012_case_file_rule_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The prototype's five options → the tokens that replace them. The keys are
#: exactly the strings `RECOVERY_OPTIONS` holds in
#: docs/Workers_Comp_Prototype.html line 655.
TEXT_TO_TOKEN: dict[str, str] = {
    "0-2 Weeks": "weeks_0_2",
    "2-4 Weeks": "weeks_2_4",
    "4-6 Weeks": "weeks_4_6",
    "6-8 Weeks": "weeks_6_8",
    "Greater than 1 Year": "over_1_year",
}

ENUM_NAME = "recovery_window"


def upgrade() -> None:
    bind = op.get_bind()

    # Refuse rather than guess. A value outside the five is a statement about
    # the data that a migration has no standing to resolve — mapping it to a
    # default would change a claim's expected recovery silently, and mapping
    # it to NULL would break a NOT NULL column at the worst moment.
    unknown = sorted(
        row[0]
        for row in bind.execute(sa.text("SELECT DISTINCT recovery FROM claim"))
        if row[0] not in TEXT_TO_TOKEN
    )
    if unknown:
        raise RuntimeError(
            f"claim.recovery holds {len(unknown)} value(s) this migration cannot map: "
            f"{unknown}. Add them to TEXT_TO_TOKEN and to RecoveryWindow, or fix the data."
        )

    recovery_window = sa.Enum(*TEXT_TO_TOKEN.values(), name=ENUM_NAME)
    recovery_window.create(bind)

    # One statement per option rather than a CASE expression: the intent is
    # a table, and a reviewer should be able to read the mapping off the
    # migration without evaluating SQL in their head.
    for text_value, token in TEXT_TO_TOKEN.items():
        bind.execute(
            sa.text("UPDATE claim SET recovery = :token WHERE recovery = :text").bindparams(
                token=token, text=text_value
            )
        )

    op.alter_column(
        "claim",
        "recovery",
        existing_type=sa.Text(),
        type_=recovery_window,
        existing_nullable=False,
        postgresql_using=f"recovery::{ENUM_NAME}",
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.alter_column(
        "claim",
        "recovery",
        existing_type=sa.Enum(*TEXT_TO_TOKEN.values(), name=ENUM_NAME),
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using="recovery::text",
    )
    for text_value, token in TEXT_TO_TOKEN.items():
        bind.execute(
            sa.text("UPDATE claim SET recovery = :text WHERE recovery = :token").bindparams(
                token=token, text=text_value
            )
        )

    # Dropped explicitly: `alter_column` away from a native enum leaves the
    # type behind, and a re-upgrade would then fail on "type already exists".
    # Story 2.2's 0010 learned the same thing about `doc_type`.
    sa.Enum(name=ENUM_NAME).drop(bind)
