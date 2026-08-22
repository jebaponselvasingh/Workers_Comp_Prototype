"""The analyst workspace's segmentation — one filter, ten dimensions, every
aggregate (FR-AN-4, Story 7.3).

Epic 7 shipped two sections whose aggregates each answer one question over the
caller's whole book. Nothing narrowed them: `fraud_panel`, `fraud_rates`,
`fraud_red_flags` and `portfolio_trends` took no filter at all, so every figure
on the workspace was a dead end in the one direction an analyst works — narrow,
then look again. This module is the narrowing, declared once and applied by all
of them.

## One vocabulary, not two — and that is the whole architectural move

The ten dimensions are **a subset of `drill_through.DrillFilters`**: the same
field names, the same `filter[…]` wire spellings, the same value vocabularies,
checked against that module at import time — four `if`s that raise
`VocabularyDivergence`, never `assert`s, for the reason recorded on that class.
Six of them were already shipped
facets (`severity_band`, `injury_type`, `state`, `employer_id`, `disability`,
`sector`) and Story 7.3 appended the other four (`region`, `icd10`, `age_group`,
`gender`) to the end of that dataclass rather than declaring them here.

The alternative — a second schema with its own spellings and a translation layer
between them — fails the story's second acceptance criterion structurally rather
than by being harder work. AC 2 requires the active segmentation to survive a
click into Story 5.5's claim list, and that only composes if both sides say
`filter[severityBand]`: a workspace URL and a drill URL then differ by a *merge*
rather than by a mapping whose only job is to be correct, forever, in both
directions. `web/src/features/dashboard/drill/filters.ts` already records that
the browser uses the API's own aliases precisely so no such layer exists.

**The import runs one way, deliberately.** `drill_through.py`'s field order is
load-bearing for every drill URL ever generated — `FILTER_KEYS` is read off the
dataclass and is the chip row's draw order — so the shipped block stays where it
is and this module points at it. Re-homing the vocabulary here would renumber
every chip on every link anybody has shared.

## Applied in the service, over rows a scoped read already returned (AD-7)

A filter narrows; it can never widen. `employer_scope(ctx)` is on the WHERE
clause of the one read each aggregate makes, and this module runs over what that
read returned — so a caller naming an employer, a region or a sector outside
their book gets an empty answer, never a row and never a 403.

**It is not pushed into SQL, and that is a structural refusal rather than a
performance choice.** Two of the ten dimensions are *derived*: `severity_band` is
the registered `risk` band of `severity_score` and `age_group` is the registered
`age_band` of `employee.age`. A SQL narrowing would therefore put part of one
filter in `data/` and the rest in `services/`, which is the split
`data/repositories/claims.py` refuses in writing twice, and which would make "the
list reconciles with the number that opened it" a property of two tiers agreeing
rather than of one rule called once.

The corollary is that segmentation adds **no read**. It narrows the fold — the
panel, the rates and the trends each still take exactly the scoped read they took
before, and the values endpoint is one more read and one pure fold.

## Zero result is an answer

`Segmentation` **has no `__post_init__` and deliberately declares none**, which
is `DrillFilters`' ruling reached by having nothing rather than by having a
method that returns: an unsatisfiable combination is a well-formed question whose
answer is "none". A nine-dimension AND makes that a normal outcome of a normal gesture
rather than an edge case, which is why every surface downstream has a zero-result
state and why `values_of` publishes only values the caller's own rows carry — a
picker that could offer a value matching nothing would make emptiness a
navigational dead end instead of an answer.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any, Final, Protocol, get_type_hints

from data.models.enums import Disability, Gender
from rules.parameters import DerivationThresholds
from services import derivations
from services.derivations import AgeBand, AgeBandDerivation, RiskBand, RiskDerivation
from services.worklist import drill_through
from services.worklist.drill_through import AppliedFilter, DrillFilters


@dataclass(frozen=True)
class Segmentation:
    """The ten dimensions, each optional, each `None` when the caller omitted it.

    `DrillFilters`' shape restricted to the segmentation vocabulary, field for
    field: every name here is a field of that dataclass, every type is that
    field's type, and the import-time checks below hold this class to it. One
    field per dimension rather than a `Mapping[str, str]`, and the **type of each
    field is the refusal**: `filter[gender]=nonsense` cannot reach this class,
    because FastAPI coerces the query parameter into `Gender` and answers 422
    before any service is called. That is the whole of the validation story —
    there is no vocabulary check in this module and there must not be one, or the
    enum would be spelled twice.

    **The field order is `FILTER_KEYS`' order**, not a reading order chosen here,
    and `SEGMENTATION_KEYS` is checked at import to be the subsequence rather
    than merely the subset. The chips a workspace draws and the chips the drill list draws
    are then one sequence: an analyst who narrows by sector and gender and then
    clicks a chart sees the same two chips in the same two positions on the list
    that opens, with the clicked slice inserted where its own facet belongs.

    **Nine dimensions, ten fields**, and the arithmetic is worth stating because
    the story counts one way and this class counts the other: the AC's
    "state/region" is one dimension of place and two stored columns —
    `claim.state` is the jurisdiction a benefit is calculated under and
    `claim.region` is the operating area a plant sits in — and no single facet
    can narrow on both without deciding that one is a rollup of the other, which
    is a data-quality judgement with an owner.

    **There is no `__post_init__` here, and its absence is the ruling rather
    than an omission** — `DrillFilters` has none either, for the same reason:
    `gender=female` beside `age_group=oldest` may be unsatisfiable on today's
    book and satisfiable tomorrow, and refusing it would mean this class knowing
    which combinations the *data* can satisfy, which is a fact about a seed
    rather than about a contract. The only refusal in this vocabulary is the type
    of each field, and it happens in FastAPI's coercion before this class is
    constructed at all.
    """

    severity_band: RiskBand | None = None
    injury_type: str | None = None
    state: str | None = None
    employer_id: int | None = None
    disability: Disability | None = None
    sector: str | None = None
    region: str | None = None
    icd10: str | None = None
    age_group: AgeBand | None = None
    gender: Gender | None = None


#: The dimension names, in the order a chip row draws them, declared once.
#:
#: Read off `Segmentation`'s own fields rather than written out, for
#: `FILTER_KEYS`' reason: an eleventh dimension added to that dataclass and
#: forgotten here would narrow every aggregate and appear in no chip — a filter
#: the analyst cannot see and therefore cannot clear.
SEGMENTATION_KEYS: Final[tuple[str, ...]] = tuple(field.name for field in fields(Segmentation))


#: Each dimension's field name → the spelling the wire uses for it.
#:
#: **Read out of `drill_through.WIRE_KEYS` rather than written down again**, which
#: is the one place this module's "one vocabulary" claim could have been made by
#: assertion instead of by construction. A second literal table would agree today
#: and would be free to disagree the day a facet was respelled, and the failure
#: would be invisible: the workspace would send `filter[ageGroup]`, the drill list
#: would accept `filter[age_group]`, and the segmentation would silently stop
#: surviving a click.
SEGMENTATION_WIRE_KEYS: Final[Mapping[str, str]] = {
    key: drill_through.WIRE_KEYS[key] for key in SEGMENTATION_KEYS
}


class VocabularyDivergence(RuntimeError):
    """The two filter vocabularies have come apart — raised at import.

    **An exception class and an `if`, not an `assert`, and the difference is a
    command-line flag.** `python -O` strips every `assert` from the bytecode, so
    a guarantee written as one is a guarantee that holds under the interpreter's
    default settings and silently does not under a deployment's. Three of the
    four invariants below are the whole of this module's "one vocabulary, not
    two" claim and the fourth is what stops a dimension from narrowing nothing;
    none of them is a debugging aid, and an optimised run is exactly where a
    silent filter would be hardest to notice.

    `RuntimeError` rather than a new root: the failure is a module that cannot
    honestly be imported, which is what that class means, and nothing catches
    this — the process is meant to stop.
    """


# Four checks, at import, and each one closes a different way the two
# vocabularies could come apart.
#
# **A subset**, so no dimension here is a name the drill list does not accept —
# which is the whole of "a drill URL is a merge rather than a translation".
if not set(SEGMENTATION_KEYS) <= set(drill_through.FILTER_KEYS):
    raise VocabularyDivergence(
        "Segmentation names dimensions DrillFilters does not have: "
        f"{sorted(set(SEGMENTATION_KEYS) - set(drill_through.FILTER_KEYS))}"
    )
# **A sub*sequence***, not merely a subset, because `appliedFilters` order is the
# chip row's order on both surfaces. A `Segmentation` whose fields were in a
# different order would draw the same chips in a different sequence on the
# workspace and on the list it opens, which reads as two different filters.
_DRILL_SUBSEQUENCE: Final[tuple[str, ...]] = tuple(
    key for key in drill_through.FILTER_KEYS if key in set(SEGMENTATION_KEYS)
)
if SEGMENTATION_KEYS != _DRILL_SUBSEQUENCE:
    raise VocabularyDivergence(
        "Segmentation's field order has diverged from DrillFilters': "
        f"{SEGMENTATION_KEYS} against {_DRILL_SUBSEQUENCE}"
    )
# And the field *types*, because a subset of names with a divergent type is a
# translation layer that has not noticed it exists yet: `Segmentation.employer_id`
# spelled `str` would build a `DrillFilters` comparing a string against an
# integer column, silently, for ever.
#
# **Compared through `get_type_hints` rather than `dataclasses.Field.type`**, and
# the difference is whether this guard survives a routine formatting change.
# `Field.type` is whatever stood in the source: the *object* `RiskBand | None`
# while a module has no `from __future__ import annotations`, and the *string*
# `"RiskBand | None"` the moment it does. Ruff's `FA` rules add that import as an
# autofix, so a fix applied to one of these two files and not the other would
# leave a str comparing unequal to a type and this module would stop importing
# for a reason no reader would connect to the diff. `get_type_hints` resolves
# both spellings against their own module's globals and answers with the type
# either way, which is the thing the guard is actually about.
_SEGMENTATION_TYPES: Final[Mapping[str, object]] = get_type_hints(Segmentation)
_DRILL_TYPES: Final[Mapping[str, object]] = {
    name: hint
    for name, hint in get_type_hints(DrillFilters).items()
    if name in set(SEGMENTATION_KEYS)
}
if _SEGMENTATION_TYPES != _DRILL_TYPES:
    _DIVERGED = {
        key: (value, _DRILL_TYPES[key])
        for key, value in _SEGMENTATION_TYPES.items()
        if _DRILL_TYPES[key] != value
    }
    raise VocabularyDivergence(
        f"Segmentation and DrillFilters disagree about a dimension's type: {_DIVERGED}"
    )


class SegmentedClaim(Protocol):
    """The ten columns the ten dimensions are decided from.

    A structural type rather than `DrillClaim`, `FraudClaim` or `TrendClaim`,
    which is `derivations.PaidColumns`' idiom and its reason: the three
    aggregates that narrow through this module carry three different projections
    of one book, and each satisfies this protocol **by shape**. Nothing here
    imports one of them, nothing here imports the ORM, and a fourth section in
    Story 7.4 joins by adding the columns rather than by being added to a union.

    Ten members for ten dimensions, and two of them are *inputs to a rule* rather
    than the dimension itself: `severity_score` and `age` are the raw columns the
    registered `risk` and `age_band` derivations band. The band is never on a
    projection, because a stored band would be the derived column Story 1.2
    banned and the second computer AD-10 forbids.

    `icd` is the column and `icd10` is the facet — `DrillFilters` carries the
    argument: the stored column is `claim.icd` and the wire name says which
    coding system its values are in.
    """

    @property
    def severity_score(self) -> int: ...

    @property
    def injury_type(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def employer_id(self) -> int: ...

    @property
    def disability(self) -> Disability: ...

    @property
    def sector(self) -> str: ...

    @property
    def region(self) -> str: ...

    @property
    def icd(self) -> str: ...

    @property
    def age(self) -> int: ...

    @property
    def gender(self) -> Gender: ...


class LabelledClaim(SegmentedClaim, Protocol):
    """A segmented claim that can also *name* its employer.

    One member wider than `SegmentedClaim`, and a separate protocol rather than
    an eleventh member on that one, because the two say different things: every
    projection that **narrows** must carry `employer_id`, and only a projection
    that **labels** a chip or a picker option needs the employer's display
    string. `TrendClaim` is the case that decides it — the Trends section splits
    by sector and has never had a use for an employer's name — so folding the
    label into the narrowing protocol would widen a projection for a payload that
    publishes no label at all.

    So `matches` and `narrowed` take the ten-member protocol and the two
    functions that publish a *human-readable* employer — `applied_of` and
    `values_of` — take this one. `FraudClaim` satisfies both, which is why the
    values endpoint folds that projection.
    """

    @property
    def employer_label(self) -> str: ...


@dataclass(frozen=True)
class Computers:
    """The two registered computers this module folds with, built once.

    A bundle rather than two parameters, for `drill_through._Computers`' reason:
    `bands_of` is called once per claim in a loop, and a two-argument call there
    would put the build order and the call order in two places.

    Public, unlike its siblings in `fraud.py` and `trends.py`, and that is the
    one difference worth naming: those bundles are built inside the aggregate
    that owns them, while this one is built by *three* aggregates and by the
    values endpoint. A private bundle with a public builder would be the same
    object under two names.
    """

    risk: RiskDerivation
    age_band: AgeBandDerivation

    @classmethod
    def of(cls, thresholds: DerivationThresholds) -> "Computers":
        """Build both from one parameter block — the registry's whole point."""
        return cls(
            risk=derivations.risk.for_thresholds(thresholds),
            age_band=derivations.age_band.for_thresholds(thresholds),
        )


