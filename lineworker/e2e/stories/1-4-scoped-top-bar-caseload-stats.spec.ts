import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import { expectedStatsFor } from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.4 — Scoped Top Bar & Caseload Stats.
 *
 * The whole point is that the numbers on screen are the *server's* answer
 * for *this* session, so nothing here stubs HTTP: the browser logs in for
 * real, the API scopes for real against the seeded database, and the tiles
 * are compared against the seed counted independently
 * (`fixtures/seed.ts`).
 */

const TILES = {
  caseload: "stat-caseload",
  activeTx: "stat-active-tx",
  highRisk: "stat-high-risk",
} as const;

async function expectTiles(
  page: Parameters<typeof byTestId>[0],
  expected: { caseload: number; activeTx: number; highRisk: number },
): Promise<void> {
  for (const [key, testId] of Object.entries(TILES)) {
    // `toHaveText` on the value element, not `toContainText` on the tile:
    // "15" contains "5", and a substring match would accept the wrong count.
    await expect(byTestId(page, `${testId}-value`)).toHaveText(
      String(expected[key as keyof typeof expected]),
    );
  }
}

test.describe("@story:1-4 @epic:1 scoped top bar & caseload stats", () => {
  test("@smoke the full-portfolio supervisor's top bar reads Caseload 100", async ({ page }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    await expect(byTestId(page, "top-bar")).toBeVisible();
    await expect(byTestId(page, "stat-caseload-value")).toHaveText("100");
    await expectTiles(page, expectedStatsFor("David Bline", "supervisor"));
  });

  test("a scoped supervisor sees only her employers' claims (AC 1, 4)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const park = expectedStatsFor("Jennifer Park", "supervisor");
    await expectTiles(page, park);

    // The pair is the proof: identical numbers would mean scoping stopped
    // working and every "expected == seed" assertion above still passed.
    expect(park.caseload).toBeLessThan(expectedStatsFor("David Bline", "supervisor").caseload);
  });

  test("her identity renders beside the numbers (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    await expect(byTestId(page, "role-badge")).toHaveText("👔 Supervisor");
    await expect(byTestId(page, "user-chip")).toContainText("Jennifer Park");
    await expect(byTestId(page, "user-chip")).toContainText("WC Supervisor");
    await expect(byTestId(page, "user-avatar")).toHaveText("JP");
  });

  test("a handler gets the handler badge and that book's counts (AC 3, 4)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    await expect(byTestId(page, "role-badge")).toHaveText("📋 Handler");
    await expect(byTestId(page, "user-chip")).toContainText("Claims Handler");
    await expectTiles(page, expectedStatsFor("Kaya Johnson", "handler"));
  });

  test("the analyst shell carries the same bar with the analyst identity (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.analyst);

    await expect(byTestId(page, "role-badge")).toHaveText("📊 Analyst");
    await expect(byTestId(page, "user-chip")).toContainText("Data Analyst");
    await expectTiles(page, expectedStatsFor("David Bline", "analyst"));
  });

  test("switching persona replaces the numbers instead of keeping the last book", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    await expect(byTestId(page, "stat-caseload-value")).toHaveText("100");

    await switchPersona(page);
    await loginAs(page, PERSONAS.handler);

    // If the cache outlived the session, this tile would still read 100.
    await expectTiles(page, expectedStatsFor("Kaya Johnson", "handler"));
    await expect(byTestId(page, "stat-caseload-value")).not.toHaveText("100");
  });

  test("the tiles are not something the browser could have computed (AC 1, 2)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await expect(byTestId(page, "stat-caseload")).toBeVisible();

    // Ask the API directly, from the page's own session, for a scope that
    // is not hers. The endpoint takes no parameters at all, so a smuggled
    // one changes nothing — the response is her book either way.
    const smuggled = await page.evaluate(async () => {
      const resp = await fetch("/api/stats/topbar?employerId=1&scopeAll=true");
      return (await resp.json()) as Record<string, number>;
    });

    expect(smuggled).toEqual(expectedStatsFor("Jennifer Park", "supervisor"));
  });

  test("the sibling-story seams are visible and filled (Tasks 4)", async ({ page }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    // Both seams this story framed have since been filled — 1.5 the SLA
    // strip, 1.6 the glossary — and the assertions follow them rather than
    // being deleted. What this story guaranteed was a *slot in the shared
    // bar* for each; something occupying it is that guarantee being kept,
    // and a deleted test would leave "every role gets one" resting on
    // nothing structural. (The contents are 1-5's and 1-6's specs to check.)
    await expect(byRole(page, "button", /Glossary/)).toBeEnabled();
    await expect(byTestId(page, "sla-strip")).toBeVisible();
  });
});
