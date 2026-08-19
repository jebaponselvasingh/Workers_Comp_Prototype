import { PERSONAS, loginAs } from "../fixtures/login";
import { claimIdsWithStatus, isOshaRecordable } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 3.5 — Auto-Generated Action Checklist & Approval.
 *
 * The epic's last surface, in a browser: a handler opens a case file, reads
 * what the claim needs next, follows one of the links, and approves the
 * assessment without leaving the card. Four things are only true here and are
 * what this spec is organised around.
 *
 * 1. **The card renders the server's list, in the server's order.** Unit tests
 *    prove the generator ranks and caps; only this proves that what reaches the
 *    DOM is the same tuple, urgency tags and all, on a real claim from the real
 *    seed rather than a fixture written to suit the assertion.
 *
 * 2. **The deep link is real navigation.** "View Bill →" switches the detail
 *    pane to Bills & Payments — the tab state Epic 2 built, reused rather than
 *    routed around (AC 3) — and lands on a surface Story 3.3 filled in and 3.4
 *    put an approve button on.
 *
 * 3. **The seam is visible and inert — and the ones that have shipped are not.**
 *    Story 4.2 enabled the Diary row and Story 6.2 the Fraud one, so what
 *    remains disabled is the RTW letter (Story 6.5), rendering with a sentence
 *    naming the epic that will enable it. A disabled control is not a missing
 *    feature here: it is the cross-epic seam the readiness review verified, and
 *    the *point* is that a handler can see the work exists (NFR-3 — no dead
 *    clicks). Both halves are asserted, because the seam mechanism is only
 *    demonstrated by a seam that is still closed *and* one that opened.
 *
 * 4. **One approval moves three surfaces.** The header's status chip, the
 *    checklist row and the queue card all change from one command, because all
 *    three read the same status row — which is the narrative's own explanation
 *    of why completions are entity-backed and there is no action-state table.
 *
 * **What this spec deliberately does not assert.** Which six rows a given claim
 * produces. That is eleven rules over a hundred claims and it belongs in
 * `tests/test_action_checklist.py`, which can set a claim's state exactly; a
 * browser test that pinned a row list would fail on a reseed for a reason that
 * has nothing to do with the browser.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const JENNIFER = { name: "Jennifer Park", role: "supervisor" };

type Page = Parameters<typeof byTestId>[0];

interface Action {
  id: string;
  key: string;
  label: string;
  urgency: "high" | "medium" | "low";
  target: string;
  enabled: boolean;
  disabledReason: string | null;
  command: string | null;
  documentId: number | null;
  documentVersion: number | null;
}

interface Checklist {
  items: Action[];
  cap: number;
  paddingFloor: number;
  rulesVersion: number;
}

async function checklistOf(page: Page, claimId: string): Promise<Checklist> {
  const response = await page.request.get(`/api/claims/${claimId}/actions`);
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as Checklist;
}

async function openClaim(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
  await expect(byTestId(page, "actions-card")).toBeVisible();
}

/** The card row the server gave this `id`. Addressed by identity, never by index. */
function actionRow(page: Page, id: string) {
  return byTestId(page, "action-row").and(page.locator(`[data-action="${id}"]`));
}

/**
 * A claim of Kaya's whose checklist contains a given rule, or `undefined`.
 *
 * Searched rather than hardcoded for `tests/test_payment_approval.py`'s
 * reason: which seeded claim carries which trigger is a property of the
 * dataset.
 *
 * **Unbounded**, and it was capped at the first 25 until the follow-up review
 * of Story 6.2 (C2). The book has grown to 45, both callers assert
 * `expect(found).toBeDefined()`, and the 6-2 spec's own sibling search scans
 * the whole queue — so the cap was a way for two specs looking for the same
 * kind of claim to disagree, and for a real "no claim raises this row" to read
 * as one. The cost of removing it is a handful of extra checklist reads on a
 * seeded stack.
 */
/**
 * Every claim id in the caller's book, in id order.
 *
 * Asked of the server rather than assembled from `claimIdsWithStatus` calls,
 * because the SIU escalation trigger cuts across status: it fires on a stored
 * fraud score, so the claims that raise it are spread through the book and a
 * list built from one status would be a search that could only find them by
 * luck. Sorted so `claimWithRule` visits the same claims in the same order on
 * every run — the reproducibility AD-15 rests on.
 */
