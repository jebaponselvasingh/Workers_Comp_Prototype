"""The typed edge of the rules tier: JSON in, frozen dataclasses out.

AD-8 gives JDM the parameters and Python the formulas. Between the two sits
exactly one translation, and it is here — so that no consumer ever indexes a
raw dict. That matters more than it looks: a scorer written against
`weights["litigation"]` fails with a `KeyError` at request time when a
document is edited, in the middle of a hundred-row loop, naming a string
rather than a rule. Validating once at the boundary turns the same mistake
into one message that names the document, the version and the missing
parameter, before a single claim is scored.

The blocks are frozen dataclasses rather than Pydantic models on purpose:
they are internal values passed between services, never a request or
response body, and `services/derivations` builds its computers from one of
them on every call. Freezing them is what makes a `Derivation` safe to build
once and call many times.

**Ordering and range checks live on the block, not in `Settings`.** Story
1.4 refused an inverted risk band at process start; the same refusal now
happens when the document is read, which is the moment the numbers actually
arrive. Nothing else moved: the check is the same check, in the tier that
owns the numbers.

**What "validated" has to mean here.** A rule document is data an operator
edits, so every reader below refuses three separate things: a parameter of
the wrong *type* (`_number`, `_integer`, `_status_set`), a parameter outside
the *range* its consumer can use (`__post_init__`), and — for the enum-valued
one — a member that names nothing. Each refusal names the document, its
version and the offending value, because a rules migration goes out without
a code review of the tier that consumes it, and "riskHighMin must be between
0 and 100" is the difference between a five-minute fix and a portfolio that
silently bands every claim `low`. This is the first JDM document; the shape
of these checks is the precedent the later ones copy.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.enums import ActionKey, ActionUrgency, ClaimStatus, DocType, RecoveryWindow

# Submodule names rather than `from rules import engine`: the package
# `__init__` re-exports both modules, so binding through it would make this
# import order-sensitive the first time someone rearranged those lines.
from rules.engine import LoadedDocument, evaluate, load

DERIVATION_THRESHOLDS_KEY = "derivation_thresholds"
PRIORITY_WEIGHTS_KEY = "priority_weights"
INTAKE_REQUIRED_DOCUMENTS_KEY = "intake_required_documents"
INJURY_CAPTURE_KEY = "injury_capture"
BENEFIT_PARAMS_KEY = "benefit_params"
RESERVE_BANDS_KEY = "reserve_bands"
WORKLIST_ACTIONS_KEY = "worklist_actions"


class RuleParameterError(ValueError):
    """A rule document is missing a parameter, or holds an unusable one."""


def _number(document: LoadedDocument, result: dict[str, Any], key: str) -> float:
    value = result.get(key)
    # `bool` before `int`: `True` is an `int` in Python, and a document that
    # answered `true` for a weight would otherwise score as 1 rather than
    # being refused.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleParameterError(
            f"{document.key} v{document.version} has no numeric {key!r} "
            f"(got {value!r}) — the document and its parameter block disagree"
        )
    # NaN and ±Infinity are JSON that `json.loads` accepts and arithmetic
    # does not: `int(inf)` is an `OverflowError`, not a `ValueError`, so
    # without this the failure would surface as a 500 from whichever caller
    # happened to convert it rather than as the typed error every other
    # malformed parameter produces here.
    if not math.isfinite(value):
        raise RuleParameterError(
            f"{document.key} v{document.version} gives {key!r} as {value!r}, "
            "which is not a finite number"
        )
    return float(value)


def _integer(document: LoadedDocument, result: dict[str, Any], key: str) -> int:
    value = _number(document, result, key)
    if value != int(value):
        raise RuleParameterError(
            f"{document.key} v{document.version} gives {key!r} as {value}, "
            "which is not a whole number"
        )
    return int(value)


def _status_set(
    document: LoadedDocument, result: dict[str, Any], key: str
) -> frozenset[ClaimStatus]:
    """A document-authored list of `ClaimStatus` values, checked member by member.

    The first enum-valued parameter in the rules tier, so it sets the shape:
    a JSON array of the *wire* strings (`"ch_assessment_process"`, never the
    display text), read into a `frozenset` of the enum, with every member
    resolved here rather than compared as a string downstream.

    Why resolve rather than compare: a scorer written against raw strings
    treats a typo as "this status never matches" — a rule that silently
    stops firing, with the queue still sorting and every test that does not
    happen to use that status still green. Resolving turns the same typo
    into one refusal, before a claim is scored, naming the value.

    An empty list is accepted: "no status counts as pending approval" is a
    legitimate way to switch the term off from the document, which is the
    whole point of the parameter being here. A duplicate is accepted too —
    it is a set, and a document repeating a member said nothing new.
    """
    value = result.get(key)
    if not isinstance(value, list):
        raise RuleParameterError(
            f"{document.key} v{document.version} has no list {key!r} "
            f"(got {value!r}) — the document and its parameter block disagree"
        )
    statuses: set[ClaimStatus] = set()
    for member in value:
        try:
            statuses.add(ClaimStatus(member))
        except ValueError as exc:
            raise RuleParameterError(
                f"{document.key} v{document.version} lists {member!r} in {key!r}, "
                f"which is not a claim status; the statuses are "
                f"{sorted(status.value for status in ClaimStatus)}"
            ) from exc
    return frozenset(statuses)


def _recovery_window_set(
    document: LoadedDocument, result: dict[str, Any], key: str
) -> frozenset[RecoveryWindow]:
    """A document-authored list of `RecoveryWindow` values, member by member.

    `_status_set`'s argument, over a different vocabulary — resolve rather
    than compare, so a typo is one refusal before a claim is classified
    instead of a condition that quietly stops matching and leaves every claim
    on Path B (which is precisely the prototype's bug this story exists to
    close).

    A **set**, not `_doc_type_list`'s ordered tuple, because nothing renders
    these: they are a membership test inside `claim_path`. So order says
    nothing and a duplicate says nothing new, which is exactly when a set is
    the honest type. An empty list is accepted for `_status_set`'s reason —
    "no recovery window is short enough to count as no lost time" switches
    Path A off from the document, which is a legitimate thing to want and the
    reason the condition is data at all.
    """
    value = result.get(key)
    if not isinstance(value, list):
        raise RuleParameterError(
            f"{document.key} v{document.version} has no list {key!r} "
            f"(got {value!r}) — the document and its parameter block disagree"
        )
    windows: set[RecoveryWindow] = set()
    for member in value:
        try:
            windows.add(RecoveryWindow(member))
        except ValueError as exc:
            raise RuleParameterError(
                f"{document.key} v{document.version} lists {member!r} in {key!r}, "
                f"which is not a recovery window; the windows are "
                f"{sorted(window.value for window in RecoveryWindow)}"
            ) from exc
    return frozenset(windows)


@dataclass(frozen=True)
class DerivationThresholds:
    """Every parameter the AD-10 derivation registry reads.

    One block for the whole registry rather than one per derivation: the
    registry builds *all* its computers from a single argument (`build`
    takes this type), which is what lets a consumer load parameters once and
    then derive freely, and what stops a second derivation from quietly
    growing a second source for the same number.

    `version` is carried for `PriorityWeights`' reason, which turned out to
    apply here too: these thresholds decide `rtw_blocked`, `siu_review` and
    the risk band, all three of which are *scoring inputs*. Change
    `riskHighMin` and the queue re-ranks. So the version of this document is
    as much a part of "which rules produced this ordering?" as the weights'
    is, and the queue cursor records both.
    """

    version: int
    risk_high_min: int
    risk_med_min: int
    siu_fraud_score_min: int
    rtw_blocked_hash_modulus: int
    payment_due_hash_modulus: int
    # Story 2.2's four (document v2). The two ratios band `days_open /
    # expected_days`; the two day constants are the expected window when the
    # recovery text names a year or names nothing this rule can read.
    treatment_early_max_ratio: float
    treatment_active_max_ratio: float
    recovery_year_expected_days: int
    recovery_default_expected_days: int
    # Story 2.5's three (document v3). `claim_path` is a registered derivation,
    # so its parameters belong in this block rather than in a document of their
    # own — see `derivation_thresholds.v3.jdm.json` and 0019 for why that cuts
    # the other way from `injury_capture` and `intake_required_documents`.
    path_minor_severity_max: int
    path_minor_recovery_windows: frozenset[RecoveryWindow]
    path_fatality_severity_min: int
    # Story 3.1's one (document v4). `indemnity_type` is a registered
    # derivation, so its cut-off belongs in this block rather than in
    # `benefit_params` — and putting it here is what gives the PTD test a
    # single reader: `services/financials` asks the derivation whether the
    # claim is permanently *totally* disabled and pays the matching rate,
    # instead of comparing a score against a threshold of its own.
    ptd_severity_threshold: int
    # Story 5.1's one (document v5). The dashboard's Fraud Flags card counts a
    # *wider* population than the queue's SIU chip does, so it gets its own
    # cut-off beside `siu_fraud_score_min` rather than borrowing it — see
    # `queue_flags.FraudFlaggedDerivation` for why collapsing the two would
    # silently show the referral count on a card captioned for the review one.
    # Two parameters in one block, adjacent, is the arrangement that makes the
    # difference legible to whoever reads either of them next.
    fraud_flag_score_min: int

    def __post_init__(self) -> None:
        # Story 1.4's `Field(ge=0, le=100)`, in its new home. `severity_score`
        # is a 0–100 column, so a cut-off outside that range bands the whole
        # portfolio into one band and says nothing about it: `riskHighMin:
        # 1000` makes every claim `low`, silently, with the queue still
        # sorting and the top bar still counting.
        for name, bound in (
            ("riskHighMin", self.risk_high_min),
            ("riskMedMin", self.risk_med_min),
        ):
            if not 0 <= bound <= 100:
                raise RuleParameterError(
                    f"{name} must be between 0 and 100 (severity_score's range), got {bound}"
                )
        # The same argument for `fraud_score`, which is also a 0–100 column:
        # `siuFraudScoreMin: 200` refers no claim at all for SIU review, and
        # a queue where the SIU filter matches nothing and the `siuReview`
        # term never fires looks exactly like a quiet portfolio.
        if not 0 <= self.siu_fraud_score_min <= 100:
            raise RuleParameterError(
                "siuFraudScoreMin must be between 0 and 100 (fraud_score's range), "
                f"got {self.siu_fraud_score_min}"
            )
        # The same check for the dashboard's cut-off, and the same failure it
        # catches one screen over: `fraudFlagScoreMin: 200` empties the Fraud
        # Flags card and empties Story 5.4's worklist of its fraud population,
        # neither of which raises anything — a portfolio with no suspected
        # fraud and a portfolio nobody is checking look identical.
        #
        # Deliberately **not** validated against `siu_fraud_score_min`. The two
        # are separate rules, not a band pair: the review population is wider
        # than the referral population today, but an operator who decided to
        # refer everything they review would be stating a policy, not inverting
        # an ordering, and refusing it here would be this tier legislating one.
        if not 0 <= self.fraud_flag_score_min <= 100:
            raise RuleParameterError(
                "fraudFlagScoreMin must be between 0 and 100 (fraud_score's range), "
                f"got {self.fraud_flag_score_min}"
            )
        # Story 1.4's `_bands_must_not_overlap`, in its new home. An
        # inverted pair puts scores in two bands at once and the derivation
        # reads them in order, so it would silently answer "high" for
        # everything above `med_min`.
        if self.risk_med_min > self.risk_high_min:
            raise RuleParameterError(
                f"riskMedMin ({self.risk_med_min}) must not exceed "
                f"riskHighMin ({self.risk_high_min})"
            )
        # A zero modulus is a ZeroDivisionError a hundred rows into a queue
        # request; a negative one silently inverts the demo bucket.
        for name, modulus in (
            ("rtwBlockedHashModulus", self.rtw_blocked_hash_modulus),
            ("paymentDueHashModulus", self.payment_due_hash_modulus),
        ):
            if modulus < 1:
                raise RuleParameterError(f"{name} must be at least 1, got {modulus}")
        # The treatment phase bands a ratio of days elapsed to days expected.
        # A boundary outside 0–1 is not a tuning choice, it is a phase that
        # can never be reached: `treatmentEarlyMaxRatio: 2` leaves every claim
        # "early" for twice its own recovery window, and a negative one skips
        # the phase entirely — both silently, with a banner still rendering.
        for name, ratio in (
            ("treatmentEarlyMaxRatio", self.treatment_early_max_ratio),
            ("treatmentActiveMaxRatio", self.treatment_active_max_ratio),
        ):
            if not 0 <= ratio <= 1:
                raise RuleParameterError(f"{name} must be between 0 and 1, got {ratio}")
        # Inverted boundaries collapse the middle phase without an error, the
        # same failure `riskMedMin > riskHighMin` is refused for: the
        # derivation reads them in order, so `active` would never be reached.
        if self.treatment_early_max_ratio > self.treatment_active_max_ratio:
            raise RuleParameterError(
                f"treatmentEarlyMaxRatio ({self.treatment_early_max_ratio}) must not exceed "
                f"treatmentActiveMaxRatio ({self.treatment_active_max_ratio})"
            )
        # Both are divisors. Zero is a `ZeroDivisionError` on a request path;
        # negative inverts the ratio and reverses the phase order.
        for name, days in (
            ("recoveryYearExpectedDays", self.recovery_year_expected_days),
            ("recoveryDefaultExpectedDays", self.recovery_default_expected_days),
        ):
            if days < 1:
                raise RuleParameterError(f"{name} must be at least 1 day, got {days}")
        # Both path cut-offs read `severity_score`, a 0-100 column, so the same
        # argument the risk bands make applies: a bound outside that range
        # silently classifies the whole portfolio one way and says nothing
        # about it. `pathMinorSeverityMax: 1000` files every claim as
        # first-aid-only and shows a fatality claim the two Path A forms.
        for name, bound in (
            ("pathMinorSeverityMax", self.path_minor_severity_max),
            ("pathFatalitySeverityMin", self.path_fatality_severity_min),
        ):
            if not 0 <= bound <= 100:
                raise RuleParameterError(
                    f"{name} must be between 0 and 100 (severity_score's range), got {bound}"
                )
        # An inverted pair is not a tuning choice: it describes a claim that is
        # simultaneously minor enough for first-aid-only handling and severe
        # enough to be fatal. `claim_path` tests the fatality branch first, so
        # the overlap would resolve silently in favour of death benefits — a
        # minor-injury claim rendering the death-benefit filing set.
        if self.path_minor_severity_max > self.path_fatality_severity_min:
            raise RuleParameterError(
                f"pathMinorSeverityMax ({self.path_minor_severity_max}) must not exceed "
                f"pathFatalitySeverityMin ({self.path_fatality_severity_min})"
            )
        # `severity_score`'s range again, for the same reason the risk bands
        # and the path cut-offs are checked against it: a threshold outside the
        # column's domain classifies the whole portfolio one way and says
        # nothing about it. `ptdSeverityThreshold: 200` files every permanently
        # disabled worker as *partially* disabled and pays two thirds of wage
        # where the statute pays all of it — silently, with the benefit card
        # still rendering a confident figure.
        if not 0 <= self.ptd_severity_threshold <= 100:
            raise RuleParameterError(
                "ptdSeverityThreshold must be between 0 and 100 (severity_score's range), "
                f"got {self.ptd_severity_threshold}"
            )

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "DerivationThresholds":
        return cls(
            version=document.version,
            risk_high_min=_integer(document, result, "riskHighMin"),
            risk_med_min=_integer(document, result, "riskMedMin"),
            siu_fraud_score_min=_integer(document, result, "siuFraudScoreMin"),
            rtw_blocked_hash_modulus=_integer(document, result, "rtwBlockedHashModulus"),
            payment_due_hash_modulus=_integer(document, result, "paymentDueHashModulus"),
            treatment_early_max_ratio=_number(document, result, "treatmentEarlyMaxRatio"),
            treatment_active_max_ratio=_number(document, result, "treatmentActiveMaxRatio"),
            recovery_year_expected_days=_integer(document, result, "recoveryYearExpectedDays"),
            recovery_default_expected_days=_integer(
                document, result, "recoveryDefaultExpectedDays"
            ),
            path_minor_severity_max=_integer(document, result, "pathMinorSeverityMax"),
            path_minor_recovery_windows=_recovery_window_set(
                document, result, "pathMinorRecoveryWindows"
            ),
            path_fatality_severity_min=_integer(document, result, "pathFatalitySeverityMin"),
            ptd_severity_threshold=_integer(document, result, "ptdSeverityThreshold"),
            fraud_flag_score_min=_integer(document, result, "fraudFlagScoreMin"),
        )


@dataclass(frozen=True)
class PriorityWeights:
    """Every constant the queue's priority score is built from.

    `version` is a field here, as it is on `DerivationThresholds`, because
    the queue's cursor records both: an ordering is only meaningful relative
    to the documents that produced it, and a page-2 request cut against v1
    must not be served from a v2 ordering. It is the rules tier's
    contribution to "why is this claim first?".

    `page_limit` and the two marker parameters sit alongside the weights
    deliberately. They are not styling: how many claims a page holds and how
    many carry the priority marker are operational tuning of the same
    worklist rule, and AD-8 says a rule element lives in one tier. Putting
    them in `Settings` would split one rule across two.

    `pending_approval_statuses` is here for the same reason, and it is the
    one parameter that is not a number. The +25 weight and *which statuses
    earn it* are one rule element; leaving the set in Python while its
    weight sat in the document meant half the rule could be retuned by an
    operator and the other half needed a deploy. Both halves are now data.
    """

    version: int
    litigation: float
    siu_review: float
    rtw_blocked: float
    pending_approval: float
    pending_approval_statuses: frozenset[ClaimStatus]
    payment_due: float
    surgery: float
    severity_factor: float
    days_open_factor: float
    days_open_cap: int
    settled_penalty: float
    marker_threshold: float
    marker_count: int
    page_limit: int

    def __post_init__(self) -> None:
        if self.days_open_cap < 0:
            raise RuleParameterError(f"daysOpenCap must not be negative, got {self.days_open_cap}")
        if self.marker_count < 0:
            raise RuleParameterError(f"markerCount must not be negative, got {self.marker_count}")
        # A page limit of zero returns an empty page with a non-null cursor
        # for ever: the queue would render nothing and never stop asking.
        if self.page_limit < 1:
            raise RuleParameterError(f"pageLimit must be at least 1, got {self.page_limit}")
        # The two *factors* multiply a magnitude that only ever grows, so a
        # negative one inverts the ordering rather than merely re-weighting
        # it: the worst-injured claim would sink below the least, and the
        # queue would still look perfectly well sorted. Deliberately not a
        # blanket ban on negative parameters — `settledPenalty` is negative
        # by design (it is added, not subtracted, so the document owns the
        # sign), and refusing every negative would forbid the one weight
        # that has to be one.
        for name, factor in (
            ("severityFactor", self.severity_factor),
            ("daysOpenFactor", self.days_open_factor),
        ):
            if factor < 0:
                raise RuleParameterError(
                    f"{name} must not be negative ({factor}) — a negative factor inverts "
                    "the ordering it is meant to weight"
                )

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "PriorityWeights":
        return cls(
            version=document.version,
            litigation=_number(document, result, "litigation"),
            siu_review=_number(document, result, "siuReview"),
            rtw_blocked=_number(document, result, "rtwBlocked"),
            pending_approval=_number(document, result, "pendingApproval"),
            pending_approval_statuses=_status_set(document, result, "pendingApprovalStatuses"),
            payment_due=_number(document, result, "paymentDue"),
            surgery=_number(document, result, "surgery"),
            severity_factor=_number(document, result, "severityFactor"),
            days_open_factor=_number(document, result, "daysOpenFactor"),
            days_open_cap=_integer(document, result, "daysOpenCap"),
            settled_penalty=_number(document, result, "settledPenalty"),
            marker_threshold=_number(document, result, "markerThreshold"),
            marker_count=_integer(document, result, "markerCount"),
            page_limit=_integer(document, result, "pageLimit"),
        )


async def thresholds_for(db: AsyncSession, as_of: date | None = None) -> DerivationThresholds:
    """Load and validate the derivation thresholds effective on `as_of`."""
    document = await load(db, DERIVATION_THRESHOLDS_KEY, as_of)
    return DerivationThresholds.of(document, evaluate(document))


@dataclass(frozen=True)
class IntakeRequirements:
    """What an intake claim is checked for (Story 2.2).

    A block of its own, in a document of its own, because it is not a
    derivation parameter: `services/claims` owns the checklist, and
    `DerivationThresholds` is the single argument every registered derivation
    is *built* from. Folding a service's parameter into it would make the
    registry's contract mean "whatever anybody needed a number for".

    `required_doc_types` is an ordered tuple rather than a set: the checklist
    renders one row per required type, and the order it renders in is the
    document's, not a hash's.
    """

    version: int
    required_doc_types: tuple[DocType, ...]

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "IntakeRequirements":
        return cls(
            version=document.version,
            required_doc_types=_doc_type_list(document, result, "requiredDocTypes"),
        )


def _doc_type_list(
    document: LoadedDocument, result: dict[str, Any], key: str
) -> tuple[DocType, ...]:
    """A document-authored list of `DocType` values, resolved member by member.

    `_status_set`'s argument, with order preserved and duplicates refused
    rather than absorbed. A repeated member in a *set* said nothing new; a
    repeated member in a checklist is a row rendered twice, which is a
    document bug worth a message rather than a silent de-duplication.
    """
    value = result.get(key)
    if not isinstance(value, list):
        raise RuleParameterError(
            f"{document.key} v{document.version} has no list {key!r} "
            f"(got {value!r}) — the document and its parameter block disagree"
        )
    types: list[DocType] = []
    for member in value:
        try:
            doc_type = DocType(member)
        except ValueError as exc:
            raise RuleParameterError(
                f"{document.key} v{document.version} lists {member!r} in {key!r}, "
                f"which is not a document type; the types are "
                f"{sorted(item.value for item in DocType)}"
            ) from exc
        if doc_type in types:
            raise RuleParameterError(
                f"{document.key} v{document.version} lists {member!r} twice in {key!r}; "
                "a checklist row rendered twice is a document bug, not a stronger requirement"
            )
        types.append(doc_type)
    return tuple(types)


async def weights_for(db: AsyncSession, as_of: date | None = None) -> PriorityWeights:
    """Load and validate the priority weights effective on `as_of`."""
    document = await load(db, PRIORITY_WEIGHTS_KEY, as_of)
    return PriorityWeights.of(document, evaluate(document))


async def intake_requirements_for(
    db: AsyncSession, as_of: date | None = None
) -> IntakeRequirements:
    """Load and validate the intake document requirements effective on `as_of`."""
    document = await load(db, INTAKE_REQUIRED_DOCUMENTS_KEY, as_of)
    return IntakeRequirements.of(document, evaluate(document))


@dataclass(frozen=True)
class InjuryCapture:
    """What the add-injury form starts at (Story 2.4).

    A block of its own, in a document of its own, for `IntakeRequirements`'
    reason: `services/claims` owns the add-injury command, and
    `DerivationThresholds` is the single argument every registered derivation
    is *built* from. Folding a service's default into it would make the
    registry's contract mean "whatever anybody needed a number for".

    Served rather than hardcoded in the SPA because the alternative is a
    number in a React component that nobody can retune without a deploy —
    and one that would sit, unversioned, next to the severity bands that were
    deliberately moved out of `config.py` in Story 2.1.
    """

    version: int
    new_injury_default_severity: int

    def __post_init__(self) -> None:
        # `severity_score` is a 0-100 column with a CHECK constraint behind
        # it, so a default outside that range is not a tuning choice: it is a
        # form whose untouched submit is refused by the command it feeds.
        if not 0 <= self.new_injury_default_severity <= 100:
            raise RuleParameterError(
                "newInjuryDefaultSeverity must be between 0 and 100 (severity_score's "
                f"range), got {self.new_injury_default_severity}"
            )

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "InjuryCapture":
        return cls(
            version=document.version,
            new_injury_default_severity=_integer(document, result, "newInjuryDefaultSeverity"),
        )


async def injury_capture_for(db: AsyncSession, as_of: date | None = None) -> InjuryCapture:
    """Load and validate the injury-capture parameters effective on `as_of`."""
    document = await load(db, INJURY_CAPTURE_KEY, as_of)
    return InjuryCapture.of(document, evaluate(document))


#: The comp rate's domain, in basis points — 0% to 150%.
#:
#: **Restated here rather than imported**, and that is the pattern
#: `DerivationThresholds` already follows for `severity_score`'s 0-100: the
#: rules tier must not import from `services/`, so a block that validates a
#: parameter against its consumer's domain writes the bound out with a comment
#: naming where it comes from. The authoritative statement is
#: `services/financials/benefit.py`'s `COMP_RATE_MIN_BP` / `COMP_RATE_MAX_BP`,
#: which is what the override command refuses against;
#: `tests/test_rule_parameters.py` asserts the two agree, so the duplication
#: cannot drift silently.
_COMP_RATE_MIN_BP = 0
_COMP_RATE_MAX_BP = 15_000


@dataclass(frozen=True)
class BenefitParams:
    """What the statutory weekly indemnity benefit is computed from (3.1).

    A block of its own, in a document of its own, for `IntakeRequirements`'
    reason: `services/financials` owns the benefit formula, and
    `DerivationThresholds` is the single argument every registered derivation
    is *built* from. Folding a service's parameters into it would make the
    registry's contract mean "whatever anybody needed a number for".

    **The PTD *threshold* is deliberately not here.** Which indemnity type a
    claim is on is a registered derivation (AD-10), so its cut-off is
    `DerivationThresholds.ptd_severity_threshold` and this block never restates
    it — `compute_benefit` asks the derivation and pays `ptd_comp_rate_bp` when
    the answer is `ptd`. That is what makes AD-8's "each rule element in
    exactly one tier" a fact about the code here rather than a convention: the
    threshold has one reader, and the rate it selects has another.

    **Rates are basis points.** 6667 is 66.67%, and the unit is the one
    `claim.comp_rate_override_bp` stores — so the default and a handler's
    override are the same kind of integer and the whole calculation is integer
    arithmetic. A float default beside an integer override would make "is this
    claim overridden?" a comparison two values can fail by 1e-14.
    """

    version: int
    default_comp_rate_bp: int
    ptd_comp_rate_bp: int
    waiting_period_days: int

    def __post_init__(self) -> None:
        # A rate outside the domain a *handler* may set is not a tuning
        # choice: it is a default the console's own override input could not
        # reproduce, and it would render on the benefit card as a comp rate
        # nobody can correct back down. Both bounds inclusive, matching the
        # command's refusal exactly.
        for name, rate in (
            ("defaultCompRateBp", self.default_comp_rate_bp),
            ("ptdCompRateBp", self.ptd_comp_rate_bp),
        ):
            if not _COMP_RATE_MIN_BP <= rate <= _COMP_RATE_MAX_BP:
                raise RuleParameterError(
                    f"{name} must be between {_COMP_RATE_MIN_BP} and {_COMP_RATE_MAX_BP} "
                    f"basis points (0-150% of AWW), got {rate}"
                )
        # A negative wait is a first payment due before the injury. Zero is
        # accepted and is a real jurisdiction's rule — "no waiting period" is a
        # legitimate thing for this parameter to say, and refusing it would
        # make the document unable to describe one.
        if self.waiting_period_days < 0:
            raise RuleParameterError(
                f"waitingPeriodDays must not be negative, got {self.waiting_period_days}"
            )

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "BenefitParams":
        return cls(
            version=document.version,
            default_comp_rate_bp=_integer(document, result, "defaultCompRateBp"),
            ptd_comp_rate_bp=_integer(document, result, "ptdCompRateBp"),
            waiting_period_days=_integer(document, result, "waitingPeriodDays"),
        )


async def benefit_params_for(db: AsyncSession, as_of: date | None = None) -> BenefitParams:
    """Load and validate the benefit parameters effective on `as_of`."""
    document = await load(db, BENEFIT_PARAMS_KEY, as_of)
    return BenefitParams.of(document, evaluate(document))


@dataclass(frozen=True)
class ReserveBands:
    """The two thresholds the reserve adequacy verdict is banded on (3.2).

    A block of its own, in a document of its own, for `BenefitParams`' reason:
    `services/financials` owns the reserve check, and `DerivationThresholds` is
    the single argument every registered derivation is built from. The reserve
    check is a service *query* rather than a registered derivation — it reads
    two tables' worth of exposure, which is not what a derivation's `build`
    contract describes — so its bands belong beside the benefit's rather than
    inside the registry's argument.

    **Ratios are basis points.** 11 500 is 115%, 6 000 is 60%. The unit is the
    comp rate's, and the reason is `BenefitParams`' reason applied to a
    comparison rather than to a product: the story pins the boundary at exactly
    1.15 and exactly 0.60, and `services/financials/reserve.py` cross-multiplies
    integers so that "exactly" is a fact about the claim rather than about which
    pair of cent figures produced the ratio.
    """

    version: int
    light_ratio_bp: int
    heavy_ratio_bp: int

    def __post_init__(self) -> None:
        # A negative ratio bound is not a tuning choice: exposure and reserve
        # are both non-negative in every real claim, so a negative
        # `lightRatioBp` makes *every* claim light and a negative
        # `heavyRatioBp` makes none heavy — silently, with a confident verdict
        # chip still rendering on every card in the portfolio.
        for name, bound in (
            ("lightRatioBp", self.light_ratio_bp),
            ("heavyRatioBp", self.heavy_ratio_bp),
        ):
            if bound < 0:
                raise RuleParameterError(f"{name} must not be negative, got {bound}")
        # `riskMedMin > riskHighMin`'s refusal, over a different pair. The
        # verdict reads the two in order — light first — so an inverted pair
        # does not merely re-tune the bands, it makes `adequate` unreachable:
        # every claim above the heavy floor would already have been called
        # light. A claim can then never be told its reserve is right.
        if self.heavy_ratio_bp > self.light_ratio_bp:
            raise RuleParameterError(
                f"heavyRatioBp ({self.heavy_ratio_bp}) must not exceed "
                f"lightRatioBp ({self.light_ratio_bp})"
            )

    # **Deliberately no upper bound.** `_status_set` accepts an empty list
    # because "no status counts as pending approval" is a legitimate way to
    # switch a term off from the document; the same argument applies here. A
    # `lightRatioBp` of 1 000 000 says "nothing is ever under-reserved", which
    # is a strange policy but a policy, and it is the kind of thing an operator
    # retunes a rule document to try. An arbitrary cap would forbid it while
    # protecting against nothing — the failures worth refusing are the two
    # above, both of which make a band silently unreachable.

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "ReserveBands":
        return cls(
            version=document.version,
            light_ratio_bp=_integer(document, result, "lightRatioBp"),
            heavy_ratio_bp=_integer(document, result, "heavyRatioBp"),
        )


async def reserve_bands_for(db: AsyncSession, as_of: date | None = None) -> ReserveBands:
    """Load and validate the reserve adequacy bands effective on `as_of`."""
    document = await load(db, RESERVE_BANDS_KEY, as_of)
    return ReserveBands.of(document, evaluate(document))


def urgency_parameter_name(key: ActionKey) -> str:
    """The document key one rule's urgency is authored under — `urgencyBillReview`.

    **Derived from the member rather than written out as an eleven-entry map**,
    which is the one decision in this block worth arguing. A hand-written map
    is eleven chances for a rule and its parameter to be spelled differently,
    and the failure that produces is silent in the worst possible way: the
    document still loads, ten rules keep their tuning, and the eleventh falls
    back to whatever the missing-key path does. Deriving the name means a
    twelfth rule cannot be added without the document gaining a key, because
    `WorklistActions.of` will refuse the load until it does.

    Exported so `tests/test_rule_parameters.py` can assert the naming against
    the committed JSON rather than against this function's own output.
    """
    return "urgency" + "".join(part.capitalize() for part in key.value.split("_"))


def _urgencies(
    document: LoadedDocument, result: dict[str, Any]
) -> Mapping[ActionKey, ActionUrgency]:
    """One `ActionUrgency` per `ActionKey`, resolved member by member.

    `_status_set`'s argument over a mapping rather than a set: resolve here, so
    that a document saying `'urgent'` is one refusal naming the value and the
    document, rather than an action whose chip renders a token no stylesheet
    has a colour for. Every member is required — an action with no urgency has
    no rank, and a generator that defaulted one would put a rule's ordering
    back in Python, which is the tier AD-8 takes it out of.

    Returned read-only, because `WorklistActions` is frozen and a frozen
    dataclass holding a mutable dict is frozen about the wrong thing: the
    registry builds these blocks once per request and hands them to a pure
    generator, and a caller that could edit the mapping could retune a rule for
    every claim after it.
    """
    urgencies: dict[ActionKey, ActionUrgency] = {}
    for key in ActionKey:
        name = urgency_parameter_name(key)
        value: Any = result.get(name)
        try:
            # Not `ActionUrgency(value)` directly: a `str` subclass would be
            # accepted and anything else raises `ValueError` anyway, but mypy
            # cannot see that a `None` from `.get` is a refusal rather than a
            # crash. Narrowing here makes the missing-key case explicitly the
            # same refusal as the wrong-value case, which is what the message
            # already claims.
            if not isinstance(value, str):
                raise ValueError(value)
            urgencies[key] = ActionUrgency(value)
        except ValueError as exc:
            raise RuleParameterError(
                f"{document.key} v{document.version} gives {name!r} as {value!r}, "
                f"which is not an action urgency; the urgencies are "
                f"{[member.value for member in ActionUrgency]}"
            ) from exc
    return MappingProxyType(urgencies)


@dataclass(frozen=True)
class WorklistActions:
    """Every tunable of the action checklist (Story 3.5, AD-8).

    A block of its own, in a document of its own, for `ReserveBands`' reason:
    `services/worklist` owns the generator, and `DerivationThresholds` is the
    single argument every *registered derivation* is built from. An action
    checklist is a service query over a claim, its documents and its money
    rather than a banding of one column, so its knobs belong beside the reserve
    check's rather than inside the registry's argument.

    **What is here is only the tuning.** The eleven trigger *conditions* — what
    makes a bill reviewable, what makes a return-to-work date overdue — are
    typed Python in `services/worklist/actions.py`. That is AD-8's split, and
    the line it draws here is the same one `ReserveBands` draws between two
    thresholds in the document and the cross-multiplied comparison in the
    service.

    **`urgencies` is keyed by the enum, not by the document's string.** The
    resolution happens once, at load, so the generator ranks on members and
    `tests/test_action_checklist.py` can hand it a block it wrote by hand with
    no engine started and no database opened — the property `rules/engine.py`'s
    docstring says the whole once-per-request arrangement exists to preserve.
    """

    version: int
    cap: int
    padding_floor: int
    urgencies: Mapping[ActionKey, ActionUrgency]

    def __post_init__(self) -> None:
        # A cap of zero renders an empty card on every claim in the portfolio,
        # silently, with the heading still there — the same class of failure
        # `pageLimit: 0` is refused for. One is a strange policy and a policy:
        # "show the handler only their next action" is a thing an operator
        # might reasonably try, and it is exactly the tuning a parameter is for.
        if self.cap < 1:
            raise RuleParameterError(f"cap must be at least 1, got {self.cap}")
        # Zero is legitimate and means "never pad" — the card then shows only
        # what actually fired, which is a defensible reading of a worklist and
        # is how the padding rule is switched off from the document.
        if self.padding_floor < 0:
            raise RuleParameterError(f"paddingFloor must not be negative, got {self.padding_floor}")
        # `riskMedMin > riskHighMin`'s refusal over a different pair, and the
        # failure it prevents is not a silently unreachable band but a directly
        # contradictory instruction: pad up to eight rows, then cut to six. The
        # generator would have to pick one, and whichever it picked would make
        # the other parameter a lie.
        if self.padding_floor > self.cap:
            raise RuleParameterError(
                f"paddingFloor ({self.padding_floor}) must not exceed cap ({self.cap})"
            )

    def urgency_of(self, key: ActionKey) -> ActionUrgency:
        """This rule's urgency. Total by construction — see `_urgencies`."""
        return self.urgencies[key]

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "WorklistActions":
        return cls(
            version=document.version,
            cap=_integer(document, result, "cap"),
            padding_floor=_integer(document, result, "paddingFloor"),
            urgencies=_urgencies(document, result),
        )


async def worklist_actions_for(db: AsyncSession, as_of: date | None = None) -> WorklistActions:
    """Load and validate the action-checklist parameters effective on `as_of`."""
    document = await load(db, WORKLIST_ACTIONS_KEY, as_of)
    return WorklistActions.of(document, evaluate(document))
