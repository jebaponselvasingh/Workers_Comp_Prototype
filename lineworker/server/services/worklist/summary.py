"""The portfolio KPI cards — ten figures over one scoped caseload (FR-SUP-1/A).

The prototype computes all ten in `renderSV` (line 1001) by folding the global
claim array in the browser, after filtering it client-side to whatever the
selected persona was allowed to see. Both halves of that are what this module
replaces: the *set* is decided by the scoped repository (AD-7) and the
*figures* are decided here (AD-1), so the SPA receives ten numbers it has no
way to have invented and no way to disagree with the queue about.

**Why a Python fold rather than `COUNT(*) FILTER`.** `stats.topbar_stats`
counts in SQL because all three of its buckets are expressible there. Four of
these ten are not: `fraud_flagged` and `total_paid` have Python computers only,
and giving them SQL twins would be a second encoding of each rule — exactly
what `risk_band.sql_is` warns against. Mixing the two forms would also mean two
statements over one scoped set, which can disagree if a row changes between
them. So this follows `sla.sla_strip` instead: one `select_claim_columns` read,
one pure fold. A hundred rows, and `risk_band.sql_is` already states the trade —
correctness of the single source outranks a plan shape at this size.

**Every band and total is asked of the registry (AD-10), never re-expressed.**
`summary_of` takes its three computers as arguments and holds no threshold, no
comparison against a score and no cut-off of its own. That is not decoration:
`test_no_module_outside_the_registry_hardcodes_the_band` greps this file for a
band boundary, comments included, and the reason it can is that there is
nothing here to find. The band's own boundary travels *out* on the summary so a
card caption can quote it — see `high_risk_severity_min`.

**Why the counts of employers and plants are computed rather than written
down.** The prototype's dataset chip says "10 employers · 15 US plants", both
literals, and the second one is wrong even for the portfolio it was written for
(its own array holds 29 distinct plants). More to the point, a scoped
supervisor reading "10 employers" would be told she is looking at a portfolio
she cannot see. Counting them over the same scoped rows as the KPIs is what
makes the chip a description of the answer rather than a caption on it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Claim
from data.models.enums import Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds
from services import derivations
from services.derivations import (
    FraudFlaggedDerivation,
    RiskBand,
    RiskDerivation,
    TotalPaidDerivation,
)


@dataclass(frozen=True)
class PortfolioClaim:
    """One claim, reduced to the thirteen facts the ten cards are folded from.

    A projection rather than the ORM entity, for `SlaSample`'s two reasons: it
    keeps `summary_of` pure and generatable, and it puts the card definitions
    next to each other instead of spread across a row object.

    The three `paid_*` fields are named exactly as `derivations.PaidColumns`
    declares them, which is the whole point of that protocol being structural —
    this class satisfies it by shape, so `total_paid` adds up a row projection
    without this module importing an ORM entity to add three integers.
    """

    stage: Stage
    severity_score: int
    fraud_flag: bool
    fraud_score: int
    osha_recordable: bool
    litigation_flag: bool
    surgery_required: bool
    paid_indemnity: int
    paid_medical: int
    paid_expense: int
    reserve: int
    employer_id: int
    plant: str


@dataclass(frozen=True)
class PortfolioSummary:
    """The two card rows, in the prototype's order, plus the chip's counts.

    **The two thresholds travel with the figures**, and that is what makes the
    captions honest: "Severity ≥ N/100" and "Score ≥ N — review needed" quote a
    number the server counted at, so superseding the rule document moves the
    count *and* the caption together with no code change. A client holding
    either number would be a second copy of a rule it cannot see change.

    They are read off the derivations rather than off `DerivationThresholds`
    for the same reason the counting is: the published number is then provably
    the one the fold used, not a parallel lookup that happens to agree.

    `rules_version` is deliberately **absent**. It is the identity of the
    document, not a figure computed from the caseload, and `summary_of` is pure
    over a caseload — the router adds it beside this block from the parameter
    block it loaded.

    **`employer_count` and `plant_count` describe the claims, not the
    assignment.** Both are counted over the rows the scope predicate returned,
    so they answer "how many employers and plants are represented in the claims
    you can see" — *not* "how many employers are on your persona's assignment".
    The two differ whenever an assigned employer has no claims in scope: a
    supervisor who has just been given a newly-onboarded employer sees her
    employer count stay where it was until that employer's first claim lands.

    That is the right reading for a chip that captions a set of figures — it
    describes the portfolio the ten numbers were folded from, and an employer
    contributing nothing to any of them would be a count with no figures behind
    it. It is written down because nothing catches the alternative: both the
    service and the test oracle count the same way, over the same scoped rows,
    so a reader who assumed "employers assigned" would find every assertion
    agreeing with them. If a later story wants the assignment count instead,
    that is a different figure from a different source (the persona's employer
    set), and it needs its own field rather than a change of meaning here.
    """

    total_claims: int
    under_treatment: int
    settled_closed: int
    high_risk: int
    total_paid_cents: int
    total_reserve_cents: int
    fraud_flagged: int
    osha_recordable: int
    litigation: int
    surgery_required: int
    employer_count: int
    plant_count: int
    high_risk_severity_min: int
    fraud_score_min: int


def summary_of(
    caseload: Sequence[PortfolioClaim],
    risk: RiskDerivation,
    fraud_flagged: FraudFlaggedDerivation,
    total_paid: TotalPaidDerivation,
) -> PortfolioSummary:
    """The ten figures over one caseload. Pure, and the reuse point for 5.3.

    Card definitions, in the prototype's order (`renderSV`, line 1002), with
    the two places this deliberately differs from it recorded:

    - **Total Claims** — every claim in scope.
    - **Under Treatment** — `stage = treatment`. A column value, not a derived
      one, so it is expressed directly (`topbar_stats`' rule); it becomes a
      registered derivation the day it grows a condition.
    - **Settled & Closed** — `stage = settled`, and **the prototype counts
      `status === "Settled & Closed"` instead**, which is 54 of the seeded 100
      against this rule's 62. `stage` is chosen because it is what every other
      surface already groups by — the queue's four stage groups, the SLA
      strip's settled segment, 5.3's settlement donut — and a KPI card that
      disagreed with the donut beneath it is the precise failure AD-10 exists
      to prevent. Recorded as a visible change from the prototype rather than
      absorbed silently.
    - **High Risk** — `risk.of(...)` is `high`. The band's boundary is not
      known here and must not become known here.
    - **Total Paid** — `total_paid.of(...)` summed, in cents. The derivation
      per claim rather than three columns summed once, so this agrees with the
      investigation card and the settled payout breakdown by construction — and
      it is worth naming what it therefore *disagrees* with, because the number
      is large. `TotalPaidDerivation` adds the static `paid_*` columns, while
      `claim_financials.paid_to_date` is the computer for an open claim, where
      those columns are routinely zero and the schedule and bills show real
      disbursements. On the seeded portfolio 38 claims have zero paid columns
      while carrying $335,985 of `status = paid` bills and expenses that the
      Bills tab displays, so a supervisor can read this card, drill into a
      treatment claim, and find a five-figure Paid to Date on a claim the card
      counted as nothing. That is not two computers for one value (AD-10 holds:
      each of the two quantities has exactly one), it is an open question about
      **which quantity this card should sum** — a recorded product decision
      carried into Epic 5 and wanting an owner, not a dev-time correction. See
      `_bmad-output/implementation-artifacts/deferred-work.md`. Until it has
      one, the prototype's figure is ported exactly, as it is everywhere else in
      this story. Do not change the arithmetic here to close the gap; changing
      it is one call site and both test oracles, and it moves Epic 7's financial
      decomposition with it.
    - **Total Reserve** — the `reserve` column summed, in cents. Over *every*
      claim in scope, settled ones included: the card is captioned "Active case
      exposure" and the prototype sums the whole array under it, so the figure
      is ported and the caption's wording is the UI's problem, not the fold's.
    - **Fraud Flags** — `fraud_flagged.of(...)`, which is the *review* rule at
      its own threshold and **not** `siu_review`; see that derivation for the
      9-versus-13 the confusion would cost.
    - **OSHA Recordable**, **Litigation**, **Surgery Required** — three boolean
      columns. `litigation_flag` rather than `attorney_rep`: `renderSV` counts
      the former, the two agree on all 100 seeded claims, and the spec's Block
      If covers the day they stop agreeing.

    A single pass with running totals rather than ten comprehensions over the
    same list: not for speed at a hundred rows, but so that "these ten numbers
    describe one set of claims" is structural. Ten separate folds is ten
    opportunities for one of them to be written against a filtered copy.
    """
    total_paid_cents = 0
    total_reserve_cents = 0
    under_treatment = 0
    settled_closed = 0
    high_risk = 0
    fraud_count = 0
    osha_count = 0
    litigation_count = 0
    surgery_count = 0
    employers: set[int] = set()
    # Keyed on `(employer_id, plant)` rather than on the name alone, for the
    # reason `employers` is keyed on an id: a name is a label, not an identity.
    # Two employers operating sites that happen to share a name — "Plant 2",
    # say, or a town both of them build in — would otherwise be counted as one
    # plant, and the chip would under-report the book. No behaviour change on
    # today's seed, where every plant name is employer-prefixed and the two
    # keyings agree; this is the invariant, written down before the seed stops
    # accidentally satisfying it.
    plants: set[tuple[int, str]] = set()

    for claim in caseload:
        total_paid_cents += total_paid.of(claim)
        total_reserve_cents += claim.reserve
        if claim.stage is Stage.treatment:
            under_treatment += 1
        if claim.stage is Stage.settled:
            settled_closed += 1
        if risk.of(claim.severity_score) is RiskBand.high:
            high_risk += 1
        if fraud_flagged.of(fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score):
            fraud_count += 1
        if claim.osha_recordable:
            osha_count += 1
        if claim.litigation_flag:
            litigation_count += 1
        if claim.surgery_required:
            surgery_count += 1
        employers.add(claim.employer_id)
        plants.add((claim.employer_id, claim.plant))

    return PortfolioSummary(
        total_claims=len(caseload),
        under_treatment=under_treatment,
        settled_closed=settled_closed,
        high_risk=high_risk,
        total_paid_cents=total_paid_cents,
        total_reserve_cents=total_reserve_cents,
        fraud_flagged=fraud_count,
        osha_recordable=osha_count,
        litigation=litigation_count,
        surgery_required=surgery_count,
        employer_count=len(employers),
        plant_count=len(plants),
        high_risk_severity_min=risk.high_min,
        fraud_score_min=fraud_flagged.fraud_score_min,
    )


async def portfolio_summary(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
) -> PortfolioSummary:
    """The KPI cards for the caller's book — the endpoint's one call.

    Takes its thresholds rather than fetching them, `topbar_stats`' signature
    and its reason: the caller loads the parameter block once and hands it
    down, so this stays a composition of scope and parameters instead of
    dragging the rules engine into every aggregate that mentions a band.

    No role appears anywhere in this path. Supervisor and analyst take the
    identical scoped route through the repository, which is the whole of AD-7
    on this surface — an analyst-specific variant is Epic 7's decision to make,
    and a branch here would pre-empt it with a number.
    """
    rows = await claim_repo.select_claim_columns(
        db,
        ctx,
        [
            Claim.stage,
            Claim.severity_score,
            Claim.fraud_flag,
            Claim.fraud_score,
            Claim.osha_recordable,
            Claim.litigation_flag,
            Claim.surgery_required,
            Claim.paid_indemnity,
            Claim.paid_medical,
            Claim.paid_expense,
            Claim.reserve,
            Claim.employer_id,
            Claim.plant,
        ],
    )
    # Named rather than positional (`PortfolioClaim(*row)`), which would work
    # and would be one reordered projection away from counting OSHA-recordable
    # injuries as litigation: both columns are booleans, so a swap type-checks,
    # runs, and produces two wrong numbers with nothing to say so.
    caseload = [
        PortfolioClaim(
            stage=row.stage,
            severity_score=row.severity_score,
            fraud_flag=row.fraud_flag,
            fraud_score=row.fraud_score,
            osha_recordable=row.osha_recordable,
            litigation_flag=row.litigation_flag,
            surgery_required=row.surgery_required,
            paid_indemnity=row.paid_indemnity,
            paid_medical=row.paid_medical,
            paid_expense=row.paid_expense,
            reserve=row.reserve,
            employer_id=row.employer_id,
            plant=row.plant,
        )
        for row in rows
    ]
    return summary_of(
        caseload,
        derivations.risk.for_thresholds(thresholds),
        derivations.fraud_flagged.for_thresholds(thresholds),
        derivations.total_paid.for_thresholds(thresholds),
    )
