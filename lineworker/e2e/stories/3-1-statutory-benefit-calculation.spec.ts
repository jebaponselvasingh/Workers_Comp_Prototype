import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  SCHEDULE_EFFECTIVE_FROM,
  claimIdsOutsideScopeOf,
  expectedBenefit,
  firstClaimInStage,
  formatBasisPoints,
  formatCents,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 3.1 — Statutory Benefit Calculation.
 *
 * The first slice of the financial engine, so this spec is about one thing
 * above all: **the figure on the card was computed by the server**. Every
 * expectation comes from `fixtures/seed.ts`, which restates the rule — the
 * comp rate, the PTD branch, the half-up rounding and the jurisdiction's
 * clamp — as a second implementation over the same seed files the stack
 * migrated with. A spec that read the answer off the response would agree
 * with any arithmetic at all.
 *
 * **The audit row is asserted in pytest, not here**, for Story 2.3's reason:
 * nothing in the product reads `audit_event`, and inventing an endpoint so a
 * spec could use one would be the test dictating the surface.
 * `server/tests/test_comp_rate_command.py` asserts the row, its before/after
 * diff and the timeline entry against the same database this stack runs.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec
 * *file*, so every test below reads the current version before it writes, as
 * a client does.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

/** Not the statutory default, so "the override applied" is visible. */
const OVERRIDE_BP = 7025;

type Page = Parameters<typeof byTestId>[0];

async function openClaim(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "benefit-card")).toBeVisible();
}

async function readClaim(page: Page, claimId: string): Promise<{ version: number }> {
  const response = await page.request.get(`/api/claims/${claimId}`);
  expect(response.status()).toBe(200);
  return response.json();
}

/** Type a percentage into the comp-rate field and commit it. */
async function commitRate(page: Page, percent: string): Promise<void> {
  const input = byTestId(page, "edit-compRate");
  await input.fill(percent);
  await input.press("Enter");
}

