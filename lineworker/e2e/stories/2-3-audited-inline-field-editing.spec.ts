import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  RECOVERY_WINDOWS,
  claimIdsOutsideScopeOf,
  expectedTimelineFor,
  firstClaimInStage,
  otherBodyKey,
  otherRecoveryWindow,
  recoveryToken,
  seededField,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 2.3 — Audited Inline Field Editing.
 *
 * The first write path in the build, so this spec is as much about what the
 * server refuses as about what it accepts: a stale version, a value off the
 * vocabulary, a role without the capability, a claim outside the book.
 * Nothing is stubbed — the browser drives the real SPA, the API runs the
 * real compare-and-swap against the real database, and the expectations come
 * from `fixtures/seed.ts` reading the same seed files the stack migrated
 * with.
 *
 * **The audit row itself is asserted in pytest, not here.** There is no API
 * that reads `audit_event` — deliberately; nothing in the product needs one
 * yet, and inventing a read endpoint so a spec could use it would be the
 * test dictating the surface. `server/tests/test_claim_edit.py` asserts the
 * row, its fixed AD-4 schema and its before/after diff against the same
 * database this stack runs. What *is* asserted here is the half a browser
 * can see: the edit persisted, the version moved, and the timeline event the
 * command emitted in the same transaction is on the case file.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec
 * *file*, and Kaya has exactly one investigation claim, so every test below
 * reads the current version before it writes — as a client does.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

async function openClaim(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "investigation-injury")).toBeVisible();
}

/**
 * The case file as the API answers it — the `version` a client would send.
 *
 * Only the one field is typed. The spec has no business restating the whole
 * payload shape (that is the generated client's job, in `web/`); what it
 * needs is the number the compare-and-swap runs on.
 */
async function readClaim(page: Page, claimId: string): Promise<{ version: number }> {
  const response = await page.request.get(`/api/claims/${claimId}`);
  expect(response.status()).toBe(200);
  return response.json();
}

/** Type into an inline field and commit it the way a handler does. */
async function commitText(page: Page, field: string, value: string): Promise<void> {
  const input = byTestId(page, `edit-${field}`);
  await input.fill(value);
  await input.press("Enter");
}

