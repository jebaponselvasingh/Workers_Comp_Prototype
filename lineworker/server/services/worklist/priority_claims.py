"""The supervisor's priority claims worklist — top 30, ranked, paged (FR-SUP-5/D).

The dashboard's KPI cards, handler table and charts turn a portfolio into
numbers. This is the surface that turns the numbers back into tasking: the
claims in the caller's book that actually need somebody's attention, in the
order the queue would rank them, each with the one thing to do next.

The prototype builds it in `renderSV` (line 1056) and gets it wrong twice, both
of them worth naming because the corrected table looks different from the
screenshot:

    claims.filter(c => c.stage==="treatment" || c.fraudFlag || c.litigationFlag)
          .slice(0, 30)

`.slice(0, 30)` over a filter in *dataset order* — so its "priority claims"
table is priority-ordered in its heading and nowhere else. And its action column
reads `cpNextActions[0]`, a pre-authored copilot string sitting on the claim
record, which is exactly what AD-2 forbids this column from being. Here the cut
is taken after scoring with Story 2.1's scorer, and the action is element 0 of
what Story 3.5's deterministic generator returns. The ten headers, the "showing
top 30 of N" caption and the LITIG-chip-instead-of-a-stage-chip Status cell are
ported exactly.

## The population is a union, not a filter chain

Active treatment ∪ fraud-flagged ∪ litigation-flagged, and the set operator is
the contract rather than an implementation detail: a claim in treatment that is
*also* litigated appears once, and a claim that is only litigated appears at all.
Written as a chain of narrowing filters — the shape a `WHERE` clause invites —
the three arms would intersect instead of unite and the table would collapse to
the handful of claims carrying all three. Each arm is also a different kind of
statement, which is why each resolves through its own owner:

- **Active treatment** is `stage == treatment`, Story 5.1's stage-over-status
  ruling (`summary.py`). The console groups by stage everywhere; a table that
  read `status` here would disagree with the Under Treatment card sitting three
  inches above it.
- **Fraud-flagged** is the registered `fraud_flagged` derivation — the
  dashboard's *review* threshold, **not** `siu_review`'s referral one. Thirteen
  seeded claims against nine, and the Fraud Flags card above this table counts
  the thirteen. Pointing this arm at `siu_review` would be defensible at every
  step and wrong on the screen, which is the failure `FraudFlaggedDerivation`'s
  own docstring exists to prevent.
- **Litigation** is `Claim.litigation_flag`, a stored column, and stays one.

## The ordering is imported, never restated

`priority.order_key` — extracted from `queue._ranked_group` by this story for
exactly this reason. AD-2 says the scorer exists once and it did; its *ordering*
was a lambda inside the queue's grouping loop, and a second surface that "sorts
by the same scorer" is one `reverse=True` away from a table that agrees with the
queue on every score and disagrees on every tie. Sharing the symbol is what makes
`test_the_order_is_story_2_1s_ordering_over_the_same_claims` a real assertion
rather than a circular one.

Nothing here re-weights. The settled penalty applies as written even though this
population contains no settled claims by construction — the scorer is one
function and this module does not get to hold a variant of it.

## The action column is a label, and never a model

`generate_actions(...)[0].label`. Deterministic, ranked by `(urgency, rule
index)`, and produced by the same function the case file's checklist renders —
so a supervisor and a handler reading one claim see one answer. No LLM path
comes near this column: Epic 6's narrative "next best actions" card is a
different, later thing and the `ai_insight` cache is not a source here.

It is also *only* a label. The worklist offers no command, writes nothing and
raises no audit event — a supervisor directs effort by talking to a handler, not
by approving a payment from a dashboard row.

**The pure generator, not `claim_actions`.** That async wrapper is the
natural-looking entry point and the wrong one: it performs six scoped reads and
two document loads *per claim*, so a ten-row page would cost forty round trips
and a full walk a hundred and twenty, on a route with no cache, beside three
other Epic 5 aggregates that each take one. `generate_actions` is pure and takes
its flags and parameters already resolved — the seam that module built for this —
so the four child sources become bulk reads over the page's ids and the parameter
blocks load once, in the router.

**One consequence, recorded rather than corrected.**
`select_latest_note_at_for_claims` is scoped to the caller's *own* diary, with an
argued docstring: counting anybody's note would publish a handler's private
working record to whoever else read the claim. A supervisor has no notes on these
claims, so `latest_note_at` is `None` for every row and the diary check-in rule
fires — meaning one claim's "next best action" can differ between this table and
that handler's checklist. The blast radius is bounded by construction:
`urgencyDiaryCheckIn` is `low` and `generate_actions` ranks on urgency first, so
the row only reaches position zero when nothing medium or high fired, which on a
population of treatment ∪ fraud ∪ litigation claims is rare. Passing the
*handler's* note time instead would be the leak that docstring refuses.
`test_the_action_column_is_generated_with_no_note_of_the_readers_own` pins it so
it cannot change silently.

## Why this module is not `priority.py`

`priority.py` is the **rule** — a pure scorer, a marker rule, an ordering and a
filter predicate, none of which knows what a request is. This module is the
**surface**: a scoped read, a population, a cursor, a page and a payload. The
split is the same one `queue.py` makes against the same neighbour, and it is what
lets Epic 6's copilot quote the score without dragging a cursor along with it.

## Two numbers, one document, and the cursor is the reason

The cap and the page size are `worklist_actions` parameters, not literals: AD-8
names "worklist caps" as JDM-owned, and `test_a_superseded_document_with_a_
smaller_cap_shortens_the_table` demonstrates the tier rather than asserting it.
They live in *that* document rather than beside `priority_weights.pageLimit`
because the queue's cursor records the `priority_weights` version — superseding
it to add a number the queue never reads would invalidate every outstanding queue
cursor in the console. See the migration and `WorklistActions` for the argument
in full.

The one place this module does spell bounds is `MIN_PAGE_LIMIT` /
`MAX_PAGE_LIMIT` / `MAX_CURSOR_AGE`, imported from `queue.py`. Those guard a
*decoded string* — a cursor is caller-supplied input and forging one is trivial —
rather than tuning the business, which is the same line `notes.py` draws.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import ClaimStatus, ReturnStatus, Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, PriorityWeights, WorklistActions
from services import derivations
from services.derivations import (
    FraudFlaggedDerivation,
    PaymentDueDerivation,
    RiskBand,
    RiskDerivation,
    RtwBlockedDerivation,
    SiuReviewDerivation,
    utc_today,
)
from services.worklist import actions as action_rules
from services.worklist.priority import QueueClaim, QueueFlags, order_key, priority_score
from services.worklist.queue import (
    MAX_CURSOR_AGE,
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    InvalidCursor,
)


@dataclass(frozen=True)
class PriorityClaim:
    """One claim, reduced to the facts this table shows and the two rules read.

    A projection rather than the ORM entity, for `QueueClaim`'s and
    `ChartClaim`'s reasons: it keeps `rank` pure and generatable, and it puts
    every input to the population, the score and the action generator next to
    each other instead of spread across a row object with forty other columns.

    **It satisfies `actions.ActionClaim` structurally, by field name** — the way
    `ChartClaim` satisfies `derivations.PaidColumns`. That is the whole point of
    that protocol being structural: the generator reads ten stored columns off
    whatever it is handed, so this class can be its input without this module
    importing an ORM entity or building a second stand-in.
    `test_the_projection_satisfies_the_action_generators_protocol` asserts the
    conformance rather than leaving it to a type-checker nobody runs at review
    time.

    **`days_open` is here rather than `froi_date`** for the reason `QueueClaim`
    gives: it is *derived* (`services/derivations/open_duration`), and both the
    scorer and the Days Open column must consume the registry's answer rather
    than age a date themselves.

    **`fraud_score` is the raw score, not a band.** The column renders the number
    the fraud model produced, tinted by the registered `fraud_flagged`
    derivation's answer — which arrives separately, on `PriorityFlags`, because
    it is a rule's answer and this class carries facts.
    """

    claim_id: str
    stage: Stage
    status: ClaimStatus
    return_status: ReturnStatus
    severity_score: int
    days_open: int
    injury_type: str
    worker_name: str
    employer_short_name: str
    handler_name: str
    surgery_required: bool
    litigation_flag: bool
    fraud_flag: bool
    fraud_score: int
    osha_recordable: bool
    osha_logged: bool
    attorney_rep: bool
    rtw_rec: date | None
    actual_rtw: date | None


@dataclass(frozen=True)
class PriorityFlags:
    """The AD-10 derived values one row needs, as the registry computed them.

    `QueueFlags`' four — the band and the three the scorer weights — plus
    `fraud_flagged`, which `QueueFlags` has no use for and this table has two:
    it is the population's fraud arm *and* the tint on the Fraud Score column.

    Kept as a class of its own rather than by widening `QueueFlags`, because
    that dataclass is the scorer's second argument and every field on it is
    something `priority_score` reads. A fifth member nothing in the formula
    touches would be an invitation to weight it.
    """

    risk: RiskBand
    siu_review: bool
    rtw_blocked: bool
    payment_due: bool
    fraud_flagged: bool


@dataclass(frozen=True)
class PriorityRow:
    """One finished row of the table — the ten columns, ready to render.

    Every field is either a stored fact or a value some registered computer
    already decided. Nothing here is a hint the client finishes: `severity_band`
    is a band rather than a comparison to make, `fraud_flagged` is the tint's
    answer rather than a cut-off to apply, and `next_best_action` is a sentence
    rather than a rule to evaluate.

    `stage` and `litigation_flag` travel together because the Status cell is a
    choice between them — the prototype shows a LITIG chip on a litigated claim
    and the stage pill otherwise, and which of the two is *displayed* is a
    presentation decision the UI owns. What it must not do is decide the flag.

    `fraud_score` is the number and `fraud_flagged` is the tint. Both, because
    the column shows the score and colours it by a threshold the browser must
    never hold — publishing only the score would push the comparison into the
    client, and publishing only the flag would lose the figure the prototype's
    column exists to show.
    """

    claim_id: str
    worker: str
    employer_short_name: str
    injury_type: str
    severity_band: RiskBand
    fraud_score: int
    fraud_flagged: bool
    handler_name: str
    days_open: int
    next_best_action: str
    stage: Stage
    litigation_flag: bool


@dataclass(frozen=True)
class PriorityClaims:
    """One page of the worklist, plus what a caption needs to explain it.

    **`total` is the population *before* the cap, and it is stable across
    pages.** The caption reads "showing top 30 of 38": thirty is `cap` and
    thirty-eight is `total`, and neither is `len(items)`. Publishing the
    post-cap count instead would make the caption say "top 30 of 30", which is
    true, circular and tells a supervisor nothing about how much of her book
    qualified. Every page of a walk reports the same `total` for the same
    reason `StageGroup.total` does — a count that shrank as the page moved
    would misdescribe the book.

    `cap` rides along beside it because the caption quotes it and because it is a
    rule document's answer: superseding `worklist_actions` has to move the row
    count *and* the sentence explaining it, together, with nothing deployed.

    The three thresholds are published for `PortfolioSummary`'s reason: the
    severity chips and the fraud tint were decided at these numbers, so a client
    holding any of them would be a second copy of a rule it cannot see change.
    They come off the derivations that did the deciding, so the published values
    are provably the ones the rows were produced at.

    **`truncated` says whether the cap actually bit**, and it ships because the
    caption is two different sentences and only the server can tell which one is
    true. A scoped supervisor with eight qualifying claims must not be told
    "showing top 30 of 8" — a sentence that quotes a cap that cut nothing and
    reads as a promise of thirty rows beside eight. The client cannot decide
    this for itself: comparing `total` against `cap` in the browser is precisely
    the client-side rule evaluation `noDerivation.test.ts` forbids, and AD-1
    forbids in general. So the comparison happens once, here, where both numbers
    were decided. `charts.Distribution` publishes `truncated` beside `limit` and
    `total_categories` for exactly this reason, and this is that convention on a
    list rather than on a ranked series.

    `rules_version` names the `worklist_actions` document the cap came from. It
    rides along unrendered, as it does on the three sibling dashboard payloads:
    it is what makes a stored or forwarded response self-describing, and the only
    thing a client wanting to invalidate on a rules change could key on.
    """

    items: tuple[PriorityRow, ...]
    next_cursor: str | None
    total: int
    cap: int
    truncated: bool
    high_risk_severity_min: int
    med_risk_severity_min: int
    fraud_flag_score_min: int
    rules_version: int


@dataclass(frozen=True)
class Cursor:
    """Where a page ended, and every rule that decided where that was.

    `queue.Cursor`'s shape and its division, narrowed to what this list has.
    There is no filter and no stage here — the worklist is one list with one
    population — so what remains is an offset, a page size, a day and **three**
    rule-document versions.

    **Compared.** Each of the three moves the page, and a page cut under a
    superseded one is not a page of the list being asked for:

    - `weights_version` (`priority_weights`) decides the *ordering*.
    - `thresholds_version` (`derivation_thresholds`) decides the flags the score
      reads **and** the fraud arm of the population — so it can change which
      claims are in the list as well as where they sit in it.
    - `actions_version` (`worklist_actions`) decides the **cap**, which is the
      one this list has and the queue does not. A cap retuned from thirty to
      twenty under a caller holding an offset of thirty would otherwise page
      past the end of a list that had shrunk beneath them.

    All three are compared against the versions effective **today**, which is
    why `priority_claims` resolves nothing at the cursor's own date: a
    comparison against the versions effective on the date the cursor recorded
    could only ever succeed, which is how the queue's arrangement was wrong on
    its first outing.

    **Reused.** `as_of` and `limit` describe the *window* rather than the rules,
    and re-deriving either mid-list is what loses or repeats a claim — a "Show
    more" issued at 23:59:59 UTC and answered at 00:00:01 would re-age every
    claim and re-rank the list around it. Both are still validated (a future
    date, a date older than `MAX_CURSOR_AGE`, a page size outside the route's
    range) because a cursor is caller-supplied input like any other.
    """

    offset: int
    limit: int
    weights_version: int
    thresholds_version: int
    actions_version: int
    as_of: date


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `queue.encode_cursor`'s form.

    Opaque by intent rather than by encryption, for that function's reason: it
    carries no claim data and nothing a caller could use to widen their scope
    (scope is never in a request — AD-7), so obscurity is doing no security
    work. What the encoding buys is that clients treat it as a token to hand
    back rather than an offset to increment.
    """
    payload = json.dumps(
        {
            "o": cursor.offset,
            "l": cursor.limit,
            "v": cursor.weights_version,
            "t": cursor.thresholds_version,
            "a": cursor.actions_version,
            "d": cursor.as_of.isoformat(),
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(raw: str, as_of: date | None = None) -> Cursor:
    """Parse a cursor, or refuse it. Never a silent fallback to page one.

    `queue.decode_cursor`'s body and every one of its arguments, which are worth
    restating because each is a defect that reached that function first:

    - Answering page one for an undecodable cursor would turn a client bug into
      an infinite "Show more" that re-appends the same claims for ever.
    - `ArithmeticError` sits in the except tuple beside `ValueError` because
      `json` accepts the literal `Infinity` and `int(float("inf"))` raises
      `OverflowError`, which is **not** a `ValueError` — a forged cursor
      carrying it escaped the queue as a 500 rather than the 400 this function
      exists to produce.
    - `limit` is held to `MIN_PAGE_LIMIT`–`MAX_PAGE_LIMIT`. The route declares no
      `limit` parameter at all, so the cursor is the *only* place a page size
      could be smuggled in; unbounded, it would be a way to ask for a ten-
      million-row page that no validator ever saw.
    - `as_of` is bounded by `MAX_CURSOR_AGE`. It cannot select a rule document
      (see `priority_claims`), so a hostile date never reaches the loader — but
      a claim aged against the year 3000 ranks by arithmetic nobody asked for,
      and "reload from the first page" is the truthful answer.

    The three version fields are parsed and bounded here and *compared* in
    `priority_claims`, which is the only place that knows what is effective
    today.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        cursor = Cursor(
            offset=int(data["o"]),
            limit=int(data["l"]),
            weights_version=int(data["v"]),
            thresholds_version=int(data["t"]),
            actions_version=int(data["a"]),
            as_of=date.fromisoformat(data["d"]),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        ArithmeticError,
        binascii.Error,
        UnicodeDecodeError,
    ) as exc:
        raise InvalidCursor("The pagination cursor is not readable.") from exc
    if cursor.offset < 0:
        raise InvalidCursor("The pagination cursor names a negative position.")
    if not MIN_PAGE_LIMIT <= cursor.limit <= MAX_PAGE_LIMIT:
        raise InvalidCursor(
            f"The pagination cursor names a page size of {cursor.limit}; "
            f"it must be between {MIN_PAGE_LIMIT} and {MAX_PAGE_LIMIT}."
        )
    today = as_of or utc_today()
    if cursor.as_of > today:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.as_of}, which is in the future; "
            "reload the worklist from the first page."
        )
    if today - cursor.as_of > MAX_CURSOR_AGE:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.as_of} and the claims it ranked have "
            f"aged {(today - cursor.as_of).days} days since; "
            "reload the worklist from the first page."
        )
    return cursor


@dataclass(frozen=True)
class _Computers:
    """The five registered computers this module folds with, built once.

    A bundle rather than five parameters, because `_flags_of` is called once per
    claim in the caller's loop and a five-argument call there would put the
    build order and the call order in two places. `queue._rows_to_cards` binds
    the same five as locals inside the one function that uses them; this module
    needs them across a helper boundary, so they travel as a value.
    """

    risk: RiskDerivation
    siu_review: SiuReviewDerivation
    rtw_blocked: RtwBlockedDerivation
    payment_due: PaymentDueDerivation
    fraud_flagged: FraudFlaggedDerivation

    @classmethod
    def of(cls, thresholds: DerivationThresholds) -> "_Computers":
        """Build all five from one parameter block — the registry's whole point."""
        return cls(
            risk=derivations.risk.for_thresholds(thresholds),
            siu_review=derivations.siu_review.for_thresholds(thresholds),
            rtw_blocked=derivations.rtw_blocked.for_thresholds(thresholds),
            payment_due=derivations.payment_due.for_thresholds(thresholds),
            fraud_flagged=derivations.fraud_flagged.for_thresholds(thresholds),
        )


def _flags_of(claim: PriorityClaim, computers: _Computers) -> PriorityFlags:
    """One claim's five derived values, every one of them asked of the registry.

    Takes the built computers rather than the threshold block, which is the
    difference between one build and one per claim — `_rows_to_cards`' shape in
    `queue.py`, for its reason: a hundred claims cost one parameter load, not a
    hundred. `rank` builds them once, above its loop.
    """
    band = computers.risk.of(claim.severity_score)
    return PriorityFlags(
        risk=band,
        siu_review=computers.siu_review.of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
        rtw_blocked=computers.rtw_blocked.of(
            claim_id=claim.claim_id,
            stage=claim.stage,
            return_status=claim.return_status,
            risk=band,
        ),
        payment_due=computers.payment_due.of(claim_id=claim.claim_id, stage=claim.stage),
        fraud_flagged=computers.fraud_flagged.of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
    )


def _qualifies(claim: PriorityClaim, flags: PriorityFlags) -> bool:
    """Is this claim in the worklist's population? The union, spelled as one.

    Three arms joined by `or`, in one expression, so that "union" is a property
    of the code a reader can check rather than an invariant three separate
    filters would have to be trusted to preserve. A claim satisfying two of them
    satisfies this once, which is the whole of the dedupe rule.
    """
    return claim.stage is Stage.treatment or flags.fraud_flagged or claim.litigation_flag


def _queue_claim(claim: PriorityClaim) -> QueueClaim:
    """This projection, narrowed to the eleven facts the scorer reads.

    **Restated field by field rather than shared by inheritance**, and the
    choice is deliberate. Making `PriorityClaim` a subclass of `QueueClaim`
    would remove this function and would introduce dataclass inheritance to a
    codebase that has none — and it would quietly widen the scorer's input, so
    that a field added here for the *table* would become a field the *rule*
    could start reading. Keeping them two projections keeps `priority_score`'s
    signature a statement about what the formula needs.

    Named rather than positional (`QueueClaim(*astuple(claim))`), which would
    work today and would be one reordered field away from scoring `injury_type`
    as a worker's name — `charts.portfolio_charts`' argument, on a projection
    where three adjacent fields are all free text.
    """
    return QueueClaim(
        claim_id=claim.claim_id,
        stage=claim.stage,
        status=claim.status,
        severity_score=claim.severity_score,
        days_open=claim.days_open,
        injury_type=claim.injury_type,
        worker_name=claim.worker_name,
        employer_short_name=claim.employer_short_name,
        surgery_required=claim.surgery_required,
        litigation_flag=claim.litigation_flag,
        fraud_flag=claim.fraud_flag,
    )


def _scored(flags: PriorityFlags) -> QueueFlags:
    """The four members the scorer weights, without the fifth it must not see.

    `PriorityFlags` says why the fraud-review flag is not on `QueueFlags`; this
    is the other half of the same decision, at the one call site where the two
    meet.
    """
    return QueueFlags(
        risk=flags.risk,
        siu_review=flags.siu_review,
        rtw_blocked=flags.rtw_blocked,
        payment_due=flags.payment_due,
    )


def rank(
    caseload: Sequence[PriorityClaim],
    thresholds: DerivationThresholds,
    weights: PriorityWeights,
    cap: int,
) -> tuple[list[tuple[PriorityClaim, PriorityFlags]], int]:
    """The population, ranked and cut. Pure — no session, no clock, no rule load.

    Returns the capped list **and the population size before the cap**, because
    the caption needs both and the second cannot be recovered from the first
    once the cut has happened. Two values from one fold rather than two passes,
    for `charts_of`'s reason: "these describe one set of claims" is then
    structural instead of a comment.

    Four steps, in this order, and the order is the rule:

    1. Derive every claim's flags through the registry (AD-10). Before the
       population, because one of the three arms *is* a derived value.
    2. Select the union (`_qualifies`).
    3. Sort with `priority.order_key` over `priority.priority_score` — the
       queue's ordering and the queue's scorer, imported. Not "the same
       arithmetic": the same two functions.
    4. Cut at `cap`. **After** the sort, which is what makes the cap mean "the
       thirty most urgent" rather than "the first thirty that qualified" — the
       prototype's `.slice(0, 30)` over an unsorted filter is precisely the
       version of this that ranks nothing.

    The score is computed **once per claim**, into the sort key, rather than
    inside the comparator: `sorted(key=…)` calls its key once per element, so a
    key that scored on the fly would still be correct, but a `cmp`-shaped
    comparator or a re-entrant key would score O(n log n) times over five
    registered derivations. Building the pair first also means the value the
    ordering used is the value a test can assert against.

    `cap` arrives as an argument rather than being read from a parameter block,
    so this function holds no number at all and a test can demonstrate the tier
    by passing a different one. Where it comes from is `priority_claims`'. A cap
    below one is refused rather than sliced: `WorklistActions.__post_init__`
    already refuses it at the only production source, but this function is
    exported and pure, and `scored[:0]` would return an empty page beside a
    non-zero `total` — a worklist that is populated and empty at once.
    """
    if cap < 1:
        raise ValueError(f"cap must be at least 1, got {cap}")
    # Built once, above the loop — see `_Computers`.
    computers = _Computers.of(thresholds)
    scored: list[tuple[PriorityClaim, PriorityFlags, QueueClaim, float]] = []
    for claim in caseload:
        flags = _flags_of(claim, computers)
        if not _qualifies(claim, flags):
            continue
        queue_claim = _queue_claim(claim)
        scored.append(
            (claim, flags, queue_claim, priority_score(queue_claim, _scored(flags), weights))
        )
    scored.sort(key=lambda entry: order_key((entry[2], entry[3])))
    return [(claim, flags) for claim, flags, _queue, _score in scored[:cap]], len(scored)


def _row(
    claim: PriorityClaim,
    flags: PriorityFlags,
    label: str,
) -> PriorityRow:
    """One claim, its flags and its action, as the ten columns render them.

    Named rather than positional for `_queue_claim`'s reason, over a
    twelve-field row where `worker`, `employer_short_name`, `injury_type` and
    `handler_name` are four adjacent strings: a swap would type-check, run, and
    put a handler's name in the Worker column of every row.
    """
    return PriorityRow(
        claim_id=claim.claim_id,
        worker=claim.worker_name,
        employer_short_name=claim.employer_short_name,
        injury_type=claim.injury_type,
        severity_band=flags.risk,
        fraud_score=claim.fraud_score,
        fraud_flagged=flags.fraud_flagged,
        handler_name=claim.handler_name,
        days_open=claim.days_open,
        next_best_action=label,
        stage=claim.stage,
        litigation_flag=claim.litigation_flag,
    )


#: What the action column shows for a claim on which no rule fired at all.
#:
#: Reachable in principle rather than on the seeded book: `generate_actions`
#: pads a short list with routine review rows, and all three padding rows are
#: conditional, so a settled claim with no documents and no schedule produces an
#: empty tuple. A settled claim cannot be in this population — but "the tuple is
#: never empty" is a fact about today's rules and today's arms, not a property
#: of the generator, and `[0]` on an empty tuple is an `IndexError` inside a
#: dashboard read. The em dash is the prototype's own answer in the same cell
#: (`renderSV` writes `"—"` when `cpNextActions` is absent), so the fallback is
#: a port rather than an invention.
NO_ACTION = "—"


async def priority_claims(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    weights: PriorityWeights,
    params: WorklistActions,
    *,
    cursor: str | None = None,
    as_of: date | None = None,
) -> PriorityClaims:
    """One page of the caller's priority worklist — the endpoint's one call.

    Takes its three parameter blocks rather than fetching any of them, which is
    `portfolio_charts`' and `handler_benchmarks`' signature and their reason: the
    route loads them once and hands them down, so this stays a composition of
    scope and parameters instead of dragging the rules engine into an aggregate
    that would then have to decide which date to resolve at.

    **Two dates, and they are not interchangeable** — `claim_queue`'s division,
    which this cursor inherits. `today` is when the request is served and is what
    the three recorded versions are compared against, so a page cut under a
    superseded document is refused rather than served from a retired ranking.
    `aged_on` is the day the claims are aged against; a cursor's recorded day
    wins there, and only there, so page two is cut from the list page one was.

    **Five awaited reads, regardless of page size.** One
    `select_priority_rows` over the whole scoped book — the ranking is a total
    order over the population, so there is no page of it to read — then four
    bulk child reads over **the page's** ids alone. That last narrowing is the
    difference between five reads and a hundred and twenty on a full walk; see
    the module docstring, and
    `test_the_aggregate_takes_exactly_five_scoped_reads`, which counts them.

    No role appears anywhere in this path. Supervisor, analyst and handler take
    the identical scoped route through the repository, which is the whole of AD-7
    on this surface — see the route's docstring for why this endpoint has no gate
    where `/dashboard/handler-benchmarks` has one.
    """
    decoded = decode_cursor(cursor, as_of) if cursor is not None else None
    today = as_of or utc_today()
    aged_on = decoded.as_of if decoded is not None else today

    if decoded is not None:
        if decoded.weights_version != weights.version:
            raise InvalidCursor(
                f"That page was ranked by priority_weights v{decoded.weights_version}, "
                f"and v{weights.version} is now effective; "
                "reload the worklist from the first page."
            )
        if decoded.thresholds_version != thresholds.version:
            raise InvalidCursor(
                f"That page was derived from derivation_thresholds "
                f"v{decoded.thresholds_version}, and v{thresholds.version} is now effective; "
                "reload the worklist from the first page."
            )
        if decoded.actions_version != params.version:
            raise InvalidCursor(
                f"That page was capped by worklist_actions v{decoded.actions_version}, "
                f"and v{params.version} is now effective; "
                "reload the worklist from the first page."
            )
        # The page size is checked like the three versions above it, and for a
        # sharper reason than they have. A cursor is unsigned base64 JSON, so
        # `decoded.limit` is caller-supplied in every sense that matters; taking
        # it on trust would hand back through the cursor exactly the parameter
        # the route refuses to declare, and one forged `l` would make a single
        # request generate actions for the whole capped list instead of a page.
        # `decode_cursor` bounds it against the transport ceiling and cannot do
        # more — the effective rule is not in scope there. Here it is.
        if decoded.limit != params.supervisor_worklist_page_limit:
            raise InvalidCursor(
                f"That page was cut at {decoded.limit} rows, and the worklist now "
                f"pages at {params.supervisor_worklist_page_limit}; "
                "reload the worklist from the first page."
            )

    days_open = derivations.days_open.for_thresholds(thresholds)
    rows = await claim_repo.select_priority_rows(db, ctx)
    # Named rather than positional (`PriorityClaim(*row)`), which would work and
    # would be one reordered projection away from a table showing every worker's
    # employer as their name — `charts.portfolio_charts`' argument, on a
    # projection carrying four adjacent free-text columns and five adjacent
    # booleans.
    caseload = [
        PriorityClaim(
            claim_id=row.claim_id,
            stage=row.stage,
            status=row.status,
            return_status=row.return_status,
            severity_score=row.severity_score,
            days_open=days_open.of(row.froi_date, aged_on),
            injury_type=row.injury_type,
            worker_name=row.worker_name,
            employer_short_name=row.employer_short_name,
            handler_name=row.handler_name,
            surgery_required=row.surgery_required,
            litigation_flag=row.litigation_flag,
            fraud_flag=row.fraud_flag,
            fraud_score=row.fraud_score,
            osha_recordable=row.osha_recordable,
            osha_logged=row.osha_logged,
            attorney_rep=row.attorney_rep,
            rtw_rec=row.rtw_rec,
            actual_rtw=row.actual_rtw,
        )
        for row in rows
    ]

    ranked, total = rank(caseload, thresholds, weights, params.supervisor_worklist_cap)
    # The document's, always — and the cursor's `limit` has just been checked to
    # equal it, so the two cannot disagree here. Reading the rule rather than
    # the decoded field is the belt to that check's braces: page size is a
    # published rule, not a caller's choice, and this line is where that
    # sentence has to be true rather than merely asserted.
    page_size = params.supervisor_worklist_page_limit
    offset = decoded.offset if decoded is not None else 0
    # A cursor is only ever issued for an offset that has rows behind it, so an
    # offset at or past the end describes a list this one is not. The
    # alternative — an empty page beside a non-zero `total` and a null
    # `nextCursor` — reads as a worklist that is simultaneously populated and
    # finished, which a client can only render as a lie. `queue._page`'s rule.
    if offset > 0 and offset >= len(ranked):
        raise InvalidCursor(
            f"That page starts at {offset} but the worklist now holds {len(ranked)} claims; "
            "reload the worklist from the first page."
        )
    window = ranked[offset : offset + page_size]

    labels = await _action_labels(db, ctx, window, params, aged_on)
    risk = derivations.risk.for_thresholds(thresholds)
    fraud = derivations.fraud_flagged.for_thresholds(thresholds)
    return PriorityClaims(
        items=tuple(_row(claim, flags, labels[claim.claim_id]) for claim, flags in window),
        # Null only when the worklist is finished — never "null because this page
        # came back short", which would strand the tail of a list the caption
        # has already told the reader how long it is.
        next_cursor=(
            None
            if offset + page_size >= len(ranked)
            else encode_cursor(
                Cursor(
                    offset=offset + page_size,
                    limit=page_size,
                    weights_version=weights.version,
                    thresholds_version=thresholds.version,
                    actions_version=params.version,
                    as_of=aged_on,
                )
            )
        ),
        total=total,
        cap=params.supervisor_worklist_cap,
        # Decided here rather than in the browser, once, from the two numbers
        # this function is the only place to hold at the same time.
        truncated=total > params.supervisor_worklist_cap,
        high_risk_severity_min=risk.high_min,
        med_risk_severity_min=risk.med_min,
        fraud_flag_score_min=fraud.fraud_score_min,
        rules_version=params.version,
    )


async def _action_labels(
    db: AsyncSession,
    ctx: CallerContext,
    window: Sequence[tuple[PriorityClaim, PriorityFlags]],
    params: WorklistActions,
    as_of: date,
) -> Mapping[str, str]:
    """The top action's label for every claim on one page — four reads, not 4N.

    The four bulk repository reads and then `generate_actions` per row, which is
    pure and synchronous. Element **0** of what it returns, because that tuple is
    totally ordered on `(urgency, rule index)` — `actions.py`'s docstring names
    this story as the reason that order had to be total rather than merely
    sorted: without the rule-index tiebreak a claim's "next best action" would
    change between releases without the claim changing.

    `latest_note_at` is passed explicitly for every row, `None` included.
    `generate_actions` made that keyword required precisely so a caller who
    forgot the read would get a `TypeError` rather than a silently re-opened
    check-in on every treatment claim in the book.

    **No `DerivationThresholds` in this signature**, and the absence is the
    point: the three flags the generator reads are already decided, on
    `PriorityFlags`, by the registry (AD-10). Taking the block would let this
    function re-derive one, which is the second computer that class exists to
    prevent.
    """
    claim_ids = [claim.claim_id for claim, _flags in window]
    documents = await claim_repo.select_documents_for_claims(db, ctx, claim_ids)
    bills = await claim_repo.select_bills_for_claims(db, ctx, claim_ids)
    weeks = await claim_repo.select_payment_schedule_for_claims(db, ctx, claim_ids)
    notes: Mapping[str, datetime] = await claim_repo.select_latest_note_at_for_claims(
        db, ctx, claim_ids
    )

    labels: dict[str, str] = {}
    for claim, flags in window:
        generated = action_rules.generate_actions(
            claim,
            documents=documents.get(claim.claim_id, ()),
            bills=bills.get(claim.claim_id, ()),
            weeks=weeks.get(claim.claim_id, ()),
            latest_note_at=notes.get(claim.claim_id),
            flags=action_rules.ClaimFlags(
                siu_review=flags.siu_review,
                rtw_blocked=flags.rtw_blocked,
                payment_due=flags.payment_due,
            ),
            params=params,
            as_of=as_of,
        )
        labels[claim.claim_id] = generated[0].label if generated else NO_ACTION
    return labels


__all__ = [
    "NO_ACTION",
    "Cursor",
    "InvalidCursor",
    "PriorityClaim",
    "PriorityClaims",
    "PriorityFlags",
    "PriorityRow",
    "decode_cursor",
    "encode_cursor",
    "priority_claims",
    "rank",
]
