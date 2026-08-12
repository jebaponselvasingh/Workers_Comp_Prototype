/**
 * The settled variant: the closed banner, the final payout breakdown, the
 * claim outcome, and the full summary of actions taken (FR-DET-1).
 *
 * **The banner omits the settlement date, and that is the data being
 * honest.** Every seeded settlement event carries the literal string
 * `Closed` where a date belongs, so the server has no date to send. The
 * prototype renders "reached final settlement on **Closed**"; here the
 * clause is dropped when there is nothing to put in it. If a real settlement
 * date ever lands in the data, this sentence gains it without a change.
 */
import type { SettledOverviewData } from "@/api/claims";
import { formatCents } from "@/lib/money";

import {
  CardGrid,
  CaseCard,
  CostBar,
  CostLegendItem,
  Kv,
  TimelineCard,
  formatDate,
} from "../Cards";
import { DISABILITY_LABEL, RETURN_STATUS_LABEL } from "../labels";

export function SettledOverview({ overview }: { overview: SettledOverviewData }) {
  return (
    <>
      <section
        data-testid="settled-banner"
        className="mb-[10px] rounded-lg border border-ok border-l-4 bg-ok-soft p-3"
      >
        <h3 className="mb-1 font-display text-[10.5px] font-bold tracking-[0.4px] text-ok uppercase">
          ✅ Claim settled &amp; closed
        </h3>
        <p className="text-[12.5px] text-text">
          This claim reached final settlement
          {overview.settlementDate !== null && (
            <>
              {" "}
              on{" "}
              <strong data-testid="settled-date">{formatDate(overview.settlementDate)}</strong>
            </>
          )}
          . Total incurred:{" "}
          <strong data-testid="settled-total">{formatCents(overview.totalPaidCents)}</strong>. No
          further reserve exposure or open actions remain.
        </p>
      </section>

      <CardGrid>
        <CaseCard title="Final payout breakdown" testId="settled-payout">
          <Kv label="Indemnity paid">{formatCents(overview.paidIndemnityCents)}</Kv>
          <Kv label="Medical paid">{formatCents(overview.paidMedicalCents)}</Kv>
          <Kv label="Expenses paid">{formatCents(overview.paidExpenseCents)}</Kv>
          <Kv label="Total incurred" valueClassName="font-bold">
            {formatCents(overview.totalPaidCents)}
          </Kv>
          <Kv label="Final reserve">{formatCents(overview.reserveCents)}</Kv>
          {overview.costSplit !== null && (
            <CostBar
              split={overview.costSplit}
              showExpense
              legend={
                <>
                  <CostLegendItem swatch="bg-steel">Indemnity</CostLegendItem>
                  <CostLegendItem swatch="bg-brand">Medical</CostLegendItem>
                  <CostLegendItem swatch="bg-faint">Expense</CostLegendItem>
                </>
              }
            />
          )}
        </CaseCard>

        <CaseCard title="Claim outcome" testId="settled-outcome">
          <Kv label="Disability type">{DISABILITY_LABEL[overview.disability]}</Kv>
          <Kv label="Return status">{RETURN_STATUS_LABEL[overview.returnStatus]}</Kv>
          <Kv label="Days to settlement">{overview.daysToSettlement}</Kv>
          <Kv label="Litigation">
            {overview.litigationFlag ? "Yes — attorney represented" : "No"}
          </Kv>
          <Kv label="Handler">{overview.handlerName}</Kv>
        </CaseCard>
      </CardGrid>

      <TimelineCard
        title="📝 Summary of actions taken"
        entries={overview.timeline}
        markDone
        testId="settled-timeline"
      />
    </>
  );
}