async function bookOf(page: Page): Promise<string[]> {
  const response = await page.request.get("/api/claims/queue?filter=all");
  expect(response.status(), await response.text()).toBe(200);
  const queue = (await response.json()) as {
    groups: Record<string, { items: { claimId: string }[] }>;
  };
  return Object.values(queue.groups)
    .flatMap((group) => group.items.map((card) => card.claimId))
    .sort();
}

async function claimWithRule(
  page: Page,
  claimIds: string[],
  key: string,
): Promise<{ claimId: string; action: Action } | undefined> {
  for (const claimId of claimIds) {
    const found = (await checklistOf(page, claimId)).items.find((item) => item.key === key);
    if (found) return { claimId, action: found };
  }
  return undefined;
}

test.describe("@story:3-5 @epic:3 auto-generated action checklist and approval", () => {
  test("@smoke a handler reads the checklist, follows a link, and approves the assessment", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    // A claim the approval command will accept. `ch_assessment_process` is the
    // AC's named case; the status is read from the seed rather than assumed of
    // a stage, because a treatment claim can already be approved.
    const awaiting = claimIdsWithStatus(KAYA.name, KAYA.role, "ch_assessment_process");
    const claimId = awaiting[0];
    const before = await checklistOf(page, claimId);

    // --- AC 1: the list is the server's, ranked and capped ---------------
    expect(before.items.length).toBeGreaterThanOrEqual(3);
    expect(before.items.length).toBeLessThanOrEqual(before.cap);
    expect(before.cap).toBe(6);

    await openClaim(page, claimId);

    const rows = byTestId(page, "action-row");
    await expect(rows).toHaveCount(before.items.length);
    // The order on screen is the order on the wire — no client-side sort. Row
    // *n* of the DOM is item *n* of the payload, asserted position by position
    // rather than as a set, because a card that re-ranked would still render
    // the same six rows.
    for (const [index, item] of before.items.entries()) {
      const row = rows.nth(index);
      await expect(row).toHaveAttribute("data-action", item.id);
      await expect(row.getByTestId("action-label")).toHaveText(item.label);
      await expect(row.getByTestId("action-urgency")).toHaveAttribute(
        "data-urgency",
        item.urgency,
      );
    }

    // --- AC 3: a deep link is a real tab switch --------------------------
    const bills = before.items.find((item) => item.target === "bills");
    if (bills) {
      await actionRow(page, bills.id).getByTestId("action-goto").click();
      await expect(byTestId(page, "bills-tab")).toBeVisible();
      await byTestId(page, "tab-overview").click();
      await expect(byTestId(page, "actions-card")).toBeVisible();
    }

    // --- AC 3: a seam control is disabled and says which epic ------------
    //
    // Whichever target is still a seam, found from the payload rather than
    // named: Story 4.2 opened `diary`, Story 6.2 opened `fraud`, and pinning a
    // target here would have made each of those a failure in a spec about the
    // checklist. What the assertion is about is the *contract* — a disabled
    // control carries the server's sentence, in the tooltip and in the DOM.
    const seam = before.items.find((item) => !item.enabled);
    if (seam) {
      const control = actionRow(page, seam.id).getByTestId("action-goto");
      await expect(control).toBeDisabled();
      await expect(control).toHaveAttribute("title", seam.disabledReason!);
      await expect(actionRow(page, seam.id)).toContainText(seam.disabledReason!);
    }

    // The fraud deep link Story 6.2 enabled is asserted in its own test below
    // — `the SIU escalation row's deep link is live and lands on AI Insights`.
    // It lived here as an `if (fraud) {…}` over this claim, which raises the SIU
    // row only by coincidence (the trigger reads a fraud score, not a status),
    // so the block passed by never running (review of Story 6.2, M12).

    // --- AC 4: approve, and watch three surfaces move --------------------
    await expect(byTestId(page, "badge-status")).toHaveText("CH Assessment Process");

    const approvalRow = actionRow(page, "assessment_approval:approve");
    await expect(approvalRow).toBeVisible();
    await approvalRow.getByTestId("action-command").click();

    // The header chip, from the response the command returned.
    await expect(byTestId(page, "badge-status")).toHaveText("CH Approved");
    // The row itself, from a *re-read* of the checklist — the trigger stopped
    // firing because the status row moved, which is the whole of AC 5's
    // mechanism applied to AC 4's command.
    await expect(actionRow(page, "assessment_approval:approve")).toHaveCount(0);

    // The queue card, which shows the same claim and re-ranks without it.
    const after = await checklistOf(page, claimId);
    expect(after.items.some((item) => item.key === "assessment_approval")).toBe(false);

    // --- AD-4: the approval is on the timeline, in the same transaction --
    await expect(
      byTestId(page, "timeline-entry")
        .filter({ hasText: /assessment approved/i })
        .first(),
    ).toBeVisible();
  });

  test("the checklist is capped and ranked on every claim in the book (AC 1)", async ({
    page,
  }) => {
    // One claim is not evidence that a cap holds. The rank check is the
    // stronger half: urgency is non-increasing down every list, which is the
    // property Story 5.4's "top action" column depends on.
    await loginAs(page, PERSONAS.handler);

    const claims = claimIdsWithStatus(KAYA.name, KAYA.role, "ch_approved").slice(0, 15);
    const order = ["high", "medium", "low"];

    for (const claimId of claims) {
      const payload = await checklistOf(page, claimId);
      expect(payload.items.length, claimId).toBeLessThanOrEqual(payload.cap);
      const ranks = payload.items.map((item) => order.indexOf(item.urgency));
      expect(ranks, `${claimId} came back unranked`).toEqual([...ranks].sort((a, b) => a - b));
    }
  });

  test("the SIU escalation row's deep link is live and lands on AI Insights", async ({ page }) => {
    // AC 3's seam half, from the other side. "View Fraud Indicators →" shipped
    // disabled with "Available with AI Insights — Epic 6"; Story 6.2 enabled it,
    // and this is the assertion this spec owes for that under AD-15 — amended,
    // never deleted.
    //
    // **A test of its own, and unconditional** (review of Story 6.2, M12). It
    // arrived inside the smoke test as `if (fraud) {…}` over the first claim
    // awaiting assessment — but the SIU trigger fires on a *fraud score*, not on
    // a status, so that claim raises the row only by coincidence and the block
    // was a guard that passed by never running. Searched here instead, and the
    // search's result is asserted: the seed is deterministic and the threshold
    // is a rule document, so "Kaya's book contains a claim over the SIU
    // threshold" has one answer per commit, and a build where it became `no`
    // should fail rather than quietly stop testing the link.
    await loginAs(page, PERSONAS.handler);

    const found = await claimWithRule(page, await bookOf(page), "siu_escalation");
    expect(found, "no claim in Kaya's book raises the SIU escalation row").toBeDefined();
    const { claimId, action } = found!;

    // The server owns `enabled` and the sentence, so the absence of a seam
    // reason is asserted on the payload rather than inferred from the DOM.
    expect(action.enabled).toBe(true);
    expect(action.disabledReason).toBeNull();

    await openClaim(page, claimId);
    const control = actionRow(page, action.id).getByTestId("action-goto");
    await expect(control).toBeEnabled();
    await control.click();

    // The tab state Epic 2 built, reused rather than routed around (AC 3's "no
    // bespoke routing") — `fraud` is the first target whose name is not also a
    // tab key, which is why `ClaimDetailPane` needed a branch of its own.
    await expect(byTestId(page, "insights-tab")).toBeVisible();
    await expect(byTestId(page, "tab-insights")).toHaveAttribute("aria-selected", "true");
    expect(new URL(page.url()).searchParams.get("claim")).toBe(claimId);

    // …and back, because a deep link that stranded the handler on the tab it
    // opened would be a worse affordance than the disabled row it replaced.
    await byTestId(page, "tab-overview").click();
    await expect(byTestId(page, "actions-card")).toBeVisible();
  });

  test("the same claim produces the same list twice (AD-2)", async ({ page }) => {
    // Determinism, from the outside. The generator is pure typed Python and
    // never LLM-originated; two reads of an unchanged claim must be byte-equal,
    // including the order — because Story 5.4 reads element 0 of this.
    await loginAs(page, PERSONAS.handler);
    const claimId = claimIdsWithStatus(KAYA.name, KAYA.role, "ch_approved")[0];

    const first = await checklistOf(page, claimId);
    const second = await checklistOf(page, claimId);

    expect(second).toEqual(first);
  });

  test("recording the OSHA 300 entry drops its own row (AC 5)", async ({ page }) => {
    // The completion is entity-backed: the row goes because `claim.osha_logged`
    // moved, not because a "done" flag was written beside it. That is the
    // narrative's "the worklist and the ledgers stay in sync because they read
    // the same status row", and it is only observable end to end.
    await loginAs(page, PERSONAS.handler);

    const recordable = claimIdsWithStatus(KAYA.name, KAYA.role, "ch_approved").filter(
      isOshaRecordable,
    );
    const found = await claimWithRule(page, recordable, "osha_log");
    expect(found, "the seed should contain a recordable claim with an unfiled entry").toBeDefined();

    await openClaim(page, found!.claimId);
    const row = actionRow(page, found!.action.id);
    await expect(row.getByTestId("action-command")).toHaveText("Mark Logged");
    await row.getByTestId("action-command").click();

    await expect(actionRow(page, found!.action.id)).toHaveCount(0);
    expect((await checklistOf(page, found!.claimId)).items.map((item) => item.key)).not.toContain(
      "osha_log",
    );
    await expect(byTestId(page, "actions-card")).toBeVisible();
  });

  test("a supervisor sees the checklist and cannot act on it (AD-7)", async ({ page }) => {
    // Reading what is outstanding is a read; the writes are handler-only. The
    // 403 must be identical for a claim in the supervisor's book and one that
    // does not exist, or the difference answers "does this claim exist?".
    await loginAs(page, PERSONAS.scopedSupervisor);

    const claimId = claimIdsWithStatus(JENNIFER.name, JENNIFER.role, "ch_assessment_process")[0];
    const payload = await checklistOf(page, claimId);
    expect(payload.items.length).toBeGreaterThan(0);

    const refused = await page.request.post(`/api/claims/${claimId}/assessment/approval`, {
      data: { expectedVersion: 1 },
    });
    const missing = await page.request.post("/api/claims/WC-99999/assessment/approval", {
      data: { expectedVersion: 1 },
    });

    expect(refused.status()).toBe(403);
    expect(missing.status()).toBe(403);
    expect(await refused.json()).toEqual(await missing.json());
  });

  test("a claim outside the caller's book has no checklist to read (AD-7)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const theirs = claimIdsWithStatus("Dante Reyes", "handler", "ch_approved").find(
      (claimId) => !claimIdsWithStatus(KAYA.name, KAYA.role, "ch_approved").includes(claimId),
    );
    const outside = await page.request.get(`/api/claims/${theirs}/actions`);
    const absent = await page.request.get("/api/claims/WC-99999/actions");

    expect(outside.status()).toBe(404);
    expect(absent.status()).toBe(404);
    // Same `type`, so the two refusals are indistinguishable to a caller
    // walking the id space.
    expect((await outside.json()).type).toBe((await absent.json()).type);
  });

  test("approving twice is refused, with the fresh claim attached (AD-4, AD-9)", async ({
    page,
  }) => {
    // The compare-and-swap from the outside: the second request carries the
    // version the first consumed. Its body is the whole case file, which is
    // what lets the card render the state that won without a second round trip.
    await loginAs(page, PERSONAS.handler);

    // **`initial`, not `ch_assessment_process`.** Both are in the approvable
    // set, and Kaya's book holds exactly one claim in the second — which the
    // smoke test above has already approved by the time this runs. A test that
    // took "the second one" would address `undefined` and fail on a 422 that
    // says nothing about compare-and-swap.
    //
    // **Why the earlier approval is still there:** `db-reset.setup.ts` is a
    // `setup` project that the whole `stories` project depends on, so the
    // database is reset **once before every spec file**, not before each one —
    // an earlier version of this comment said per-file and was wrong (code
    // review, 2026-08-17). What keeps this deterministic is `workers: 1` and
    // `fullyParallel: false`: tests run in declaration order, so "already
    // approved" is a fact about this file's own order rather than a hope.
    // Anything here that mutates a claim must therefore assume its
    // predecessors have run — which is why every test in this file picks its
    // claim by *current status* rather than by position.
    const claimId = claimIdsWithStatus(KAYA.name, KAYA.role, "initial")[0];
    const detail = await page.request.get(`/api/claims/${claimId}`);
    const version = (await detail.json()).version as number;

    const first = await page.request.post(`/api/claims/${claimId}/assessment/approval`, {
      data: { expectedVersion: version },
    });
    const second = await page.request.post(`/api/claims/${claimId}/assessment/approval`, {
      data: { expectedVersion: version },
    });

    expect(first.status()).toBe(200);
    expect(second.status()).toBe(409);
    expect((await second.json()).claim.header.status).toBe("ch_approved");
  });
});