@dataclass(frozen=True)
class SegmentedBands:
    """The two derived dimensions, as the registry computed them (AD-10).

    Two of the ten, and the other eight are columns compared as stored. They are
    bundled rather than returned as a pair for `DrillFlags`' reason: the
    predicate table below reads them by name, and a two-tuple would put their
    order in every call site.

    `severity_band` is the **same** registered `risk` derivation the High Risk
    card counts, 5.3's donut draws and 7.2's cohort splits on, so a segmentation
    by `high` and a slice of that donut are one band rather than two readings of
    one score. Nothing here re-bands.
    """

    severity_band: RiskBand
    age_group: AgeBand


def bands_of(claim: SegmentedClaim, computers: Computers) -> SegmentedBands:
    """One claim's two derived dimensions, both asked of the registry.

    Takes the built computers rather than the threshold block, which is the
    difference between one build and one per claim — `drill_through._flags_of`'s
    shape, for its reason.

    Both arguments are keyword at the call sites below, which is not decoration:
    a projection carries `severity_score` and `age` as two adjacent integers, and
    a positional pair would band a worker's age as a severity score — type-checking
    the whole way and producing a plausible distribution of the wrong column.
    """
    return SegmentedBands(
        severity_band=computers.risk.of(claim.severity_score),
        age_group=computers.age_band.of(age=claim.age),
    )


