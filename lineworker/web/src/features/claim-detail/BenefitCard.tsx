/**
 * The benefit-calculation card — Story 3.1's surface (AC 1, 2, 3, 4).
 *
 * Six facts and a paragraph, every one of them decided by
 * `services/financials` (AD-1): the weekly indemnity, the comp rate it was
 * computed at, the indemnity type, the jurisdiction's statutory bounds, the
 * payment-schedule note and the reserve rationale. This component multiplies
 * nothing, bands nothing and compares nothing — it formats cents through
 * `lib/money.ts`, basis points through `lib/rate.ts`, and renders the
 * server's sentence as sent.
 *
 * **Rendered on the investigation and treatment variants only**, which is
 * where the prototype puts it (`investigationOverviewHTML` and
 * `treatmentOverviewHTML` both call `benefitCardHTML`). The *payload* is on
 * the case file at every stage, because the figure is a fact about the claim
 * throughout and Story 3.3's payment schedule reads it from the Bills tab —
 * but an intake claim's card would be showing a benefit nobody is paying yet,
 * and a settled claim's a benefit that has stopped.
 *
 * **The comp rate is the one editable value, and it is the only optimistic
 * one.** AD-9 permits an optimistic update for a user-entered scalar; here the
 * scalar has no derived *appearance* of its own — a comp rate is not banded or
 * coloured — so the digits can move immediately while everything computed from
 * them waits for the server. Story 2.4's severity card takes the opposite call
 * because its number is coloured by a band.
 *
 * **The provenance line replaces the prototype's disclaimer.** The prototype
 * closes with "Illustrative figures for prototype purposes — verify against
 * the current WC board benefit schedule". Until per-jurisdiction statutory
 * data is validated (NFR-4, Deferred) the equivalent honest note is the date
 * the schedule on file took effect — which is a fact the table holds rather
 * than a sentence hardcoded in a component.
 */
import * as React from "react";

import type { ClaimDetail } from "@/api/claims";
import { useClaimWriteInFlight, useEditCompRate } from "@/api/claims";
import { formatCents } from "@/lib/money";
import { basisPointsToPercent, formatBasisPoints, parseBasisPoints } from "@/lib/rate";

import { CaseCard, Kv } from "./Cards";
import { InlineEditField, type FieldFeedback } from "./InlineEditField";
import { INDEMNITY_TYPE_LABEL } from "./labels";
import { feedbackFromError } from "./useInlineEdits";

/**
 * One basis point — the comp rate's storage granularity, as the input's step.
 *
 * **Not the prototype's `step="0.5"`**, and the divergence is a correctness
 * fix rather than a preference (code review, 2026-08-14). A step is not a
 * convenient increment: the browser treats a value that is not a multiple of
 * it as a `stepMismatch`, and `stepUp()` snaps to the next multiple above
 * `min` — so with 0.5 the *statutory default itself* (66.67%) is invalid, and
 * one click of the up arrow makes it 67.00, silently discarding the decimals
 * a handler typed. 0.01 is the unit `claim.comp_rate_override_bp` stores and
 * the unit `parseBasisPoints` accepts, so every value the field can hold is a
 * multiple of it by construction.
 *
 * A presentation constant all the same, not a rule: the *bounds* are the
 * server's and arrive on the payload, which is the part that would be a rule
 * if it lived here.
 */
const RATE_STEP = 0.01;

