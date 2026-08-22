"""The one registry of derived-value computers (AD-10).

Import this package to reach a derivation; never re-implement one at a call
site. Entries register themselves on import, so the imports below are the
registration list — a derived value that is not imported here does not
exist as far as `get()` is concerned.

    thresholds = await rules.thresholds_for(db)
    risk = derivations.risk.for_thresholds(thresholds)
    band = risk.of(claim.severity_score)

Story 1.4 registered `risk`. Story 2.1 adds the four the queue needs —
`days_open`, `siu_review`, `rtw_blocked`, `payment_due` — and moves the
parameter source from `Settings` to the versioned JDM documents in `rules/`
(AD-8), which is why the builder argument is now `DerivationThresholds`.
Story 2.2 adds the two the case file needs, `treatment_phase` and
`coordination_status`, and supersedes the thresholds document with a v2
carrying the phase parameters. Story 2.5 adds `claim_path` and a v3 carrying
its three — the entry that closes the prototype's always-Path-B gap, and the
clearest illustration of what this registry is for: the forms card and Epic
3's action worklist ask the same function rather than each deciding what a
fatality claim looks like. Story 3.1 adds `indemnity_type` and a v4 carrying
its cut-off, and it is the first entry whose answer another *service* consumes
rather than a card: `services/financials` asks it whether a claim is
permanently totally disabled instead of comparing a score itself, so the PTD
condition has one reader rather than two. Story 3.3 adds the five the Bills &
Payments summary needs — `paid_to_date`, `total_claim_projected`,
`installments_paid`, `next_payment_due` and `bills_on_file` — which is what
makes the treatment Overview card and the Bills tab agree on a figure by
construction rather than by two components being kept in step. Each one
function, here, called by every consumer. Story 3.4 adds `next_batch_date`,
which is the first entry whose parameter is *deployment* config rather than a
rules-tier threshold — see `batch_calendar.py` for why a disbursement calendar
is not an AD-8 parameter. Story 4.1 adds `meeting_status`, the first entry
belonging to the diary aggregate rather than to a claim: the prototype answers
"is this meeting still ahead?" with a `>=` in the component that draws the
card, and Story 4.2's today's-meetings summary is the second component that
would have written one. Story 5.1 adds `fraud_flagged` — and it is the entry
that shows most plainly why registration is by *name* rather than by shape: it
is `siu_review` with a different threshold, so the one thing standing between
the dashboard's Fraud Flags card and the queue's referral count is that they
ask for different names. Reusing `siu_review` there would have been a one-word
change that showed 9 claims under a caption promising 13, and nothing in the
type system would have objected. Story 5.2 adds the epic's two *new* derived
values, `handler_complexity` and `cycle_time_status`, and they are the first
entries whose subject is a **handler's book** rather than a claim, a meeting or
a date. They are also the first to take their tunables at `.of()` from a
document that is not `derivation_thresholds` — `next_batch_date` set that
precedent for deployment config, and these extend it to a rules-tier block that
belongs to one aggregate (see `rules/parameters.HandlerPerformance`). The reason
either of them is registered at all is the reason every entry above is: the
prototype computes both inside the function that draws the dashboard table row,
where Epic 7's fraud workspace would have written each of them a second time.

Story 7.1 adds `fraud_band`, and it is the entry that makes this package's
Story 5.1 paragraph a convention rather than an anecdote. That paragraph explains
why `fraud_flagged` is registered by *name* beside `siu_review`: the two are one
threshold apart over one column pair, and only the name keeps the dashboard's
count of 13 from becoming the queue's 9. `fraud_band` is the third rule over the
same pair and the first that is **not** a population — it bands `fraud_score`
with no `fraud_flag` conjunct, so a claim nobody triaged still lands in a band —
and its high cut-off happens to read the same 55 as the review threshold today.
Three rules, three names, three parameters, one column pair; see
`fraud_score_band.py` on why the coincidence is exactly the reason they must not
be collapsed.

Story 7.3 adds `age_band`, and it is the first entry that bands a **person**
rather than a claim, a handler's book or a date. The reason it exists is the
reason every entry above does — the analyst workspace segments by nine
dimensions and `employee.age` is the only one that is a number, so without a
registered banding the picker would offer thirty-eight values and the first
component to want four groups would write its own edges. What is new about it
is the *shape of the vocabulary*: its members are ordinal words (`youngest`,
`younger`, `older`, `oldest`) and carry no numbers at all, because a member
spelled `age_35_44` would put the cut-off in this tier as well as in the
document and would go on asserting the old edge after the document moved. The
edges ride on the payload and the UI composes the range label from them; see
`worker_age_band.py`.

**Naming rule for the modules below** (code review, 2026-08-10): a module
is named after the *rule* (`risk_band`, `open_duration`, `queue_flags`),
never after the derived value it exports (`risk`, `days_open`).
Re-exporting a name that matches its own module silently rebinds the package
attribute — `import services.derivations.risk` would hand back the
`Derivation` instance, and `services.derivations.risk.RiskBand` would raise
`AttributeError` with no obvious cause. This package is the template every
later derivation copies, so the trap is worth avoiding by convention rather
than documenting per entry.
"""

