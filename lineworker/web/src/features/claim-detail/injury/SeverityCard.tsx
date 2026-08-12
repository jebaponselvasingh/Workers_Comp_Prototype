/**
 * The severity-score card — the one editable number on the Injury Diagram
 * tab (Story 2.4, AC 3).
 *
 * The score is a column; everything drawn around it is the server's answer
 * about that column. The figure's colour, the bar's colour, the word beside
 * it and the trend line all come from `header.risk`, which is the `risk`
 * derivation the gauge above and the queue card beside it read (AD-10) — so
 * this card cannot disagree with either about one claim.
 *
 * **The bar's width is the score, not a derivation.** `width: 78%` from
 * `severityScore: 78` is the prototype's `width:${c.sevScore}%`: a 0-100
 * column rendered against a 0-100 track, with no threshold, no ratio and
 * nothing to get wrong. The *colour* is banded, and that banding is the
 * server's.
 *
 * **The title shows the cause read-only.** The prototype makes it an inline
 * editor here as well as on the investigation overview; two editors for one
 * column is two write paths in the UI for one in the API (AD-12), so the
 * Overview card keeps it and this one reads it.
 */
import * as React from "react";

import type { ClaimDetail } from "@/api/claims";
import { useClaimWriteInFlight, useEditSeverity } from "@/api/claims";

import { CaseCard, RISK_TEXT } from "../Cards";
import { InlineEditField, type FieldFeedback } from "../InlineEditField";
import { RISK_TREND, SEVERITY_WORD } from "../labels";
import { feedbackFromError } from "../useInlineEdits";

/** The three marks under the bar — the prototype's, verbatim. */
const BAND_MARKS = ["Mild", "Moderate", "Severe"] as const;

/** Band → the token the bar is filled with. `RISK_TEXT`'s background twin. */
const BAR_FILL: Record<string, string> = {
  high: "bg-error",
  med: "bg-warn",
  low: "bg-ok",
};

export function SeverityCard({ claim }: { claim: ClaimDetail }) {
  const edit = useEditSeverity(claim.claimId);
  // Not `edit.isPending`: the body-part select and the add/remove popover on
  // this same tab send the same `expectedVersion` from the same cache entry,
  // so a second commit launched before this one settles would 409 about the
  // handler's own edit (code review, 2026-08-12).
  const busy = useClaimWriteInFlight(claim.claimId);
  const [feedback, setFeedback] = React.useState<FieldFeedback | undefined>();
  const { severityScore, risk, cause } = claim.header;
  // The bounds are the server's, published on the case file — the browser
  // keeps no copy of a numeric rule (`noDerivation.test.ts`).
  const { severityMin, severityMax } = claim.injury;

  const commit = (next: string): void => {
    setFeedback(undefined);
    const parsed = Number(next);
    // **Refused here as well as server-side, and the two refusals differ on
    // purpose.** A number input hands back `""` for "12x" and any integer at
    // all for "101", so without this the first would submit `0` — a real
    // edit nobody made — and the second would cost a round trip to learn
    // what the field already knows. Anything that survives is still checked
    // by the command, which is the enforcement (AC 4).
    if (next.trim() === "" || !Number.isInteger(parsed)) {
      setFeedback({
        kind: "invalid",
        message: "Severity score must be a whole number.",
        attempted: next,
      });
      return;
    }
    if (parsed < severityMin || parsed > severityMax) {
      setFeedback({
        kind: "invalid",
        message: `Severity score must be between ${severityMin} and ${severityMax}.`,
        attempted: next,
      });
      return;
    }
    edit.mutate(
      // The version is read at commit time rather than captured on render,
      // so a card left open across somebody else's edit does not send a
      // version it has already been told is stale (2.3's rule).
      { severityScore: parsed, expectedVersion: claim.version },
      { onError: (error) => setFeedback(feedbackFromError(error, next)) },
    );
  };

  return (
    <CaseCard
      testId="injury-severity"
      title={
        <>
          Severity score —{" "}
          <span
            data-testid="injury-cause"
            className="font-sans text-[10.5px] font-normal tracking-normal normal-case text-muted-text"
          >
            {cause}
          </span>
        </>
      }
    >
      <div className="mb-[5px] flex items-center justify-between gap-3">
        <span
          className={`flex items-baseline gap-1 font-display text-[15px] font-bold ${RISK_TEXT[risk]}`}
        >
          <span className="w-[64px]">
            <InlineEditField
              field="severityScore"
              label="Severity score"
              value={String(severityScore)}
              numeric={{ min: severityMin, max: severityMax }}
              saving={busy}
              feedback={feedback}
              onCommit={commit}
            />
          </span>
          <span data-testid="injury-severity-word">
            /100 — {SEVERITY_WORD[risk]}
          </span>
        </span>
        <span data-testid="injury-risk-trend" className="text-[11px] text-muted-text">
          {RISK_TREND[risk]}
        </span>
      </div>

      <div className="h-[7px] overflow-hidden rounded-full bg-surface-2">
        <span
          data-testid="injury-severity-bar"
          style={{ width: `${severityScore}%` }}
          className={`block h-full ${BAR_FILL[risk]}`}
        />
      </div>
      <div className="mt-[3px] flex justify-between text-[9px] text-faint">
        {BAND_MARKS.map((mark) => (
          <span key={mark}>{mark}</span>
        ))}
      </div>
    </CaseCard>
  );
}
