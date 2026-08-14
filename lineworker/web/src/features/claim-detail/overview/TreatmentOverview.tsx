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
 * state, so "View full Bills & Payments →" hands the pane a tab key. Story 3.3
 * filled that tab in, so the journey now ends on the full financial picture —
 * and on figures identical to this card's, because both render the same
 * `reserveCheck` block computed by one assembler on the server (AC 4).
 *
 * **The reserve-check verdict arrives from the case file, not from this
 * block** (Story 3.2). `claim.reserveCheck` sits beside `claim.benefit`
 * rather than on the treatment overview, because Story 3.3's Bills financial
 * summary renders the same judgement and one field under one query key is
 * what makes the two surfaces agree structurally rather than by convention.
 * This component maps the verdict to a colour and a label — the enum
 * convention's UI half — and renders the server's sentence as sent. It
 * compares no figures: the ratio, its boundaries and the word for them are
 * all `services/financials`' (AD-1, AD-9, AD-10).
 */
import type { ClaimDetail, RecoveryWindow, TreatmentOverviewData } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { BenefitCard } from "../BenefitCard";
import { CardGrid, CaseCard, Kv, TimelineCard } from "../Cards";
import { EditableRow } from "../EditableRow";
import {
  COMM_STATUS_LABEL,
  COORDINATION_LABEL,
  PHASE_LABEL,
  RECOVERY_LABEL,
  RESERVE_VERDICT_LABEL,
  RETURN_STATUS_LABEL,
} from "../labels";
import { VERDICT_ACCENT } from "../reserveAccent";
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
  const reserve = claim.reserveCheck;
  const verdict = VERDICT_ACCENT[reserve.verdict];

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
          {/* **The bills' paid figure, not `overview.paidMedicalCents`**
              (code review, 2026-08-14). That field is `claim.paid_medical`, a
              snapshot which reads $0 on all 38 open seeded claims — so this
              row said "Medical paid $0.00" directly above a live indemnity
              figure and directly above a link to a tab showing the same
              claim's paid bills as a real number. It is precisely the defect
              the row below was corrected for a review earlier, one row up, and
              it takes the same shape: both figures are the server's, from the
              rows the verdict beside them was computed from. */}
          <Kv label="Medical paid" testId="treatment-medical-paid">
            {formatCents(reserve.disbursedMedicalCents)}
          </Kv>
          {/* **The schedule's figures, not `overview.paidIndemnityCents`**
              (code review, 2026-08-14). This card shows an indemnity-paid
              figure and a reserve verdict, and the two have to come from one
              notion of "paid" or the card contradicts itself — which it did:
              the column reads $0 on every open seeded claim while the verdict
              was computed from a projection in which elapsed weeks count as
              disbursed, so a handler saw "Indemnity paid $0.00" above "no
              exposure remains — reallocate the surplus". The prototype has one
              answer here too: its treatment card renders
              `sch.paidSoFar of sch.totalScheduled` and `billsHTML` says the
              static column "is often 0 even though the schedule already
              show[s] real disbursements". Both figures are the server's; this
              component divides nothing. */}
          <Kv label="Indemnity paid" testId="treatment-indemnity-paid">
            {formatCents(reserve.disbursedIndemnityCents)} of{" "}
            {formatCents(reserve.scheduledIndemnityCents)}
          </Kv>
          <Kv label="Reserve balance" valueClassName="font-bold">
            {formatCents(overview.reserveCents)}
          </Kv>
          <Kv label="Reserve check">
            <span
              data-testid="treatment-reserve-check"
              data-verdict={reserve.verdict}
              className={`font-bold ${verdict.text}`}
            >
              {RESERVE_VERDICT_LABEL[reserve.verdict]}
            </span>
          </Kv>
          {/* The prototype's `.ratbox`, tinted by the verdict: the sentence
              the server wrote about why this claim is banded where it is. On
              the card rather than behind a tooltip because a verdict without
              its two figures is an instruction to trust it. */}
          <p
            data-testid="treatment-reserve-rationale"
            className={`mt-2 rounded border p-[8px_10px] text-[11.5px] leading-relaxed text-text ${verdict.box}`}
          >
            <b>Reserve check:</b> {reserve.rationale}
          </p>
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

      {/* The prototype's placement: below the two-column grid, above the
          timeline, on this variant and on investigation alike. */}
      <div className="mb-[10px]">
        <BenefitCard claim={claim} />
      </div>

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