test.describe("@story:3-1 @epic:3 statutory benefit calculation", () => {
  test("@smoke the benefit is computed, overridable and resettable", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const atDefault = expectedBenefit(claimId);
    const overridden = expectedBenefit(claimId, OVERRIDE_BP);

    await openClaim(page, claimId);

    // --- AC 1 and 3: the card states the server's answer ----------------
    await expect(byTestId(page, "benefit-weekly")).toHaveText(
      `${formatCents(atDefault.weeklyCents)}/wk`,
    );
    await expect(byTestId(page, "benefit-type")).toContainText(
      atDefault.indemnityType.toUpperCase(),
    );
    await expect(byTestId(page, "benefit-bounds")).toHaveText(
      `${formatCents(atDefault.stateMinCents)} – ${formatCents(atDefault.stateMaxCents)}/wk`,
    );
    await expect(byTestId(page, "benefit-state")).toHaveText(atDefault.stateName);
    await expect(byTestId(page, "edit-compRate")).toHaveValue(
      formatBasisPoints(atDefault.defaultCompRateBp),
    );
    // The 7-day waiting period is displayed here and *applied* in Story 3.3.
    await expect(byTestId(page, "benefit-schedule")).toContainText(
      "Weekly, starting 7-day waiting period after DOI",
    );
    // The provenance line that replaces the prototype's "Illustrative
    // figures" disclaimer (NFR-4).
    await expect(byTestId(page, "benefit-provenance")).toContainText(
      `Statutory schedule effective ${SCHEDULE_EFFECTIVE_FROM}`,
    );
    // Nothing about the override is on screen for a claim that has none.
    await expect(byTestId(page, "benefit-reset")).toHaveCount(0);
    await expect(byTestId(page, "benefit-rationale")).not.toContainText("manually adjusted");

    // --- AC 4: the override, recomputed server-side ---------------------
    await commitRate(page, formatBasisPoints(OVERRIDE_BP));

    await expect(byTestId(page, "benefit-weekly")).toHaveText(
      `${formatCents(overridden.weeklyCents)}/wk`,
    );
    // The rationale's closing sentence is the server's, and it quotes both
    // rates — which is also where the client's basis-point formatting and the
    // server's `format_comp_rate` are compared against one claim.
    await expect(byTestId(page, "benefit-rationale")).toContainText(
      `Comp rate manually adjusted to ${formatBasisPoints(OVERRIDE_BP)}% ` +
        `(default ${formatBasisPoints(atDefault.defaultCompRateBp)}%) by handler.`,
    );

    // It is on the claim, not just on the screen.
    await page.reload();
    await expect(byTestId(page, "edit-compRate")).toHaveValue(
      formatBasisPoints(OVERRIDE_BP),
    );
    await expect(byTestId(page, "benefit-weekly")).toHaveText(
      `${formatCents(overridden.weeklyCents)}/wk`,
    );

    // --- AC 4: ↺ restores the default, exactly --------------------------
    await byTestId(page, "benefit-reset").click();

    await expect(byTestId(page, "benefit-reset")).toHaveCount(0);
    await expect(byTestId(page, "edit-compRate")).toHaveValue(
      formatBasisPoints(atDefault.defaultCompRateBp),
    );
    await expect(byTestId(page, "benefit-weekly")).toHaveText(
      `${formatCents(atDefault.weeklyCents)}/wk`,
    );
    await expect(byTestId(page, "benefit-rationale")).not.toContainText("manually adjusted");
  });

  test("the card renders on treatment and on no other stage (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    // The prototype puts the card on the investigation and treatment
    // variants. The *payload* carries a benefit at every stage — Story 3.3
    // reads it from the Bills tab — but an intake claim's card would show a
    // benefit nobody is paying yet.
    await openClaim(page, firstClaimInStage(KAYA.name, KAYA.role, "treatment"));

    await page.goto(`/workspace?claim=${firstClaimInStage(KAYA.name, KAYA.role, "intake")}`);
    await expect(byTestId(page, "intake-checklist")).toBeVisible();
    await expect(byTestId(page, "benefit-card")).toHaveCount(0);

    await page.goto(`/workspace?claim=${firstClaimInStage(KAYA.name, KAYA.role, "settled")}`);
    await expect(byTestId(page, "settled-banner")).toBeVisible();
    await expect(byTestId(page, "benefit-card")).toHaveCount(0);
  });

  test("a rate outside the statutory range is refused at the field (AC 4, NFR-3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openClaim(page, claimId);
    const held = await readClaim(page, claimId);

    await commitRate(page, "400");

    await expect(byTestId(page, "edit-compRate-invalid")).toContainText("0.00% and 150.00%");
    // What was typed is kept so it can be corrected, and nothing was written.
    await expect(byTestId(page, "edit-compRate")).toHaveValue("400");
    await expect(page.locator("[role=dialog], [role=alertdialog]")).toHaveCount(0);
    expect((await readClaim(page, claimId)).version).toBe(held.version);

    await page.reload();
    await expect(byTestId(page, "edit-compRate")).toHaveValue(
      formatBasisPoints(expectedBenefit(claimId).defaultCompRateBp),
    );
  });

  test("a stale override conflicts and the card shows the rate that won (AD-9)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openClaim(page, claimId);
    const held = await readClaim(page, claimId);

    // Somebody else's write lands between this pane's read and its save.
    // Issued through the request context, which shares the session, so it is
    // a genuine second writer rather than a mocked response.
    const winner = await page.request.patch(`/api/claims/${claimId}/comp-rate`, {
      data: { expectedVersion: held.version, compRateBp: 8000 },
    });
    expect(winner.status()).toBe(200);

    await commitRate(page, "70.00");

    await expect(byTestId(page, "edit-compRate-conflict")).toContainText(
      "Updated by someone else",
    );
    await expect(byTestId(page, "edit-compRate")).toHaveValue(formatBasisPoints(8000));
    await expect(byTestId(page, "benefit-weekly")).toHaveText(
      `${formatCents(expectedBenefit(claimId, 8000).weeklyCents)}/wk`,
    );
    await expect(page.locator("[role=dialog], [role=alertdialog]")).toHaveCount(0);
  });

  test("the API refuses a read-only role and an out-of-scope claim (AD-7)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const held = await readClaim(page, claimId);
    const someoneElses = claimIdsOutsideScopeOf(SARAH.name, SARAH.role)[0];

    await test.step("a supervisor cannot override", async () => {
      await switchPersona(page);
      await loginAs(page, PERSONAS.scopedSupervisor);
      const refused = await page.request.patch(`/api/claims/${claimId}/comp-rate`, {
        data: { expectedVersion: held.version, compRateBp: OVERRIDE_BP },
      });
      expect(refused.status()).toBe(403);
      expect((await refused.json()).type).toBe("/problems/edit-not-permitted");
    });

    await test.step("a handler cannot override outside their book", async () => {
      await switchPersona(page);
      await loginAs(page, PERSONAS.scopedHandler);
      const refused = await page.request.patch(`/api/claims/${someoneElses}/comp-rate`, {
        data: { expectedVersion: 1, compRateBp: OVERRIDE_BP },
      });
      const invented = await page.request.patch("/api/claims/WC-99999/comp-rate", {
        data: { expectedVersion: 1, compRateBp: OVERRIDE_BP },
      });
      expect(refused.status()).toBe(404);
      expect(invented.status()).toBe(404);
      // The same answer for "not yours" and "does not exist", so the route
      // cannot be walked to find out whose claims exist.
      expect((await refused.json()).type).toBe((await invented.json()).type);
    });
  });

  test("a supervisor reads the same benefit they cannot edit", async ({ page }) => {
    // Role gates capability, scope gates visibility: the figure a reserve is
    // reviewed against is not handler-only.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const asHandler = await (await page.request.get(`/api/claims/${claimId}`)).json();

    await switchPersona(page);
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    const asSupervisor = await (await page.request.get(`/api/claims/${claimId}`)).json();

    expect(asSupervisor.benefit).toEqual(asHandler.benefit);
  });
});
