"""`siu_review`, `fraud_flagged`, `rtw_blocked`, `payment_due` — the
operational flags over `claim`'s fraud, stage and return columns.

**These are demo definitions, and that is a decision, not an oversight.**
The architecture's Deferred list says so in as many words: "the prototype's
hash-bucket demo derivations (`siu_review`, `payment_due`) get real business
definitions when rules are authored in JDM; AD-10 fixes only where they're
computed." So what this module settles is the *where* — one computer each,
here, called by the queue today and by the claim detail, the dashboard and
the copilot as those land, so no two screens can disagree about whether a
claim is blocked. What it does **not** settle is the *what*: two of the
three are a hash of the claim id, which is a stand-in for a rule nobody has
written yet.

Two consequences worth stating plainly, because a reader arriving at
`rtw_blocked` a year from now will otherwise assume it means something:

- A hash bucket is deterministic but arbitrary. `WC-20017` is "payment due"
  because of its digits, not because a cheque is owed. Nothing downstream
  may reason about *why* a claim carries these flags.
- The tunables are JDM parameters (`rtwBlockedHashModulus`,
  `paymentDueHashModulus`, `siuFraudScoreMin`) so that replacing a demo with
  a real definition changes a document and this module — never the twenty
  call sites that ask whether a claim is blocked.

`siu_review` is the one of the original three with real content: a
fraud-flagged claim whose score clears the SIU referral threshold. Its
threshold is a parameter for the same reason the risk bands are.

Story 5.1 adds a fourth, `fraud_flagged`, and homes it here rather than in a
module of its own precisely so the two fraud rules are read together: they
share a column pair, differ only in their threshold, and are the pair most
likely to be collapsed by a later reader who notices they look alike. Sitting
one screen apart with the difference written down is the cheapest guard
against that; see `FraudFlaggedDerivation`. (The module name still describes
the *rules* rather than one consumer — the queue chips one of them, the
dashboard counts another, and Story 5.4's worklist population will read both.)
"""

from dataclasses import dataclass

from data.models.enums import ReturnStatus, Stage
from services.derivations.registry import Derivation, register
from services.derivations.risk_band import RiskBand


def hash_bucket(business_id: str) -> int:
    """The prototype's `hashStr`, ported exactly (line 646).

    `h = (h * 31 + charCode) >>> 0` — a Java-style string hash truncated to
    an unsigned 32-bit integer on every step. Reproduced rather than replaced
    with `hash()` or a digest for one reason: the prototype *is* this story's
    data contract, so the same claim must fall in the same demo bucket here
    as it does there. Python's `hash()` is salted per process and would make
    the queue's flags change on every restart; a digest would put different
    claims in the buckets the design was reviewed against.

    JavaScript evaluates `h * 31` in double precision, but `h` never exceeds
    2**32, so the product stays well inside the 53-bit exact range and the
    two languages agree bit for bit.
    """
    h = 0
    for char in business_id:
        h = (h * 31 + ord(char)) & 0xFFFFFFFF
    return h


@dataclass(frozen=True)
class SiuReviewDerivation:
    """Fraud-flagged, and scored above the SIU referral threshold.

    Both conditions, not either: the flag without a score is an unranked
    suspicion, and a high score without the flag is a claim the fraud model
    rated but nobody triaged. The queue's +35 weight is worth only the
    intersection.
    """

    fraud_score_min: int

    def of(self, *, fraud_flag: bool, fraud_score: int) -> bool:
        return fraud_flag and fraud_score >= self.fraud_score_min