#: One predicate per dimension, in one mapping — total by construction against
#: `SEGMENTATION_KEYS`, and checkable as such (the assertion below runs at
#: import).
#:
#: `drill_through._PREDICATES`' shape and its reason: a `match` statement would
#: read the same and pass mypy, and nothing would notice an eleventh dimension
#: added to `Segmentation` without a branch. Here it fails at import.
#:
#: **Each entry is the same rule the shipped facet uses**, which is what makes
#: the two vocabularies one: the two derived dimensions go through the registry,
#: and the eight stored ones compare the exact stored value with no trim,
#: case-fold or merge — `injury_type`'s and `state`'s ruling, which this module
#: inherits because it narrows the same columns and a canonicalizing fold here
#: would produce a population no drill-through could reproduce.
_PREDICATES: Final[Mapping[str, Callable[[SegmentedClaim, SegmentedBands, Any], bool]]] = {
    "severity_band": lambda _claim, bands, value: bands.severity_band == value,
    "injury_type": lambda claim, _bands, value: claim.injury_type == value,
    "state": lambda claim, _bands, value: claim.state == value,
    "employer_id": lambda claim, _bands, value: claim.employer_id == value,
    "disability": lambda claim, _bands, value: claim.disability == value,
    "sector": lambda claim, _bands, value: claim.sector == value,
    "region": lambda claim, _bands, value: claim.region == value,
    "icd10": lambda claim, _bands, value: claim.icd == value,
    "age_group": lambda _claim, bands, value: bands.age_group == value,
    "gender": lambda claim, _bands, value: claim.gender == value,
}

