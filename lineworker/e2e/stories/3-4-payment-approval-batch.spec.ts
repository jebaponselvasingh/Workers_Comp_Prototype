import { PERSONAS, loginAs } from "../fixtures/login";
import { claimIdsInStage, firstClaimInStage, formatCents } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 3.4 — Payment Approval & Batch.
 *
 * The whole payment lifecycle in a browser: a handler approves a week, the
 * batch disburses it, and the figures move. Three things are worth asserting
 * here rather than in a unit test, and the spec is organised around them.
 *
 * 1. **The two commands are one story on screen.** Approval sets
 *    `payment_scheduled` and only the batch sets `paid`; a handler sees that
 *    as a chip changing twice, and the sentence between the two changes ("✓
 *    Scheduled for next payment batch — Friday, Aug 21") is the promise the
 *    batch then keeps. Server tests prove each transition; only this proves
 *    they compose into something a person can follow.
 *
 * 2. **The figures are recomputed, not patched.** Paid To Date and
 *    Installments Paid move after the batch because the derivations read the
 *    rows — so the assertions below compare a *before* reading against an
 *    *after* reading of the same surfaces, rather than checking a number
 *    against a constant.
 *
 * 3. **The refusals are the ones a handler can act on.** A supervisor gets a
 *    403 that says nothing about the claim; a stale approval gets a 409 whose
 *    message distinguishes "somebody else approved it" from "the batch already
 *    paid it", inline, at the control (UX-DR11 — no blocking dialog).
 *
 * **How the batch is triggered.** Through `POST /api/admin/payment-batch`,
 * which is served only under `ENV=e2e` (`api/routers/admin.py`) and which
 * calls the *real* command — there is no second code path. AD-15 requires a
 * deterministic trigger and forbids waiting on a wall clock; the scheduler is
 * off in this profile for the same reason.
 *
 * **What this spec deliberately does not assert.** That the batch runs on a
 * Tuesday. The cadence is deployment configuration, its arithmetic has its own
 * unit tests, and a browser test that waited for a weekday would be the
 * flakiest thing in the suite.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

interface ScheduleRow {
  weekNo: number;
  status: string;
  version: number;
  approvable: boolean;
  amountCents: number;
}

interface Financials {
  summary: {
    paidToDateCents: number;
    installmentsPaid: number;
    weekCount: number;
    nextBatchDate: string;
    disbursedIndemnityCents: number;
  };
  schedule: ScheduleRow[];
  bills: { items: { id: number; status: string; version: number; approvable: boolean }[] };
}

async function financialsOf(page: Page, claimId: string): Promise<Financials> {
  const response = await page.request.get(`/api/claims/${claimId}/financials`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Financials;
}

async function openClaim(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
}

async function openBillsTab(page: Page): Promise<void> {
  await byTestId(page, "tab-bills").click();
  await expect(byTestId(page, "bills-tab")).toBeVisible();
}

/** Trigger the real batch command. Returns its run summary. */
async function runBatch(page: Page): Promise<{
  rowsPaid: number;
  rowsPaidByEntity: Record<string, number>;
  claimsTouched: string[];
}> {
  const response = await page.request.post("/api/admin/payment-batch");
  expect(response.status(), await response.text()).toBe(200);
  return await response.json();
}

/**
 * The row a schedule table renders for one week.
 *
 * `.and()` rather than `.filter({has})`: `data-week` is on the `<tr>` itself,
 * and `has` matches *descendants* — so the filter version selected nothing and
 * waited thirty seconds to say so. Test-id first, attribute second, never a
 * CSS class (AD-15's selector policy).
 */
function weekRow(page: Page, weekNo: number) {
  return byTestId(page, "schedule-row").and(page.locator(`[data-week="${weekNo}"]`));
}

test.describe("@story:3-4 @epic:3 payment approval and batch", () => {
  test("@smoke a handler approves a week, the batch disburses it, and the figures move", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    // An intake claim: stage decides before the calendar does, so every week
    // is `pending_approval` however old the injury is — which makes "there is
    // something to approve" a property of the seed rather than of today's date.
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    const before = await financialsOf(page, claimId);
    const week = before.schedule.find((row) => row.approvable);
    expect(week, "an intake claim should have an approvable week").toBeDefined();
    expect(week!.status).toBe("pending_approval");
    expect(before.summary.installmentsPaid).toBe(0);

    await openClaim(page, claimId);
    await openBillsTab(page);

    // --- AC 1: approve ---------------------------------------------------
    await weekRow(page, week!.weekNo).getByTestId("schedule-week-button").click();
    const sheet = byTestId(page, "line-item-sheet");
    await expect(sheet).toBeVisible();
    await sheet.getByTestId("approve-payment").click();

    // The chip and the sheet move together, because both read one payload.
    await expect(sheet.getByTestId("approve-scheduled")).toContainText(
      "Scheduled for next payment batch",
    );
    await expect(sheet.getByTestId("approve-payment")).toHaveCount(0);
    await expect(weekRow(page, week!.weekNo)).toHaveAttribute(
      "data-status",
      "payment_scheduled",
    );
    await expect(weekRow(page, week!.weekNo)).toContainText("Payment Scheduled");
    // **Not paid.** The story's one-sentence invariant, on screen: approval
    // queues money, it does not disburse it.
    await expect(weekRow(page, week!.weekNo)).not.toContainText("Paid");

    // The batch date the sheet promises is the one the summary states, because
    // both render the same server-computed field.
    await page.keyboard.press("Escape");
    const noted = await byTestId(page, "summary-next-batch").textContent();
    expect(noted?.trim()).toBeTruthy();

    // --- AC 2: the batch --------------------------------------------------
    const run = await runBatch(page);
    expect(run.rowsPaid).toBeGreaterThanOrEqual(1);
    expect(run.claimsTouched).toContain(claimId);

    await page.reload();
    await openBillsTab(page);

    // --- AC 3: paid, and the figures recomputed ---------------------------
    await expect(weekRow(page, week!.weekNo)).toHaveAttribute("data-status", "paid");
    await weekRow(page, week!.weekNo).getByTestId("schedule-week-button").click();
    await expect(byTestId(page, "line-item-sheet").getByTestId("approve-paid")).toContainText(
      "Payment already made",
    );
    await page.keyboard.press("Escape");

    const after = await financialsOf(page, claimId);
    expect(after.summary.installmentsPaid).toBe(before.summary.installmentsPaid + 1);
    expect(after.summary.disbursedIndemnityCents).toBe(
      before.summary.disbursedIndemnityCents + week!.amountCents,
    );
    expect(after.summary.paidToDateCents).toBeGreaterThan(before.summary.paidToDateCents);

    await expect(byTestId(page, "metric-installments")).toHaveText(
      `${after.summary.installmentsPaid} of ${after.summary.weekCount}`,
    );
    await expect(byTestId(page, "summary-paid-to-date")).toHaveText(
      formatCents(after.summary.paidToDateCents),
    );

    // --- AC 1 and 3: the audit trail, as a handler sees it ----------------
    // The approval appended a timeline event in the same transaction as the
    // write (AD-4), so it is on the case file's Overview — which is the only
    // place a handler can see that anything was audited at all.
    await byTestId(page, "tab-overview").click();
    await expect(
      byTestId(page, "timeline-entry").filter({ hasText: /approved for payment/i }).first(),
    ).toBeVisible();
  });

  test("the batch is idempotent — a second run pays nothing (AC 2)", async ({ page }) => {
    // Idempotence *by construction*: the second run selects nothing because
    // the first run's rows are no longer `payment_scheduled`. There is no run
    // ledger to consult and therefore nothing that can drift from the rows.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    const payload = await financialsOf(page, claimId);
    const week = payload.schedule.find((row) => row.approvable)!;

    const approved = await page.request.post(`/api/claims/${claimId}/payments/approvals`, {
      data: { kind: "week", targetId: week.weekNo, expectedVersion: week.version },
    });
    expect(approved.status()).toBe(200);

    const first = await runBatch(page);
    const second = await runBatch(page);

    expect(first.rowsPaid).toBeGreaterThanOrEqual(1);
    expect(second.rowsPaid).toBe(0);
    expect(second.claimsTouched).toEqual([]);
  });

  test("the batch pays only what was approved (AC 2)", async ({ page }) => {
    // The status guard, from the outside: a claim nobody has approved anything
    // on comes through a batch run completely unchanged. This is what stops
    // "run the batch" from meaning "pay the whole portfolio".
    await loginAs(page, PERSONAS.handler);

    // A *different* intake claim from the ones the tests above approve on. The
    // AD-15 reset runs once per spec file, so state carries between tests here
    // — which is what makes "nothing was approved on this claim" a real
    // precondition rather than an assumption.
    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "intake")[1];
    const before = await financialsOf(page, claimId);
    expect(before.schedule.every((row) => row.status !== "payment_scheduled")).toBe(true);

    await runBatch(page);

    const after = await financialsOf(page, claimId);
    expect(after.schedule.map((row) => row.status)).toEqual(
      before.schedule.map((row) => row.status),
    );
    expect(after.summary.paidToDateCents).toBe(before.summary.paidToDateCents);
  });

  test("a stale approval is refused inline, with the reason (AC 1, AD-9)", async ({ page }) => {
    // The 409 path a handler actually reaches: two tabs, or a sheet left open
    // across a batch run. What is asserted is that the refusal appears *at the
    // control*, names which conflict it was, and leaves the figures refreshed
    // rather than showing the state the failed approval was written against.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    const payload = await financialsOf(page, claimId);
    const week = payload.schedule.find((row) => row.approvable)!;

    await openClaim(page, claimId);
    await openBillsTab(page);
    await weekRow(page, week.weekNo).getByTestId("schedule-week-button").click();
    const sheet = byTestId(page, "line-item-sheet");
    await expect(sheet.getByTestId("approve-payment")).toBeVisible();

    // Somebody else approves it and the batch pays it, out from under the open
    // sheet. The version the sheet is holding is now two behind.
    const other = await page.request.post(`/api/claims/${claimId}/payments/approvals`, {
      data: { kind: "week", targetId: week.weekNo, expectedVersion: week.version },
    });
    expect(other.status()).toBe(200);
    await runBatch(page);

    await sheet.getByTestId("approve-payment").click();

    await expect(sheet.getByTestId("approve-error")).toContainText("disbursed in a batch");
    // The fresh payload the 409 carried was installed, so the sheet now shows
    // what actually happened rather than what it was about to attempt.
    await expect(sheet.getByTestId("approve-paid")).toBeVisible();
    await expect(sheet.getByTestId("approve-payment")).toHaveCount(0);
    // Inline at the control, never a native dialog (UX-DR11, NFR-3).
    await expect(sheet).toBeVisible();
  });

  test("a supervisor cannot approve, and learns nothing from the refusal (AD-7)", async ({
    page,
  }) => {
    // Role gates capability; scope gates visibility. The 403 is answered
    // before the claim is looked up, so it is identical for a claim inside the
    // supervisor's book, one outside it, and one that does not exist.
    await loginAs(page, PERSONAS.scopedSupervisor);

    const inScope = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    const refused = await page.request.post(`/api/claims/${inScope}/payments/approvals`, {
      data: { kind: "week", targetId: 1, expectedVersion: 1 },
    });
    const invented = await page.request.post("/api/claims/WC-99999/payments/approvals", {
      data: { kind: "week", targetId: 1, expectedVersion: 1 },
    });

    expect(refused.status()).toBe(403);
    expect(invented.status()).toBe(403);
    expect((await refused.json()).detail).toBe((await invented.json()).detail);
  });

  test("the approve button appears exactly where the server permits it (AC 1)", async ({
    page,
  }) => {
    // `approvable` is a field on the row, not a status comparison the browser
    // makes — the approvable set differs between a week (three statuses) and a
    // line item (one), and a client that reimplemented either would offer a
    // button the command refuses.
    //
    // Walked over *every* bill on the claim rather than a hand-picked one, so
    // the assertion is "the button follows the server's answer" rather than
    // "this particular status has no button" — which would pass on a seed that
    // happened not to contain the interesting case.
    await loginAs(page, PERSONAS.handler);

    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "intake")[2];
    const payload = await financialsOf(page, claimId);
    expect(payload.bills.items.length).toBeGreaterThan(0);

    await openClaim(page, claimId);
    await openBillsTab(page);

    for (const bill of payload.bills.items) {
      await byTestId(page, "bills-card-row")
        .and(page.locator(`[data-status="${bill.status}"]`))
        .first()
        .getByRole("button")
        .click();

      const sheet = byTestId(page, "line-item-sheet");
      await expect(sheet).toBeVisible();
      await expect(sheet.getByTestId("approve-payment")).toHaveCount(bill.approvable ? 1 : 0);
      await page.keyboard.press("Escape");
      await expect(sheet).toBeHidden();
    }
  });

  test("the summary states when the next batch runs (AC 2)", async ({ page }) => {
    // The sentence Story 3.3 deliberately withheld — "shipping it before the
    // mechanism would promise a handler a disbursement schedule nothing in
    // this system yet runs". The mechanism exists now, and the date is the
    // server's, computed from the deployed cadence.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const payload = await financialsOf(page, claimId);

    await openClaim(page, claimId);
    await openBillsTab(page);

    await expect(byTestId(page, "summary-batch-note")).toContainText(
      "Approved payments are disbursed in the next scheduled batch run",
    );
    // Strictly in the future: the sentence sits beside an approval the handler
    // is about to make, and today's batch may already have run.
    expect(new Date(payload.summary.nextBatchDate).getTime()).toBeGreaterThan(Date.now());
  });
});
