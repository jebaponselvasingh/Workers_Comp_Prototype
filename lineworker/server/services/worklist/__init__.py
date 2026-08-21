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

Story 5.4 adds `priority_claims`, and it is the entry that makes this package's
whole argument load-bearing rather than decorative: it is the first module here
that composes *two* of the others. Its ordering is `priority`'s — the same
scorer and, from this story, the same `order_key`, which was a lambda inside
`queue._ranked_group` until a second consumer needed it and is now one symbol
with two callers. Its action column is element 0 of what `actions.
generate_actions` returns, the pure generator that module built its structural
protocols for. So "the supervisor's worklist ranks claims the way the handler's
queue does and names the same next action the handler's checklist does" is not
two implementations agreeing; it is two call sites of one function each, and the
tests assert the equalities rather than restating the answers.

Structurally it departs from its three Epic 5 siblings in exactly one way, and
the reason is the cursor: it takes *five* awaited reads rather than one — the
scoped book, then four bulk child reads over the page's ids, because the action
generator reads a claim's documents, bills, schedule and diary. Five rather than
the forty a page of ten would cost through the single-claim reads, and
`test_the_aggregate_takes_exactly_five_scoped_reads` is what keeps it there. Like
`charts` it gates on scope and not on role, and the route's docstring carries an
argument that had to be *applied* rather than inherited: this payload names a
handler in every row, as the owner of a claim the caller can already read.

Story 5.5 adds `drill_through`, and it is the entry that makes the four
aggregates above *openable*. It answers one question — "which claims are behind
this number?" — for every one of them, and the way it answers is the whole
design: each of its twelve filter facets calls the same symbol the counting
surface called. `severityBand` is `derivations.risk`, the High Risk card's own
band; `fraudFlagged` is the registered fraud rule and deliberately not
`siu_review`; `stage` is the column `summary` and `charts` both group on and not
`status`; `priority` is `priority_claims.qualifies_for_worklist`, imported,
which is why that predicate stopped being private in this story. So "the list a
KPI opens reconciles with the number on the card" is not two implementations
agreeing — it is one predicate with two callers, twelve times over, and the
tests read both endpoints on one scope and assert the equality rather than
comparing either against a constant.

Structurally it is `charts`' shape with `priority_claims`' cursor: one scoped
read (`select_drill_rows`), one pure fold, every parameter block injected by the
router — and, like both of them, no role branch anywhere on the path. What it
does *not* share with `priority_claims` is the cap: a drill-through shows all of
its population, because a list that showed thirty of a card's ninety-two while
reporting ninety-two would be the one failure this module exists to prevent.

Story 7.1 opens Epic 7 with `fraud`, and it is the first module here whose
surface belongs to **one persona** rather than to one question. The four Epic 5
aggregates and `drill_through` answer for whoever holds the session cookie, gated
by scope alone or — in `benchmarks`' case — by an oversight capability two roles
share. This one is the analyst's workspace: `FRAUD_ANALYTICS_ROLES` is a new
allowlist over `{analyst}` beside `benchmarks.PERMITTED_ROLES` rather than a
widening of it, so a supervisor is refused here while every Epic 5 route keeps
answering both roles identically.

