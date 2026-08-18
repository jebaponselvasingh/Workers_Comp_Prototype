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

Story 3.5 closes the epic with `actions` — the deterministic checklist
generator AD-2 homes here by name. It writes even less than the approval command
does: it produces a *list*, and the three completions a handler can perform from
it are `services/claims`' commands, because `claim` and `document` are that
package's tables. Story 5.4's supervisor worklist reads element 0 of what this
generates and Epic 6's copilot may quote it, which is why `generate_actions` is
a pure function over structural protocols rather than a step inside the
endpoint's assembly — the same reason `priority_score` is.

Story 5.1 opens Epic 5 with `summary` — the first *portfolio* aggregate, as
against the caseload aggregates above. It is the same shape as `sla`: one
scoped projection read, one pure fold, so the ten KPI cards, 5.2's handler
benchmarking and 5.3's charts fold the same rows through the same registered
derivations rather than each counting a portfolio their own way. That the
supervisor's dashboard and the handler's top bar cannot disagree about "high
risk" is not a coincidence of two correct implementations; it is that there is
one, and both call it.

Story 5.2 adds `benchmarks`, and it is the entry this package's Story 1.5
paragraph promised by name: "the dashboard (5.3), handler benchmarking (5.2) …
reach `sla.strip_of` rather than re-deriving an average beside it". It is also
the first aggregate here to **gate on role** rather than on scope alone — a
count of your own book is your own data in another shape, but a table of peers'
cycle times is information about colleagues, and `BenchmarksNotPermitted` says
so before the read. Everything else about it is `summary`'s shape: one scoped
projection read, one pure fold, the derivations passed in.

Story 5.3 adds `charts`, the second half of the sentence above and the last
consumer Story 1.5 named: seven distribution surfaces over the same scoped book
— stage, severity band, recovery status, injury type, employer spend and state,
plus the SLA strip itself. It is where the package's whole argument becomes
checkable on one screen. Its severity donut counts the *same*
`derivations.risk` the KPI card above it counts, its stage donut groups on the
*same* column the card beside it groups on, its employer bars sum the *same*
`derivations.total_paid`, and its four tiles are the *same* `sla.strip_of` the
top bar renders — none of which is a claim about two implementations agreeing,
because in each case there is one and both surfaces call it. The tests assert
those equalities between the two aggregates rather than against restated
numbers, which is the only form of the assertion that survives a retune.

Structurally it is `summary`'s shape again, widened: one scoped projection read
(`sla.SAMPLE_COLUMNS` plus seven columns), one pure fold, every parameter block
and the settings injected by the router. It gates on scope and **not** on role,
unlike `benchmarks` — seven distributions over your own book name nobody, so
there is no capability to gate; the route's docstring carries the argument.
"""

from services.worklist import (
    actions,
    approvals,
    benchmarks,
    charts,
    priority,
    queue,
    sla,
    summary,
)
from services.worklist.actions import (
    Action,
    ClaimActions,
    ClaimFlags,
    claim_actions,
    generate_actions,
)
from services.worklist.approvals import (
    APPROVAL_KINDS,
    ApprovalKind,
    ApprovalNotPermitted,
    ApprovalResult,
    approve_payment,
)
from services.worklist.benchmarks import (
    BenchmarkClaim,
    BenchmarksNotPermitted,
    HandlerBenchmark,
    HandlerBenchmarks,
    benchmarks_of,
    handler_benchmarks,
    require_benchmarks_access,
)
from services.worklist.charts import (
    INJURY_TYPE_LIMIT,
    STATE_LIMIT,
    CategoryCount,
    ChartClaim,
    Distribution,
    EmployerPaid,
    LabelCount,
    PortfolioCharts,
    charts_of,
    portfolio_charts,
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
from services.worklist.summary import (
    PortfolioClaim,
    PortfolioSummary,
    portfolio_summary,
    summary_of,
)

__all__ = [
    "APPROVAL_KINDS",
    "INJURY_TYPE_LIMIT",
    "MAX_PAGE_LIMIT",
    "MIN_PAGE_LIMIT",
    "STAGE_ORDER",
    "STATE_LIMIT",
    "Action",
    "ApprovalKind",
    "ApprovalNotPermitted",
    "ApprovalResult",
    "BenchmarkClaim",
    "BenchmarksNotPermitted",
    "CategoryCount",
    "ChartClaim",
    "ClaimActions",
    "ClaimFlags",
    "ClaimQueue",
    "Cursor",
    "Distribution",
    "EmployerPaid",
    "HandlerBenchmark",
    "HandlerBenchmarks",
    "InvalidCursor",
    "LabelCount",
    "PortfolioCharts",
    "PortfolioClaim",
    "PortfolioSummary",
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
    "actions",
    "approvals",
    "approve_payment",
    "benchmarks",
    "benchmarks_of",
    "charts",
    "charts_of",
    "claim_actions",
    "claim_queue",
    "decode_cursor",
    "encode_cursor",
    "generate_actions",
    "handler_benchmarks",
    "matches",
    "portfolio_charts",
    "portfolio_summary",
    "priority",
    "priority_markers",
    "priority_score",
    "queue",
    "require_benchmarks_access",
    "sla",
    "sla_strip",
    "strip_of",
    "summary",
    "summary_of",
    "topbar_stats",
]
