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
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

# Submodule names rather than `from rules import engine`: the package
# `__init__` re-exports both modules, so binding through it would make this
# import order-sensitive the first time someone rearranged those lines.
from rules.engine import LoadedDocument, evaluate, load

DERIVATION_THRESHOLDS_KEY = "derivation_thresholds"
PRIORITY_WEIGHTS_KEY = "priority_weights"


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
    return float(value)


def _integer(document: LoadedDocument, result: dict[str, Any], key: str) -> int:
    value = _number(document, result, key)
    if value != int(value):
        raise RuleParameterError(
            f"{document.key} v{document.version} gives {key!r} as {value}, "
            "which is not a whole number"
        )
    return int(value)


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

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "DerivationThresholds":
        return cls(
            version=document.version,
            risk_high_min=_integer(document, result, "riskHighMin"),
            risk_med_min=_integer(document, result, "riskMedMin"),
            siu_fraud_score_min=_integer(document, result, "siuFraudScoreMin"),
            rtw_blocked_hash_modulus=_integer(document, result, "rtwBlockedHashModulus"),
            payment_due_hash_modulus=_integer(document, result, "paymentDueHashModulus"),
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
    """

    version: int
    litigation: float
    siu_review: float
    rtw_blocked: float
    pending_approval: float
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

    @classmethod
    def of(cls, document: LoadedDocument, result: dict[str, Any]) -> "PriorityWeights":
        return cls(
            version=document.version,
            litigation=_number(document, result, "litigation"),
            siu_review=_number(document, result, "siuReview"),
            rtw_blocked=_number(document, result, "rtwBlocked"),
            pending_approval=_number(document, result, "pendingApproval"),
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


async def weights_for(db: AsyncSession, as_of: date | None = None) -> PriorityWeights:
    """Load and validate the priority weights effective on `as_of`."""
    document = await load(db, PRIORITY_WEIGHTS_KEY, as_of)
    return PriorityWeights.of(document, evaluate(document))
