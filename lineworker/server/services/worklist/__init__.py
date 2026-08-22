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

Story 7.2 adds `trends`, the workspace's second section and the first aggregate
in this package with a **time axis**. Every other module here answers a question
about today; this one folds the same scoped book into periods, so the console can
finally say whether any of it is moving. It reuses rather than re-declares:
`fraud.FRAUD_ANALYTICS_ROLES` gates it, because a second section of one workspace
must not carry a second spelling of one allowlist; `derivations.risk` bands its
severity cohort, so a cohort here and a slice on 5.3's donut are one band with
two callers; `derivations.days_open` and `derivations.total_paid` compute two of
its five metrics; and `sla.strip_of` computes the other two, once per bucket,
which is `benchmarks.py`'s second-consumer arrangement applied N times over one
read instead of once.

What it adds to the package's argument is that AD-2 survives being called in a
loop. The settlement mean and the RTW rate are published per bucket and per
cohort, which is the most tempting place in this codebase to write a second
average — and there is none: this module never names an SLA duration column, the
columns reach it as `sla.SAMPLE_COLUMNS` and the answers as `sla.strip_of`'s. Its
one genuinely new rule is a fourth zero-fill policy, and it is a fourth because
it bands a *timeline* rather than a vocabulary: counts and sums zero-fill because
an empty period really did see nothing, while means and rates go `null` because a
mean over an empty set is not zero — `sla`'s own `no_data` vocabulary, inherited
rather than invented.

Story 7.4 adds `decomposition`, the workspace's fourth section and the first
aggregate in this package whose subject is **money**. Every other module here
counts claims, averages days or bands a score; this one sums three cent figures
over the segmented book, breaks them down by any one of the ten segmentation
dimensions, and counts Epic 3's reserve verdict across a portfolio. It reuses
rather than re-declares at every seam: `fraud.require_fraud_analytics_access`
gates it, because a fourth section of one workspace must not carry a fourth
spelling of one allowlist; `segmentation.narrowed` narrows it, over rows one
scoped read returned; `derivations.total_paid` and
`derivations.total_claim_projected` are the two money computers, so a portfolio
total here and the Total Paid KPI card cannot disagree; and the verdict comes
from `services/financials/reserve.reserve_checks_for_claims`, the bulk seam that
folds stored rows through the *same* `reserve_check_from_rows` the case file and
the Bills tab reach the verdict through.

What it adds to the package's argument is that AD-2 survives being *counted*.
The temptation on this surface is enormous and is one comparison wide: a reserve,
an exposure and the two published band edges all arrive on one payload, so
re-checking a claim against the ratios is a line somebody would write — and it
would agree with the case file on most claims and disagree on the ones where a
week boundary or a missing bill decides. There is no such line: this module holds
no ratio, no band and no reserve comparison, and its distribution is a `dict` of
counters keyed on a verdict somebody else computed. `tests/test_reserve_block.py`
greps this package for the arithmetic — the reason it can is that there is
nothing here to find — and the agreement test asserts the rest the only way it
can be asserted: the bucket the portfolio puts a sampled claim in *is* what
`reserve_check_for_claim` answers for that claim.

Its one structural departure is the read count, and it is stated rather than
hidden: the decomposition takes one scoped read like its three siblings, and the
adequacy distribution takes **three** — the claims, then the payment-schedule
weeks and the bills every verdict's exposure terms are read off, both in bulk and
neither materialized, because an analyst route is read-only by role capability
and `reserve_check_for_claim`'s refresh-then-read is a write.

Story 7.3 adds `segmentation`, and it is the only module in this package that
answers no question of its own. Every other one folds a book into a figure; this
one *narrows the book* — ten dimensions, ANDed, applied inside `fraud` and
`trends` over rows their existing scoped reads already returned, so the whole
workspace recomputes under one filter and not one aggregate gains a query. Its
vocabulary is a **subset of `drill_through`'s**, asserted at import against that
module's field names and wire spellings, which is what makes a workspace filter
and a drill-through filter the same words: an analyst's active segmentation
survives a click into Story 5.5's list as a merge rather than a translation. Two
of its ten dimensions are registered derivations — `risk`, and the new `age_band`
over `employee.age` — which is exactly why it lives here and not in `data/`: half
a filter in SQL and half in Python is the split the repository refuses in
writing, twice.

