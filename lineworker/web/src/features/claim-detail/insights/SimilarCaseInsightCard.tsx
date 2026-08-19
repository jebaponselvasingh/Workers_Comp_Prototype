/**
 * The "Similar case outcomes" card (Story 6.2, AC 2).
 *
 * The neighbours are exactly the list `services/rag.similar_claims` returned,
 * in the order it returned them — nearest first, cosine distance ascending.
 * This component does not sort, filter, count or convert: the distance is
 * published raw precisely because a "92% similar" is a presentation decision
 * nobody's service made, and turning one into the other here would put a figure
 * on screen that no layer computed (NFR-3, AD-1).
 *
 * **Two states below "generated", and they are different facts.** A card whose
 * search found nothing says so — `bookIsEmpty` is the server's boolean, not
 * `neighbours.length === 0` worked out here — because "no comparable claims in
 * this caseload" is an answer about the caller's book, and rendering an empty
 * table would read as a load that never finished.
 *
 * **The staleness disclosure is a row, not a footnote** (AD-12). When any
 * neighbour's vector is flagged stale or older than the deployment's disclosure
 * window, the server composes a sentence and the card shows it. The threshold
 * and the counting both happen server-side; this renders `stalenessDisclosure`
 * when it is present and nothing when it is null.
 */
import type { SimilarCaseContent } from "@/api/claims";

import { Kv } from "../Cards";
import { InsightBullets } from "./InsightShell";

export function SimilarCaseInsightCard({ content }: { content: SimilarCaseContent }) {
  return (
    <>
      <p data-testid="insight-similar-summary" className="text-[11.5px] text-text">
        {content.narrative.summary}
      </p>

      {content.bookIsEmpty ? (
        <p data-testid="insight-similar-none" className="mt-[8px] text-[11.5px] text-faint">
          No comparable claims were found in this caseload to compare against.
        </p>
      ) : (
        <div className="mt-[8px] flex flex-col">
          {content.neighbours.map((neighbour) => (
            <Kv
              key={neighbour.claimId}
              testId="insight-neighbour"
              label={
                <span className="font-mono text-[10.5px]">
                  {neighbour.claimId}
                  {neighbour.stale ? " ·" : ""}
                </span>
              }
            >
              {neighbour.employerShortName} · {neighbour.injuryType}
            </Kv>
          ))}
        </div>
      )}

      {content.stalenessDisclosure !== null && (
        <p
          data-testid="insight-similar-staleness"
          className="mt-[8px] rounded border border-warn/30 bg-warn-soft px-[7px] py-[5px] text-[10.5px] text-warn"
        >
          {content.stalenessDisclosure}
        </p>
      )}

      <InsightBullets items={content.narrative.takeaways} testId="insight-similar-takeaway" />
    </>
  );
}
