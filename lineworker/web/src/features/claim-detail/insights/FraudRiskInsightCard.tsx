/**
 * The "Fraud risk indicators" card (Story 6.2, AC 3).
 *
 * **Two variants, and the browser branches on `outcome` and on nothing else.**
 * The payload carries this claim's fraud score *and* both thresholds it was
 * measured against, so `content.signals.fraudScore >= content.signals.
 * fraudFlagScoreMin` is one line away and reads like formatting. It is not: it
 * would be the client re-deciding a verdict two registered derivations already
 * reached on the server, against numbers a rule document owns — the exact
 * comparison the prototype writes twice, once against a cut-off that appears in
 * no rule document at all. `outcome` is that verdict, published.
 *
 * **The low-risk variant is a confirmation, not an empty list** (AC 3). It says
 * the score sits below both thresholds and nothing needs referring, in the ok
 * tokens, with what would change the assessment beneath it. A card that
 * rendered "Red flags" over nothing would read as "we have not checked", which
 * is a different and worse thing to tell a handler.
 *
 * The two thresholds are shown side by side because they are two different
 * rules funding two different pieces of work — referral to SIU and the wider
 * review population — and a card that showed one score against the wrong one
 * would be defensible at every step and wrong on the screen.
 */
import type { FraudRiskCard as FraudRiskCardData } from "@/api/claims";

import { Kv } from "../Cards";
import { InsightBullets } from "./InsightShell";
import { FRAUD_OUTCOME_LABEL, FRAUD_OUTCOME_TONE } from "./insightTone";

export function FraudRiskInsightCard({ content }: { content: NonNullable<FraudRiskCardData["content"]> }) {
  const signals = content.signals;

  return (
    <>
      <div
        data-testid="insight-fraud-outcome"
        data-outcome={content.outcome}
        className={`mb-[8px] rounded border px-[8px] py-[6px] text-[11px] font-semibold ${FRAUD_OUTCOME_TONE[content.outcome]}`}
      >
        {FRAUD_OUTCOME_LABEL[content.outcome]}
      </div>

      <div className="mb-[8px] flex flex-col">
        <Kv label="Fraud score" testId="insight-fraud-score">
          {signals.fraudScore}
        </Kv>
        <Kv label="SIU referral threshold" testId="insight-fraud-siu-min">
          {signals.siuFraudScoreMin}
        </Kv>
        <Kv label="Review threshold" testId="insight-fraud-review-min">
          {signals.fraudFlagScoreMin}
        </Kv>
      </div>

      {content.outcome === "red_flags" ? (
        <>
          <p data-testid="insight-fraud-summary" className="text-[11.5px] text-text">
            {content.narrative.summary}
          </p>
          <InsightBullets
            items={content.narrative.redFlags}
            testId="insight-fraud-flag"
            tone="text-error"
          />
        </>
      ) : (
        <>
          <p data-testid="insight-fraud-confirmation" className="text-[11.5px] text-text">
            {content.narrative.confirmation}
          </p>
          <InsightBullets items={content.narrative.monitoring} testId="insight-fraud-monitoring" />
        </>
      )}
    </>
  );
}
