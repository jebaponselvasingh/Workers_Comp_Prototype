"""The financial engine — Epic 3's calculations, in one place (AD-2).

Story 3.1 opens the package with the statutory weekly indemnity benefit: the
formula, the jurisdiction's clamp, the indemnity type it is paid under and the
reserve-rationale paragraph that explains the number. Story 3.2 adds the
reserve adequacy verdict (`reserve.py`) and, because that verdict needs the
indemnity still ahead of the reserve, the payment-schedule projection the
verdict is computed from (`schedule.py`) — the generator Story 3.3 must
*persist through* rather than write a second copy of. Stories 3.3–3.5 add the
Bills surfaces, the approval batch and the action checklist, and each of them
reads `compute_benefit` and `classify_reserve` rather than re-deriving a weekly
figure from an AWW or a verdict from a ratio.

Import this package to reach the calculation; never re-implement one at a call
site. What it deliberately does **not** own is the *claim row*:
`services/claims` is the write-owner of `claim` (AD-12), so the comp-rate
override is a command over there, and this package computes from the column
without ever writing it.

Two conventions established here that every later financial story inherits, and
both are argued at length in `benefit.py`:

- **Money is integer cents and rates are integer basis points**, so the whole
  calculation is integer arithmetic and no float ever reaches a figure a
  handler reads.
- **Rounding is half up, on cents** — `round_half_up`, the only rounding in
  the engine.
"""

from services.financials.benefit import (
    BASIS_POINTS_PER_UNIT,
    COMP_RATE_MAX_BP,
    COMP_RATE_MIN_BP,
    Benefit,
    BenefitClaim,
    MissingStateRate,
    benefit_for_claim,
    compute_benefit,
    round_half_up,
)
from services.financials.rationale import format_comp_rate, format_dollars, reserve_rationale
from services.financials.reserve import (
    CLOSED_FINAL_RATIONALE,
    ZERO_RESERVE_CLEAR_RATIO_BP,
    ZERO_RESERVE_EXPOSED_RATIO_BP,
    ReserveCheck,
    ReserveClaim,
    ReserveVerdict,
    classify_reserve,
    reserve_check_for_claim,
    unpaid_medical_cents,
)
from services.financials.schedule import (
    MAX_WEEKS,
    MIN_WEEKS,
    SCHEDULE_WEEKS,
    PaymentProjection,
    ScheduleClaim,
    ScheduleWeek,
    project_payments,
    scheduled_weeks,
)

__all__ = [
    "BASIS_POINTS_PER_UNIT",
    "CLOSED_FINAL_RATIONALE",
    "COMP_RATE_MAX_BP",
    "COMP_RATE_MIN_BP",
    "MAX_WEEKS",
    "MIN_WEEKS",
    "SCHEDULE_WEEKS",
    "ZERO_RESERVE_CLEAR_RATIO_BP",
    "ZERO_RESERVE_EXPOSED_RATIO_BP",
    "Benefit",
    "BenefitClaim",
    "MissingStateRate",
    "PaymentProjection",
    "ReserveCheck",
    "ReserveClaim",
    "ReserveVerdict",
    "ScheduleClaim",
    "ScheduleWeek",
    "benefit_for_claim",
    "classify_reserve",
    "compute_benefit",
    "format_comp_rate",
    "format_dollars",
    "project_payments",
    "reserve_check_for_claim",
    "reserve_rationale",
    "round_half_up",
    "scheduled_weeks",
    "unpaid_medical_cents",
]
