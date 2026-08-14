"""`indemnity_type` — TTD / TPD / PPD / PTD for one claim (Story 3.1, AC 3).

Named for the *rule* rather than for the value it exports, beside
`path_classification`: a module that re-exported its own name would rebind
the package attribute, so `services.derivations.indemnity_type.IndemnityType`
would raise `AttributeError` with no obvious cause. The registry's docstring
states the convention and `tests/test_derivations.py` enforces it.

Which kind of indemnity a claim pays is a function of two columns a handler
can edit (`disability`, `return_status`) and one they can set outright
(`severity_score`), so it is derived per request and is **never a column**
(AD-10, and Story 1.2's ban on derived columns). A stored type would be stale
the moment somebody corrected a severity score, with nothing saying so.

## The PTD test lives here, and that is the whole point of the placement

`ptdSeverityThreshold` is in `derivation_thresholds` rather than in
`benefit_params` because this is a *registered* derivation and
`DerivationThresholds` is the single argument every registered derivation is
built from. The consequence is the part worth reading: **the "is this claim
permanently totally disabled?" question is asked exactly once**, of this
function, and `services/financials.compute_benefit` consumes the answer — it
pays `ptdCompRateBp` when `indemnity_type` is `ptd` and `defaultCompRateBp`
otherwise, rather than comparing a score against a threshold of its own.

The prototype does the opposite: `computeBenefit` computes `isPTD` inline and
then uses it twice, for the rate and for the label. Two readers of one
condition is one readers too many the day somebody changes only one of them —
and the condition here decides both what a worker is paid and what their claim
is called.

## What the four types mean, and where each branch comes from

- **PTD** — permanent *total* disability: permanently disabled at or above the
  threshold. Paid at full wage.
- **PPD** — permanent *partial*: permanently disabled below it.
- **TPD** — temporary *partial*: the worker is back at work on modified or
  therapeutic duty, so they are earning something and the benefit tops it up.
- **TTD** — temporary *total*: the default for a temporarily disabled worker.

The temporary split is `return_status is returned_and_under_therapy`, which is
the prototype's ``(c.returnStatus||"").includes("Therapy")`` written as an
equality against a token rather than a substring scan over a display string.
It is **not** a rules-tier parameter, unlike the path classification's
qualifying recovery windows: "has this worker returned to modified duty?" is a
question the vocabulary answers, not a policy an operator retunes — there is
exactly one `ReturnStatus` member that describes a partial return, and moving
it would be a change to what the words mean.

**A worker who has returned fully recovered is still TTD, not TPD**, which
reads oddly and is the prototype's behaviour ported exactly. It is defensible:
`disability` is what the *claim* pays on, and a temporary total disability that
has since resolved was still a total disability while it lasted. It is also the
kind of thing a product owner should confirm, so it is recorded rather than
quietly "fixed" — see the story's Dev Agent Record.
"""

from dataclasses import dataclass
from enum import StrEnum

from data.models.core import Claim
from data.models.enums import Disability, ReturnStatus
from services.derivations.registry import Derivation, register


class IndemnityType(StrEnum):
    """Snake/lowercase values per the enum convention — the UI owns labels.

    Here rather than in `data/models/enums.py` for `RiskBand`'s reason: no
    column holds one. `BodyRegion` and `ClaimPath` moved to the data layer
    only because `additional_injury.body_key` and `path_required_form.path`
    are native enum columns; nothing stores an indemnity type, and AD-10 says
    nothing may.

    The prototype's strings are the label and the abbreviation glued together
    (`"TTD — Temporary Total Disability"`) and it recovers the short form with
    `.split(" — ")[0]`. Both halves are display text, so both live in the
    browser (`web/src/features/claim-detail/labels.ts`); the wire carries the
    token. The one exception is the reserve-rationale sentence, which is prose
    the *server* writes and therefore spells the abbreviation itself.
    """

    ttd = "ttd"
    tpd = "tpd"
    ppd = "ppd"
    ptd = "ptd"


@dataclass(frozen=True)
class IndemnityTypeDerivation:
    """Classifies a claim as `ttd`, `tpd`, `ppd` or `ptd`.

    Permanent first, because the permanent/temporary split is the primary
    one: a permanently disabled worker's return status says nothing about
    which benefit they are on.
    """

    ptd_severity_threshold: int

    def of(
        self,
        *,
        disability: Disability,
        severity_score: int,
        return_status: ReturnStatus,
    ) -> IndemnityType:
        """The claim's indemnity type.

        Keyword-only, and three scalars rather than a `Claim`: the caller that
        has a row uses `of_claim` below, and a test that wants the boundary
        between two types should not have to build an ORM object with forty
        other columns to ask about three.
        """
        if disability is Disability.permanent:
            if severity_score >= self.ptd_severity_threshold:
                return IndemnityType.ptd
            return IndemnityType.ppd
        if return_status is ReturnStatus.returned_and_under_therapy:
            return IndemnityType.tpd
        return IndemnityType.ttd

    def of_claim(self, claim: Claim) -> IndemnityType:
        """`of`, for a caller that already holds the row."""
        return self.of(
            disability=claim.disability,
            severity_score=claim.severity_score,
            return_status=claim.return_status,
        )


indemnity_type = register(
    Derivation(
        name="indemnity_type",
        describes="the indemnity a claim pays (ttd / tpd temporary, ppd / ptd permanent)",
        build=lambda thresholds: IndemnityTypeDerivation(
            ptd_severity_threshold=thresholds.ptd_severity_threshold,
        ),
    )
)
