import type { Page } from "@playwright/test";

import { PERSONAS, loginAs } from "../fixtures/login";
import {
  drillUrl,
  expectedDrillClaimsFor,
  expectedFraudAnalyticsFor,
  expectedFraudInjuryCut,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 7.1 — Fraud Analytics Workspace.
 *
 * Nothing here stubs HTTP. The browser logs in for real, `services/worklist`
 * bands, folds and rates the seeded portfolio under the caller's employer scope
 * for real, Recharts draws it in a real browser, and every figure is compared
 * against the seed counted independently (`fixtures/seed.ts`).
 *
 * Four properties get their own tests beyond "the section renders":
 *
 * - **The analyst has a section the supervisor does not.** Since Epic 5 the two
 *   roles read one dashboard byte for byte; this is the first thing in the
 *   console that separates them, so it is asserted from both directions — the
 *   analyst's two-entry navigation, and a supervisor bounced from the URL with
 *   no nav rendered at all.
 * - **Every segment reaches its own claims (AC 4).** A band opens
 *   `filter[fraudBand]`, a pipeline stage opens **two** facets, and the list is
 *   Story 5.5's list with Story 5.5's chips — the reason the drill facets were
 *   extended rather than a second list built.
 * - **The browser sorts nothing (AC 3).** A sort control changes the URL of a
 *   *request*, not the order of rows already in the page, and the rendered
 *   order is compared against the oracle both before and after.
 * - **The AI card is a cache, and says so (AC 2, AD-10).** It renders its empty
 *   state on the freshly reset stack, and its clauses *with a generation
 *   timestamp* after one claim's insights are generated against `model-stub`.
 *
 * Read-only is proved by **absence** — `textbox`/`combobox`/`copilot-pane`
 * counted at zero on the claim view — which is `5-5`'s discipline and the only
 * form of the assertion a disabled input would not satisfy.
 */

const ANALYST = PERSONAS.analyst;
const SUPERVISOR = PERSONAS.fullPortfolioSupervisor;

/** The band legend's rows, as rendered text, in the order it drew them. */
async function bandRows(page: Page): Promise<string[]> {
  return byTestId(page, "fraud-band-distribution-legend-row").allTextContents();
}

/** One bar chart's accessible value list, as rendered text. */
async function barRows(page: Page, testId: string): Promise<string[]> {
  return byTestId(page, `${testId}-values`).locator("li").allTextContents();
}

/** One rate table's first row, flattened to the four cells the oracle carries. */
async function topRateRow(page: Page, testId: string): Promise<string> {
  const cells = await byTestId(page, `${testId}-row`)
    .first()
    .locator("td")
    .allTextContents();
  return cells.map((cell) => cell.trim()).join(" ");
}

/** The Fraud section has answered — a legend is on screen, not just the frame. */
async function fraudSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "fraud-band-distribution-legend")).toBeVisible();
}

