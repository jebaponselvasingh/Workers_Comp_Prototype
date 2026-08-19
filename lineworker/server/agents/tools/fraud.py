"""`fraud_signals` — the claim's two stored fraud columns and the two registered
derivations over them, as a tool envelope.

**No fraud modelling happens here, and none happens anywhere in this build.**
`claim.fraud_score` and `claim.fraud_flag` are seeded columns; `siu_review` and
`fraud_flagged` are the two registered derivations that band them
(`services/derivations/queue_flags.py`). Real fraud scoring is a Deferred
architecture decision, so the insight *narrates* those four facts and invents
no third rule — which is also why this wrapper publishes the two thresholds
that produced the verdicts: an insight that says a claim is referable should be
able to say what it was referable *above*.

**The two derivations are two rules, not one rule read twice**, and this is the
one place a wrapper could plausibly collapse them. `siu_review` is the
*referral* threshold (`siu_fraud_score_min` — the queue's SIU chip) and
`fraud_flagged` is the wider *review* threshold (`fraud_flag_score_min` — the
dashboard's Fraud Flags card); on the seeded portfolio they are 9 claims and 13.
They fund different work, `FraudFlaggedDerivation`'s docstring argues the
distinction at length, and an insight that reported one under the other's
caption would be defensible at every step and wrong on the screen.

**Three reads rather than one call, and the thin-wrapper rule survives it.**
The claim's columns come off `claim_detail`'s header, the thresholds come from
the rules tier, and the two verdicts come from the registry's own singletons —
which is exactly the shape `services/worklist/actions.py::claim_actions` uses
to build its `ClaimFlags`. Nothing is *computed* here: every comparison is
inside a registered derivation, so this module has no cut-off in it and no
`>=` of its own. That is the property AD-10 is protecting, and a wrapper that
had inlined `score >= 60` would be the second computer it exists to prevent.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from data.context import CallerContext
from rules.parameters import thresholds_for
from services import derivations
from services.claims.detail import ClaimNotVisible, claim_detail


@dataclass(frozen=True)
class FraudSignals:
    """The four facts the fraud insight may state, and the two cut-offs behind them.

    A value object rather than a loose dict so the AD-2 equality test has
    something to compare by value: every field here is either a stored column
    or a registered derivation's answer, and the test rebuilds all six from the
    same services to assert the persisted card copied rather than invented.

    `thresholds_version` names the rule document that answered, for
    `ClaimActions.rules_version`' reason: every rule that decided something in a
    response is named in it.
    """

    fraud_score: int
    fraud_flag: bool
    siu_review: bool
    fraud_flagged: bool
    siu_fraud_score_min: int
    fraud_flag_score_min: int
    thresholds_version: int


async def fraud_signals(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> ToolResult[FraudSignals]:
    """The claim's fraud columns, both derived verdicts, and both thresholds.

    `ok=False` for a claim the caller cannot see, `reserve_check`'s rule and its
    reason.

    **The verdicts decide which narrative the model is asked for**, which is
    what makes the fraud card's low-risk variant a deterministic choice rather
    than a model's judgement (AC 3). `agents/insights.py` reads `siu_review` and
    `fraud_flagged` off this envelope, picks the schema and the prompt framing
    from them, and never lets the completion pick its own outcome — the low-risk
    confirmation is a *variant*, and which variant applies is a service's
    answer.
    """
    try:
        detail = await claim_detail(db, ctx, claim_business_id)
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")

    header = detail.header
    thresholds = await thresholds_for(db)
    signals = FraudSignals(
        fraud_score=header.fraud_score,
        fraud_flag=header.fraud_flag,
        siu_review=derivations.siu_review.for_thresholds(thresholds).of(
            fraud_flag=header.fraud_flag, fraud_score=header.fraud_score
        ),
        fraud_flagged=derivations.fraud_flagged.for_thresholds(thresholds).of(
            fraud_flag=header.fraud_flag, fraud_score=header.fraud_score
        ),
        siu_fraud_score_min=thresholds.siu_fraud_score_min,
        fraud_flag_score_min=thresholds.fraud_flag_score_min,
        thresholds_version=thresholds.version,
    )
    # Scores are already integers on a 0-100 scale and the thresholds are the
    # same scale, so there is nothing to format — `next_actions` records why an
    # empty `display` is a statement rather than an omission.
    return ToolResult.succeeded(signals)