# Every dimension has a predicate, and every predicate names a dimension. A
# dimension declared on `Segmentation` with no entry here would be accepted by
# the routes, published as a chip, and narrow nothing — an aggregate that says it
# is filtered and is not, which on this workspace means four cards, five charts
# and three tables quietly describing the whole book. An import-time equality is
# the cheapest place to make that loud — and it is an `if` rather than an
# `assert` for `VocabularyDivergence`' recorded reason: under `python -O` the
# assert form would make "every dimension narrows something" a property of the
# interpreter's flags.
if set(_PREDICATES) != set(SEGMENTATION_KEYS):
    raise VocabularyDivergence(
        f"Segmentation and _PREDICATES have diverged: {set(_PREDICATES) ^ set(SEGMENTATION_KEYS)}"
    )


def matches(claim: SegmentedClaim, bands: SegmentedBands, filters: Segmentation) -> bool:
    """Does this claim survive every dimension the caller set?

    An `and` over the *set* dimensions only — `drill_through.matches`' rule and
    its reason: an omitted dimension is not a wildcard predicate that happens to
    return `True`, it is a question nobody asked. The check is `is not None` on
    the field rather than a truthiness test on the value, which matters for
    `employer_id`: id 0 does not exist today and a truthiness test would be a
    silent trap for the day a surrogate sequence starts anywhere else.

    **AND across dimensions, and no OR anywhere.** The story's AC says composed
    filters AND together, and there is deliberately no multi-value form: one
    value per dimension, one predicate per dimension, and a caller who wants two
    sectors asks two questions. An OR *within* a dimension is a real product
    request and a real contract change — it multiplies the wire form
    (`filter[sector]=A&filter[sector]=B`), the cursor payload, the chip row and
    every oracle — and it is not what this story was asked for.
    """
    for key in SEGMENTATION_KEYS:
        value = getattr(filters, key)
        if value is None:
            continue
        if not _PREDICATES[key](claim, bands, value):
            return False
    return True


