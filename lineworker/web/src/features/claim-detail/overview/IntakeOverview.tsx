/**
 * The intake variant: intake summary, reported injury, the document
 * checklist, and the full case timeline (FR-DET-1).
 *
 * The checklist is the one card here with no prototype precedent — the
 * prototype's intake overview has no checklist at all. The epics' acceptance
 * criterion ("Received/Missing per required form") is therefore the contract
 * for it, and the required set is a JDM parameter the server compares the
 * claim's documents against. This component renders rows.
 */
import type { IntakeOverviewData } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { CardGrid, CaseCard, Kv, RISK_TEXT, TimelineCard, formatDate } from "../Cards";
import { COMM_STATUS_LABEL, DOC_TYPE_LABEL, SEVERITY_WORD } from "../labels";

export function IntakeOverview({ overview }: { overview: IntakeOverviewData }) {
  return (
    <>
      <CardGrid>
        <CaseCard title="Intake summary" testId="intake-summary">
          <Kv label="Employee">
            {overview.workerName} ({overview.employeeBusinessId})
          </Kv>
          <Kv label="Role / Plant">
            {overview.workerRole} · {overview.plant}
          </Kv>
          <Kv label="Date of injury">{formatDate(overview.doi)}</Kv>
          <Kv label="FROI filed">{formatDate(overview.froiDate)}</Kv>
          <Kv label="Assigned">{formatDate(overview.assignDate)}</Kv>
          <Kv label="Handler">{overview.handlerName}</Kv>
          <Kv label="Communication status">
            {COMM_STATUS_LABEL[overview.commStatus]}
          </Kv>
        </CaseCard>

        <CaseCard title="Reported injury details" testId="intake-injury">
          <Kv label="Injury type">{overview.injuryType}</Kv>
          <Kv label="Cause">{overview.cause}</Kv>
          <Kv label="Body part">{overview.bodyPart}</Kv>
          <Kv
            label="Initial severity"
            testId="intake-severity"
            valueClassName={`font-bold ${RISK_TEXT[overview.risk]}`}
          >
            {SEVERITY_WORD[overview.risk]} ({overview.severityScore}/100)
          </Kv>
          <Kv label="AWW">{formatCents(overview.awwCents)}</Kv>
          <Kv label="Initial reserve">{formatCents(overview.reserveCents)}</Kv>
        </CaseCard>
      </CardGrid>

      <div className="mb-[10px]">
        <CaseCard title="📋 Intake document checklist" testId="intake-checklist">
          {overview.checklist.length === 0 ? (
            // The rule document may legitimately require nothing; that is a
            // different fact from "we have not checked" and says so.
            <p data-testid="intake-checklist-empty" className="text-[11.5px] text-faint">
              No documents are required at intake.
            </p>
          ) : (
            <ul className="flex flex-col">
              {overview.checklist.map((row) => (
                <li
                  key={row.docType}
                  data-testid="checklist-row"
                  data-doc-type={row.docType}
                  data-received={row.received}
                  className="flex items-baseline justify-between gap-3 border-b border-hairline py-[5px] text-[11.5px] last:border-b-0"
                >
                  <span className="min-w-0">{DOC_TYPE_LABEL[row.docType]}</span>
                  <span
                    data-testid="checklist-state"
                    className={`shrink-0 rounded-[2px] px-[6px] py-px text-[9.5px] font-bold tracking-[0.3px] uppercase ${
                      row.received ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"
                    }`}
                  >
                    {row.received ? "Received" : "Missing"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CaseCard>
      </div>

      <TimelineCard title="Case timeline" entries={overview.timeline} />
    </>
  );
}
