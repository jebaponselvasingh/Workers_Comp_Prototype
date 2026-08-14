/**
 * The Bills & Payments tab (Story 3.3, UX-DR5) — the prototype's `billsHTML`.
 *
 * Four stacked cards in the prototype's order — financial summary, week-by-week
 * schedule, medical bills, expenses — plus the read-only sheet any row opens.
 * Story 2.2 shipped this tab as an explicit empty state naming Epic 3; that
 * seam is gone from `DetailTabs` and this is what replaced it.
 *
 * **A single column, not `CardGrid`.** The schedule is a four-column table
 * twenty rows deep and the summary is a four-up card row; side by side they
 * both lose. The prototype stacks them for the same reason the Documents tab
 * does.
 *
 * **This tab fetches its own payload** rather than reading the case file, and
 * that is the one structural decision worth stating. The schedule, the bills
 * and the expenses are far more data than the Overview needs, on a case file
 * the console re-reads after every command — so the cost is paid by the tab
 * that shows it, and the query is enabled only while this panel is mounted
 * (`DetailTabs` mounts it only when selected).
 *
 * The two surfaces still cannot disagree, which is AC 4. Not because they
 * share a cache entry — they do not — but because the server computes both
 * from one assembler over one set of rows: `reserveCheck` on the case file and
 * `reserveCheck` here are the same computation, and the paid figures the
 * Overview card shows are the same block's `disbursedIndemnityCents` and
 * `scheduledIndemnityCents` that the schedule sub-heading renders. Identity by
 * construction rather than by two components being kept in step.
 *
 * **Which row's sheet is open is local UI state** (AD-9), like the tab
 * selection above it: not a server resource, and not shareable the way
 * `?claim=` is.
 */
import { useState } from "react";

import { useClaimFinancials } from "@/api/claims";

import { FinancialSummaryCard } from "./FinancialSummaryCard";
import { LineItemDialog, type SheetTarget } from "./LineItemDialog";
import { LineItemsCard } from "./LineItemsCard";
import { PaymentScheduleCard } from "./PaymentScheduleCard";

function BillsSkeleton() {
  return (
    <div data-testid="bills-skeleton" aria-hidden className="flex flex-col gap-[10px]">
      <span className="block h-28 w-full animate-pulse rounded-lg bg-surface-2" />
      <span className="block h-48 w-full animate-pulse rounded-lg bg-surface-2" />
      <span className="block h-24 w-full animate-pulse rounded-lg bg-surface-2" />
    </div>
  );
}

export function BillsTab({ claimId }: { claimId: string }) {
  const financials = useClaimFinancials(claimId);
  const [target, setTarget] = useState<SheetTarget | null>(null);

  if (financials.isPending) return <BillsSkeleton />;

  if (financials.isError) {
    // No 404 branch, unlike `ClaimDetailPane`. This tab is only reachable from
    // a case file that has already loaded, so "not in this caseload" is not a
    // state a handler can arrive at here — and if the claim vanished under
    // them, the pane behind this panel says so.
    return (
      <p role="alert" data-testid="bills-error" className="text-[11.5px] text-error">
        ⚠ This claim&apos;s financial detail could not be loaded. Try again in a moment.
      </p>
    );
  }

  const { summary, schedule, bills, expenses, reserveCheck } = financials.data;

  return (
    <div data-testid="bills-tab" className="flex min-w-0 flex-col">
      <FinancialSummaryCard summary={summary} reserveCheck={reserveCheck} />

      <PaymentScheduleCard
        schedule={schedule}
        summary={summary}
        onOpenWeek={(weekNo) => {
          const week = schedule.find((row) => row.weekNo === weekNo);
          if (week) setTarget({ kind: "week", week });
        }}
      />

      <LineItemsCard
        testId="bills-card"
        icon="🧾"
        title="Medical bills"
        items={bills.items}
        count={bills.count}
        totalCents={bills.totalCents}
        paidCents={bills.paidCents}
        // Unreachable against seeded data — every claim carries at least four
        // bills — but a card with a heading over nothing reads as a load that
        // never finished, so it gets a sentence (NFR-3).
        emptyMessage="No medical bills on file for this claim."
        onOpen={(id) => {
          const item = bills.items.find((row) => row.id === id);
          if (item) setTarget({ kind: "bill", item });
        }}
      />

      <LineItemsCard
        testId="expenses-card"
        icon="🚗"
        title="Expenses"
        items={expenses.items}
        count={expenses.count}
        totalCents={expenses.totalCents}
        paidCents={expenses.paidCents}
        emptyMessage="No expenses filed for this claim."
        onOpen={(id) => {
          const item = expenses.items.find((row) => row.id === id);
          if (item) setTarget({ kind: "expense", item });
        }}
      />

      <LineItemDialog claimId={claimId} target={target} onClose={() => setTarget(null)} />
    </div>
  );
}
