"""`claim_path` — which statutory handling path a claim is on (Story 2.5).

**This is the derivation that closes the prototype's biggest silent gap.**
`pathDocsHTML` reads `c.path || "B"` against a dataset in which *no claim
carries a `path` field at all* (verified: zero occurrences of `"path":` across
the 100-claim array), so every claim in the console — a first-aid-only hand
laceration and a permanently disabling amputation alike — renders the same
four Path B forms. The banner says "Path B — Follow-Up Treatment" with total
confidence, and it has never once classified anything.

Here the path is computed, from the claim, against parameters in a versioned
rule document (AD-8), through exactly one registered function (AD-10) — so the
forms card, and any later surface that needs the path (Epic 3's action
worklist), call this and cannot drift from each other.

## Not a column

`claim.path` does not exist and this story does not add it. Story 1.2 banned
derived columns and the ban is right here: the classification is a function of
`severity_score`, `disability`, `surgery_required`, `recovery` and
`return_status`, all five of which Story 2.3 and 2.4 made editable. A stored
path would be stale the moment a handler corrected a severity score, and
nothing would say so.

## The fatality branch, and why its cut-off sits at the top of the domain

**The seeded schema carries no fatality indicator of any kind.** There is no
`fatality` flag, no `deceased` outcome, no death-benefit status; `ReturnStatus`
offers `under_treatment`, `returned_and_under_therapy` and
`returned_and_fully_recovered`, none of which means the worker died. So Path C
has to be recognised from severity and outcome — which the story permits — and
the cut-off is the whole question.

It is deliberately set at the top of `severity_score`'s domain, and the reason
is concrete rather than cautious. The seeded book's seven `permanent` claims
are *all* amputations, scoring 75-98, and several of them **returned to work
fully recovered**. Any cut-off low enough to make Path C fire against this
dataset would file a living amputation survivor as a death-benefit claim and
show their handler the AFF-1 (dependants' claim), C-64 (proof of death) and
C-65 (burial expenses). That is not a cosmetic misclassification; it is the
console telling a handler that an injured worker is dead.

So the parameters state the strongest thing the schema *can* say — a
maximum-severity, permanently disabling injury the worker has not returned
from — and no seeded claim meets it. Path C is therefore exercised by test
fixtures rather than by the dev seed, which the story anticipated. Replacing
this proxy with a real fatality indicator is recorded as deferred work; it is
an NFR-4 question (the same one that governs the placeholder form URLs), not
something a dev-time guess should close.

## One rule element, one tier

AD-8's split, exactly: the JDM document owns *the numbers and the qualifying
window set* (`pathMinorSeverityMax`, `pathMinorRecoveryWindows`,
`pathFatalitySeverityMin`); this module owns *the branching*. No cut-off is
written here, and no `if` is written in the document.
"""

from dataclasses import dataclass

from data.models.core import Claim
from data.models.enums import ClaimPath, Disability, RecoveryWindow, ReturnStatus
from services.derivations.registry import Derivation, register


@dataclass(frozen=True)
class ClaimPathDerivation:
    """Classifies a claim onto path A (minor), B (follow-up) or C (fatality).

    Ordered most-severe first, which is the ordering the parameter block's
    `pathMinorSeverityMax <= pathFatalitySeverityMin` check is there to make
    unambiguous: with a coherent pair the two conditions cannot both hold, and
    with an incoherent one the document is refused before a claim is seen.

    B is the fallback rather than a condition of its own, exactly as the
    prototype's own default is — "full WC claim lifecycle" is what a claim is
    when it is neither trivial nor fatal, and writing a positive test for it
    would be a third rule that has to be kept in step with the other two.
    """

    minor_severity_max: int
    minor_recovery_windows: frozenset[RecoveryWindow]
    fatality_severity_min: int

    def of(
        self,
        *,
        severity_score: int,
        disability: Disability,
        recovery: RecoveryWindow,
        surgery_required: bool,
        return_status: ReturnStatus,
    ) -> ClaimPath:
        """The claim's path.

        Keyword-only, and five scalars rather than a `Claim`: the caller that
        has a row uses `of_claim` below, and a test that wants the boundary
        between two paths should not have to build an ORM object with thirty
        other columns to ask about five.
        """
        if (
            severity_score >= self.fatality_severity_min
            and disability is Disability.permanent
            # The worker has not come back. Neither "returned" status can
            # describe a fatality, and requiring the negative rather than
            # naming `under_treatment` positively means a fourth outcome added
            # later (a real `deceased` member) does not silently fall out of
            # this branch on the day it is introduced.
            and return_status is ReturnStatus.under_treatment
        ):
            return ClaimPath.c
        if (
            severity_score < self.minor_severity_max
            # First aid does not involve an operating theatre, and a permanent
            # disability is not a minor injury however low the score.
            and not surgery_required
            and disability is Disability.temporary
            # "No lost time", as far as this schema can express it: the
            # recovery window the carrier recorded is one the document counts
            # as short enough. The set is a parameter because which windows
            # those are is a policy question, not a fact about the code.
            and recovery in self.minor_recovery_windows
        ):
            return ClaimPath.a
        return ClaimPath.b

    def of_claim(self, claim: Claim) -> ClaimPath:
        """`of`, for a caller that already holds the row."""
        return self.of(
            severity_score=claim.severity_score,
            disability=claim.disability,
            recovery=claim.recovery,
            surgery_required=claim.surgery_required,
            return_status=claim.return_status,
        )


claim_path = register(
    Derivation(
        name="claim_path",
        describes="statutory handling path of a claim (a minor / b follow-up / c fatality)",
        build=lambda thresholds: ClaimPathDerivation(
            minor_severity_max=thresholds.path_minor_severity_max,
            minor_recovery_windows=thresholds.path_minor_recovery_windows,
            fatality_severity_min=thresholds.path_fatality_severity_min,
        ),
    )
)