What it adds to the package's argument is the third rule over one column pair.
Story 5.1's paragraph above explains why `fraud_flagged` had to be registered
beside `siu_review`; this story registers `fraud_band`, which bands `fraud_score`
with **no** `fraud_flag` conjunct and is therefore neither of them — and its high
edge carries the same number as the review threshold today, which is exactly why
it is a third parameter rather than a reuse. The distribution, the pipeline and
the rate breakdowns each call a different one of the three by name, and the tests
fold synthetic projections where the three disagree, because the seed cannot tell
them apart. Structurally it is `charts`' shape three times over — one scoped read,
one pure fold, every parameter block injected by the router — with two departures
it states rather than hides: the band distribution zero-fills where `charts`
omits (a rule's vocabulary is always complete), and the red-flag view takes *two*
scoped reads, because a coverage figure needs a denominator the insight join
cannot produce.
"""

# **`priority_claims` is deliberately missing from this list**, and it is the
# one submodule that is. Its name and its entry point's name coincide — the
# module is `priority_claims` and the aggregate it exists for is
# `priority_claims(db, ctx, …)` — so re-exporting both would make
# `services.worklist.priority_claims` a module at import time and a function
# immediately afterwards, depending on which import ran last. The function wins,
# because it is what every caller wants; anything needing the module reaches it
# by path (`from services.worklist.priority_claims import Cursor`), which is
# what `tests/test_priority_claims.py` does.
from services.worklist import (
    actions,
    approvals,
    benchmarks,
    charts,
    drill_through,
    fraud,
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
from services.worklist.drill_through import (
    FILTER_KEYS,
    AppliedFilter,
    DrillClaim,
    DrillClaims,
    DrillFilters,
    DrillFlags,
    DrillRow,
    RankedClaim,
    drill_through_claims,
)
from services.worklist.fraud import (
    FRAUD_ANALYTICS_ROLES,
    EmployerRate,
    FraudAnalyticsNotPermitted,
    FraudClaim,
    FraudClauseReader,
    FraudPanel,
    FraudRates,
    FraudRateSort,
    FraudRateSorts,
    FraudRedFlags,
    HandlerRate,
    InjuryTypeRate,
    RateBreakdown,
    RedFlagClause,
    fraud_panel,
    fraud_rates,
    fraud_red_flags,
    panel_of,
    rates_of,
    red_flags_of,
    require_fraud_analytics_access,
)
from services.worklist.priority import (
    QueueClaim,
    QueueFilter,
    QueueFlags,
    matches,
    order_key,
    priority_markers,
    priority_score,
)
from services.worklist.priority_claims import (
    NO_ACTION,
    PriorityClaim,
    PriorityClaims,
    PriorityFlags,
    PriorityRow,
    priority_claims,
    qualifies_for_worklist,
    rank,
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
    "Action",
    "ApprovalKind",
    "ApprovalNotPermitted",
    "ApprovalResult",
    "AppliedFilter",
    "BenchmarkClaim",
    "BenchmarksNotPermitted",
    "CategoryCount",
    "ChartClaim",
    "ClaimActions",
    "ClaimFlags",
    "ClaimQueue",
    "Cursor",
    "Distribution",
    "DrillClaim",
    "DrillClaims",
    "DrillFilters",
    "DrillFlags",
    "DrillRow",
    "EmployerPaid",
    "EmployerRate",
    "FILTER_KEYS",
    "FRAUD_ANALYTICS_ROLES",
    "FraudAnalyticsNotPermitted",
    "FraudClauseReader",
    "FraudClaim",
    "FraudPanel",
    "FraudRateSort",
    "FraudRateSorts",
    "FraudRates",
    "FraudRedFlags",
    "HandlerBenchmark",
    "HandlerBenchmarks",
    "HandlerRate",
    "INJURY_TYPE_LIMIT",
    "InjuryTypeRate",
    "InvalidCursor",
    "LabelCount",
    "MAX_PAGE_LIMIT",
    "MIN_PAGE_LIMIT",
    "NO_ACTION",
    "PortfolioCharts",
    "PortfolioClaim",
    "PortfolioSummary",
    "PriorityClaim",
    "PriorityClaims",
    "PriorityFlags",
    "PriorityRow",
    "QueueCard",
    "QueueClaim",
    "QueueFilter",
    "QueueFlags",
    "RankedClaim",
    "RateBreakdown",
    "RedFlagClause",
    "STAGE_ORDER",
    "STATE_LIMIT",
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
    "drill_through",
    "drill_through_claims",
    "encode_cursor",
    "fraud",
    "fraud_panel",
    "fraud_rates",
    "fraud_red_flags",
    "generate_actions",
    "handler_benchmarks",
    "matches",
    "order_key",
    "panel_of",
    "portfolio_charts",
    "portfolio_summary",
    "priority",
    "priority_claims",
    "priority_markers",
    "priority_score",
    "qualifies_for_worklist",
    "queue",
    "rank",
    "rates_of",
    "red_flags_of",
    "require_benchmarks_access",
    "require_fraud_analytics_access",
    "sla",
    "sla_strip",
    "strip_of",
    "summary",
    "summary_of",
    "topbar_stats",
]