export function BenefitCard({ claim }: { claim: ClaimDetail }) {
  const { benefit } = claim;
  const edit = useEditCompRate(claim.claimId);
  // Every command against this claim, not just this one: `expectedVersion`
  // comes from the cached case file and no mutation advances it optimistically,
  // so a second commit launched before the first settles would 409 about the
  // handler's own edit (Story 2.3's code review, and `useClaimWriteInFlight`).
  const busy = useClaimWriteInFlight(claim.claimId);
  const [feedback, setFeedback] = React.useState<FieldFeedback | undefined>();

  const commit = (next: string): void => {
    setFeedback(undefined);
    const basisPoints = parseBasisPoints(next);
    // **Refused here as well as server-side, and the two refusals differ on
    // purpose** — `SeverityCard`'s argument. A number input hands back `""`
    // for "70x", which would submit as a rate nobody typed; and a third
    // decimal place is a value this system cannot store, so rounding it away
    // silently would be the console inventing a number. Anything that
    // survives is still checked by the command, which is the enforcement.
    if (basisPoints === null) {
      setFeedback({
        kind: "invalid",
        message: "Comp rate must be a percentage with up to two decimals.",
        attempted: next,
      });
      return;
    }
    if (basisPoints < benefit.compRateMinBp || basisPoints > benefit.compRateMaxBp) {
      setFeedback({
        kind: "invalid",
        message: `Comp rate must be between ${formatBasisPoints(
          benefit.compRateMinBp,
        )}% and ${formatBasisPoints(benefit.compRateMaxBp)}% of AWW.`,
        attempted: next,
      });
      return;
    }
    send(basisPoints, next);
  };

  const send = (compRateBp: number | null, attempted?: string): void => {
    edit.mutate(
      // The version is read at commit time rather than captured on render, so
      // a card left open across somebody else's edit does not send a version
      // it has already been told is stale (Story 2.3's rule).
      { compRateBp, expectedVersion: claim.version },
      { onError: (error) => setFeedback(feedbackFromError(error, attempted)) },
    );
  };

  return (
    <CaseCard testId="benefit-card" title="Benefit calculation & reserve details">
      <div className="grid gap-x-[10px] lg:grid-cols-2">
        <div>
          <Kv
            label={
              <span className="inline-flex items-center gap-1">
                Comp rate
                {benefit.isOverridden && (
                  <button
                    type="button"
                    data-testid="benefit-reset"
                    title="Reset to the statutory default"
                    aria-label="Reset comp rate to the statutory default"
                    disabled={busy}
                    onClick={() => {
                      setFeedback(undefined);
                      send(null);
                    }}
                    className="text-[11px] text-steel disabled:opacity-60"
                  >
                    ↺
                  </button>
                )}
              </span>
            }
          >
            <span className="inline-flex items-baseline gap-1">
              {/* The prototype types its `.crinput` in JetBrains Mono, and
                  Tailwind's preflight gives form controls `font: inherit`, so
                  the token on the wrapper reaches the input. */}
              <span className="w-[64px] font-mono">
                <InlineEditField
                  field="compRate"
                  label="Comp rate"
                  value={formatBasisPoints(benefit.compRateBp)}
                  numeric={{
                    min: basisPointsToPercent(benefit.compRateMinBp),
                    max: basisPointsToPercent(benefit.compRateMaxBp),
                    step: RATE_STEP,
                  }}
                  saving={busy}
                  feedback={feedback}
                  onCommit={commit}
                />
              </span>
              <span>% of AWW</span>
            </span>
          </Kv>
          {/* The card's figures carry the mono token (Epic 1's design
              tokens), which the prototype applies to the comp-rate input
              alone — extended here to the two money rows beside it so the
              three read as one set of numbers rather than one number and two
              sentences. */}
          <Kv
            label="Weekly indemnity benefit"
            testId="benefit-weekly"
            valueClassName="font-mono font-bold"
          >
            {formatCents(benefit.weeklyCents)}/wk
          </Kv>
          <Kv label="Indemnity type" testId="benefit-type">
            {INDEMNITY_TYPE_LABEL[benefit.indemnityType]}
          </Kv>
        </div>

        <div>
          <Kv
            label={`${benefit.stateCode} statutory min/max`}
            testId="benefit-bounds"
            valueClassName="font-mono"
          >
            {formatCents(benefit.stateMinCents)} – {formatCents(benefit.stateMaxCents)}/wk
          </Kv>
          <Kv label="Payment schedule" testId="benefit-schedule">
            <span className="inline-block max-w-[170px]">
              Weekly, starting {benefit.waitingDays}-day waiting period after DOI
            </span>
          </Kv>
          <Kv label="State" testId="benefit-state">
            {benefit.stateName}
          </Kv>
        </div>
      </div>

      {/* The prototype's `.ratbox`: the reserve rationale, on the steel-soft
          surface, as a paragraph the server wrote. */}
      <p
        data-testid="benefit-rationale"
        className="mt-2 rounded border border-steel/25 bg-steel-soft p-[10px_12px] text-[12px] leading-relaxed text-steel"
      >
        <b className="text-text">Reserve rationale:</b> {benefit.reserveRationale}
      </p>

      <p data-testid="benefit-provenance" className="mt-[6px] text-[9.5px] text-faint">
        Statutory schedule effective {benefit.scheduleEffectiveDate} — verify against the
        current {benefit.stateCode} WC board benefit schedule.
      </p>
    </CaseCard>
  );
}