from services.derivations.batch_calendar import (
    NextBatchDateDerivation,
    next_batch_date,
)
from services.derivations.care_coordination import (
    CoordinationDerivation,
    CoordinationResult,
    CoordinationStatus,
    coordination_status,
)
from services.derivations.claim_financials import (
    BillsOnFileDerivation,
    InstallmentsPaidDerivation,
    LineItem,
    NextPaymentDueDerivation,
    PaidToDate,
    PaidToDateDerivation,
    ScheduleRow,
    TotalClaimProjectedDerivation,
    bills_on_file,
    installments_paid,
    next_payment_due,
    paid_to_date,
    paid_total,
    total_claim_projected,
    unpaid_total,
)
from services.derivations.claim_money import (
    CostSplit,
    CostSplitDerivation,
    PaidColumns,
    TotalPaidDerivation,
    cost_split,
    split_of,
    total_paid,
)
from services.derivations.complexity_blend import (
    ComplexityBand,
    HandlerComplexity,
    HandlerComplexityDerivation,
    HandlerMix,
    handler_complexity,
)
from services.derivations.cycle_deviation import (
    CycleStatus,
    CycleTimeStatusDerivation,
    cycle_time_status,
)
from services.derivations.fraud_score_band import (
    FraudBand,
    FraudBandDerivation,
    fraud_band,
)
from services.derivations.indemnity_classification import (
    IndemnityType,
    IndemnityTypeDerivation,
    indemnity_type,
)
from services.derivations.meeting_horizon import (
    MeetingStatus,
    MeetingStatusDerivation,
    ScheduledMeeting,
    meeting_status,
    upcoming_predicate,
)
from services.derivations.open_duration import (
    OpenDurationDerivation,
    SettlementDurationDerivation,
    days_open,
    days_to_settlement,
    utc_today,
)
from services.derivations.path_classification import ClaimPathDerivation, claim_path
from services.derivations.queue_flags import (
    FraudFlaggedDerivation,
    PaymentDueDerivation,
    RtwBlockedDerivation,
    SiuReviewDerivation,
    fraud_flagged,
    hash_bucket,
    payment_due,
    rtw_blocked,
    siu_review,
)
from services.derivations.registry import Derivation, get, register, registered_names
from services.derivations.risk_band import RiskBand, RiskDerivation, risk
from services.derivations.treatment_progress import (
    TreatmentPhase,
    TreatmentPhaseDerivation,
    TreatmentPhaseResult,
    treatment_phase,
)
from services.derivations.worker_age_band import (
    AgeBand,
    AgeBandDerivation,
    age_band,
)

__all__ = [
    "AgeBand",
    "AgeBandDerivation",
    "BillsOnFileDerivation",
    "ClaimPathDerivation",
    "ComplexityBand",
    "CoordinationDerivation",
    "CoordinationResult",
    "CoordinationStatus",
    "CostSplit",
    "CostSplitDerivation",
    "CycleStatus",
    "CycleTimeStatusDerivation",
    "Derivation",
    "FraudBand",
    "FraudBandDerivation",
    "FraudFlaggedDerivation",
    "HandlerComplexity",
    "HandlerComplexityDerivation",
    "HandlerMix",
    "IndemnityType",
    "IndemnityTypeDerivation",
    "InstallmentsPaidDerivation",
    "LineItem",
    "MeetingStatus",
    "MeetingStatusDerivation",
    "NextBatchDateDerivation",
    "NextPaymentDueDerivation",
    "OpenDurationDerivation",
    "PaidColumns",
    "PaidToDate",
    "PaidToDateDerivation",
    "PaymentDueDerivation",
    "RiskBand",
    "RiskDerivation",
    "RtwBlockedDerivation",
    "ScheduleRow",
    "ScheduledMeeting",
    "SettlementDurationDerivation",
    "SiuReviewDerivation",
    "TotalClaimProjectedDerivation",
    "TotalPaidDerivation",
    "TreatmentPhase",
    "TreatmentPhaseDerivation",
    "TreatmentPhaseResult",
    "age_band",
    "bills_on_file",
    "claim_path",
    "coordination_status",
    "cost_split",
    "cycle_time_status",
    "days_open",
    "days_to_settlement",
    "fraud_band",
    "fraud_flagged",
    "get",
    "handler_complexity",
    "hash_bucket",
    "indemnity_type",
    "installments_paid",
    "meeting_status",
    "next_batch_date",
    "next_payment_due",
    "paid_to_date",
    "paid_total",
    "payment_due",
    "register",
    "registered_names",
    "risk",
    "rtw_blocked",
    "siu_review",
    "split_of",
    "total_claim_projected",
    "total_paid",
    "unpaid_total",
    "upcoming_predicate",
    "treatment_phase",
    "utc_today",
]
