"""`services/worklist` — the caseload's read aggregates and, from Epic 3,
its action-checklist writes.

Story 1.4 gives it read aggregates only: this story touches no claim
entity, so there are no AD-4 commands and no AD-12 ownership here yet. Its
job is to compose two things it does not own — the scoped repository
(AD-7) and the derivations registry (AD-10) — into the numbers a screen
renders (AD-1: a figure on screen came from a service).

Story 1.5 adds the SLA strip (`sla`), which AD-2 homes here by name: the
dashboard (5.3), handler benchmarking (5.2) and any later copilot
narration reach `sla.strip_of` rather than re-deriving an average beside
it.

Story 2.1 adds the third and largest: `priority` (the exactly-once scorer
AD-2 names, the marker rule that reads its output, and the eight-filter
predicate table) and `queue` (the scoping, grouping and paging around
them). Epic 5's top-30 worklist imports `priority_score` and
`priority_markers` from here unchanged — which is the reason both are pure
functions in their own module rather than steps inside the assembly.

Story 3.4 gives the package its **first command** (`approvals`), and it writes
nothing: AD-12 names the delegation by name — "worklist approval calls
financials' command" — so this module gates capability, resolves the claim and
hands the write to `services/financials`. The write-owner role the docstring
above promised for Epic 3 turns out to be a *calling* role, which is the
architecture working rather than a shortfall: two services writing one table is
the failure AD-12 exists to prevent, and the approval surface is exactly where
it would have happened.
"""

from services.worklist import approvals, priority, queue, sla
from services.worklist.approvals import (
    APPROVAL_KINDS,
    ApprovalKind,
    ApprovalNotPermitted,
    ApprovalResult,
    approve_payment,
)
from services.worklist.priority import (
    QueueClaim,
    QueueFilter,
    QueueFlags,
    matches,
    priority_markers,
    priority_score,
)
from services.worklist.queue import (
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    STAGE_ORDER,
    ClaimQueue,
    Cursor,
    InvalidCursor,
    QueueCard,
    StageGroup,
    claim_queue,
    decode_cursor,
    encode_cursor,
)
from services.worklist.sla import SlaMetric, SlaMetricKey, SlaSample, SlaStatus, sla_strip, strip_of
from services.worklist.stats import TopBarStats, topbar_stats

__all__ = [
    "APPROVAL_KINDS",
    "MAX_PAGE_LIMIT",
    "MIN_PAGE_LIMIT",
    "STAGE_ORDER",
    "ApprovalKind",
    "ApprovalNotPermitted",
    "ApprovalResult",
    "ClaimQueue",
    "Cursor",
    "InvalidCursor",
    "QueueCard",
    "QueueClaim",
    "QueueFilter",
    "QueueFlags",
    "SlaMetric",
    "SlaMetricKey",
    "SlaSample",
    "SlaStatus",
    "StageGroup",
    "TopBarStats",
    "approvals",
    "approve_payment",
    "claim_queue",
    "decode_cursor",
    "encode_cursor",
    "matches",
    "priority",
    "priority_markers",
    "priority_score",
    "queue",
    "sla",
    "sla_strip",
    "strip_of",
    "topbar_stats",
]
