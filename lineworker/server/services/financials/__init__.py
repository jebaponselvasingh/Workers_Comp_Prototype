"""The financial engine — Epic 3's calculations, in one place (AD-2).

Story 3.1 opens the package with the statutory weekly indemnity benefit: the
formula, the jurisdiction's clamp, the indemnity type it is paid under and the
reserve-rationale paragraph that explains the number. Stories 3.2–3.5 add the
reserve adequacy verdict, the payment schedule, the approval batch and the
action checklist, and each of them reads `compute_benefit` rather than
re-deriving a weekly figure from an AWW.

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
- **Rounding is half up, on cents** — `_round_half_up`, the only rounding in
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
)
from services.financials.rationale import format_comp_rate, reserve_rationale

__all__ = [
    "BASIS_POINTS_PER_UNIT",
    "COMP_RATE_MAX_BP",
    "COMP_RATE_MIN_BP",
    "Benefit",
    "BenefitClaim",
    "MissingStateRate",
    "benefit_for_claim",
    "compute_benefit",
    "format_comp_rate",
    "reserve_rationale",
]
