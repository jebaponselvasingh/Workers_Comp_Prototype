/**
 * One claim in the queue — the prototype's `.qc` card, rendering a payload.
 *
 * **There is no derivation in this file, and that is the point.** Days open,
 * the risk band behind the dot, the four badges and the 🔺 marker all arrive
 * from `GET /api/claims/queue`, where `services/derivations` and
 * `services/worklist` decided them (AD-1, AD-10). Grep this directory for a
 * threshold, a comparison against a score, or a count of anything: there is
 * none, which is what stops the queue and the (Story 2.2) detail pane from
 * ever disagreeing about whether a claim is blocked.
 *
 * Four rows, exactly as the prototype's `buildQCard` draws them:
 *   1. claim id (mono, 🔺 when marked) · days open
 *   2. risk dot · worker name · FRAUD / LITIG / PAY DUE / SIU badges
 *   3. injury type
 *   4. stage pill · employer short name
 *
 * **Truncation is visual only.** The prototype clamps the injury type to 30
 * characters in the string it builds; here the full text is in the DOM and
 * CSS narrows it, with a `title` for the pointer. A clamp in the markup
 * would hide the tail from a screen reader too — and the server sends the
 * whole string precisely so the decision can be made here.
 */
import type { ClaimCard as ClaimCardData, RiskBand, Stage } from "@/api/claims";

import { STAGE_LABEL } from "./stageLabels";

/** Severity band → dot colour, from the prototype's `.rdot` rules. */
const RISK_DOT: Record<RiskBand, string> = {
  high: "bg-error",
  med: "bg-warn",
  low: "bg-ok",
};

const RISK_LABEL: Record<RiskBand, string> = {
  high: "High risk",
  med: "Medium risk",
  low: "Low risk",
};

/** The prototype's `.stag` palette, on tokens. */
const STAGE_PILL: Record<Stage, string> = {
  intake: "bg-steel-soft text-steel",
  investigation: "bg-warn-soft text-warn",
  treatment: "bg-ok-soft text-ok",
  settled: "bg-surface-2 text-faint",
};

interface BadgeSpec {
  /** Which payload field switches it on. */
  field: keyof Pick<
    ClaimCardData,
    "fraudFlag" | "litigationFlag" | "paymentDue" | "siuReview"
  >;
  label: string;
  className: string;
  /** What a screen reader hears — the abbreviations are for the eye. */
  title: string;
}

const BADGES: readonly BadgeSpec[] = [
  {
    field: "fraudFlag",
    label: "FRAUD",
    className: "bg-warn-soft text-warn",
    title: "Fraud indicator on this claim",
  },
  {
    field: "litigationFlag",
    label: "LITIG",
    className: "bg-error-soft text-error",
    title: "In litigation",
  },
  {
    field: "paymentDue",
    label: "PAY DUE",
    className: "bg-steel-soft text-steel",
    title: "Indemnity payment due",
  },
  {
    field: "siuReview",
    label: "SIU",
    className: "bg-warn-soft text-warn",
    title: "Referred for Special Investigation Unit review",
  },
];

interface ClaimCardProps {
  card: ClaimCardData;
  selected: boolean;
  onSelect: (claimId: string) => void;
}

export function ClaimCard({ card, selected, onSelect }: ClaimCardProps) {
  return (
    <li>
      <button
        type="button"
        data-testid="queue-card"
        data-claim-id={card.claimId}
        // `aria-current` rather than `aria-pressed`: this is "the one of
        // these you are looking at", not a toggle that stays down.
        aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(card.claimId)}
        className={`w-full border-b border-border border-l-[3px] px-[11px] py-[9px] text-left hover:bg-surface-2 ${
          selected ? "border-l-brand bg-brand-soft/40" : "border-l-transparent bg-surface"
        }`}
      >
        <div className="mb-px flex items-baseline justify-between">
          <span
            data-testid="queue-card-id"
            className="font-mono text-[10px] text-faint"
          >
            {card.priorityMarker && (
              // Decorative beside the label below it: a screen reader
              // hearing "red triangle pointing up" learns nothing, while
              // "Priority" says what the mark means.
              <span aria-hidden data-testid="queue-card-marker">
                🔺{" "}
              </span>
            )}
            {card.priorityMarker && <span className="sr-only">Priority. </span>}
            {card.claimId}
          </span>
          <span
            data-testid="queue-card-days-open"
            title="Days since the first report of injury"
            className="font-mono text-[10px] text-faint"
          >
            {card.daysOpen}d
          </span>
        </div>

        <div className="mb-px flex flex-wrap items-center gap-x-1 text-[13px] font-semibold">
          <span
            data-testid="queue-card-risk"
            data-risk={card.risk}
            title={RISK_LABEL[card.risk]}
            className={`inline-block size-1.5 rounded-full ${RISK_DOT[card.risk]}`}
          />
          <span className="sr-only">{RISK_LABEL[card.risk]}.</span>
          <span data-testid="queue-card-worker">{card.workerName}</span>
          {BADGES.filter((badge) => card[badge.field]).map((badge) => (
            <span
              key={badge.label}
              data-testid={`queue-card-badge-${badge.field}`}
              title={badge.title}
              className={`rounded-[2px] px-[5px] py-px text-[9px] font-bold ${badge.className}`}
            >
              {badge.label}
            </span>
          ))}
        </div>

        <div
          data-testid="queue-card-injury"
          title={card.injuryType}
          className="mb-[3px] truncate text-[11px] text-muted-text"
        >
          {card.injuryType}
        </div>

        <div className="flex items-center justify-between">
          <span
            data-testid="queue-card-stage"
            className={`rounded-[2px] px-[6px] py-0.5 text-[9px] font-bold tracking-[0.3px] uppercase ${STAGE_PILL[card.stage]}`}
          >
            ● {STAGE_LABEL[card.stage]}
          </span>
          <span data-testid="queue-card-employer" className="text-[10px] text-faint">
            {card.employerShortName}
          </span>
        </div>
      </button>
    </li>
  );
}
