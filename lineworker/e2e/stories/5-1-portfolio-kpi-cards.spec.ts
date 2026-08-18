import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  EXPECTED_KPI_CAPTIONS,
  expectedPortfolioSummaryFor,
  type ExpectedPortfolioSummary,
} from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 5.1 — Portfolio KPI Cards.
 *
 * Nothing here stubs HTTP: the browser logs in for real, `services/worklist`
 * folds the seeded portfolio under the caller's employer scope for real, and
 * the cards are compared against the seed counted independently
 * (`fixtures/seed.ts`). That is the whole point — the prototype produced these
 * ten numbers in the browser, so a spec that mocked the response would be
 * testing the half that was never in doubt.
 *
 * Two properties get their own tests beyond "the figures are right", because
 * they are what the story is exposed on:
 *
 * - **Scope narrows the portfolio, and the chip says so.** Jennifer Park's
 *   Total Claims is lower than David Bline's *and* her chip names three
 *   employers rather than ten. Equal numbers would satisfy every seed-derived
 *   assertion while scoping had silently stopped working.
 * - **The captions quote the rule document.** "Severity ≥ 65/100" is asserted
 *   from the oracle's restated threshold, so a caption holding a constant of
 *   its own and one reading the response are indistinguishable *here* — which
 *   is why `DashboardPage.test.tsx` renders a superseded response and this
 *   spec checks only that the number on screen is the seeded rule's.
 */

const CARD_ORDER = [
  "kpi-total-claims",
  "kpi-under-treatment",
  "kpi-settled-closed",
  "kpi-high-risk",
  "kpi-total-paid",
  "kpi-total-reserve",
  "kpi-fraud-flags",
  "kpi-osha-recordable",
  "kpi-litigation",
  "kpi-surgery-required",
] as const;

async function expectCards(
  page: Parameters<typeof byTestId>[0],
  expected: ExpectedPortfolioSummary,
): Promise<void> {
  for (const testId of CARD_ORDER) {
    // `toHaveText` on the value element, not `toContainText` on the card:
    // "100" contains "10", and a substring match would accept the wrong count.
    await expect(byTestId(page, `${testId}-value`)).toHaveText(
      expected.cards[testId],
    );
  }
}

test.describe("@story:5-1 @epic:5 portfolio KPI cards", () => {
  test("@smoke the full-portfolio supervisor's dashboard reads 100 claims across ten cards", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    await expect(
      byRole(page, "heading", "Manufacturing WC — Portfolio Overview"),
    ).toBeVisible();

    const bline = expectedPortfolioSummaryFor(PERSONAS.fullPortfolioSupervisor);
    await expect(byTestId(page, "kpi-total-claims-value")).toHaveText("100");
    await expectCards(page, bline);

    // Every count in the chip is computed, including the plants the prototype
    // hardcoded as "15 US plants" over its own 29.
    await expect(byTestId(page, "dataset-chip")).toHaveText(bline.chip);
  });

  test("the two rule captions carry the thresholds the documents hold (AC 2)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    for (const [testId, caption] of Object.entries(EXPECTED_KPI_CAPTIONS)) {
      await expect(byTestId(page, testId)).toContainText(caption);
    }
  });

  test("a scoped supervisor sees only her three employers' portfolio (AC 1, 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const park = expectedPortfolioSummaryFor(PERSONAS.scopedSupervisor);
    await expectCards(page, park);
    await expect(byTestId(page, "dataset-chip")).toHaveText(park.chip);

    // The pair is the proof: identical numbers would mean scoping stopped
    // working and every "expected == seed" assertion above still passed.
    const bline = expectedPortfolioSummaryFor(PERSONAS.fullPortfolioSupervisor);
    expect(park.totalClaims).toBeLessThan(bline.totalClaims);
    await expect(byTestId(page, "kpi-total-claims-value")).not.toHaveText(
      String(bline.totalClaims),
    );
  });

  test("the analyst lands on the same dashboard, not a variant of it (Epic 5 preamble)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.analyst);

    await expect(byRole(page, "region", "Portfolio dashboard")).toBeVisible();
    // The analyst persona is David Bline wearing the analyst hat, so the
    // figures are his: role gates capability, scope gates visibility, and no
    // analyst-specific dashboard exists until Epic 7.
    await expectCards(page, expectedPortfolioSummaryFor(PERSONAS.analyst));
  });

  test("switching persona replaces the portfolio instead of keeping the last one", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    await expect(byTestId(page, "kpi-total-claims-value")).toHaveText("100");

    await switchPersona(page);
    await loginAs(page, PERSONAS.scopedSupervisor);

    // If the cache outlived the session, this card would still read 100.
    await expectCards(
      page,
      expectedPortfolioSummaryFor(PERSONAS.scopedSupervisor),
    );
  });

  test("the figures are not something the browser could have computed (AC 4)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await expect(byTestId(page, "kpi-total-claims")).toBeVisible();

    // Ask the API directly, from the page's own session, for a scope that is
    // not hers. The endpoint takes no parameters at all, so a smuggled one
    // changes nothing — the response is her book either way.
    const smuggled = await page.evaluate(async () => {
      const resp = await fetch(
        "/api/dashboard/summary?employerId=1&scopeAll=true",
      );
      return (await resp.json()) as Record<string, number>;
    });
    const park = expectedPortfolioSummaryFor(PERSONAS.scopedSupervisor);

    expect(smuggled.totalClaims).toBe(park.totalClaims);
    expect(String(smuggled.litigation)).toBe(park.cards["kpi-litigation"]);
  });

  test("the top bar's own tiles are not repeated on the page (UX-DR2)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    // Caseload, Active Tx, High Risk and the SLA strip already live in the
    // shared bar. The dashboard's High Risk *card* is a different figure with
    // a different caption; what must not happen is a second copy of the bar.
    await expect(byTestId(page, "stat-caseload-value")).toHaveText("100");
    await expect(byTestId(page, "sla-strip")).toBeVisible();
    await expect(byTestId(page, "top-bar")).toHaveCount(1);
  });
});
