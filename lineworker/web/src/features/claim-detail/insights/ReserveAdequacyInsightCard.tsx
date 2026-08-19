/**
 * The "Reserve adequacy review" card (Story 6.2, AC 2).
 *
 * **Every money row renders `display`, never `cents`.** The server sends both —
 * the integer for Story 7.1's aggregation, the string for the reader — and the
 * string was produced by `services/financials.format_dollars`, the same
 * function the deterministic rationale one line above is written with. A card
 * that formatted cents itself would be a second rounding of one figure, in a
 * card whose whole subject is that two numbers agree.
 *
 * **The verdict's colour comes from `VERDICT_ACCENT`**, the map Story 3.2 wrote
 * and Story 3.3's Bills summary already shares. Three surfaces, one answer to
 * "what colour is a light reserve" — and note which way round that map goes
 * (light is the error tone), which it argues at length.
 *
 * **`billsOnFile: false` is a state, not a blank.** It means the claim's
 * medical bills are not on file, so the exposure cannot be judged: there is no
 * ratio, no medical exposure figure and no projected total, and the schema
 * refuses to carry any of them in that state. The card says so in a sentence
 * rather than rendering an em dash in three rows, because "unavailable" and
 * "zero" are different facts and only one of them is true.
 */
import type { ReserveAdequacyContent } from "@/api/claims";

import { Kv } from "../Cards";
import { RESERVE_VERDICT_LABEL } from "../labels";
import { VERDICT_ACCENT } from "../reserveAccent";
import { InsightBullets } from "./InsightShell";

export function ReserveAdequacyInsightCard({ content }: { content: ReserveAdequacyContent }) {
  const accent = VERDICT_ACCENT[content.verdict];

  return (
    <>
      <div
        data-testid="insight-reserve-verdict"
        data-verdict={content.verdict}
        className={`mb-[8px] rounded border px-[8px] py-[6px] text-[11px] font-semibold ${accent.box} ${accent.text}`}
      >
        {RESERVE_VERDICT_LABEL[content.verdict]}
      </div>

      <p data-testid="insight-reserve-rationale" className="text-[11px] text-muted-text">
        {content.verdictRationale}
      </p>

      <p data-testid="insight-reserve-summary" className="mt-[8px] text-[11.5px] text-text">
        {content.narrative.summary}
      </p>

      <div className="mt-[8px] flex flex-col">
        <Kv label="Reserve" testId="insight-reserve-amount">
          {content.reserve.display}
        </Kv>
        <Kv label="Indemnity still scheduled" testId="insight-reserve-indemnity">
          {content.remainingIndemnity.display}
        </Kv>
        {content.remainingMedical !== null && (
          <Kv label="Medical unpaid" testId="insight-reserve-medical">
            {content.remainingMedical.display}
          </Kv>
        )}
        {content.projectedRemaining !== null && (
          <Kv label="Projected exposure" testId="insight-reserve-projected">
            {content.projectedRemaining.display}
          </Kv>
        )}
      </div>

      {!content.billsOnFile && (
        <p data-testid="insight-reserve-indeterminate" className="mt-[8px] text-[11px] text-faint">
          This claim&apos;s medical bills are not on file, so its exposure cannot be judged and
          no ratio is quoted.
        </p>
      )}

      <InsightBullets
        items={content.narrative.considerations}
        testId="insight-reserve-consideration"
      />
    </>
  );
}
