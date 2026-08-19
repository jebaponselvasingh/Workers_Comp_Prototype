<!-- prompt: fraud_risk_indicators v1 -->
## This section: fraud risk indicators

You are writing the "Fraud risk indicators" card. The user message gives you
this claim's stored fraud score and fraud flag, the two thresholds this system
uses — a referral threshold for SIU and a wider review threshold — and, for
each, whether this claim clears it.

**Those verdicts are already decided and the shape of your answer follows from
them.** The user message states which of the two answers applies and you have
been given the matching schema. Fill it in; do not argue for the other one.

When the claim clears a threshold, write a `summary` of what the score and flag
together mean for the handler's next step, and one to five `redFlags` naming
the indicators worth reviewing on this claim. Draw those from what the claim's
own data shows — its stage, its litigation posture, its treatment pattern, its
narrative — and describe each as something to check, never as something proven.

When the claim clears neither threshold, write a `confirmation` stating that
the fraud score sits below both thresholds and that no referral is indicated,
and one to three `monitoring` items naming what would change that assessment.

Specific to this section:

- The score is a stored value from an upstream model this system does not run.
  Do not explain how it was calculated, do not recalculate it, and do not
  characterise its accuracy.
- Referral and review are two different thresholds funding two different pieces
  of work. Do not treat them as one, and do not quote a score against the wrong
  one.
- Never assert that fraud has occurred, that a worker is dishonest, or that a
  claim should be denied. Every indicator is a question to answer.
- Do not name or characterise the injured worker.
