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

Story 3.4 adds the package's write half: `approval.py` moves a payment row to
`payment_scheduled` and `batch.py` is the only place anything moves to `paid`.
Together with `materialize.py` those are the three modules that write, and the
three tables they write are the ones AD-12 gives this package exclusive
ownership of.
"""

from services.financials.approval import (
    APPROVABLE_LINE_ITEM_STATUSES,
    APPROVABLE_WEEK_STATUSES,
    APPROVE_BILL_ACTION,
    APPROVE_EXPENSE_ACTION,
    APPROVE_WEEK_ACTION,
    Approval,
    PaymentNotVisible,
    StalePaymentRow,
    approve_bill_payment,
    approve_expense_payment,
    approve_schedule_week,
    line_item_is_approvable,
    week_is_approvable,
)
from services.financials.batch import (
    BATCH_ACTION,
    PaymentBatchRun,
    run_payment_batch,
    system_context,
)
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
from services.financials.materialize import (
    DECIDED_STATUSES,
    MATERIALIZE_ACTION,
    MaterializationPlan,
    StoredWeek,
    materialize_schedule,
    plan_materialization,
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
    indemnity_terms,
    reserve_check_for_claim,
    reserve_check_from_rows,
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
from services.financials.summary import (
    ClaimFinancials,
    FinancialSummary,
    LineItemGroup,
    LineItemView,
    ScheduleWeekView,
    claim_financials,
)

__all__ = [
    "APPROVABLE_LINE_ITEM_STATUSES",
    "APPROVABLE_WEEK_STATUSES",
    "APPROVE_BILL_ACTION",
    "APPROVE_EXPENSE_ACTION",
    "APPROVE_WEEK_ACTION",
    "BASIS_POINTS_PER_UNIT",
    "BATCH_ACTION",
    "CLOSED_FINAL_RATIONALE",
    "COMP_RATE_MAX_BP",
    "COMP_RATE_MIN_BP",
    "DECIDED_STATUSES",
    "MATERIALIZE_ACTION",
    "MAX_WEEKS",
    "MIN_WEEKS",
    "SCHEDULE_WEEKS",
    "ZERO_RESERVE_CLEAR_RATIO_BP",
    "ZERO_RESERVE_EXPOSED_RATIO_BP",
    "Approval",
    "Benefit",
    "BenefitClaim",
    "ClaimFinancials",
    "FinancialSummary",
    "LineItemGroup",
    "LineItemView",
    "MaterializationPlan",
    "MissingStateRate",
    "PaymentBatchRun",
    "PaymentNotVisible",
    "PaymentProjection",
    "ReserveCheck",
    "ReserveClaim",
    "ReserveVerdict",
    "ScheduleClaim",
    "ScheduleWeek",
    "ScheduleWeekView",
    "StalePaymentRow",
    "StoredWeek",
    "approve_bill_payment",
    "approve_expense_payment",
    "approve_schedule_week",
    "benefit_for_claim",
    "claim_financials",
    "classify_reserve",
    "compute_benefit",
    "indemnity_terms",
    "line_item_is_approvable",
    "format_comp_rate",
    "format_dollars",
    "materialize_schedule",
    "plan_materialization",
    "project_payments",
    "reserve_check_for_claim",
    "reserve_check_from_rows",
    "reserve_rationale",
    "round_half_up",
    "run_payment_batch",
    "scheduled_weeks",
    "system_context",
    "unpaid_medical_cents",
    "week_is_approvable",
]
