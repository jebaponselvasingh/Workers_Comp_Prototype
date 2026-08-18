import { PERSONAS, loginAs } from "../fixtures/login";
import {
  CHART_SLA_TILES,
  expectedPortfolioChartsFor,
  type ExpectedPortfolioCharts,
} from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 5.3 — Portfolio Analytics Charts.
 *
 * Nothing here stubs HTTP: the browser logs in for real, `services/worklist`
 * folds the seeded portfolio into six distributions and an SLA strip under the
 * caller's employer scope for real, Recharts draws them in a real browser with
 * real layout, and every figure is compared against the seed counted
 * independently (`fixtures/seed.ts`). That is the point — the prototype
 * produced all seven of these charts in the browser, so a spec that mocked the
 * response would be testing the half that was never in doubt.
 *
 * Four properties get their own tests beyond "the figures are right":
 *
 * - **The dashboard SLA tiles and the top-bar strip are one value (AC 3).**
 *   Both are on screen at once, both are read from the DOM, and the two are
 *   compared against each other rather than each against the oracle — which is
 *   the only form of the assertion that would fail if a second aggregation
 *   appeared.
 * - **Scope narrows every series (AC 2, AD-7).** Jennifer Park's employer chart
 *   holds exactly her three employers, her stage donut has no intake row at
 *   all, and her totals are strictly below David Bline's.
 * - **The analyst reads the same charts as the supervisor**, because no figure
 *   on this endpoint could have come from a role branch.
 * - **No parameter she can add changes any of it (AD-1).** The route declares
 *   none, so a smuggled one is ignored rather than rejected.
 */

/** Every surface's `data-testid`, in the prototype's grid order. */
const SURFACES = [
  "chart-settlement-status",
  "chart-severity",
  "chart-sla",
  "chart-recovery-status",
  "chart-injury-type",
  "chart-employer-paid",
  "chart-state",
];

async function legendRows(
  page: Parameters<typeof byTestId>[0],
  testId: string,
): Promise<string[]> {
  return byTestId(page, `${testId}-legend-row`).allTextContents();
}

async function barRows(
  page: Parameters<typeof byTestId>[0],
  testId: string,
): Promise<string[]> {
  return byTestId(page, `${testId}-values`).locator("li").allTextContents();
}

async function expectCharts(
  page: Parameters<typeof byTestId>[0],
  expected: ExpectedPortfolioCharts,
): Promise<void> {
  // Every series is asserted **as a list**, because the order is the contract:
  // the enum-keyed ones publish in their enum's declaration order and the
  // ranked ones in count-descending, label-ascending order, both decided on the
  // server over a scope the browser does not hold. A per-row loop would be
  // satisfied by a chart that rendered every row in the wrong position.
  await expect(byTestId(page, "chart-settlement-status-legend-row")).toHaveCount(
    expected.stage.legend.length,
  );
  expect(await legendRows(page, "chart-settlement-status")).toEqual(expected.stage.legend);
  expect(await legendRows(page, "chart-severity")).toEqual(expected.severity.legend);

  await expect(byTestId(page, "chart-settlement-status-total")).toContainText(
    expected.stage.total,
  );
  await expect(byTestId(page, "chart-severity-total")).toContainText(
    expected.severity.total,
  );

  expect(await barRows(page, "chart-recovery-status")).toEqual(expected.recoveryBars);
  expect(await barRows(page, "chart-injury-type")).toEqual(expected.injuryBars);
  expect(await barRows(page, "chart-employer-paid")).toEqual(expected.employerBars);
  expect(await barRows(page, "chart-state")).toEqual(expected.stateBars);

  for (const [testId, text] of Object.entries(expected.slaTiles)) {
    // `toHaveText` on the value element, not `toContainText` on the tile:
    // "62d" contains "2", and a substring match would accept the wrong figure.
    await expect(byTestId(page, `${testId}-value`)).toHaveText(text);
  }
}

