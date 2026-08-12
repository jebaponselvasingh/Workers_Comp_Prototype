/**
 * The case header (UX-DR4): who, which claim, what happened, and how bad.
 *
 * Four rows, exactly as the prototype's `.chead` draws them:
 *   1. the injured worker's name
 *   2. `claimId · role · employer · state` (claim id in mono)
 *   3. 🩹 injury summary — type — body part, cause · ICD-10 · severity
 *   4. the badge row: stage pill, then the conditional flags
 *
 * …with the risk gauge on the right.
 *
 * **The badges are payload booleans, not a rule.** Each is switched on by
 * one field the server decided; nothing here combines two flags or compares
 * a score against a cut-off. The fraud badge shows the score because the
 * prototype does, and it shows it *uncoloured* — the prototype tints it at
 * 55 and 35, two numbers that appear in no rule document, and putting an
 * unversioned threshold in the browser is the thing AD-8 and `risk` exist to
 * prevent. Recorded as a discrepancy in the story's Dev Agent Record.
 */
import type { CaseHeaderData } from "@/api/claims";

import { RiskGauge } from "./RiskGauge";
import { SEVERITY_WORD, STAGE_LABEL } from "./labels";

/** The prototype's `.stag` palette, shared with the queue card's stage pill. */
const STAGE_PILL: Record<CaseHeaderData["stage"], string> = {
  intake: "bg-steel-soft text-steel",
  investigation: "bg-warn-soft text-warn",
  treatment: "bg-ok-soft text-ok",
  settled: "bg-surface-2 text-faint",
};

function Badge({
  children,
  className,
  testId,
}: {
  children: React.ReactNode;
  className: string;
  testId: string;
}) {
  return (
    <span
      data-testid={testId}
      className={`rounded-[3px] px-[6px] py-[2px] text-[9.5px] font-bold tracking-[0.3px] uppercase ${className}`}
    >
      {children}
    </span>
  );
}

function Dot() {
  return (
    <span aria-hidden className="inline-block size-[3px] rounded-full bg-border" />
  );
}

export function CaseHeader({ header }: { header: CaseHeaderData }) {
  return (
    <header
      data-testid="case-header"
      className="mb-[10px] flex items-start justify-between gap-4 border-b border-border pb-3"
    >
      <div className="min-w-0">
        <h1 data-testid="case-header-name" className="font-display text-[19px] font-bold">
          {header.workerName}
        </h1>

        <div className="mt-[3px] flex flex-wrap items-center gap-[6px] text-[11px] text-muted-text">
          <span data-testid="case-header-claim-id" className="font-mono">
            {header.claimId}
          </span>
          <Dot />
          <span>{header.workerRole}</span>
          <Dot />
          <span data-testid="case-header-employer">{header.employerName}</span>
          <Dot />
          <span>{header.state}</span>
        </div>

        <p data-testid="case-header-injury" className="mt-1 text-[11.5px] text-faint">
          <span aria-hidden>🩹 </span>
          {header.injuryType} — {header.bodyPart}, {header.cause.toLowerCase()} · ICD-10{" "}
          {header.icd} · {SEVERITY_WORD[header.risk]} severity
        </p>

        <div data-testid="case-header-badges" className="mt-2 flex flex-wrap items-center gap-[6px]">
          <Badge testId="badge-stage" className={STAGE_PILL[header.stage]}>
            {STAGE_LABEL[header.stage]}
          </Badge>
          {header.fraudFlag && (
            <Badge testId="badge-fraud" className="bg-warn-soft text-warn">
              ⚠ Fraud Score: {header.fraudScore}
            </Badge>
          )}
          {header.litigationFlag && (
            <Badge testId="badge-litigation" className="bg-error-soft text-error">
              ⚖ Litigation
            </Badge>
          )}
          {header.surgeryRequired && (
            <Badge testId="badge-surgery" className="bg-surface-2 text-muted-text">
              🔪 Surgery
            </Badge>
          )}
          {header.oshaRecordable && (
            <Badge testId="badge-osha" className="bg-error-soft text-error">
              OSHA Rec.
            </Badge>
          )}
        </div>
      </div>

      <RiskGauge risk={header.risk} />
    </header>
  );
}
