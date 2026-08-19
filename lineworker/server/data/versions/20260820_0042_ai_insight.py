"""ai_insight — the per-claim AI narrative cache (Story 6.2, AC 1).

Story 6.1 created the three tables Epic 6 *retrieves* from. This is the first
table it *writes narratives into*, and it is owned by `services/rag` under
AD-12 exactly as those three are: one module inserts and updates a row here,
and everything else asks.

**Structure only, and — unusually — no seed revision follows it.** The
0035/0036, 0032/0033, 0026/0027 and 0040/0041 pairs all seed what they create,
and 0041 goes further by pre-creating one `claim_embedding` row per claim as
*work to be done*. Nothing of the sort is possible here: an embedding row can
be pre-created because it is a queue entry with a NULL vector, whereas an
insight row's whole content is a model's answer, and a migration cannot reach a
model server any more than it can reach Ollama's embeddings endpoint. So this
table starts empty and stays empty until `agents/insights.py` generates through
`services/rag`. The empty state is a first-class answer rather than a gap — the
API reports every missing kind as `not_generated` and the tab renders four
explicit empty cards (NFR-3).

## No `version` column

AD-4's compare-and-swap arbitrates concurrent writers of a mutable row, and
this table has neither a second writer nor a mutation. `services/rag` is the
sole writer (AD-12) and its writes are whole-row replacements of a derived
value: a refresh does not edit a narrative, it generates a new one and
overwrites, so two runs racing on one claim leave the row holding one of two
complete generations and never a blend. There is nothing for a CAS to protect.
`claim_embedding` (0040) reaches the same conclusion from the derived-data
direction and `timeline_event` (0010) from the append-only one.

The corollary is the part worth stating, because an unexplained missing
`version` is indistinguishable from an oversight. A version column is what an
inline edit sends back as `expectedVersion` — so its absence here is the schema
saying that FR-H-9's "AI insights are never user-editable" is structural rather
than a habit the UI happens to keep. AD-10 puts AI narratives in a cache with a
generation timestamp, never in a claim column and never behind an edit
affordance; a story that wanted a handler to correct a sentence would be
proposing that model output become claim data, and the honest move is to
regenerate the cache instead.

## No `stale` flag either, and that is where this parts company with 0040

`claim_embedding` needs one because a stale embedding is a *wrong answer*: a
similar-case search ranked against a claim's pre-edit summary returns the wrong
neighbours with nothing on any screen to say so. A stale insight is a *dated*
answer, and it is dated beside its own `generated_at` on the card — which is
the whole of AD-10's "always rendered with its generation timestamp". The
reader can see how old it is, so nothing here has to decide for them.

## `UNIQUE (claim_id, kind)`, and what it buys

The cache holds the latest generation per kind, not a history, so the pair is
the row's identity. Three things follow. A refresh **replaces** rather than
appends, which is what makes "refresh twice, still four rows" a property of the
schema rather than of the command. `upsert_insight` is one idempotent
`INSERT … ON CONFLICT DO UPDATE` rather than a select-then-branch with a race
in the gap — without a conflict target there is nothing to name in `ON
CONFLICT`. And the constraint's backing index doubles as the index the
`claim_id` foreign key needs: PostgreSQL does not create one automatically, so
without it Story 8.1's purge cascade would scan this table once per deleted
claim (0035's reason for `ix_email_log_claim_id`).

## `ai_insight_attempt`, and why the queue needs a second table

The scheduled refresh's work queue is "claims missing at least one kind",
ordered and bounded. Ordered by `claim.id` alone — which is how this shipped —
a claim whose generation fails *deterministically* sits at the head of every
batch for ever: it is still missing a kind, so it is still pending, so it is
selected again, and the run re-spends its completions every tick. With the
refresh breaking on an unavailable model server (it does, deliberately: a
container that did not answer for one claim will not answer for the next
ninety-nine), one such claim at the head can stop the entire portfolio from ever
generating. That is the review of Story 6.2's H4.

The fix is one row per claim recording when it was last *attempted*, regardless
of outcome, and an `ORDER BY` that puts the least-recently-attempted claim
first. A claim that has just been tried goes to the back of the queue behind
every other pending claim, so a permanent failure costs one slot per full lap
instead of the whole batch — a backoff whose interval is "everything else
first", which needs no arithmetic and no tuning knob.

It is a separate table rather than a column because there is nowhere on
`ai_insight` to put it: a kind that *fails* writes no row at all (that is the
I/O matrix's "no half-written card"), so the attempt has to be recorded
somewhere a failure can reach. Per claim rather than per kind because the queue
selects claims. And no failure counter: the ordering already de-prioritises a
failing claim, and a counter would be a second piece of state to decide when to
reset.

**Writes to it emit no `audit_event`, and the exemption is argued rather than
assumed.** AD-4 is stated without a carve-out, so a table that is written and
committed on every tick with no event beside it has to say why. An audit trail
answers "who changed what, and when"; this table records neither a change nor a
fact about a claim, only that a *scheduler* looked at one — the same class of
thing as a cursor position or the contents of a work queue, and nothing else in
this build audits one of those. What a refresh actually produced is audited, as
`ai_insight.generated`, once per card, with the claim, the kind and the model.

The cost of the alternative is what settles it: one event per claim per tick,
in the table Epic 8's retention and review pass reads, over the whole book on a
configured interval — a flood of "a job ran" burying the events that say a
model wrote something into the case file's neighbourhood. `AiInsightAttempt`'s
class docstring carries the same paragraph and
`tests/test_ai_insights.py::test_the_attempt_cursor_is_deliberately_unaudited`
asserts it, so the decision is falsifiable rather than invisible (follow-up
review of Story 6.2, B5).

## The enum is written out literally

`insight_kind`'s four members appear below as strings rather than imported from
`data/models/enums.py`, under the rule 0013, 0014, 0026, 0032, 0035 and 0040
all keep: a migration is a frozen historical record of what the database was
asked to become, and one that read a live enum would silently change what it
did the day somebody added a fifth kind.
`tests/test_ai_insight_migration.py` asserts the two lists agree today.

The type is created by `create_table` as a side effect of the column, so
`downgrade()` has to drop it by hand — the trap 0010, 0013, 0014, 0026 and 0032
each record.

## Grants

Full CRUD, and each verb is wanted. `services/rag` INSERTs and UPDATEs on every
refresh; DELETE is there because this table is PHI-class under AD-11 — a
narrative about a claim is claim data — and Story 8.1's purge cascade should
not discover that it cannot touch a table (0035's argument for
`email_template`). That `services/rag` is the *only* application writer is a
design rule enforced by where the code lives, not by the grant (0010's argument
for `timeline_event`).

Revision ID: 0042_ai_insight
Revises: 0041_seed_knowledge_corpus
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_ai_insight"
down_revision: str | None = "0041_seed_knowledge_corpus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The four cached narratives, in the order the tab renders them. Written out
#: rather than imported — see the module docstring.
INSIGHT_KINDS: tuple[str, ...] = (
    "similar_case_outcomes",
    "reserve_adequacy_review",
    "next_best_actions",
    "fraud_risk_indicators",
)

INSIGHT_KIND_ENUM = "insight_kind"


def upgrade() -> None:
    op.create_table(
        "ai_insight",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(*INSIGHT_KINDS, name=INSIGHT_KIND_ENUM),
            nullable=False,
        ),
        # The validated structure the kind's Pydantic model produced, never a
        # prose blob. JSONB rather than Text so Story 7.1 can aggregate the
        # fraud kind portfolio-wide without parsing a paragraph, and so the
        # figures inside it — every one of them copied from deterministic
        # service output under AD-2 — stay addressable.
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # Which model answered. NOT NULL because a narrative whose author is
        # unknown is a narrative the card cannot label, and the label is half
        # of what stops a generated sentence reading as a claim fact.
        sa.Column("model", sa.Text(), nullable=False),
        # NOT NULL and supplied by the command rather than defaulted by the
        # database: a refresh writes four rows for one claim and they should
        # carry one instant, the same reason `audit_event.at` is a parameter.
        # There is no `server_default` for that reason and 0008's rule does not
        # bite — every INSERT into this table comes from one function.
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_ai_insight_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_insight")),
        # The cache key — see the module docstring on the three things it buys.
        sa.UniqueConstraint("claim_id", "kind", name=op.f("uq_ai_insight_claim_id")),
    )

    op.create_table(
        "ai_insight_attempt",
        # The claim's own key is the primary key: one row per claim, replaced in
        # place by every attempt. No surrogate id and no history — this is a
        # cursor, not a log, and the audit trail of what a refresh actually did
        # is `audit_event` (AD-4).
        sa.Column("claim_id", sa.Integer(), nullable=False),
        # NOT NULL and supplied by the command, `ai_insight.generated_at`'s rule:
        # the value is the run's own instant, so the attempt and the four cards
        # it produced carry one timestamp.
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_ai_insight_attempt_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("claim_id", name=op.f("pk_ai_insight_attempt")),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_insight TO lineworker_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_insight_attempt TO lineworker_app")
    # The identity sequence is new; Story 1.2's blanket sequence grant ran
    # before it existed, so it has to be repeated for it (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_table("ai_insight_attempt")
    op.drop_table("ai_insight")
    # `create_table` created the type as a side effect of the column; dropping
    # the table does not drop it, and a re-`upgrade()` against the leftover
    # type fails with "type already exists" (0010, 0013, 0014, 0026 and 0032
    # all record the same trap).
    sa.Enum(name=INSIGHT_KIND_ENUM).drop(op.get_bind(), checkfirst=True)