Story 7.5 adds `export`, and it is the second module here that answers no
question of its own — but it is the opposite kind of passenger from
`segmentation`. That one narrows the book every other module folds; this one
takes what they folded and makes it rectangular. It holds no threshold, no
derivation, no `sorted` and no arithmetic on a published number, because the
whole claim of the story is that an exported figure cannot differ from a
displayed one: each of its eight targets is handed an already-computed aggregate
— a `FraudPanel`, a `FraudRates`, a `PortfolioTrends`, a
`FinancialDecomposition`, a `ReserveAdequacy`, or `drill_through.select`'s whole
ranked list — by the same function the sibling route calls, and shapes it. Its
six entry points are the only reads in this package that write anything: one
content-free `audit_event` each, through `services/audit`, because an export is
the one thing a dashboard does that moves PHI out of the system (AD-4, NFR-5).
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
    decomposition,
    drill_through,
    export,
    fraud,
    priority,
    queue,
    segmentation,
    sla,
    summary,
    trends,
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
from services.worklist.decomposition import (
    BREAKDOWN_LIMIT,
    DEFAULT_BREAKDOWN,
    BreakdownDimension,
    BreakdownGroup,
    CostDriverCohort,
    CostDriverPair,
    FinancialBreakdown,
    FinancialClaim,
    FinancialDecomposition,
    MoneyTotals,
    ReserveAdequacy,
    VerdictCount,
    adequacy_of,
    decomposition_of,
    financial_decomposition,
    reserve_adequacy,
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
from services.worklist.export import (
    MEDIA_TYPE,
    ExportColumn,
    ExportFormat,
    ExportTable,
    ExportTarget,
    ExportTooLarge,
    ExportUnit,
    ExportUnwritable,
    export_claims,
    export_financials,
    export_fraud,
    export_fraud_rates,
    export_reserve_adequacy,
    export_trends,
    filename_for,
    render_csv,
    render_xlsx,
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
    segmentation_values,
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
from services.worklist.segmentation import (
    SEGMENTATION_KEYS,
    SEGMENTATION_WIRE_KEYS,
    DimensionValue,
    DimensionValues,
    LabelledClaim,
    Segmentation,
    SegmentationValues,
    SegmentedBands,
    SegmentedClaim,
    to_drill_filters,
    values_of,
)
from services.worklist.sla import SlaMetric, SlaMetricKey, SlaSample, SlaStatus, sla_strip, strip_of
from services.worklist.stats import TopBarStats, topbar_stats
from services.worklist.summary import (
    PortfolioClaim,
    PortfolioSummary,
    portfolio_summary,
    summary_of,
)
from services.worklist.trends import (
    PortfolioTrends,
    TrendAnchor,
    TrendBucket,
    TrendClaim,
    TrendCohort,
    TrendGrain,
    TrendMetric,
    TrendPoint,
    TrendRangeInvalid,
    TrendRangeTooWide,
    TrendSeries,
    TrendWindow,
    cohort_key_of,
    portfolio_trends,
    trends_of,
    window_for,
)

__all__ = [
    "MEDIA_TYPE",
    "ExportColumn",
    "ExportFormat",
    "ExportTable",
    "ExportTarget",
    "ExportTooLarge",
    "ExportUnit",
    "ExportUnwritable",
    "export",
    "export_claims",
    "export_financials",
    "export_fraud",
    "export_fraud_rates",
    "export_reserve_adequacy",
    "export_trends",
    "filename_for",
    "render_csv",
    "render_xlsx",
    "reserve_adequacy",
    "financial_decomposition",
    "decomposition_of",
    "decomposition",
    "adequacy_of",
    "VerdictCount",
    "ReserveAdequacy",
    "MoneyTotals",
    "FinancialDecomposition",
    "FinancialClaim",
    "FinancialBreakdown",
    "CostDriverPair",
    "CostDriverCohort",
    "BreakdownGroup",
    "BreakdownDimension",
    "DEFAULT_BREAKDOWN",
    "BREAKDOWN_LIMIT",
    "APPROVAL_KINDS",
    "SEGMENTATION_KEYS",
    "SEGMENTATION_WIRE_KEYS",
    "Action",
    "AppliedFilter",
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
    "DrillClaim",
    "DrillClaims",
    "DrillFilters",
    "DrillFlags",
    "DimensionValue",
    "DimensionValues",
    "DrillRow",
    "EmployerPaid",
    "EmployerRate",
    "FILTER_KEYS",
    "FRAUD_ANALYTICS_ROLES",
    "FraudAnalyticsNotPermitted",
    "FraudClaim",
    "FraudClauseReader",
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
    "LabelledClaim",
    "MAX_PAGE_LIMIT",
    "MIN_PAGE_LIMIT",
    "NO_ACTION",
    "PortfolioCharts",
    "PortfolioClaim",
    "PortfolioSummary",
    "PortfolioTrends",
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
    "SegmentationValues",
    "SegmentedBands",
    "SegmentedClaim",
    "Segmentation",
    "SlaSample",
    "SlaStatus",
    "StageGroup",
    "TopBarStats",
    "TrendAnchor",
    "TrendBucket",
    "TrendClaim",
    "TrendCohort",
    "TrendGrain",
    "TrendMetric",
    "TrendPoint",
    "TrendRangeInvalid",
    "TrendRangeTooWide",
    "TrendSeries",
    "TrendWindow",
    "actions",
    "approvals",
    "approve_payment",
    "benchmarks",
    "benchmarks_of",
    "charts",
    "charts_of",
    "claim_actions",
    "claim_queue",
    "cohort_key_of",
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
    "portfolio_trends",
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
    "segmentation",
    "segmentation_values",
    "sla",
    "sla_strip",
    "strip_of",
    "summary",
    "summary_of",
    "to_drill_filters",
    "topbar_stats",
    "trends",
    "trends_of",
    "values_of",
    "window_for",
]
