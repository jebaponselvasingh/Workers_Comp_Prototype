/**
 * Story 3.3 — the Bills & Payments tab renders what it was sent.
 *
 * Every assertion here is of the same kind: the payload said X, the screen
 * says X. That is deliberate and it is the point of the tab — the summary, the
 * chips, the totals and the verdict are all decided on the server, so what a
 * component test can prove is that none of them was recomputed, reordered or
 * quietly relabelled on the way to the DOM. `noDerivation.test.ts` proves the
 * arithmetic is absent as *source*; this proves the rendering is faithful.
 *
 * The one place that is not true is the loading, error and empty branches
 * (NFR-3), which are states the server cannot describe.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_FINANCIALS,
  CLAIM_FINANCIALS_UNPAID,
  ME_HANDLER,
  type StubRoute,
  stubApi,
} from "@/test/api-mock";

import { LINE_ITEM_STATUS_LABEL, RESERVE_VERDICT_LABEL, SCHEDULE_STATUS_LABEL } from "../labels";
import { BillsTab } from "./BillsTab";

const CLAIM_ID = "WC-20017";
const SUMMARY = CLAIM_FINANCIALS.body.summary;

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderTab(claimFinancials: StubRoute = CLAIM_FINANCIALS) {
  stubApi({ me: ME_HANDLER, claimFinancials });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <BillsTab claimId={CLAIM_ID} />
    </QueryClientProvider>,
  );
}

// --- NFR-3: the three states the server cannot describe -------------------

test("a request in flight renders a skeleton rather than a zero", async () => {
  // "pending" never settles, which is the only way a loading state is
  // observable — a stub that resolved immediately would make a skeleton and a
  // zeroed summary indistinguishable.
  renderTab("pending");

  expect(await screen.findByTestId("bills-skeleton")).toBeInTheDocument();
  expect(screen.queryByTestId("bills-tab")).not.toBeInTheDocument();
});

test("a failed request says so instead of rendering an empty tab", async () => {
  // A 403 rather than a 500: the shared client retries 5xx twice with backoff,
  // so a 500 would race the retry timer instead of testing the branch (Story
  // 1.4's lesson, restated on the pane's own error test).
  renderTab({
    status: 403,
    body: { type: "/problems/forbidden", title: "Forbidden", status: 403, detail: "no" },
  });

  const alert = await screen.findByTestId("bills-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(screen.queryByTestId("bills-tab")).not.toBeInTheDocument();
});

test("a claim with no expenses says so rather than showing a bare heading", async () => {
  renderTab(CLAIM_FINANCIALS_UNPAID);

  expect(await screen.findByTestId("expenses-card-empty")).toHaveTextContent(
    "No expenses filed for this claim",
  );
});

// --- AC 1: the summary ----------------------------------------------------

test("the four paycards show the server's figures", async () => {
  renderTab();

  expect(await screen.findByTestId("summary-paid-to-date")).toHaveTextContent("$12,738");
  expect(screen.getByTestId("summary-total-projected")).toHaveTextContent("$57,738");
  expect(screen.getByTestId("summary-reserve")).toHaveTextContent("$45,000");
  expect(screen.getByTestId("summary-reserve-check")).toHaveTextContent(
    RESERVE_VERDICT_LABEL[CLAIM_FINANCIALS.body.reserveCheck.verdict],
  );
});

test("the metrics row shows the four figures the story names", async () => {
  renderTab();

  expect(await screen.findByTestId("metric-weekly")).toHaveTextContent("$5,679");
  expect(screen.getByTestId("metric-installments")).toHaveTextContent(
    `${SUMMARY.installmentsPaid} of ${SUMMARY.weekCount}`,
  );
  expect(screen.getByTestId("metric-next-due")).toHaveTextContent(SUMMARY.nextPaymentDue!);
  expect(screen.getByTestId("metric-bills-on-file")).toHaveTextContent(
    String(SUMMARY.billsOnFile),
  );
});

test("the cost bar's three widths are the server's percentages", async () => {
  renderTab();

  const split = SUMMARY.costSplit!;
  expect(await screen.findByTestId("cost-bar-indemnity")).toHaveStyle({
    width: `${split.indemnityPct}%`,
  });
  expect(screen.getByTestId("cost-bar-medical")).toHaveStyle({ width: `${split.medicalPct}%` });
  expect(screen.getByTestId("cost-bar-expense")).toHaveStyle({ width: `${split.expensePct}%` });
});

test("a claim with nothing disbursed gets the sentence, not a zero-width bar", async () => {
  // The server sends `costSplit: null` — "this claim has no composition" — and
  // the card must render the prototype's sentence rather than three segments
  // of zero width, which is a *drawing* of a fact the sentence states better.
  renderTab(CLAIM_FINANCIALS_UNPAID);

  expect(await screen.findByTestId("summary-no-payments")).toHaveTextContent(
    "No payments disbursed yet",
  );
  expect(screen.queryByTestId("cost-bar")).not.toBeInTheDocument();
});

test("the reserve rationale is the server's sentence, rendered as sent", async () => {
  renderTab();

  expect(await screen.findByTestId("summary-reserve-rationale")).toHaveTextContent(
    CLAIM_FINANCIALS.body.reserveCheck.rationale,
  );
});

test("the card renders the verdict it was sent even when the figures contradict it", async () => {
  // A *possible* payload cannot distinguish "rendered what it was sent" from
  // "happened to agree" — Story 3.2's device, applied to the second surface
  // that shows this verdict. An exposure of $63,000 against a $45,000 reserve
  // is 140%, which is unambiguously light; the payload says `heavy`, and the
  // card must say "Reserve Heavy".
  renderTab({
    status: 200,
    body: {
      ...CLAIM_FINANCIALS.body,
      reserveCheck: {
        ...CLAIM_FINANCIALS.body.reserveCheck,
        verdict: "heavy",
        projectedRemainingCents: 6_300_000,
        reserveCents: 4_500_000,
      },
    },
  });

  expect(await screen.findByTestId("summary-reserve-check")).toHaveTextContent("Reserve Heavy");
});

test("a ledger-sourced breakdown says so, and a live one says nothing", async () => {
  // The second review's other finding: on a settled claim the paid columns
  // answer the summary while the schedule and line-item cards below show their
  // own rows, and the seeded figures were never reconciled — so the legend can
  // read "Medical $475" above a bills card reading "$6,144 paid". Both are
  // true of different records; what was missing was any sign that they *are*
  // different records. The note appears only in that case, because it is the
  // only one where the cards below disagree.
  renderTab({
    status: 200,
    body: {
      ...CLAIM_FINANCIALS.body,
      summary: { ...SUMMARY, paidFromColumns: true },
    },
  });

  await expect(screen.findByTestId("summary-paid-provenance")).resolves.toHaveTextContent(
    "carrier's ledger",
  );
});

test("a live breakdown carries no provenance note", async () => {
  renderTab();
  await screen.findByTestId("bills-tab");

  expect(SUMMARY.paidFromColumns).toBe(false);
  expect(screen.queryByTestId("summary-paid-provenance")).not.toBeInTheDocument();
});

// --- AC 2: the schedule ---------------------------------------------------

test("every scheduled week renders with its own status chip", async () => {
  renderTab();
  await screen.findByTestId("bills-tab");

  const rows = screen.getAllByTestId("schedule-row");
  expect(rows).toHaveLength(CLAIM_FINANCIALS.body.schedule.length);

  CLAIM_FINANCIALS.body.schedule.forEach((week, index) => {
    const row = rows[index];
    expect(row).toHaveAttribute("data-week", String(week.weekNo));
    expect(within(row).getByText(`Wk ${week.weekNo}`)).toBeInTheDocument();
    expect(within(row).getByText(SCHEDULE_STATUS_LABEL[week.status])).toBeInTheDocument();
  });
});

test("the schedule renders the weeks in the order the server sent them", async () => {
  // Week order is the server's — `ORDER BY week_no`, not `id`, because a week
  // inserted late by a refresh carries a higher id than weeks it precedes. A
  // component that sorted would be re-deciding a total order the table already
  // guarantees.
  renderTab();
  await screen.findByTestId("bills-tab");

  const rendered = screen
    .getAllByTestId("schedule-row")
    .map((row) => Number(row.getAttribute("data-week")));

  expect(rendered).toEqual(CLAIM_FINANCIALS.body.schedule.map((w) => w.weekNo));
});

test("the schedule sub-heading states the two figures the Overview card shows", async () => {
  // AC 4's pair, on this side of the jump-link. `ClaimDetailPane.test.tsx`
  // asserts they match what the treatment card renders.
  renderTab();

  expect(await screen.findByTestId("schedule-disbursed")).toHaveTextContent("$11,359");
  expect(screen.getByTestId("schedule-scheduled")).toHaveTextContent("$40,000");
});

// --- AC 3: the line items -------------------------------------------------

test("the bills card states the three figures the server computed", async () => {
  renderTab();

  expect(await screen.findByTestId("bills-card-heading-figures")).toHaveTextContent(
    "3 on file ($1,275 of $5,655 paid)",
  );
});

test("bills and expenses render with their own status labels", async () => {
  renderTab();
  await screen.findByTestId("bills-tab");

  const billRows = screen.getAllByTestId("bills-card-row");
  expect(billRows).toHaveLength(3);
  CLAIM_FINANCIALS.body.bills.items.forEach((item, index) => {
    expect(billRows[index]).toHaveAttribute("data-category", item.category);
    expect(within(billRows[index]).getByText(item.label)).toBeInTheDocument();
    expect(
      within(billRows[index]).getByText(LINE_ITEM_STATUS_LABEL[item.status]),
    ).toBeInTheDocument();
  });

  expect(screen.getAllByTestId("expenses-card-row")).toHaveLength(2);
});

test("a pending-submission bill does not read as payment scheduled", async () => {
  // The prototype's label bug, refused. `BILL_STATUS_LABEL` maps
  // `PendingSubmission` to the string "Payment Scheduled", so a bill nobody
  // has filed reads on screen as money already queued for disbursement. The
  // fixture carries one of each state that matters here.
  renderTab();
  await screen.findByTestId("bills-tab");

  const pending = screen
    .getAllByTestId("bills-card-row")
    .find((row) => row.getAttribute("data-status") === "pending_submission");

  expect(pending).toBeDefined();
  expect(within(pending!).getByText("Pending Submission")).toBeInTheDocument();
  expect(within(pending!).queryByText("Payment Scheduled")).not.toBeInTheDocument();
});

// --- the read-only sheet --------------------------------------------------

test("clicking a week opens a sheet describing it, with no approve button", async () => {
  // Story 3.3 ships no status-mutating command, so an approve control here
  // would be a button that either does nothing or writes through a path that
  // does not exist. 3.4 adds the command and the button together.
  renderTab();
  await screen.findByTestId("bills-tab");

  await userEvent.click(screen.getAllByTestId("schedule-week-button")[2]);

  const sheet = await screen.findByTestId("line-item-sheet");
  expect(within(sheet).getByTestId("line-sheet-title")).toHaveTextContent("Week 3");
  expect(within(sheet).getByTestId("line-sheet")).toHaveAttribute("data-kind", "week");
  expect(within(sheet).getByTestId("line-sheet-note")).toHaveTextContent("falls due");
  expect(within(sheet).queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
});

test("clicking a bill opens its own sheet naming the category", async () => {
  renderTab();
  await screen.findByTestId("bills-tab");

  await userEvent.click(within(screen.getAllByTestId("bills-card-row")[1]).getByRole("button"));

  const sheet = await screen.findByTestId("line-item-sheet");
  expect(within(sheet).getByTestId("line-sheet")).toHaveAttribute("data-kind", "bill");
  expect(within(sheet).getByTestId("line-sheet-title")).toHaveTextContent(
    "Diagnostic Imaging (MRI/CT/X-Ray)",
  );
  // The category's *label*, not its token.
  expect(within(sheet).getByText("Diagnostic Imaging")).toBeInTheDocument();
});

test("the sheet closes on Escape", async () => {
  // Radix's dialog, not the prototype's modal — which closes on ✕ and backdrop
  // but not Escape, and traps no focus (UX-DR11).
  renderTab();
  await screen.findByTestId("bills-tab");
  await userEvent.click(screen.getAllByTestId("schedule-week-button")[0]);
  await screen.findByTestId("line-item-sheet");

  await userEvent.keyboard("{Escape}");

  expect(screen.queryByTestId("line-item-sheet")).not.toBeInTheDocument();
});