def narrowed[ClaimT: SegmentedClaim](
    caseload: Sequence[ClaimT], filters: Segmentation, computers: Computers
) -> list[ClaimT]:
    """The claims that survive the filter, in the order they arrived. Pure.

    The one call each aggregate makes, and it is generic over the projection so
    the caller gets its *own* row type back: `fraud_rates` folds `FraudClaim`s
    and `portfolio_trends` folds `TrendClaim`s, and neither should have to cast
    what this module hands back.

    A named function rather than a comprehension repeated in three services,
    because the comprehension has two halves — band, then match — and a service
    that inlined it would be one refactor away from banding inside the loop's
    `if` and rebuilding the computers per claim.

    **Order is preserved**, which is `claim_id` order from every repository read
    in this codebase. It matters to exactly one consumer today —
    `fraud.red_flags_of` fixes a clause's published spelling by first arrival —
    and a filter that reordered its input would make that spelling a property of
    the filter rather than of the data.
    """
    return [claim for claim in caseload if matches(claim, bands_of(claim, computers), filters)]


def applied_of(
    filters: Segmentation, caseload: Sequence[LabelledClaim]
) -> tuple[AppliedFilter, ...]:
    """The chips, in `SEGMENTATION_KEYS` order, with the employer display resolved.

    `drill_through._applied`'s shape over the smaller vocabulary, and the same
    `AppliedFilter` class rather than a second chip type — the browser draws both
    rows with one component, and two structurally identical payload shapes under
    two names is how the two come to disagree about what `display` means.

    **One of the ten is server-resolved and nine are not**, which is that
    function's split with `handler_id` removed: an employer id is not a label and
    nothing in the browser can turn `employerId=3` into "Boeing Everett" on a
    cold URL load, while the two enums, the two bands and the five free-text
    dimensions all carry a value the SPA already has copy for or *is* the label.
    `handler_id` is absent from this vocabulary entirely — a handler is a desk
    rather than a dimension of a claim, and the drill list still offers it.

    Resolved from `caseload` — the rows the aggregate already read — and never
    from a second query, which is what keeps the values endpoint at one scoped
    read. It also keeps the resolution *inside the scope*: a caller naming an
    employer outside their book gets `display: null` rather than that employer's
    name, so the chip cannot become an oracle for the existence of an employer
    they cannot see (AD-7).
    """
    applied: list[AppliedFilter] = []
    for key in SEGMENTATION_KEYS:
        value = getattr(filters, key)
        if value is None:
            continue
        display: str | None = None
        if key == "employer_id":
            display = next(
                (claim.employer_label for claim in caseload if claim.employer_id == value),
                None,
            )
        applied.append(
            AppliedFilter(
                key=SEGMENTATION_WIRE_KEYS[key],
                value=drill_through.chip_value(value),
                display=display,
            )
        )
    return tuple(applied)


