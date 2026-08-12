/**
 * The treatment variant: phase banner, paid-vs-reserve, care & RTW
 * coordination, and the recent timeline (FR-DET-1).
 *
 * Both derived states — the phase and the coordination status — arrive from
 * the server as a value plus the sentence that describes it (AC 5). This
 * component maps the value to a short label and a colour, which is what the
 * enum convention leaves to the UI, and renders the sentence as sent.
 *
 * **The Bills jump-link is a tab switch, not a route.** The tab is local UI
 * state, so "View full Bills & Payments →" hands the pane a tab key; the
 * Bills tab then shows its Epic-3 empty state, which is the honest end of
 * that journey until the financial engine lands.
 *
 * **The reserve-check verdict is an explicit placeholder.** The prototype
 * shows "Reserve Adequate / Light / Heavy" here, computed from the bill and
 * payment-schedule tables that arrive in Story 3.3 and judged by the rule
 * Story 3.2 owns. Rendering a verdict from the claim's paid columns alone
 * would be a different rule wearing the same words.
 */
import type { ClaimDetail, RecoveryWindow, TreatmentOverviewData } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { CardGrid, CaseCard, Kv, TimelineCard } from "../Cards";
import { EditableRow } from "../EditableRow";
import {
  COMM_STATUS_LABEL,
  COORDINATION_LABEL,
  PHASE_LABEL,
  RECOVERY_LABEL,
  RETURN_STATUS_LABEL,
} from "../labels";
import { useInlineEdits } from "../useInlineEdits";

/** The prototype's phase colours: steel, warn, then ok as MMI approaches. */
const PHASE_ACCENT: Record<TreatmentOverviewData["phase"], string> = {
  early: "border-l-steel text-steel",
  active: "border-l-warn text-warn",
  approaching_mmi: "border-l-ok text-ok",
};

/** The prototype's coordination card tints — error, warn, warn, ok. */
const COORDINATION_ACCENT: Record<
  TreatmentOverviewData["coordinationStatus"],
  { card: string; text: string }
> = {
  coordination_gap: { card: "border-error bg-error-soft", text: "text-error" },
  awaiting_information: { card: "border-warn bg-warn-soft", text: "text-warn" },
  legal_coordination: { card: "border-warn bg-warn-soft", text: "text-warn" },
  on_track: { card: "border-ok bg-ok-soft", text: "text-ok" },
};

export function TreatmentOverview({
  claim,
  overview,
  onOpenBills,
}: {
  /** The whole case file: the recovery-window edit needs its `version`. */
  claim: ClaimDetail;
  overview: TreatmentOverviewData;
  onOpenBills: () => void;
}) {
  const accent = COORDINATION_ACCENT[overview.coordinationStatus];
  const edits = useInlineEdits(claim);

  return (
    <>
      <section
        data-testid="treatment-phase-banner"
        data-phase={overview.phase}
        className={`mb-[10px] rounded-lg border border-border border-l-4 bg-surface p-3 ${
          PHASE_ACCENT[overview.phase]
        }`}
      >
        <h3 className="mb-1 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
          🩺 Current treatment phase
        </h3>
        <p data-testid="treatment-phase-label" className="font-display text-[15px] font-bold">
          {PHASE_LABEL[overview.phase]}
        </p>
        <p data-testid="treatment-phase-note" className="mt-1 text-[12px] text-muted-text">
          {overview.phaseNote}
        </p>
        <p data-testid="treatment-phase-day" className="mt-2 text-[11px] text-faint">
          Day {overview.daysOpen} of claim · Expected recovery:{" "}
          {RECOVERY_LABEL[overview.recovery]} ({overview.expectedDays} days)
        </p>
        {/* **The one editable field on this card, and the reason it is here.**
            The recovery window is the only thing a handler can change that
            moves a *derived* value: the phase above, its note and the
            expected-days figure are all `treatment_phase`'s answer, and they
            are recomputed by the server the moment this commits (AC 3). The
            investigation card edits it too — same command, same vocabulary —
            but nothing on that card visibly depends on it. */}
        <div className="mt-2 max-w-[220px]">
          <EditableRow
            edits={edits}
            field="recovery"
            label="Recovery window"
            value={overview.recovery}
            options={claim.editOptions.recoveryWindows.map((value) => ({
              value,
              label: RECOVERY_LABEL[value],
            }))}
            onCommit={(next) => edits.commit({ recovery: next as RecoveryWindow })}
          />
        </div>
      </section>

      <CardGrid>
        <CaseCard title="💵 Paid to date vs. reserve" testId="treatment-financials">
          <Kv label="Medical paid">{formatCents(overview.paidMedicalCents)}</Kv>
          <Kv label="Indemnity paid">{formatCents(overview.paidIndemnityCents)}</Kv>
          <Kv label="Reserve balance" valueClassName="font-bold">
            {formatCents(overview.reserveCents)}
          </Kv>
          <Kv label="Reserve check" testId="treatment-reserve-check">
            <span className="text-faint">Verdict arrives with the financial engine</span>
          </Kv>
          <button
            type="button"
            data-testid="treatment-bills-link"
            onClick={onOpenBills}
            className="mt-2 w-full rounded-md border border-border bg-surface-2 py-[6px] text-[11px] font-semibold text-text hover:bg-surface"
          >
            View full Bills &amp; Payments →
          </button>
        </CaseCard>

        <CaseCard
          testId="treatment-coordination"
          className={accent.card}
          title={<span className={accent.text}>🤝 Care &amp; RTW coordination</span>}
        >
          <p
            data-testid="treatment-coordination-label"
            data-status={overview.coordinationStatus}
            className={`text-[13px] font-bold ${accent.text}`}
          >
            {COORDINATION_LABEL[overview.coordinationStatus]}
          </p>
          <p
            data-testid="treatment-coordination-note"
            className="mt-1 mb-2 text-[11.5px] text-text"
          >
            {overview.coordinationNote}
          </p>
          <Kv label="Communication status">{COMM_STATUS_LABEL[overview.commStatus]}</Kv>
          <Kv label="Return status">{RETURN_STATUS_LABEL[overview.returnStatus]}</Kv>
          <Kv label="Claim handler">{overview.handlerName}</Kv>
        </CaseCard>
      </CardGrid>

      <TimelineCard
        // "Recent" only when it is: the server says whether it cut the log,
        // so the heading describes what is on screen instead of hedging on
        // every claim.
        title={overview.timelineTruncated ? "Recent case timeline" : "Case timeline"}
        entries={overview.timeline}
      />
    </>
  );
}