test.describe("@story:7-1 @epic:7 fraud analytics workspace", () => {
  test("@smoke the analyst reaches the Fraud section, and a band opens its claims read-only (AC 1, AC 4)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);

    // The console's first navigation. **Amended by Story 7.2**, which added the
    // Trends destination and re-cut Portfolio's catch-all to be the complement
    // of *every* section rather than of one — so the list is three now, and it
    // is still asserted as a list because the partition that decides which entry
    // is marked current below is read off this same set. Segmentation and
    // financial decomposition are 7.3-7.4 and their slots stay deliberately
    // unbuilt rather than stubbed.
    await expect(byTestId(page, "workspace-nav")).toBeVisible();
    await expect(byTestId(page, "workspace-nav").locator("a")).toHaveText([
      "Portfolio",
      "Fraud",
      "Trends",
    ]);

    await byTestId(page, "nav-fraud").click();
    await fraudSettled(page);

    const expected = expectedFraudAnalyticsFor(ANALYST);
    // Asserted as a **list**, because the order is the contract: low to high,
    // decided on the server over a rule's vocabulary the browser does not hold.
    // All three rows are present even where a band is empty — the zero-fill,
    // which is the one distribution on this console that does not omit.
    expect(await bandRows(page)).toEqual(expected.bandRows);
    // The two populations, which are two rules and are not the band beside them.
    await expect(byTestId(page, "fraud-kpi-flagged-value")).toHaveText(expected.flagged);
    await expect(byTestId(page, "fraud-kpi-siu-value")).toHaveText(expected.siu);

    // The flagged list is Story 5.5's list, embedded — the same card the
    // handler's queue draws, never a second implementation.
    const flagged = expectedDrillClaimsFor(ANALYST, { fraudFlagged: "true" });
    await expect(byTestId(page, "queue-card").first()).toBeVisible();
    expect(
      await byTestId(page, "queue-card").evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-claim-id") ?? ""),
      ),
    ).toEqual(flagged.claimIds.slice(0, flagged.pages[0].length));

    // Keyed on the segment rather than on its position: the legend is in the
    // rule's declaration order, and a positional click would open a different
    // band's list the moment the vocabulary changed.
    await byTestId(page, "fraud-band-distribution-legend-link")
      .and(page.locator("[data-key='high']"))
      .click();

    const highBand = expectedDrillClaimsFor(ANALYST, { fraudBand: "high" });
    await expect(byTestId(page, "queue-card").first()).toBeVisible();
    await expect(byTestId(page, "drill-count")).toContainText(highBand.count);
    // The chip says what it is and can be cleared — both halves, because a chip
    // that rendered and did not clear would satisfy half of AC 4.
    await expect(byTestId(page, "drill-chip")).toHaveText(["Fraud band: High✕"]);
    // The navigation still says where the analyst is. `/dashboard/claims` is
    // where every chart and table in this section *goes*, and it used to mark
    // neither destination — Portfolio matched exactly, Fraud matched by prefix,
    // and the drill route is neither — so a click on a chart landed on a page
    // whose nav had gone blank under a component that promises the opposite.
    await expect(byTestId(page, "workspace-nav").locator("[aria-current='page']")).toHaveText(
      "Portfolio",
    );

    // A claim opens read-only: the case header renders and nothing on the page
    // can write.
    const first = highBand.claimIds[0];
    await byTestId(page, "queue-card").first().click();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(first);
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("combobox")).toHaveCount(0);
    // No copilot pane either — dashboard-scope copilot is architecture-deferred
    // and a claim-scoped one is the handler's workspace.
    await expect(byTestId(page, "copilot-pane")).toHaveCount(0);
  });

  test("a supervisor sees no navigation and is bounced from the fraud URL (AC 2)", async ({
    page,
  }) => {
    // The inversion of the property Epic 5 shipped, and it is the story rather
    // than a side effect of it. Both halves in one test because they are one
    // fact: the supervisor's console is what Epic 5 shipped, with nothing added
    // to the shell and nothing new reachable from the address bar.
    await loginAs(page, SUPERVISOR);
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "workspace-nav")).toHaveCount(0);

    await page.goto("/dashboard/fraud");

    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "fraud-analytics")).toHaveCount(0);
    // …and the dashboard she was returned to still works: the KPI cards this
    // story must not have touched are on screen.
    await expect(byTestId(page, "kpi-fraud-flags-value")).toBeVisible();
  });

  test("the API refuses a supervisor before it reads anything (AC 2)", async ({ page }) => {
    // The client guard is a courtesy; this is the control. Asserted through the
    // API rather than the browser, because what is being checked is that the
    // *endpoints* are gated and not merely that the route is hidden.
    await loginAs(page, SUPERVISOR);

    for (const path of [
      "/api/dashboard/fraud",
      "/api/dashboard/fraud/rates",
      "/api/dashboard/fraud/red-flags",
    ]) {
      const response = await page.request.get(path);
      expect(response.status(), path).toBe(403);
      expect(response.headers()["cache-control"]).toBe("no-store");
      expect(((await response.json()) as { type: string }).type).toBe(
        "/problems/fraud-analytics-not-permitted",
      );
    }
  });

  test("the SIU pipeline drills with both facets in the URL (AC 1, AC 4)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/fraud");
    await fraudSettled(page);

    const expected = expectedFraudAnalyticsFor(ANALYST);
    // Only the stages and handlers the referred claims are actually in — the
    // omission rule, which is the opposite of the zero-filled bands above and is
    // the difference the two assertions exist to hold apart.
    expect(await barRows(page, "siu-pipeline-stage")).toEqual(expected.siuStageRows);
    expect(await barRows(page, "siu-pipeline-handler")).toEqual(expected.siuHandlerRows);

    await byTestId(page, "siu-pipeline-stage-value-link")
      .and(page.locator("[data-key='treatment']"))
      .click();

    // **Both** facets. A segment of this chart is "the referred claims in that
    // stage"; dropping the first would open a list several times longer than the
    // bar that was clicked.
    await expect(page).toHaveURL(
      new RegExp(drillUrl({ stage: "treatment", siuReview: "true" }).replace(/[[\]?]/g, "\\$&")),
    );
    const drill = expectedDrillClaimsFor(ANALYST, {
      stage: "treatment",
      siuReview: "true",
    });
    await expect(byTestId(page, "drill-count")).toContainText(drill.count);
    await expect(byTestId(page, "drill-chip")).toHaveText([
      "Stage: Under Treatment✕",
      "SIU review: Yes✕",
    ]);
  });

  test("a sort control re-orders a rate table server-side, and Back restores the workspace (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/fraud");
    await fraudSettled(page);

    const expected = expectedFraudAnalyticsFor(ANALYST);
    const cut = expectedFraudInjuryCut(ANALYST);
    // The default order is the server's — worst rate first — and the top row is
    // the seed's, computed independently including the label tie-break.
    await expect(byTestId(page, "fraud-rate-injury-row").first()).toBeVisible();
    expect(await topRateRow(page, "fraud-rate-injury")).toContain(
      expected.topInjuryRate.split(" ")[0],
    );
    expect(await topRateRow(page, "fraud-rate-employer")).toContain(
      expected.topEmployerRate.split(" ")[0],
    );
    expect(await topRateRow(page, "fraud-rate-handler")).toContain(
      expected.topHandlerRate.split(" ")[0],
    );
    // The cut is stated, from the server's two numbers rather than a constant.
    await expect(byTestId(page, "fraud-rate-injury-truncation")).toHaveText(
      `Showing ${String(cut.shown)} of ${String(cut.total)} injury types.`,
    );

    const injuryKeys = async (): Promise<string[]> =>
      byTestId(page, "fraud-rate-injury-row").evaluateAll((rows) =>
        rows.map((row) => row.getAttribute("data-row-key") ?? ""),
      );
    const before = await injuryKeys();

    // Changing the control issues a *request*; the rows that come back are the
    // server's. Alphabetical is the one permutation no plausible client-side
    // sort would produce from a rate ordering.
    const sorted = page.waitForResponse(
      (response) =>
        response.url().includes("/api/dashboard/fraud/rates") &&
        decodeURIComponent(response.url()).includes("sort[injuryType]=label_asc"),
    );
    await byTestId(page, "fraud-rate-injury-sort").selectOption("label_asc");
    await sorted;

    const ordered = await injuryKeys();
    expect([...ordered].sort((a, b) => a.localeCompare(b))).toEqual(ordered);
    // **And it is the same eight injury types**, which the ordering assertion
    // above cannot see. A sort is a display preference; it must never change
    // which rows exist. With the cut applied after the order — as it was — this
    // table published a different eight of twenty under every option, worst of
    // all under "Lowest rate", where a card headed "Flagged-claim rates" listed
    // the eight injury types with no flagged claim in them and captioned it
    // "Showing 8 of 20 injury types" with nothing naming which eight.
    expect(new Set(ordered)).toEqual(new Set(before));
    expect(ordered).toHaveLength(cut.shown);
    // …and the caption still describes the same cut it did before the click.
    await expect(byTestId(page, "fraud-rate-injury-truncation")).toHaveText(
      `Showing ${String(cut.shown)} of ${String(cut.total)} injury types.`,
    );
    // …and only that table moved: three independent controls, three parameters.
    expect(await topRateRow(page, "fraud-rate-employer")).toContain(
      expected.topEmployerRate.split(" ")[0],
    );

    // A row is a click target, and the browser's Back button returns to the
    // workspace with its figures intact.
    await byTestId(page, "fraud-rate-handler")
      .getByTestId("fraud-rate-link")
      .first()
      .click();
    await expect(byTestId(page, "drill-claims")).toBeVisible();
    await page.goBack();
    await fraudSettled(page);
    expect(await bandRows(page)).toEqual(expected.bandRows);
  });

  test("the red-flag card renders its empty state on a freshly reset stack (AC 2)", async ({
    page,
  }) => {
    // The seeded state and the ordinary one: nothing is generated for a claim
    // until a refresh reaches it. A **first-class empty card**, not a spinner and
    // not a gap — this surface reads the cache and can never fill it.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/fraud");
    await fraudSettled(page);

    await expect(byTestId(page, "fraud-red-flag-card-body")).toHaveAttribute(
      "data-status",
      "not_generated",
    );
    // **One** paragraph, about this portfolio. The shell's per-claim default —
    // "Use Refresh above to generate this claim's insights" — names a control
    // this page does not have, about a claim this card is not about; it rendered
    // anyway, with a second paragraph beneath it saying something else.
    const empty = byTestId(page, "fraud-red-flag-card-empty");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText("No fraud narratives are cached for this portfolio yet");
    await expect(empty).not.toContainText("Refresh");
    await expect(byTestId(page, "fraud-red-flag-empty-note")).toHaveCount(0);
    // No provenance line, because there is no provenance.
    await expect(byTestId(page, "fraud-red-flag-card-generated")).toHaveCount(0);
    // …and no chip: the payload carries no `outcome`, so an error-toned "Review
    // indicated" over a cold cache was a verdict the browser invented.
    await expect(page.getByText("Review indicated")).toHaveCount(0);
  });

  test("one generated claim puts its clauses on the card with a timestamp (AC 2, AD-10)", async ({
    page,
  }) => {
    // Driven through the **existing** per-claim refresh, against `model-stub` —
    // there is no second code path and this story adds none. One claim rather
    // than the portfolio: four stub completions instead of four hundred, and the
    // property under test is that a cached narrative reaches this view with its
    // generation time, which one claim demonstrates exactly as well as a hundred.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/fraud");
    await fraudSettled(page);

    const flagged = expectedDrillClaimsFor(ANALYST, { fraudFlagged: "true" });
    const claimId = flagged.claimIds[0];
    const refreshed = await page.request.post(`/api/claims/${claimId}/insights/refresh`);
    expect(refreshed.status(), await refreshed.text()).toBe(200);
    expect(
      ((await refreshed.json()) as { failedKinds: string[] }).failedKinds,
      "the model stub produced an answer some kind's schema refused",
    ).toEqual([]);

    await page.reload();
    await fraudSettled(page);

    await expect(byTestId(page, "fraud-red-flag-card-body")).toHaveAttribute(
      "data-kind",
      "fraud_risk_indicators",
    );
    // The coverage figure moved, which is what says this view reads the cache
    // rather than the claims.
    await expect(byTestId(page, "fraud-red-flag-coverage")).toContainText("1 of 100 claims");
    // At least one clause, and its provenance beside it. The *text* is the
    // stub's and is deliberately not asserted — AD-15's rule about asserting
    // structure rather than prose holds here as it does in 6.2.
    await expect(byTestId(page, "fraud-red-flag-clause").first()).toBeVisible();
    await expect(byTestId(page, "fraud-red-flag-card-generated")).toContainText("Generated");
    // Rows are inert: a clause is not a claim population, and inventing one
    // would be the browser deciding which claims a phrase names.
    await expect(byTestId(page, "fraud-red-flag-clause").first().locator("a")).toHaveCount(0);
  });
});