test.describe("@story:5-3 @epic:5 portfolio analytics charts", () => {
  test("@smoke the full-portfolio supervisor's seven surfaces draw the seeded book", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    await expect(
      byRole(page, "heading", "Portfolio analytics"),
    ).toBeVisible();

    const bline = expectedPortfolioChartsFor(PERSONAS.fullPortfolioSupervisor);
    await expectCharts(page, bline);

    // All seven surfaces are on screen, and six of them actually drew an SVG —
    // a chart library that measured its container as zero would render nothing
    // while every assertion above still passed off the legends and the
    // screen-reader lists.
    for (const testId of SURFACES) {
      await expect(byTestId(page, testId)).toBeVisible();
    }
    for (const testId of SURFACES.filter((id) => id !== "chart-sla")) {
      await expect(byTestId(page, testId).locator("svg")).toHaveCount(1);
    }

    // The two cuts, captioned from `limit` and `totalCategories` rather than
    // from the prototype's hardcoded "(top 8)" heading.
    // Asserted non-null with a message before it is used: if the seeded scope
    // ever drops to eight injury types or ten states, the oracle returns null
    // and a bare `!` would fail here as an opaque type error. This says which
    // seed fact changed.
    expect(bline.truncation.injury, "seed no longer truncates injury types").not.toBeNull();
    expect(bline.truncation.state, "seed no longer truncates states").not.toBeNull();
    await expect(byTestId(page, "chart-injury-type-truncation")).toHaveText(
      bline.truncation.injury!,
    );
    await expect(byTestId(page, "chart-state-truncation")).toHaveText(
      bline.truncation.state!,
    );
    // Ten of ten employers: nothing was cut, so nothing apologises for a cut.
    await expect(byTestId(page, "chart-employer-paid-truncation")).toHaveCount(0);
  });

  test("the dashboard SLA tiles and the top-bar strip show one value (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    await expect(byTestId(page, "chart-sla")).toBeVisible();

    // **Both read from the DOM and compared against each other**, not each
    // against the oracle. Two surfaces that had each been computed correctly
    // from two different aggregations would satisfy an oracle comparison
    // twice over; only a direct comparison says "these are one number", which
    // is what AD-2 actually promises.
    for (const [key, chartTestId] of Object.entries(CHART_SLA_TILES)) {
      const topBar = byTestId(page, `sla-${chartTestId.replace("chart-sla-", "")}-value`);
      const dashboard = byTestId(page, `${chartTestId}-value`);
      const shown = await topBar.textContent();
      await expect(dashboard, `${key} disagrees with the top bar`).toHaveText(shown!);

      // …and the same target sentence, which carries the direction and the
      // pass/warn verdict the colour is drawn from. A tile showing the right
      // number against the wrong comparison would still be two renderings of
      // two things.
      const topTarget = await byTestId(
        page,
        `sla-${chartTestId.replace("chart-sla-", "")}-target`,
      ).textContent();
      await expect(byTestId(page, `${chartTestId}-target`)).toHaveText(topTarget!);
    }
  });

  test("a scoped supervisor's series stay inside her book (AC 2, AD-7)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const park = expectedPortfolioChartsFor(PERSONAS.scopedSupervisor);
    await expectCharts(page, park);

    // Exactly her three employers, by name — the strongest single statement
    // that the aggregate was scoped rather than sliced.
    expect(park.employerBars).toHaveLength(3);
    for (const label of ["Toyota", "GM", "3M"]) {
      expect(park.employerBars.some((row) => row.startsWith(`${label}:`))).toBe(true);
    }

    // Her book contains no intake claim, so the category is **absent** rather
    // than drawn as a zero slice — the omission contract, on the one persona
    // that exercises it.
    expect(park.stage.legend.some((row) => row.startsWith("Intake:"))).toBe(false);
    await expect(byTestId(page, "chart-settlement-status-legend-row")).toHaveCount(3);

    // Seven states of seven: a ranked series the server did not cut carries no
    // caption at all, which is the other side of the conditional the smoke test
    // asserts.
    expect(park.truncation.state).toBeNull();
    await expect(byTestId(page, "chart-state-truncation")).toHaveCount(0);
    expect(park.truncation.injury, "Park's scope no longer truncates injury types").not.toBeNull();
    await expect(byTestId(page, "chart-injury-type-truncation")).toHaveText(
      park.truncation.injury!,
    );

    // The pair is the proof: equal totals would mean scoping stopped working
    // and every seed-derived assertion above would still have passed.
    const bline = expectedPortfolioChartsFor(PERSONAS.fullPortfolioSupervisor);
    expect(Number(park.stage.total)).toBeLessThan(Number(bline.stage.total));
    expect(park.employerBars.length).toBeLessThan(bline.employerBars.length);
    expect(park.stateBars.length).toBeLessThan(bline.stateBars.length);
  });

  test("the analyst reads the same charts as the supervisor (Epic 5 preamble)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.analyst);

    // Role gates capability, scope gates visibility, and the analyst persona is
    // David Bline wearing the analyst hat — so the portfolio is his. There is no
    // role branch anywhere in this service path for a figure to differ by.
    await expectCharts(page, expectedPortfolioChartsFor(PERSONAS.analyst));
  });

  test("the figures are not something the browser could have computed (AD-1)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await expect(byTestId(page, "chart-employer-paid")).toBeVisible();

    // Ask the API from the page's own session for a scope, a wider cut and an
    // ordering that are not hers. The endpoint takes no parameters at all, so
    // every smuggled one is ignored — the answer is her three employers, her
    // eight injury types and her seven states either way.
    const smuggled = await page.evaluate(async () => {
      const resp = await fetch(
        "/api/dashboard/charts?scopeAll=true&limit=50&sort=-paidCents&employerId=3",
      );
      return (await resp.json()) as {
        byEmployer: { items: { label: string }[] };
        byInjuryType: { limit: number; totalCategories: number };
        byState: { truncated: boolean };
      };
    });
    const park = expectedPortfolioChartsFor(PERSONAS.scopedSupervisor);

    expect(smuggled.byEmployer.items.map((row) => `${row.label}:`)).toEqual(
      park.employerBars.map((row) => row.slice(0, row.indexOf(":") + 1)),
    );
    // The cap ignored the `limit` she sent, and the state series is still the
    // uncut seven rather than a widened view of the portfolio.
    expect(smuggled.byInjuryType.limit).toBe(8);
    expect(smuggled.byInjuryType.totalCategories).toBe(14);
    expect(smuggled.byState.truncated).toBe(false);
  });
});
