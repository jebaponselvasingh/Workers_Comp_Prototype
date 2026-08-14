"""The reserve adequacy verdict — computed once, here (Story 3.2).

AD-2: `classify_reserve` exists exactly once and every consumer calls it. Today
that is the treatment Overview's paid-vs-reserve card; Story 3.3's Bills
financial summary is the next, and it renders *this* value under the same query
key rather than judging a ratio of its own (AD-10). Epic 6's "reserve adequacy
review" insight may quote the rationale below and must never re-derive it.

## The rule, in one paragraph

Projected remaining exposure is unpaid indemnity plus unpaid medical. Compare
it to the reserve: more than `light_ratio` of it and the claim is **light**
(under-reserved — recommend moving the reserve up); less than `heavy_ratio` and
it is **heavy** (over-reserved — surplus to reallocate); between the two,
**adequate**. A settled claim is none of these — it is `closed_final`, and the
band arithmetic is not run at all. The prototype's `reserveCheck` (line 1407),
ported.

## The comparison is exact integer arithmetic, and the bands are basis points

The story names the bands as `light_ratio: 1.15` and `heavy_ratio: 0.60`; the
document declares them as `lightRatioBp: 11500` and `heavyRatioBp: 6000`. Same
values, different unit, and the reason is Story 3.1's `defaultCompRateBp`
argument applied to a comparison rather than to a product: AC 4 asks for the
boundary to be pinned at *exactly* 1.15 and *exactly* 0.60, and a float ratio
compared against a float threshold makes "exactly" depend on which pair of
cent figures produced it. Cross-multiplying integers —

    projected × 10 000  >  light_ratio_bp × reserve

— is the same comparison with no rounding anywhere in it, so "strictly greater
than 1.15" is a fact about the claim rather than about IEEE-754. The ratio is
still *published*, as basis points, for the reader who wants to see it; it is
not what the verdict is decided by. (See `ReserveCheck.ratio_bp`.)

## The zero-reserve case is the prototype's sentinel, and it is deliberate

`reserveCheck` answers `ratio = reserve > 0 ? projected / reserve : (projected
> 0 ? 2 : 1)` — a claim with no reserve at all is light while any exposure
remains and adequate when none does. That is not a division guard dressed up as
a rule: it is the honest verdict. A claim carrying exposure against a zero
reserve *is* under-reserved, and one carrying none against a zero reserve has
nothing to be wrong about. The branch is kept explicit below rather than left
to fall out of the cross-multiplication, because a reader checking this
function against the prototype should find the prototype's two sentinels.

## An exposure term that is not on file withholds the verdict it could flip

**Story 3.3 closed this seam and the machinery stays.** The `bill` table now
exists and is seeded for every claim, so `unpaid_medical_cents` returns a sum
and both exposure terms are always known — no seeded claim reaches
`indeterminate`. What follows is therefore about a state the console can still
enter rather than one it is stuck in, and it is kept for two reasons: the
typed distinction between "no unpaid bills" and "no bills on file" is the one
Epic 6's honest-degradation work inherits, and deleting a refusal because
today's data never triggers it is how it comes back as a silent default.

`remaining_medical_cents` is `None` — not zero — when the bills cannot be
seen. Zero is a claim with no unpaid bills; `None` is a claim whose bills
nobody can see, and treating the second as the first is how this console came
to advise releasing the reserve on two thirds of an open book.

**Which verdicts survive a missing term is arithmetic, not preference.** The
unknown quantity is non-negative, so the exposure computed without it is a
*lower bound* on the real one. That makes exactly one of the three bands still
sound:

- **`light` stands.** If indemnity alone already exceeds `light_ratio` of the
  reserve, adding bills can only push it further past the same threshold. The
  claim is under-reserved whatever the bills say.
- **`adequate` and `heavy` do not.** Both are claims about an *upper* bound —
  "exposure is no more than this" — and an unknown addition can move either of
  them up a band. A `heavy` verdict is the dangerous one, because its rationale
  tells a handler to reallocate money away from a claim whose costs nobody has
  totalled.

So a claim that cannot be judged is `indeterminate`, which is an answer rather
than an absence — the same argument `closed_final` makes. `projected_remaining_cents`
and `ratio_bp` are then `None` as well, because a total with an unknown term is
not a total and a ratio computed from one is not a ratio. Publishing a lower
bound under a name that reads as the whole figure is the mislabel this section
exists to refuse.

This is Epic 6's honest-degradation principle arriving early, and it costs
nothing that was worth having: the seeded portfolio keeps every one of its
under-reserved warnings and loses only verdicts that were never entitled to
confidence.

## The rationale is service prose, never an LLM's

Deterministic sentences, one per verdict, each embedding the figures it is
entitled to — the prototype's `note`, clause for clause, plus the two this
console needs and the prototype has no equivalent of.
`services/financials/rationale.py` argues at length why a *sentence* is the one
thing this server formats money into; the same argument applies here, and
`format_dollars` is the same function, so the reserve reads identically in this
paragraph and in the row above it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import ScheduleWeekStatus, Stage
from data.repositories import claims as claim_repo
from rules.parameters import ReserveBands, reserve_bands_for
from services.derivations.claim_financials import (
    LineItem,
    ScheduleRow,
    paid_total,
    unpaid_total,
)
from services.financials.benefit import BASIS_POINTS_PER_UNIT, Benefit, round_half_up
from services.financials.materialize import materialize_schedule
from services.financials.rationale import format_dollars
from services.financials.schedule import ScheduleClaim

#: The ratio a zero-reserve claim is reported at when exposure remains, and
#: when none does. The prototype's `2` and `1`, in basis points. They are
#: *reported* values only — the verdict for a zero reserve is decided by the
#: explicit branch in `classify_reserve`, not by comparing these.
ZERO_RESERVE_EXPOSED_RATIO_BP: Final[int] = 2 * BASIS_POINTS_PER_UNIT
ZERO_RESERVE_CLEAR_RATIO_BP: Final[int] = BASIS_POINTS_PER_UNIT


class ReserveVerdict(StrEnum):
    """Snake_case values per the enum convention — the UI owns the labels.

    `closed_final` is a verdict rather than the absence of one, which is why it
    is a member here and not a `None`. A settled claim has been *judged*: there
    is no further exposure, and saying so is different from having no answer.
    The card colours it muted for the same reason the prototype does.

    `indeterminate` is a member on the same argument, one step further. "This
    claim's bills are not on file, so its reserve cannot be judged" is a
    statement about the claim, and it is the statement a handler needs — the
    alternative is a confident band computed from half the inputs, which is
    what this console did until it was pointed out. Neither of the prototype's
    four states corresponds to it, because the prototype has no data that can
    be absent.
    """

    light = "light"
    adequate = "adequate"
    heavy = "heavy"
    closed_final = "closed_final"
    indeterminate = "indeterminate"


#: The settled claim's sentence — the prototype's, verbatim.
#:
#: A module constant rather than a fourth branch's literal because it is the
#: one rationale that names no figures, so a test asserting "a settled claim
#: says nothing about exposure" has something to compare against by identity.
CLOSED_FINAL_RATIONALE: Final[str] = "Claim settled and closed. No further reserve exposure."


class ReserveClaim(ScheduleClaim, Protocol):
    """The claim columns the reserve check reads, on top of the schedule's.

    `BenefitClaim`'s arrangement: extend the narrower protocol rather than
    restate its members, so the two lists cannot drift and the split says what
    each half is for — those three project a schedule, this one is the number
    the projection is judged against.
    """

    @property
    def reserve(self) -> int: ...


@dataclass(frozen=True)
class ReserveCheck:
    """One claim's reserve adequacy verdict, and everything the card states.

    Every money field is integer cents and says so. `ratio_bp` is the exposure
    expressed as basis points of the reserve — 11 500 is 115% — for the reader
    who wants the number behind the word; it is **reported, not decided by**
    (see the module docstring).

    **Three fields are nullable, and each `None` means one specific thing.**
    `remaining_medical_cents` is `None` when the bills are not on file at all,
    which is different from a claim that has none (that is `0`).
    `projected_remaining_cents` is `None` exactly when that is, because a total
    with an unknown term is not a total. `ratio_bp` is `None` whenever no
    complete comparison happened — a settled claim, or an incomplete exposure —
    which makes "there is a ratio" and "a band decided this" the same fact.

    The exposure terms are published separately rather than only as their sum:
    a handler asking why a claim is light is answered by "eleven weeks of
    indemnity still scheduled" or by "$38,000 of bills unpaid", and those are
    different conversations. The sum is sent rather than left to be added in a
    browser (AD-1) — and asserted to be the sum by a property test, which is
    what stops the terms drifting apart.

    **`scheduled_indemnity_cents` and `disbursed_indemnity_cents` are the two
    halves `remaining_indemnity_cents` is the difference of**, and they are
    published for a reason worth stating (code review, 2026-08-14). The card
    this block feeds is headed "Paid to date vs. reserve", so it shows an
    indemnity-paid figure *and* this verdict — and if those two come from
    different notions of "paid" the card contradicts itself. It did: the row
    read `claim.paid_indemnity`, which is 0 for every open seeded claim, while
    the verdict was computed from the schedule projection, in which weeks that
    have elapsed count as disbursed. A handler saw "Indemnity paid $0.00" above
    "no exposure remains — reallocate the surplus".

    The prototype has one answer, not two: its treatment card renders
    `sch.paidSoFar of sch.totalScheduled` (line 1364), and `billsHTML` says why
    in as many words — the static column "is often 0 even though the schedule
    already show[s] real disbursements". So the projection is the live figure
    and the column is a stale snapshot. Publishing both halves here puts the
    number the card shows and the number the verdict used in one block, where
    `remaining = max(0, scheduled - disbursed)` is an identity a test can hold.

    Named `disbursed_` rather than `paid_` deliberately: `overview.paid_indemnity_cents`
    is the column and still exists, so two fields called the same thing meaning
    different things is precisely the trap this is undoing.

    **`disbursed_medical_cents` is the same fix one row up** (code review,
    2026-08-14). The 3.2 review corrected the indemnity row and left the card's
    "Medical paid" reading `claim.paid_medical` — which is 0 on all 38 open
    seeded claims, directly above a live indemnity figure and directly above a
    link to a tab showing that claim's paid bills as a real number. It is the
    identical defect in the identical place, so it takes the identical shape:
    the block publishes the figure the verdict's *other* exposure term was
    computed from, and the card renders it. `remaining_medical_cents` and this
    are the unpaid and paid halves of one bill list, read once.

    `bands_version` names the rule document that answered, for
    `params_version`'s reason on `Benefit`: every rule that decided something
    in a response is named in it.
    """

    verdict: ReserveVerdict
    ratio_bp: int | None
    projected_remaining_cents: int | None
    remaining_indemnity_cents: int
    remaining_medical_cents: int | None
    scheduled_indemnity_cents: int
    disbursed_indemnity_cents: int
    disbursed_medical_cents: int
    reserve_cents: int
    rationale: str
    bands_version: int


def _rationale(
    verdict: ReserveVerdict,
    *,
    reserve_cents: int,
    known_exposure_cents: int,
    exposure_complete: bool,
) -> str:
    """The sentence for this verdict, with the figures it is entitled to.

    Both sides of the comparison in every sentence that makes one, deliberately:
    the verdict *is* a comparison, and a note stating one side of it would be
    asking the reader to take the other on trust. The prototype does the same
    and the wording of its three is its own.

    **The two incomplete-exposure sentences are this console's, not the
    prototype's**, which has no data that can be absent. Both say plainly that
    the bills are not on file — one because that is the whole answer, the other
    because a `light` verdict reached without them is reached on indemnity
    alone, and a handler acting on it should know that the figure quoted is a
    floor rather than a total.
    """
    reserve = format_dollars(reserve_cents)
    known = format_dollars(known_exposure_cents)
    if verdict is ReserveVerdict.closed_final:
        return CLOSED_FINAL_RATIONALE
    if verdict is ReserveVerdict.indeterminate:
        return (
            f"Medical bills are not yet on file, so remaining exposure cannot be "
            f"totalled and the reserve ({reserve}) cannot be judged. Scheduled "
            f"indemnity alone accounts for {known}. A verdict follows once bills "
            f"are recorded."
        )
    if verdict is ReserveVerdict.light:
        if not exposure_complete:
            return (
                f"Scheduled indemnity alone ({known}) already exceeds current reserve "
                f"({reserve}) before medical bills are counted. Recommend "
                f"re-evaluating reserve upward."
            )
        return (
            f"Projected remaining exposure ({known}) exceeds current reserve "
            f"({reserve}). Recommend re-evaluating reserve upward."
        )
    if verdict is ReserveVerdict.heavy:
        return (
            f"Current reserve ({reserve}) comfortably exceeds projected remaining "
            f"exposure ({known}). Consider reallocating surplus."
        )
    return f"Reserve ({reserve}) is well aligned with projected remaining exposure ({known})."


def classify_reserve(
    *,
    stage: Stage,
    reserve_cents: int,
    remaining_indemnity_cents: int,
    remaining_medical_cents: int | None,
    scheduled_indemnity_cents: int,
    disbursed_indemnity_cents: int,
    disbursed_medical_cents: int,
    bands: ReserveBands,
) -> ReserveCheck:
    """The verdict, its ratio, the exposure behind it and the sentence for it.

    Pure: no database, no clock, no I/O. `reserve_check_for_claim` below is the
    async wrapper that assembles the two exposure terms — the arrangement
    `compute_benefit`/`benefit_for_claim` already uses, and the reason a
    Hypothesis property can range over the whole input space without a
    connection.

    Keyword-only, because six of the seven arguments are integers of the same
    kind: positionally, swapping the two exposure terms would produce a
    perfectly plausible verdict computed from the wrong halves, and swapping
    either with the reserve would invert the whole rule silently.

    **`remaining_medical_cents` may be `None`, and that is not the same as 0.**
    `None` is "the bills are not on file"; 0 is "there are no unpaid bills".
    The module docstring works through which verdicts survive the first — the
    short version is that the unknown term is non-negative, so the exposure
    computed without it is a lower bound: `light` still holds, `adequate` and
    `heavy` are withheld as `indeterminate`.

    **The two indemnity halves are carried, not consulted.** Nothing below
    reads `scheduled_indemnity_cents` or `disbursed_indemnity_cents` — the
    decision is made on `remaining_indemnity_cents`, and these travel so the
    card can state the figure the verdict was reached from instead of a second
    one from a different source. The identity
    `remaining = max(0, scheduled - disbursed)` is the projection's to hold and
    `tests/test_reserve_block.py` asserts it end to end.

    **`stage` is checked before anything else.** A settled claim gets no band
    arithmetic at all — not "the arithmetic, then overridden", which would leave
    a ratio in the payload that nothing had judged.
    """
    complete = remaining_medical_cents is not None
    # The exposure that is *known*: the whole of it when the bills are on file,
    # and a lower bound on it when they are not.
    known = remaining_indemnity_cents + (remaining_medical_cents or 0)
    # A total is only a total when every term is in it. `None` here is what
    # stops a lower bound travelling under a name that reads as the whole
    # figure — the mislabel the module docstring refuses.
    projected = known if complete else None

    if stage is Stage.settled:
        return ReserveCheck(
            verdict=ReserveVerdict.closed_final,
            ratio_bp=None,
            # Still reported, and still the real figures: a settled claim's
            # schedule is fully paid and its bills are settled, so these are
            # zero in practice — but reporting the *computed* values rather
            # than hardcoded zeroes means a settled claim with an unpaid bill
            # shows up in the payload instead of being asserted away.
            projected_remaining_cents=projected,
            remaining_indemnity_cents=remaining_indemnity_cents,
            remaining_medical_cents=remaining_medical_cents,
            scheduled_indemnity_cents=scheduled_indemnity_cents,
            disbursed_indemnity_cents=disbursed_indemnity_cents,
            disbursed_medical_cents=disbursed_medical_cents,
            reserve_cents=reserve_cents,
            # A settled claim is closed whether or not its bills are on file:
            # the sentence names no figures, so there is nothing in it that an
            # unknown term could make untrue.
            rationale=_rationale(
                ReserveVerdict.closed_final,
                reserve_cents=reserve_cents,
                known_exposure_cents=known,
                exposure_complete=complete,
            ),
            bands_version=bands.version,
        )

    if reserve_cents > 0:
        # Cross-multiplied rather than divided — see the module docstring. The
        # comparisons are the prototype's `>` and `<`, kept strict: a claim
        # sitting exactly on a boundary is `adequate`, which is the answer that
        # does not ask a handler to act.
        light = known * BASIS_POINTS_PER_UNIT > bands.light_ratio_bp * reserve_cents
        heavy = known * BASIS_POINTS_PER_UNIT < bands.heavy_ratio_bp * reserve_cents
        ratio_bp: int | None = round_half_up(known * BASIS_POINTS_PER_UNIT, reserve_cents)
    else:
        # The prototype's sentinel, kept as an explicit branch. See the module
        # docstring: no reserve at all is light while exposure remains and
        # adequate when none does, and that is a verdict rather than a guard.
        light = known > 0
        heavy = False
        ratio_bp = ZERO_RESERVE_EXPOSED_RATIO_BP if light else ZERO_RESERVE_CLEAR_RATIO_BP

    if light:
        # Sound on a lower bound: an unknown non-negative addition cannot bring
        # an exposure back down under the threshold it has already passed.
        verdict = ReserveVerdict.light
    elif not complete:
        # `adequate` and `heavy` are both claims about an upper bound, and an
        # unknown term can move either up a band. Withheld rather than guessed.
        verdict = ReserveVerdict.indeterminate
    elif heavy:
        verdict = ReserveVerdict.heavy
    else:
        verdict = ReserveVerdict.adequate

    if not complete:
        # One invariant rather than two: a ratio exists exactly when a complete
        # comparison produced it. The `light`-on-a-lower-bound case is the one
        # this catches — the verdict is sound, but the ratio behind it is a
        # floor, and a floor published under the name of the ratio is the same
        # mislabel `projected_remaining_cents` is `None` to avoid.
        ratio_bp = None
    return ReserveCheck(
        verdict=verdict,
        ratio_bp=ratio_bp,
        projected_remaining_cents=projected,
        remaining_indemnity_cents=remaining_indemnity_cents,
        remaining_medical_cents=remaining_medical_cents,
        scheduled_indemnity_cents=scheduled_indemnity_cents,
        disbursed_indemnity_cents=disbursed_indemnity_cents,
        disbursed_medical_cents=disbursed_medical_cents,
        reserve_cents=reserve_cents,
        rationale=_rationale(
            verdict,
            reserve_cents=reserve_cents,
            known_exposure_cents=known,
            exposure_complete=complete,
        ),
        bands_version=bands.version,
    )


async def unpaid_medical_cents(db: AsyncSession, ctx: CallerContext, claim_business_id: str) -> int:
    """Medical bills on this claim that are not yet paid, in cents.

    **Story 3.3 filled this in, and the seam closed exactly where 3.2 left
    it.** Until the `bill` table existed there were no rows to sum and no
    honest number to return, so this answered `None` — "nobody can see this
    claim's bills" — and `classify_reserve` withheld the two verdicts an
    unknown non-negative term could flip. The table now exists and is seeded
    for all 100 claims, so the answer is a sum and every verdict completes
    without a line of the classifier changing.

    **`0` now means what 3.2 refused to let it mean.** A claim whose bills are
    all paid has no unpaid medical exposure, and saying so is a statement this
    function is finally entitled to make. The `None` branch survives in
    `classify_reserve` rather than being deleted with the seam: it is the
    typed way to say "a term is not on file", and Epic 6's honest-degradation
    work inherits it.

    Scoped through `data/repositories` with the caller context (AD-7), exactly
    as `select_documents` and `select_photos` are — the shape 3.2's docstring
    predicted. The **business id** rather than the claim row, because that is
    what the scoped repositories key on, and `ctx` is now genuinely read
    rather than accepted for decoration.
    """
    return unpaid_total(await claim_repo.select_bills(db, ctx, claim_business_id))


def indemnity_terms(weeks: Sequence[ScheduleRow]) -> tuple[int, int, int]:
    """`(scheduled, disbursed, remaining)` indemnity, in cents, from stored rows.

    **Summed from the rows rather than computed as `weekly × weeks`**, which is
    the change Story 3.3 makes to how this verdict is reached. Once a schedule
    is materialized the rows are the truth: a week that has been paid keeps the
    amount it was paid at even if the claim's comp rate has since been
    overridden (`services/financials/materialize.py` freezes decided weeks), so
    a product of the *current* weekly figure and the week count would restate
    history the moment a rate moved.

    `payment_scheduled` deliberately does **not** count as disbursed. A week
    approved into the next batch is money committed, not money paid, and the
    figure this feeds is the one the card labels "Indemnity paid".

    Remaining is floored at zero, `PaymentProjection`'s rule and for its
    reason: a schedule that shortened after weeks had been paid would otherwise
    hand the classifier a negative exposure, which bands as heavy with total
    confidence.
    """
    scheduled = sum(week.amount_cents for week in weeks)
    disbursed = sum(week.amount_cents for week in weeks if week.status is ScheduleWeekStatus.paid)
    return scheduled, disbursed, max(0, scheduled - disbursed)


def reserve_check_from_rows(
    claim: ReserveClaim,
    *,
    weeks: Sequence[ScheduleRow],
    bills: Sequence[LineItem],
    bands: ReserveBands,
) -> ReserveCheck:
    """`classify_reserve`, with its exposure terms read off the stored rows.

    The one place a `payment_schedule_week` row set becomes a reserve verdict.
    Both callers go through it — `reserve_check_for_claim` below, which fetches
    what it needs, and `services/financials/summary.py`, which already holds
    the rows — so the case file and the Bills tab reach the verdict by the same
    route rather than by two that agree today. That is what makes AC 4's
    "identical figures" a property of the code rather than of the fixtures.

    **Takes the bill rows rather than a pre-summed unpaid figure** (code
    review, 2026-08-14). Both halves of the list are published — the unpaid one
    is the exposure the verdict is computed from, the paid one is what the
    card's "Medical paid" row renders — and summing them in one place is what
    stops the two coming from different reads of one table.
    """
    scheduled, disbursed, remaining = indemnity_terms(weeks)
    return classify_reserve(
        stage=claim.stage,
        reserve_cents=claim.reserve,
        remaining_indemnity_cents=remaining,
        remaining_medical_cents=unpaid_total(bills),
        disbursed_medical_cents=paid_total(bills),
        # The two halves the remaining figure is the difference of, so the card
        # states the indemnity-paid figure this verdict was reached from rather
        # than `claim.paid_indemnity`, which is a stale snapshot on an open
        # claim and contradicted it — see `ReserveCheck`.
        scheduled_indemnity_cents=scheduled,
        disbursed_indemnity_cents=disbursed,
        bands=bands,
    )


async def reserve_check_for_claim(
    db: AsyncSession,
    ctx: CallerContext,
    claim: ReserveClaim,
    benefit: Benefit,
    as_of: date,
    *,
    claim_pk: int,
    claim_ref: str,
) -> ReserveCheck:
    """The verdict for one claim, over a freshly refreshed schedule.

    `benefit` is handed in rather than recomputed. `compute_benefit` is the one
    place a weekly indemnity figure exists (AD-2), and `claim_detail` has
    already asked it — asking again would be a second `state_rate_schedule`
    lookup and a second `benefit_params` load per case-file read, to arrive at
    the identical number.

    **The schedule is materialized before it is read**, which is the reason
    this function grew a `ctx` and the claim's two identifiers in Story 3.3.
    Three of the five week statuses move with the calendar, so a verdict
    computed from rows nobody had refreshed would drift from the Bills tab's
    the moment a week elapsed — and AC 4 requires that those two surfaces
    cannot disagree. `materialize_schedule` is a no-op when nothing has moved,
    so the ordinary case costs one indexed SELECT.

    Raises nothing of its own: the two failures available here — a missing rate
    schedule and a missing rule document — are raised by the calls that own
    them and mapped to problem documents by the router.
    """
    weeks = await materialize_schedule(
        db,
        claim,
        claim_pk=claim_pk,
        claim_ref=claim_ref,
        benefit=benefit,
        as_of=as_of,
        ctx=ctx,
    )
    return reserve_check_from_rows(
        claim,
        weeks=weeks,
        bills=await claim_repo.select_bills(db, ctx, claim_ref),
        bands=await reserve_bands_for(db, as_of),
    )


__all__ = [
    "CLOSED_FINAL_RATIONALE",
    "ZERO_RESERVE_CLEAR_RATIO_BP",
    "ZERO_RESERVE_EXPOSED_RATIO_BP",
    "ReserveCheck",
    "ReserveClaim",
    "ReserveVerdict",
    "classify_reserve",
    "indemnity_terms",
    "reserve_check_for_claim",
    "reserve_check_from_rows",
    "unpaid_medical_cents",
]