@dataclass(frozen=True)
class DimensionValue:
    """One value a caller may pick, and what it reads as.

    `value` is the wire form (`high`, `female`, `Aerospace`, `3`) and `label` is
    a human name **or `None`** — `AppliedFilter.display`'s split, restated on a
    picker so the client's rule is the same one in both places:
    `label ?? UI_LABEL[key][value] ?? value`. Only `employer_id` carries a label,
    for that class's recorded reason.
    """

    value: str
    label: str | None


@dataclass(frozen=True)
class DimensionValues:
    """Every value one dimension carries inside the caller's book.

    `key` is the wire facet name, so a picker's `<select>` and the
    `filter[…]` parameter it writes are one string.

    **Folded from the caller's own scoped rows**, which is the property that makes
    this endpoint worth having rather than a convenience: a picker cannot offer a
    value with no claims behind it, cannot enumerate a sector, a region or an
    employer outside the caller's book, and therefore cannot lead an analyst to a
    zero-result page that was unreachable from the data. It is also what keeps
    `AppliedFilter.display`'s two null cases (`deferred-work.md`) out of reach
    through the control.
    """

    key: str
    values: tuple[DimensionValue, ...]


@dataclass(frozen=True)
class SegmentationValues:
    """What the caller may segment by, what they currently are, and the age edges.

    Three things on one payload because they are one screen's worth of state and
    they are folded from one read.

    - `dimensions` — the pickers' options, over the **unfiltered** scoped book.
      Deliberately not narrowed by `applied_filters` beside them: a picker whose
      options were cut by the active filter could not be used to widen one, which
      would make every filter a one-way door. The asymmetry is the point.
    - `applied_filters` — the server's reading of the URL, in chip order, which
      is what makes "the chips, the request and the result agree" a property
      rather than a hope: an unknown parameter name produces no chip because it
      narrowed nothing.
    - `claims_in_scope` and `claims_matching` — the caller's whole book, and how
      much of it survives the filter. The second is what a zero-result state is
      decided on, `DistributionDonut`'s rule that emptiness is a *total* and never
      a row count.
    - the three age edges — published because the band's members carry no numbers
      (`services/derivations/worker_age_band.py`), so the range of years a picker
      shows is composed in the browser from the document's own cut-offs and there
      is exactly one source for it.
    """

    dimensions: tuple[DimensionValues, ...]
    applied_filters: tuple[AppliedFilter, ...]
    claims_in_scope: int
    claims_matching: int
    age_younger_min: int
    age_older_min: int
    age_oldest_min: int


