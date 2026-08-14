"""The statutory weekly indemnity benefit — computed once, here (Story 3.1).

AD-2: `compute_benefit` exists exactly once and every consumer calls it. Today
that is the Overview card's benefit block; Story 3.3's payment schedule and
Epic 6's copilot tools are the next two, and neither may re-derive a weekly
figure from an AWW and a percentage. AD-1 says the same thing from the other
end: the SPA never multiplies anything — it receives cents and formats them.

## The calculation, in one paragraph

Take the comp rate in basis points (the handler's override if there is one,
otherwise the statutory default, or the PTD rate when the claim's indemnity
type is `ptd`), apply it to the average weekly wage, round half up to the
nearest cent, then clamp into the jurisdiction's statutory weekly minimum and
maximum. Four inputs, three of them from data an operator owns: the rate
parameters are a versioned JDM document (AD-8), the bounds are a
`state_rate_schedule` row, the PTD test is a registered derivation (AD-10).
The only thing written in Python is the arithmetic itself, which is exactly
AD-8's split.

## Everything is an integer, and that is the point

Money is integer cents, as everywhere in this schema. Rates are integer
**basis points** — 6667 is 66.67% — which is the unit
`claim.comp_rate_override_bp` stores and the unit `benefit_params` publishes.
So no float enters the calculation at any stage, and the two questions that
matter are exact: what is this claim's weekly benefit, and is this claim
overridden. A percentage float would make the first depend on IEEE-754 rounding
and the second a comparison two values can fail by 1e-14.

The prototype computes in *dollars* (`Math.round(c.aww * (pct/100))`), so its
figures are rounded to the dollar before being clamped to dollar bounds. Here
the AWW is cents and the answer is cents, which means this console can differ
from the prototype by up to 50 cents on an unclamped benefit. That is the money
convention doing its job rather than a divergence to reconcile: the prototype
rounds because its whole data model is dollars.

## The rounding convention, written down once for Epic 3

**Round half up, on cents.** `round_half_up` below is the only rounding in the
financial engine, and Stories 3.2–3.5 inherit it: a reserve adequacy ratio, a
bill line and a payment-schedule week all round the same way, so a schedule
that sums its weeks agrees with the weekly figure shown beside it. Half *up*
rather than Python's default half-to-even, because `round()`'s banker's
rounding would send an exactly-half cent to whichever neighbour is even — a
rule no benefit schedule states and no handler could reproduce by hand.

## What this module does not decide

The **waiting period** is displayed here (the payment-schedule note) and
*applied* in Story 3.3, which generates the weeks starting at DOI + the
parameter. Nothing here builds a schedule, and `payment_schedule_week` is not
this story's table.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.core import StateRateSchedule
from data.models.enums import ReturnStatus
from data.repositories import state_rates as rate_repo
from rules.parameters import BenefitParams, DerivationThresholds, benefit_params_for
from services import derivations
from services.derivations import IndemnityType, RiskBand
from services.financials.rationale import RationaleClaim, reserve_rationale

#: 100% of wage, in basis points. The divisor of the whole calculation.
BASIS_POINTS_PER_UNIT: Final[int] = 10_000

#: The comp rate's domain — 0% to 150%, inclusive at both ends.
#:
#: **Not a JDM parameter**, on `SEVERITY_MIN`/`SEVERITY_MAX`'s argument
#: (`services/claims/reference.py`): the band *thresholds* are tuning and live
#: in a rule document, but the range of values the console will accept at all
#: is not something an operator should be able to widen by editing data. The
#: bounds are the prototype's own input attributes (`min="0" max="150"`), and
#: they are what `services/claims/comp_rate.py` refuses against.
#:
#: **Published on the case file all the same**, exactly as the severity bounds
#: are: a number input and a pre-flight refusal both need them, and two
#: literals in a React component is the shape `noDerivation.test.ts` refuses.
#:
#: `rules/parameters.py` restates them to validate the *default* rate a
#: document may declare — it must not import from `services/` — and
#: `tests/test_rule_parameters.py` asserts the two statements agree.
COMP_RATE_MIN_BP: Final[int] = 0
COMP_RATE_MAX_BP: Final[int] = 15_000


class MissingStateRate(LookupError):
    """No `state_rate_schedule` row covers the claim's jurisdiction.

    A *data* error, and deliberately not a default. The prototype answers
    ``STATE_WC_RATES[c.state] || {max:1200, min:250}``, so a claim filed in an
    unlisted state is clamped to two numbers belonging to no jurisdiction while
    the card prints that state's name beside them — a wrong statutory figure
    shown with total confidence, which is the failure NFR-4 is about.

    Unreachable against a correctly migrated database: 0023 refuses to complete
    while any seeded claim's state has no schedule. It is raised, and mapped to
    a problem document, for the claim inserted afterwards.
    """

    def __init__(self, state_code: str) -> None:
        super().__init__(
            f"no statutory rate schedule for state {state_code!r} — a weekly benefit "
            "cannot be computed, and this system does not substitute a default"
        )
        self.state_code = state_code


class BenefitClaim(RationaleClaim, Protocol):
    """The claim columns the benefit reads, on top of the rationale's seven.

    A structural type rather than `Claim`, for `PaidColumns`' reason: a service
    may pass a row projection, a test may pass a small stand-in, and the read
    surface is documented by being written down. Extending `RationaleClaim`
    rather than restating its members keeps the two lists from drifting — and
    makes the split legible: those seven are read to *describe* the claim,
    these four to *compute* its benefit.
    """

    @property
    def state(self) -> str: ...
    @property
    def aww(self) -> int: ...
    @property
    def return_status(self) -> ReturnStatus: ...
    @property
    def comp_rate_override_bp(self) -> int | None: ...


@dataclass(frozen=True)
class Benefit:
    """One claim's weekly indemnity benefit, and everything the card states.

    Every money field is integer cents and says so; the two rate fields are
    integer basis points and say so. `comp_rate_bp` is what was *applied* —
    the override when there is one, the statutory rate otherwise — and
    `default_comp_rate_bp` is what the ↺ reset would restore, so the card can
    show both without deciding which is which.

    `is_overridden` is published rather than left to be inferred from
    `comp_rate_bp != default_comp_rate_bp`. The two are not the same question:
    a handler who types the default back in has still overridden the claim, and
    the row records that (the column is non-null), which is what the ↺ control
    and the rationale's closing sentence are about.

    `params_version` names the rule document that answered, for
    `thresholds_version`'s reason — every rule that decided something in a
    response is named in it.
    """

    weekly_cents: int
    comp_rate_bp: int
    default_comp_rate_bp: int
    is_overridden: bool
    indemnity_type: IndemnityType
    state_code: str
    state_name: str
    state_min_cents: int
    state_max_cents: int
    schedule_effective_date: date
    waiting_days: int
    reserve_rationale: str
    comp_rate_min_bp: int
    comp_rate_max_bp: int
    params_version: int


def round_half_up(value: int, divisor: int) -> int:
    """Integer division rounding halves away from the lower neighbour.

    `divmod` floors, so `remainder` is in `[0, divisor)` for a positive
    divisor and the test below is exact — no float, no `Decimal`, no
    `round()`'s banker's rounding. See the module docstring for why half-up
    rather than half-even, and note that this is the convention every later
    financial story inherits.
    """
    quotient, remainder = divmod(value, divisor)
    return quotient + 1 if remainder * 2 >= divisor else quotient


def compute_benefit(
    claim: BenefitClaim,
    rate: StateRateSchedule,
    params: BenefitParams,
    thresholds: DerivationThresholds,
) -> Benefit:
    """The weekly benefit, its type, its bounds and its rationale.

    Pure: no database, no clock, no I/O. `benefit_for_claim` below is the
    async wrapper that fetches the two arguments this cannot invent, which is
    what lets the property tests run this ten thousand times without a
    connection.

    **`thresholds` is a fourth argument the story's sketch did not name**, and
    it is here so that the PTD condition has one reader. The prototype computes
    `isPTD` inline and uses it twice — once to pick the comp rate and once to
    label the claim — so changing one leaves the other. Here the *derivation*
    answers `ptd` or not, and this function pays accordingly; the threshold
    itself is in `derivation_thresholds` because that is the single argument
    every registered derivation is built from (AD-10).

    Building the `risk` band from the same argument is not a second banding: it
    is the one registered `risk` derivation, called for the rationale's
    severity wording, which is a *sentence about* the band rather than a second
    opinion on it.
    """
    indemnity = derivations.indemnity_type.for_thresholds(thresholds).of(
        disability=claim.disability,
        severity_score=claim.severity_score,
        return_status=claim.return_status,
    )
    band: RiskBand = derivations.risk.for_thresholds(thresholds).of(claim.severity_score)

    default_bp = (
        params.ptd_comp_rate_bp if indemnity is IndemnityType.ptd else params.default_comp_rate_bp
    )
    override_bp = claim.comp_rate_override_bp
    is_overridden = override_bp is not None
    comp_rate_bp = override_bp if override_bp is not None else default_bp

    # The formula, in two steps that are deliberately not folded together: the
    # unclamped figure is what the rate produces, and the clamp is what the
    # jurisdiction imposes on it. A reader tracing "why is this claim on the
    # state minimum?" needs to see the two as separate facts.
    weekly_cents = round_half_up(claim.aww * comp_rate_bp, BASIS_POINTS_PER_UNIT)
    weekly_cents = max(rate.weekly_min_cents, min(rate.weekly_max_cents, weekly_cents))

    return Benefit(
        weekly_cents=weekly_cents,
        comp_rate_bp=comp_rate_bp,
        default_comp_rate_bp=default_bp,
        is_overridden=is_overridden,
        indemnity_type=indemnity,
        state_code=rate.state_code,
        state_name=rate.state_name,
        state_min_cents=rate.weekly_min_cents,
        state_max_cents=rate.weekly_max_cents,
        schedule_effective_date=rate.effective_date,
        waiting_days=params.waiting_period_days,
        reserve_rationale=reserve_rationale(
            claim,
            band=band,
            indemnity_type=indemnity,
            rate=rate,
            comp_rate_bp=comp_rate_bp,
            default_comp_rate_bp=default_bp,
            is_overridden=is_overridden,
        ),
        comp_rate_min_bp=COMP_RATE_MIN_BP,
        comp_rate_max_bp=COMP_RATE_MAX_BP,
        params_version=params.version,
    )


async def benefit_for_claim(
    db: AsyncSession,
    claim: BenefitClaim,
    thresholds: DerivationThresholds,
    as_of: date | None = None,
) -> Benefit:
    """`compute_benefit`, with its two data arguments fetched.

    Split from the calculation so that the pure half stays testable without a
    database — the arrangement `services/worklist/priority.py` already uses for
    the queue's scorer, and the reason a Hypothesis property can range over
    every seeded state and every valid rate.

    Raises `MissingStateRate` when the claim's jurisdiction has no schedule.
    Nothing defaults; see that exception's docstring.
    """
    rate = await rate_repo.rate_for_state(db, claim.state)
    if rate is None:
        raise MissingStateRate(claim.state)
    params = await benefit_params_for(db, as_of)
    return compute_benefit(claim, rate, params, thresholds)
