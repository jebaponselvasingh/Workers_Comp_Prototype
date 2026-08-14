import { PERSONAS, loginAs } from "../fixtures/login";
import {
  LINE_ITEM_STATUS_LABEL,
  RESERVE_VERDICT_LABEL,
  SCHEDULE_STATUS_LABEL,
  claimIdsInStage,
  expectedBillTotals,
  expectedBills,
  expectedExpenseTotals,
  expectedPaidToDate,
  expectedReserveCheck,
  expectedWeekCount,
  firstClaimInStage,
  formatCents,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 3.3 — Bills & Indemnity Payment Schedule.
 *
 * The third slice of the financial engine, and the tab a handler actually
 * spends money from. Two things are worth asserting in a browser rather than
 * in a unit test, and the spec is organised around them:
 *
 * 1. **The figures are the server's.** Every expectation comes from
 *    `fixtures/seed.ts`, which restates the rules — the schedule's clamp, the
 *    effective-breakdown fallback, the paid/unpaid partition — as a second
 *    implementation over the same seed files the stack migrated with. A spec
 *    that read the totals off the response would agree with any rule at all.
 *
 * 2. **AC 4: the jump-link lands on the same numbers it left.** That is a
 *    claim about two *surfaces*, fetched by two requests under two query keys,
 *    and it is the one thing no server test can finish proving. The `@smoke`
 *    path below walks it: read the Overview card, click through, assert the
 *    identical figures on the Bills tab.
 *
 * **What the oracle does and does not prove**, restated from the 3.2 spec
 * because it still applies: it is a second implementation, so it catches an
 * arithmetic slip or a boundary flipped from strict to inclusive; it shares
 * the server's assumption that an elapsed week on an approved claim counts as
 * disbursed, so it cannot be evidence for that assumption.
 *
 * **What this story deliberately does not ship**, and the spec asserts absent:
 * any control that changes a status. The approve button belongs to Story 3.4
 * with the command behind it, so the line-item sheet is read-only — and a
 * button that did nothing would be worse than no button.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

async function openClaim(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
}

async function openBillsTab(page: Page): Promise<void> {
  await byTestId(page, "tab-bills").click();
  await expect(byTestId(page, "bills-tab")).toBeVisible();
}

async function financialsOf(page: Page, claimId: string): Promise<Record<string, never>> {
  const response = await page.request.get(`/api/claims/${claimId}/financials`);
  expect(response.status()).toBe(200);
  return await response.json();
}

test.describe("@story:3-3 @epic:3 bills and indemnity payment schedule", () => {
  test("@smoke the Bills tab shows the financial picture, and the jump-link keeps its figures", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const reserve = expectedReserveCheck(claimId);
    const paid = expectedPaidToDate(claimId);

    await openClaim(page, claimId);

    // --- AC 4, first half: what the Overview card says -------------------
    // Read *before* the jump, so the comparison below is between two
    // renderings a handler actually sees in sequence rather than between two
    // reads of one payload.
    const overviewPaid = await byTestId(page, "treatment-indemnity-paid").textContent();
    const overviewVerdict = await byTestId(page, "treatment-reserve-check").textContent();
    expect(overviewPaid).toBe(
      `${formatCents(reserve.disbursedIndemnityCents)} of ` +
        `${formatCents(reserve.scheduledIndemnityCents)}`,
    );
    // The medical row beside it, corrected by the second review: it read the
    // `paid_medical` column, which is $0 on every open claim, directly above
    // the live indemnity figure and directly above this jump-link. Both rows
    // now come from the rows the verdict was computed from.
    await expect(byTestId(page, "treatment-medical-paid")).toHaveText(
      formatCents(reserve.disbursedMedicalCents),
    );
    expect(reserve.disbursedMedicalCents).toBeGreaterThan(0);

    // --- AC 4: the link is a tab switch, and the tab is built now --------
    await byTestId(page, "treatment-bills-link").click();
    await expect(byTestId(page, "tab-bills")).toHaveAttribute("aria-selected", "true");
    await expect(byTestId(page, "bills-tab")).toBeVisible();
    // The Epic 3 seam this story removed.
    await expect(byTestId(page, "tab-empty-bills")).toHaveCount(0);

    // --- AC 1: the four paycards ----------------------------------------
    await expect(byTestId(page, "summary-paid-to-date")).toHaveText(
      formatCents(paid.totalCents),
    );
    await expect(byTestId(page, "summary-total-projected")).toHaveText(
      formatCents(paid.totalCents + reserve.reserveCents),
    );
    await expect(byTestId(page, "summary-reserve")).toHaveText(
      formatCents(reserve.reserveCents),
    );

    // --- AC 4, second half: the same figures on both surfaces ------------
    await expect(byTestId(page, "summary-reserve-check")).toHaveText(
      RESERVE_VERDICT_LABEL[reserve.verdict],
    );
    expect(overviewVerdict?.trim()).toBe(RESERVE_VERDICT_LABEL[reserve.verdict]);
    await expect(byTestId(page, "schedule-disbursed")).toHaveText(
      formatCents(reserve.disbursedIndemnityCents),
    );
    await expect(byTestId(page, "schedule-scheduled")).toHaveText(
      formatCents(reserve.scheduledIndemnityCents),
    );

    // --- AC 2: the week-by-week schedule ---------------------------------
    await expect(byTestId(page, "payment-schedule")).toBeVisible();
    await expect(byTestId(page, "schedule-row")).toHaveCount(expectedWeekCount(claimId));

    // --- AC 3: the bills, with the heading the server computed -----------
    const bills = expectedBillTotals(claimId);
    await expect(byTestId(page, "bills-card-heading-figures")).toHaveText(
      `${bills.count} on file (${formatCents(bills.paidCents)} of ` +
        `${formatCents(bills.totalCents)} paid)`,
    );
    await expect(byTestId(page, "bills-card-row")).toHaveCount(bills.count);
  });

  test("every scheduled week carries the status the rule gives it (AC 2)", async ({ page }) => {
    // The schedule is where a date-driven rule is easiest to get subtly wrong,
    // so this walks every row of a claim rather than sampling one. The
    // statuses come from the payload and the *labels* from the restated map,
    // which is what makes this a check on the UI's half of the enum
    // convention rather than a re-read of the response.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const payload = await financialsOf(page, claimId);
    const schedule = payload.schedule as unknown as {
      weekNo: number;
      amountCents: number;
      status: string;
    }[];

    await openClaim(page, claimId);
    await openBillsTab(page);

    const rows = byTestId(page, "schedule-row");
    await expect(rows).toHaveCount(schedule.length);

    for (const [index, week] of schedule.entries()) {
      const row = rows.nth(index);
      await expect(row).toHaveAttribute("data-week", String(week.weekNo));
      await expect(row).toContainText(`Wk ${week.weekNo}`);
      await expect(row).toContainText(formatCents(week.amountCents));
      await expect(row).toContainText(SCHEDULE_STATUS_LABEL[week.status]);
    }
  });

  test("the summary's figures are the sums of the rows beneath them (AC 1)", async ({
    page,
  }) => {
    // The identity a totals row has to satisfy to be worth showing, checked
    // against the *oracle* rather than against the payload's own rows — so a
    // server that summed consistently but wrongly still fails.
    await loginAs(page, PERSONAS.handler);

    for (const claimId of claimIdsInStage(KAYA.name, KAYA.role, "treatment")) {
      const payload = await financialsOf(page, claimId);
      const summary = payload.summary as unknown as Record<string, number>;
      const paid = expectedPaidToDate(claimId);
      const bills = expectedBillTotals(claimId);
      const expenses = expectedExpenseTotals(claimId);

      expect(summary.paidToDateCents, `${claimId} paid to date`).toBe(paid.totalCents);
      expect(summary.paidIndemnityCents, `${claimId} indemnity`).toBe(paid.indemnityCents);
      expect(summary.paidMedicalCents, `${claimId} medical`).toBe(paid.medicalCents);
      expect(summary.paidExpenseCents, `${claimId} expense`).toBe(paid.expenseCents);
      expect(summary.billsOnFile, `${claimId} bills on file`).toBe(bills.count);
      expect(summary.weekCount, `${claimId} weeks`).toBe(expectedWeekCount(claimId));
      expect((payload.bills as unknown as Record<string, number>).paidCents).toBe(
        bills.paidCents,
      );
      expect((payload.expenses as unknown as Record<string, number>).paidCents).toBe(
        expenses.paidCents,
      );
    }
  });

  test("an open claim shows what was actually disbursed, not its empty paid columns", async ({
    page,
  }) => {
    // The prototype's own rule and the reason this story has a fallback at
    // all: every open seeded claim has `paid_indemnity = paid_medical =
    // paid_expense = 0`, so a summary that read the columns would report
    // "$0 paid to date" on a claim with an elapsed schedule and paid bills.
    // That is the contradiction Story 3.2's review found one surface over.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    expect(expectedPaidToDate(claimId).fromColumns).toBe(false);

    await openClaim(page, claimId);
    await openBillsTab(page);

    await expect(byTestId(page, "summary-paid-to-date")).not.toHaveText("$0");
  });

  test("the line-item sheet is read-only until Story 3.4 (AC 3)", async ({ page }) => {
    // The absence is the assertion. This story ships no status-mutating
    // command, so an "Approve Payment" control would be a button that either
    // does nothing or writes through a path that does not exist — and the
    // prototype's own modal has exactly that button, which is what makes
    // "port the sheet, not the button" worth pinning.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const bill = expectedBills(claimId)[0];

    await openClaim(page, claimId);
    await openBillsTab(page);
    await byTestId(page, "bills-card-row").first().getByRole("button").click();

    const sheet = byTestId(page, "line-item-sheet");
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText(bill.label);
    await expect(sheet).toContainText(LINE_ITEM_STATUS_LABEL[bill.status]);
    await expect(sheet.getByRole("button", { name: /approve/i })).toHaveCount(0);

    // Radix's dialog, not the prototype's modal: Escape closes it and focus is
    // trapped while it is open (UX-DR11).
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });

  test("a settled claim's expenses are all paid, and the Overview's figure is a different one", async ({
    page,
  }) => {
    // **The story's Dev Notes ask for a claim to be verified here, and it does
    // not hold.** The readiness review moved `expense` into this story because
    // it "feeds the settled-stage payout breakdown rendered by Story 2.2".
    // That card reads `claim.paid_expense`, and all 62 seeded settled claims
    // already carry a non-zero one from the Story 1.2 seed — so the surface
    // was never empty and this table is not what fills it.
    //
    // The two figures are also different numbers, because the prototype
    // invented its line items and its paid columns independently. Both are on
    // screen, one tab apart, and this test pins that state rather than
    // asserting an agreement that does not exist — the reconciliation is a
    // data decision recorded in `deferred-work.md`.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "settled");
    const expenses = expectedExpenseTotals(claimId);
    expect(expenses.count, "a settled claim should carry expenses").toBeGreaterThan(0);
    // Every one of them paid — the prototype's settled branch, and what makes
    // the breakdown's expense figure a real number rather than a zero.
    expect(expenses.paidCents).toBe(expenses.totalCents);

    await openClaim(page, claimId);
    await openBillsTab(page);

    await expect(byTestId(page, "expenses-card-heading-figures")).toHaveText(
      `${expenses.count} on file (${formatCents(expenses.paidCents)} of ` +
        `${formatCents(expenses.totalCents)} paid)`,
    );

    // The Epic 2 surface the review named, populated from its own column and
    // showing its own figure. Asserted non-empty rather than equal, which is
    // the honest assertion: they are two unreconciled numbers about one claim.
    await byTestId(page, "tab-overview").click();
    await expect(byTestId(page, "settled-payout")).toBeVisible();
  });

  test("a claim awaiting approval shows its schedule and no disbursements (NFR-3)", async ({
    page,
  }) => {
    // The intake branch: stage before calendar, so every week is pending
    // approval however old the injury is — and with nothing disbursed the cost
    // bar has no composition to draw, which is the sentence rather than three
    // zero-width segments.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    await openClaim(page, claimId);
    await openBillsTab(page);

    await expect(byTestId(page, "schedule-row")).toHaveCount(expectedWeekCount(claimId));
    const rows = byTestId(page, "schedule-row");
    const count = await rows.count();
    for (let index = 0; index < count; index += 1) {
      await expect(rows.nth(index)).toHaveAttribute("data-status", "pending_approval");
    }

    // No week is due, so the metric says so rather than naming a date for
    // money nobody has authorised.
    await expect(byTestId(page, "metric-next-due")).toHaveText("—");
  });

  test("the tab refuses a claim outside the caller's book (AD-7)", async ({ page }) => {
    // The financial endpoint has to refuse exactly as the case file does, or
    // the *difference* between the two refusals is itself the answer to "does
    // this claim exist?".
    await loginAs(page, PERSONAS.handler);

    const outside = claimIdsInStage("David Bline", "supervisor", "treatment").find(
      (id) => !claimIdsInStage(KAYA.name, KAYA.role, "treatment").includes(id),
    );
    expect(outside, "no claim outside Kaya's book").toBeDefined();

    const financials = await page.request.get(`/api/claims/${outside}/financials`);
    const detail = await page.request.get(`/api/claims/${outside}`);

    expect(financials.status()).toBe(404);
    expect(detail.status()).toBe(404);
    expect((await financials.json()).detail).toBe((await detail.json()).detail);
  });
});