#: Which dimensions publish their values in a declared order, and which sort.
#:
#: Four of the ten have a *vocabulary* — two registered bands and two enum
#: columns — and a vocabulary has an order its own declaration decided: `RiskBand`
#: reads high → med → low because that is a legend's order, `AgeBand` reads
#: youngest → oldest because that is a scale's, and the two enums read as the
#: schema declares them. Sorting any of those alphabetically would give a
#: severity picker "High, Low, Medium", which is a list nobody can scan.
#:
#: The other six are free text or an id, where there is no declared order and the
#: honest one is the one a reader can verify from the control itself — ascending
#: by what is on screen. See `_ordered_values`.
_DECLARED_ORDER: Final[Mapping[str, tuple[str, ...]]] = {
    "severity_band": tuple(band.value for band in RiskBand),
    "age_group": tuple(band.value for band in AgeBand),
    "disability": tuple(member.value for member in Disability),
    "gender": tuple(member.value for member in Gender),
}


def _ordered_values(key: str, found: Mapping[str, str | None]) -> tuple[DimensionValue, ...]:
    """One dimension's values, in the order a picker draws them.

    Two rules, and which one applies is `_DECLARED_ORDER`'s answer rather than a
    branch on the value's type: a vocabulary keeps its declaration order and
    everything else sorts ascending by the string a reader sees.

    The tie-break on the sorted branch is the **value** behind the label, which
    matters for exactly one dimension and is not cosmetic there: two employers
    may share a `short_name`, and an order decided only by the label would leave
    the two options in whatever order the fold happened to see them — a picker
    that renders differently on two consecutive requests, from one book, with
    nothing on screen to say why.

    A value the vocabulary does not contain is **kept**, at the end, sorted. It
    cannot happen while the column is a native enum, and dropping it silently is
    the wrong failure for the day one of these dimensions becomes free text: an
    option missing from a picker is a claim population the analyst cannot reach
    and cannot see that they cannot reach.
    """
    declared = _DECLARED_ORDER.get(key)
    if declared is None:
        return tuple(
            DimensionValue(value=value, label=label)
            for value, label in sorted(
                found.items(), key=lambda entry: (entry[1] or entry[0], entry[0])
            )
        )
    ranked = {value: index for index, value in enumerate(declared)}
    return tuple(
        DimensionValue(value=value, label=found[value])
        for value in sorted(found, key=lambda value: (ranked.get(value, len(ranked)), value))
    )


