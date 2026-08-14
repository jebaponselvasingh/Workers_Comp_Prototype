/**
 * The claim financial summary — four paycards, the cost bar, the reserve
 * ratbox and the metrics row (Story 3.3, AC 1).
 *
 * The prototype's first `billsHTML` card, ported. **Every figure is the
 * server's**: this component formats cents and picks a colour, and computes
 * nothing — the totals, the percentages and the verdict all arrive decided
 * (AD-1, AD-10), which is what lets the treatment Overview card show the same
 * numbers without the two being kept in step by hand.
 *
 * **The "next batch: Tuesdays & Fridays" line the prototype puts here is
 * deliberately absent.** It belongs to Story 3.4 with the batch it describes;
 * shipping the sentence before the mechanism would promise a handler a
 * disbursement schedule nothing in this system yet runs.
 */
import type { FinancialSummary, ReserveCheck } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { CostBar, CostLegendItem, formatDate } from "../Cards";
import { RESERVE_VERDICT_LABEL } from "../labels";
import { VERDICT_ACCENT } from "../reserveAccent";

function PayCard({
  label,
  children,
  testId,
  valueClassName = "",
}: {
  label: string;
  children: React.ReactNode;
  testId: string;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-2 p-[10px] text-center">
      <div
        data-testid={testId}
        className={`font-mono text-[15px] leading-tight font-bold ${valueClassName}`}
      >
        {children}
      </div>
      <div className="mt-1 text-[10px] tracking-[0.3px] text-muted-text uppercase">{label}</div>
    </div>
  );
}

function Metric({
  label,
  children,
  testId,
}: {
  label: string;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <div>
      <div data-testid={testId} className="font-mono text-[12px] font-semibold text-text">
        {children}
      </div>
      <div className="text-[10px] text-muted-text">{label}</div>
    </div>
  );
}

export function FinancialSummaryCard({
  summary,
  reserveCheck,
}: {
  summary: FinancialSummary;
  reserveCheck: ReserveCheck;
}) {
  const verdict = VERDICT_ACCENT[reserveCheck.verdict];

  return (
    <section
      data-testid="financial-summary"
      className="mb-[10px] rounded-lg border border-border bg-surface p-3"
    >
      <h3 className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
        💵 Claim financial summary
      </h3>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <PayCard
          label="Total Paid To Date"
          testId="summary-paid-to-date"
          valueClassName="text-ok"
        >
          {formatCents(summary.paidToDateCents)}
        </PayCard>
        <PayCard label="Total Claim (Projected)" testId="summary-total-projected">
          {formatCents(summary.totalClaimProjectedCents)}
        </PayCard>
        <PayCard label="Reserve Remaining" testId="summary-reserve">
          {formatCents(summary.reserveCents)}
        </PayCard>
        {/* The verdict word rather than a figure, which is why this one card
            is not mono and is a size smaller — "Awaiting Bill Data" is four
            times the width of a dollar amount. */}
        <PayCard
          label="Reserve Check"
          testId="summary-reserve-check"
          valueClassName={`font-display !text-[12px] ${verdict.text}`}
        >
          <span data-verdict={reserveCheck.verdict}>
            {RESERVE_VERDICT_LABEL[reserveCheck.verdict]}
          </span>
        </PayCard>
      </div>

      {summary.costSplit === null ? (
        /* NFR-3: the designed state for a claim with no disbursements, not a
           blank. The prototype's own sentence, and it names the figure that
           *is* known rather than showing a bar of three zero-width segments. */
        <p data-testid="summary-no-payments" className="mt-[10px] text-[11px] text-faint">
          No payments disbursed yet — reserve of {formatCents(summary.reserveCents)} held
          against projected exposure.
        </p>
      ) : (
        <CostBar
          split={summary.costSplit}
          showExpense
          legend={
            <>
              <CostLegendItem swatch="bg-steel">
                Indemnity {formatCents(summary.paidIndemnityCents)} (
                {summary.costSplit.indemnityPct}%)
              </CostLegendItem>
              <CostLegendItem swatch="bg-brand">
                Medical {formatCents(summary.paidMedicalCents)} ({summary.costSplit.medicalPct}
                %)
              </CostLegendItem>
              <CostLegendItem swatch="bg-faint">
                Expenses {formatCents(summary.paidExpenseCents)} (
                {summary.costSplit.expensePct}%)
              </CostLegendItem>
            </>
          }
        />
      )}

      {/* **Which source the breakdown came from, said out loud** (code review,
          2026-08-14). On a settled claim the paid columns answer, while the
          schedule and the bills cards below this one show their own rows — and
          the seeded figures were never reconciled with each other, so the
          legend can read "Medical $475" above a bills card reading "$6,144
          paid". Both are true of different records of one claim; what was
          missing was any indication that they *are* different records, which
          left two figures 40px apart looking like one of them was wrong.
          Rendered only when the columns answered, because that is the only
          case where the cards below disagree. The reconciliation itself is a
          data decision, recorded in `deferred-work.md`. */}
      {summary.paidFromColumns && (
        <p data-testid="summary-paid-provenance" className="mt-2 text-[10.5px] text-faint">
          Paid figures above are the carrier&apos;s ledger for this closed claim. The schedule
          and line items below are the claim&apos;s own recorded rows and are totalled
          separately.
        </p>
      )}

      {/* The prototype's `.ratbox`: the sentence the server wrote about why
          this claim is banded where it is, tinted by the verdict. On the card
          rather than behind a tooltip because a verdict without its figures is
          an instruction to trust it. */}
      <p
        data-testid="summary-reserve-rationale"
        className={`mt-[10px] rounded border p-[8px_10px] text-[11.5px] leading-relaxed text-text ${verdict.box}`}
      >
        <b>Reserve check:</b> {reserveCheck.rationale}
      </p>

      <div
        data-testid="summary-metrics"
        className="mt-[10px] grid grid-cols-2 gap-2 border-t border-hairline pt-[10px] sm:grid-cols-4"
      >
        <Metric label="Weekly Indemnity" testId="metric-weekly">
          {formatCents(summary.weeklyIndemnityCents)}
        </Metric>
        <Metric label="Installments Paid" testId="metric-installments">
          {summary.installmentsPaid} of {summary.weekCount}
        </Metric>
        <Metric label="Next Payment Due" testId="metric-next-due">
          {formatDate(summary.nextPaymentDue)}
        </Metric>
        <Metric label="Bills On File" testId="metric-bills-on-file">
          {summary.billsOnFile}
        </Metric>
      </div>
    </section>
  );
}