test.describe("@story:2-3 @epic:2 audited inline field editing", () => {
  test("@smoke an inline edit persists, logs itself, and reaches the queue card", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const before = expectedTimelineFor(claimId).length;
    const seeded = seededField(claimId, "injury_type");
    const edited = `${seeded} (corrected)`;

    await openClaim(page, claimId);
    await expect(byTestId(page, "edit-injuryType")).toHaveValue(seeded);

    await commitText(page, "injuryType", edited);

    // --- AC 1: it is on the claim, not just on the screen ---------------
    await expect(byTestId(page, "case-header-injury")).toContainText(edited);
    await page.reload();
    await expect(byTestId(page, "edit-injuryType")).toHaveValue(edited);

    // --- AC 4: the command emitted a timeline event in the same
    // transaction, and the case file shows it without a manual refresh ---
    const entries = byTestId(page, "timeline-entry");
    await expect(entries).toHaveCount(before + 1);
    await expect(entries.last()).toContainText("Injury details updated (injury type)");
    await expect(entries.last()).toContainText("edit");

    // --- AC 3: the queue card and the case file agree ------------------
    const card = page.locator(`[data-testid="queue-card"][data-claim-id="${claimId}"]`);
    await expect(card).toContainText(edited);
  });

  test("the selects offer exactly the server's vocabulary (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    await openClaim(page, claimId);

    // Compared against the spec's own copy of the prototype's lists: a
    // select built from something else would be able to offer a value the
    // command answers 422 to, which the handler would meet as a failure
    // rather than as an option that was never there.
    const bodyParts = byTestId(page, "edit-bodyKey").locator("option");
    await expect(bodyParts).toHaveCount(11);
    await expect(byTestId(page, "edit-bodyKey")).toHaveValue(seededField(claimId, "body_key"));

    // The *labels* are the browser's and the *values* are the server's
    // tokens — the split the code review introduced, asserted on both sides
    // so a UI that rendered raw tokens (or a server that went back to
    // shipping display strings) fails here.
    const windows = byTestId(page, "edit-recovery").locator("option");
    await expect(windows).toHaveCount(RECOVERY_WINDOWS.length);
    await expect(windows).toHaveText(RECOVERY_WINDOWS.map((window) => window.label));
    await expect(windows.first()).toHaveAttribute("value", RECOVERY_WINDOWS[0].token);
    await expect(byTestId(page, "edit-recovery")).toHaveValue(
      recoveryToken(seededField(claimId, "recovery")),
    );
  });

  test("choosing a body part relabels the claim from the server's mapping (AC 1)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const target = otherBodyKey(claimId);

    await openClaim(page, claimId);
    await byTestId(page, "edit-bodyKey").selectOption(target.key);

    // The label the header shows is the *server's*, derived from the key —
    // the seeded wording ("Wrist(s) & Hand(s)") and the diagram's labels are
    // two vocabularies, and the browser must not be the one mapping between
    // them.
    await expect(byTestId(page, "case-header-injury")).toContainText(target.label);
    await page.reload();
    await expect(byTestId(page, "edit-bodyKey")).toHaveValue(target.key);
  });

  test("a concurrent edit is refused and the fresh value is rendered inline (AC 2)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");

    await openClaim(page, claimId);
    const held = await readClaim(page, claimId);

    // Somebody else's edit lands between this pane's read and its save — the
    // stale write the compare-and-swap exists for. Issued through the
    // request context, which shares the session, so it is a genuine second
    // writer rather than a mocked response.
    const winner = await page.request.patch(`/api/claims/${claimId}`, {
      data: { expectedVersion: held.version, cause: "Won The Race" },
    });
    expect(winner.status()).toBe(200);

    await commitText(page, "cause", "Lost The Race");

    // Rolled back, replaced with what the server holds, and told why —
    // inline at the field, with no native dialog anywhere (NFR-3, UX-DR11).
    await expect(byTestId(page, "edit-cause-conflict")).toContainText("Updated by someone else");
    await expect(byTestId(page, "edit-cause")).toHaveValue("Won The Race");
    await expect(page.locator("[role=dialog], [role=alertdialog]")).toHaveCount(0);

    // …and the refusal did not write: one edit event on the claim, not two.
    const events = byTestId(page, "timeline-entry");
    await expect(events.last()).toContainText("Injury details updated (cause)");

    // The next attempt carries the version the conflict taught the client,
    // so the handler's own retry succeeds where a silent one would have
    // overwritten the other writer.
    //
    // **Asserted after a reload.** The previous version checked the field's
    // value and the absence of the notice immediately, both of which the
    // *optimistic* state satisfies before the request has left the browser —
    // so the test could not have detected a retry that failed (code review).
    // A reload can only show what the database holds.
    await commitText(page, "cause", "Second Attempt");
    await expect(byTestId(page, "edit-cause-conflict")).toHaveCount(0);

    await page.reload();
    await expect(byTestId(page, "edit-cause")).toHaveValue("Second Attempt");
    const afterRetry = await readClaim(page, claimId);
    expect(afterRetry.version).toBe(held.version + 2);
  });

  test("a value the command refuses is reported at the field (AC 2, NFR-3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    await openClaim(page, claimId);

    await commitText(page, "icd", "not-a-code");

    await expect(byTestId(page, "edit-icd-invalid")).toContainText("ICD-10");
    // What was typed is kept so it can be corrected, and the claim is
    // unchanged behind it.
    await expect(byTestId(page, "edit-icd")).toHaveValue("not-a-code");
    await expect(page.locator("[role=dialog], [role=alertdialog]")).toHaveCount(0);

    await page.reload();
    await expect(byTestId(page, "edit-icd")).toHaveValue(seededField(claimId, "icd"));
  });

  test("the API refuses a read-only role and an out-of-scope claim (AD-7)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const held = await readClaim(page, claimId);

    // A handler's own claim, but somebody else's book: the same 404 the GET
    // answers, so the PATCH cannot be used to find out whose claims exist.
    const someoneElses = claimIdsOutsideScopeOf(SARAH.name, SARAH.role)[0];

    await test.step("a supervisor cannot edit", async () => {
      // Through the real logout, not `goto("/")`: the route guard sends a
      // signed-in handler straight back to the workspace, so navigating to
      // the login screen never reaches it.
      await switchPersona(page);
      await loginAs(page, PERSONAS.scopedSupervisor);
      const refused = await page.request.patch(`/api/claims/${claimId}`, {
        data: { expectedVersion: held.version, cause: "Not Allowed" },
      });
      expect(refused.status()).toBe(403);
      expect((await refused.json()).type).toBe("/problems/edit-not-permitted");
    });

    await test.step("a handler cannot edit outside their book", async () => {
      await switchPersona(page);
      await loginAs(page, PERSONAS.scopedHandler);
      const refused = await page.request.patch(`/api/claims/${someoneElses}`, {
        data: { expectedVersion: 1, cause: "Not Mine" },
      });
      const invented = await page.request.patch("/api/claims/WC-99999", {
        data: { expectedVersion: 1, cause: "Nobody's" },
      });
      expect(refused.status()).toBe(404);
      expect(invented.status()).toBe(404);
      expect((await refused.json()).type).toBe((await invented.json()).type);
    });
  });

  test("editing the recovery window recomputes the treatment phase (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    // **The one place an inline edit visibly moves a derived value.** `risk`
    // is a band of the severity score, which Story 2.4 owns and this command
    // refuses; `recovery` is the only editable field that feeds a registered
    // derivation, and the treatment banner is where that derivation is drawn.
    // Before the code review no shipped surface could exercise AC 3's
    // recompute clause at all.
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await page.goto(`/workspace?claim=${claimId}`);
    await expect(byTestId(page, "treatment-phase-banner")).toBeVisible();

    const before = await byTestId(page, "treatment-phase-day").textContent();
    const target = otherRecoveryWindow(claimId);

    await byTestId(page, "edit-recovery").selectOption(target.token);

    // The expected-days figure is `treatment_phase`'s answer, recomputed by
    // the server from the new window — the browser holds no rule that could
    // have produced it.
    await expect(byTestId(page, "treatment-phase-day")).not.toHaveText(before ?? "");
    await expect(byTestId(page, "treatment-phase-day")).toContainText(target.label);

    await page.reload();
    await expect(byTestId(page, "edit-recovery")).toHaveValue(target.token);
    await expect(byTestId(page, "treatment-phase-day")).toContainText(target.label);
  });

  test("the financials card stays read-only (Epic 3, Story 2.4)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openClaim(page, firstClaimInStage(KAYA.name, KAYA.role, "investigation"));

    // The reserve and the severity score are editable in the prototype and
    // are *not* editable here: 2.4 owns the score, Epic 3 owns the money.
    // An input over either would be a form with no command behind it.
    const financials = byTestId(page, "investigation-financials");
    await expect(financials.locator("input, select, textarea")).toHaveCount(0);
  });
});