def values_of(
    caseload: Sequence[LabelledClaim],
    filters: Segmentation,
    computers: Computers,
    thresholds: DerivationThresholds,
) -> SegmentationValues:
    """What the caller's book can be segmented by. Pure — no session, no clock.

    `fraud.panel_of`'s split and its reasons: the arithmetic is database-free and
    constructible by hand, and the one scoped read happens in the async wrapper.

    **One pass**, accumulating one set per dimension and the matched count
    together. Ten comprehensions over the same list would produce the same values
    today and is the wrong shape — `panel_of`'s argument: "these options describe
    one set of claims" becomes structural here, where ten passes would be ten
    opportunities for one of them to be written against a filtered copy, which on
    this surface would mean a picker offering options from a population the
    figures beside it do not describe.

    **The values are over the whole scoped book and the count is over the filtered
    one**, in one traversal, and the asymmetry is the design rather than an
    oversight — see `SegmentationValues`. A picker narrowed by the active filter
    could not widen it.

    The two bands are computed through the registry (AD-10) exactly as the
    predicates compute them, so a band offered by the picker is a band some claim
    is actually in.
    """
    found: dict[str, dict[str, str | None]] = {key: {} for key in SEGMENTATION_KEYS}
    matching = 0

    for claim in caseload:
        bands = bands_of(claim, computers)
        if matches(claim, bands, filters):
            matching += 1
        found["severity_band"][bands.severity_band.value] = None
        found["age_group"][bands.age_group.value] = None
        found["injury_type"][claim.injury_type] = None
        found["state"][claim.state] = None
        found["disability"][claim.disability.value] = None
        found["sector"][claim.sector] = None
        found["region"][claim.region] = None
        found["icd10"][claim.icd] = None
        found["gender"][claim.gender.value] = None
        # The one labelled dimension. Last label wins and they are all the same:
        # `employer_label` is a column on the joined `employer` row, so every
        # claim of one employer carries the identical string — `panel_of`'s note
        # on the handler name, and recorded rather than guarded because a guard
        # here would be asserting a foreign key.
        found["employer_id"][str(claim.employer_id)] = claim.employer_label

    return SegmentationValues(
        dimensions=tuple(
            DimensionValues(
                key=SEGMENTATION_WIRE_KEYS[key], values=_ordered_values(key, found[key])
            )
            for key in SEGMENTATION_KEYS
        ),
        applied_filters=applied_of(filters, caseload),
        claims_in_scope=len(caseload),
        claims_matching=matching,
        # Read off the threshold block rather than off the derivation, and this
        # is the one place in the package that does. `PortfolioCharts`' rule —
        # publish the edges the *derivation* decided with — exists so a legend
        # provably quotes the rule its segments were produced at, and it applies
        # where the edges and the figures are on one payload. Here they are not:
        # nothing on this payload was banded *at* these three numbers, they are
        # the material the browser composes an age range from, and
        # `AgeBandDerivation` is built from exactly these fields one line away in
        # `Computers.of`.
        age_younger_min=thresholds.age_younger_min,
        age_older_min=thresholds.age_older_min,
        age_oldest_min=thresholds.age_oldest_min,
    )


def to_drill_filters(filters: Segmentation) -> DrillFilters:
    """This segmentation as the drill list's own filter set.

    **A field-by-field build rather than `DrillFilters(**asdict(filters))`**, for
    `api/routers/dashboard.py`'s reason on the route's own construction: the
    mapping between the two vocabularies is the place a renamed dimension should
    fail to compile, and a keyword-splat is the place it silently would not.
    Ten lines, and each of them is a line a rename breaks.

    It is what makes "one vocabulary" executable rather than merely asserted: the
    import-time checks say the *names* agree, and this function is what a test
    calls to say that a segmentation and the drill-through it opens select the
    same claims. Nothing on a request path calls it — the drill route declares
    all twenty-four facets itself, because the analyst's segmentation and the
    supervisor's chart click reach that list through the same query string.
    """
    return DrillFilters(
        severity_band=filters.severity_band,
        injury_type=filters.injury_type,
        state=filters.state,
        employer_id=filters.employer_id,
        disability=filters.disability,
        sector=filters.sector,
        region=filters.region,
        icd10=filters.icd10,
        age_group=filters.age_group,
        gender=filters.gender,
    )


__all__ = [
    "SEGMENTATION_KEYS",
    "SEGMENTATION_WIRE_KEYS",
    "Computers",
    "DimensionValue",
    "DimensionValues",
    "LabelledClaim",
    "SegmentedBands",
    "SegmentedClaim",
    "Segmentation",
    "SegmentationValues",
    "VocabularyDivergence",
    "applied_of",
    "bands_of",
    "matches",
    "narrowed",
    "to_drill_filters",
    "values_of",
]