@dataclass(frozen=True)
class FraudFlaggedDerivation:
    """Fraud-flagged, and scored above the *review* threshold — not the SIU one.

    **This is a second rule, not a second copy of `siu_review`**, and the
    numbers are what force the distinction rather than a preference. The
    prototype chips a queue card for SIU at `fraudFlag && fraudScore >= 60`
    (line 953) and counts its dashboard Fraud Flags card under the caption
    "Score ≥ 55 — review needed" (line 1069). On the seeded portfolio that is
    9 claims against 13. Referral and review are different populations because
    they fund different work: a referral opens an investigation, a review is
    the wider set somebody reads before deciding whether to refer.

    So the two thresholds are two parameters, adjacent in one block, and the
    two derivations are two entries in one registry. Pointing the dashboard at
    `siu_review` would have shown 9 under a caption promising 55 — defensible
    at every step, wrong on the screen, and invisible without the prototype
    open beside it.

    `fraud_flag and fraud_score >= min`, matching `SiuReviewDerivation`'s
    "both conditions, not either": the flag without a score is an unranked
    suspicion, and a score without the flag is a claim the model rated and
    nobody triaged. On today's seed the two conditions happen to coincide
    exactly at 55, which is why the card reproduces the prototype's 13 — a
    coincidence of the data, not a licence to drop either half.
    """

    fraud_score_min: int

    def of(self, *, fraud_flag: bool, fraud_score: int) -> bool:
        return fraud_flag and fraud_score >= self.fraud_score_min


@dataclass(frozen=True)
class RtwBlockedDerivation:
    """Demo definition: in treatment, not yet returned, and either unlucky
    in the hash bucket or high risk.

    The `risk == high` disjunct is the only part of this with meaning — a
    severely injured worker still under treatment plausibly is blocked from
    returning. The modulus is the placeholder. Kept together because the
    prototype's queue was reviewed against exactly this population, and
    dropping the arbitrary half would change which cards a stakeholder sees.
    """

    hash_modulus: int

    def of(
        self,
        *,
        claim_id: str,
        stage: Stage,
        return_status: ReturnStatus,
        risk: RiskBand,
    ) -> bool:
        if stage is not Stage.treatment or return_status is not ReturnStatus.under_treatment:
            return False
        return hash_bucket(claim_id) % self.hash_modulus == 0 or risk is RiskBand.high


@dataclass(frozen=True)
class PaymentDueDerivation:
    """Demo definition: in treatment, and *not* in the zero hash bucket.

    Note the inversion against `rtw_blocked` (`!= 0` here, `== 0` there) —
    it is the prototype's, preserved. It makes payment-due the common case
    among treatment claims and blocked-return the rare one, which is the
    population shape the queue's ordering was designed against. A real
    definition will read the payment schedule instead (Epic 3), at which
    point this class changes and no caller does.
    """

    hash_modulus: int

    def of(self, *, claim_id: str, stage: Stage) -> bool:
        return stage is Stage.treatment and hash_bucket(claim_id) % self.hash_modulus != 0


siu_review = register(
    Derivation(
        name="siu_review",
        describes="claim.fraud_flag and fraud_score at or above the SIU referral threshold",
        build=lambda thresholds: SiuReviewDerivation(
            fraud_score_min=thresholds.siu_fraud_score_min
        ),
    )
)

fraud_flagged = register(
    Derivation(
        name="fraud_flagged",
        describes=(
            "claim.fraud_flag and fraud_score at or above the fraud REVIEW threshold "
            "(wider than siu_review's referral threshold — see FraudFlaggedDerivation)"
        ),
        build=lambda thresholds: FraudFlaggedDerivation(
            fraud_score_min=thresholds.fraud_flag_score_min
        ),
    )
)

rtw_blocked = register(
    Derivation(
        name="rtw_blocked",
        describes="demo: in treatment, under treatment, and hash-bucketed or high risk",
        build=lambda thresholds: RtwBlockedDerivation(
            hash_modulus=thresholds.rtw_blocked_hash_modulus
        ),
    )
)

payment_due = register(
    Derivation(
        name="payment_due",
        describes="demo: in treatment and outside the zero payment hash bucket",
        build=lambda thresholds: PaymentDueDerivation(
            hash_modulus=thresholds.payment_due_hash_modulus
        ),
    )
)
