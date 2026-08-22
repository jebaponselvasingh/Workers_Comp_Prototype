import type { Page } from "@playwright/test";

import { PERSONAS, loginAs } from "../fixtures/login";
import {
  drillUrl,
  expectedDrillClaimsFor,
  expectedTrendsFor,
  type DrillFilters,
  type ExpectedTrends,
  type TrendGrain,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 7.2 — Trend & Cohort Analytics.
 *
 * Nothing here stubs HTTP. The browser logs in for real, `services/worklist`
 * buckets, folds and splits the seeded portfolio under the caller's employer
 * scope for real, Recharts draws it in a real browser, and every figure on
 * screen is compared against the seed bucketed independently
 * (`fixtures/seed.ts`, the Story 7.2 banner).
 *
 * **What this spec is defending against is an oracle that agrees.** Story 7.1's
 * review found `seed.ts::topRate` carrying the same sort-then-cut defect as the
 * implementation, so the one property that was wrong was the one property the
 * independent check could not see. A trend story has that hazard twice — **a
 * window is a cut and a cohort split is a partition** — so three things are
 * asserted here that an ordinary "the numbers match" spec would not:
 *
 * - **Which buckets exist, as a set**, and again after the grain changes. A
 *   window that lost its oldest bucket and gained one past the end is still
 *   twelve points in ascending order with plausible values in all of them.
 * - **Which cohort values exist, as a set.** A split that dropped a sector
 *   still draws its remaining lines in slot order under a correct legend.
 * - **Which buckets are gaps**, per line, by key — never "the chart has some
 *   nulls". AC 3's prohibition is that an absent mean must not be drawn as
 *   zero, and the only way to see that is to name the bucket and read what it
 *   printed.
 *
 * Every figure is compared as a **rendered string**, so a card publishing basis
 * points where a percentage belongs, or cents where dollars belong, fails here
 * rather than passing a comparison of integers.
 *
 * The charts themselves are never read: the figure is `aria-hidden` and the
 * accessible `sr-only` list beside it is the rendering under test, which is
 * `5-3`'s discipline for the donuts applied to a time axis.
 */

const ANALYST = PERSONAS.analyst;
const SUPERVISOR = PERSONAS.fullPortfolioSupervisor;

/** The Trends section has answered — a series is on screen, not just a frame. */
async function trendsSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "trend-volume-series")).toBeVisible();
}

/** One card's lines, by the cohort key each publishes, in the drawn order. */
async function lineKeys(page: Page, testId: string): Promise<string[]> {
  return byTestId(page, `${testId}-line`).evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-cohort-key") ?? ""),
  );
}

/** The hue each of one card's lines is drawn in, in the drawn order. */
async function lineFills(page: Page, testId: string): Promise<string[]> {
  return byTestId(page, `${testId}-line`).evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-fill") ?? ""),
  );
}

/** One line of one card, as the accessible list renders it, bucket by bucket. */
async function linePoints(
  page: Page,
  testId: string,
  cohortKey: string,
): Promise<string[]> {
  return byTestId(page, `${testId}-line`)
    .and(page.locator(`[data-cohort-key='${cohortKey}']`))
    .getByTestId(`${testId}-point`)
    .allTextContents();
}

/** The bucket keys one line published, in the order it drew them. */
async function lineBuckets(
  page: Page,
  testId: string,
  cohortKey: string,
): Promise<string[]> {
  return byTestId(page, `${testId}-line`)
    .and(page.locator(`[data-cohort-key='${cohortKey}']`))
    .getByTestId(`${testId}-point`)
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-bucket-key") ?? ""));
}

/**
 * Change one selector and wait for the answer that selector asked for.
 *
 * A *request*, which is the point: a grain or a cohort is a server parameter,
 * so the new lines have to arrive over the wire rather than be re-derived from
 * the ones already on screen (AD-1). Only usable for a selector set this session
 * has not asked for before — the hook holds an answer stale-free for thirty
 * seconds, so a return trip is served from the cache and no response ever fires.
 */
async function reselect(
  page: Page,
  testId: string,
  value: string,
  parameter: string,
): Promise<void> {
  const answered = page.waitForResponse(
    (response) =>
      response.url().includes("/api/dashboard/trends") &&
      decodeURIComponent(response.url()).includes(parameter),
  );
  await byTestId(page, testId).selectOption(value);
  await answered;
}

/**
 * The chips a filter set draws, with the remove control's own glyph.
 *
 * The oracle publishes what each chip *says*; the ✕ is the button inside it, and
 * asserting the two together is half of "visible, and clearable" — a chip
 * rendered without its control would satisfy a text comparison that stopped at
 * the label.
 */
function clearable(chips: string[]): string[] {
  return chips.map((chip) => `${chip}✕`);
}

/**
 * The last bucket of the window that actually holds a claim, for one cohort.
 *
 * Picked rather than hard-coded because the window ends on *today*: a spec that
 * always drilled the newest bucket would be asserting an empty list on the days
 * the seed has nothing in the current period. The choice is made through the
 * drill oracle — which restates the six facets for itself — so the bucket is
 * chosen by the same rule the assertion is then made against.
 */
function drillableBucket(expected: ExpectedTrends, cohortKey?: string): string {
  for (const key of [...expected.bucketKeys].reverse()) {
    const drill = expectedDrillClaimsFor(ANALYST, expected.drillFilters(key, cohortKey));
    if (drill.claimIds.length !== 0) return key;
  }
  throw new Error("no bucket in this window holds a claim for that cohort");
}

/** Assert one card renders exactly the oracle's lines, values and footnote. */
async function expectCard(
  page: Page,
  expected: ExpectedTrends,
  testId: string,
): Promise<void> {
  const card = expected.card[testId];
  // The card's heading, read as the **accessible name of the surface** it
  // labels: `ChartFrame` wires the two with `aria-labelledby`, so this is the
  // title a screen reader announces for the region rather than a string found
  // somewhere inside it.
  await expect(byTestId(page, testId)).toHaveAccessibleName(card.title);
  // The **set** of lines as well as their order: a split that lost a cohort
  // still draws the survivors in slot order under a heading that reads true.
  const keys = await lineKeys(page, testId);
  expect(keys, testId).toEqual(card.lines.map((line) => line.cohortKey));
  expect(new Set(keys), testId).toEqual(new Set(card.lines.map((line) => line.cohortKey)));
  for (const line of card.lines) {
    expect(await linePoints(page, testId, line.cohortKey), `${testId}/${line.cohortKey}`)
      .toEqual(line.points);
  }
  await expect(byTestId(page, `${testId}-footnote`)).toHaveText(card.footnote);
}

test.describe("@story:7-2 @epic:7 trend and cohort analytics", () => {
  test("@smoke the analyst reaches Trends, splits it by cohort, and a bucket opens its claims (AC 1, AC 2)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);

    // The console's navigation is four entries since Story 7.4 — Portfolio,
    // Fraud, this story's and Financial. Asserted as a list rather than a count
    // because the partition that decides which one is marked current is read off
    // this same set, and a section added without being subtracted from
    // Portfolio's catch-all marks two entries at once.
    await expect(byTestId(page, "workspace-nav").locator("a")).toHaveText([
      "Portfolio",
      "Fraud",
      "Trends",
      "Financial",
    ]);

    await byTestId(page, "nav-trends").click();
    await trendsSettled(page);
    await expect(byTestId(page, "workspace-nav").locator("[aria-current='page']")).toHaveText(
      "Trends",
    );

    const unsplit = expectedTrendsFor(ANALYST);
    // The window itself, before any figure in it: the caption states how much of
    // the book the twelve buckets cover, which is the sentence that keeps a
    // reader from taking a window over 91 claims for a portfolio of 100.
    await expect(byTestId(page, "trend-window-caption")).toHaveText(unsplit.windowCaption);

    // **Five series, each over the same window**, and each rendered in its own
    // unit — days, basis points and cents are three scales on one screen, and
    // the oracle compares the strings so a card printing the wire value fails.
    for (const card of unsplit.cards) {
      await expectCard(page, unsplit, card.testId);
    }
    expect(unsplit.cards.map((card) => card.testId)).toEqual([
      "trend-volume",
      "trend-days-open",
      "trend-settlement",
      "trend-rtw-rate",
      "trend-paid",
    ]);
    // One line while nothing is split, and the legend is absent rather than a
    // legend of one — an unsplit series carries no comparison to make.
    expect(await lineKeys(page, "trend-volume")).toEqual([""]);
    await expect(byTestId(page, "trend-legend")).toHaveCount(0);

    // AC 2: the same five metrics, split one dimension at a time.
    await reselect(page, "trend-cohort", "severity_band", "cohort=severity_band");
    const split = expectedTrendsFor(ANALYST, { cohort: "severity_band" });

    // The cohort **vocabulary**, as a set and in the server's slot order. An
    // ordering assertion alone is satisfied by the wrong population.
    await expect(byTestId(page, "trend-legend-link")).toHaveText(split.legendRows);
    const legendKeys = await byTestId(page, "trend-legend-link").evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-key") ?? ""),
    );
    expect(new Set(legendKeys)).toEqual(new Set(split.cohortKeys));
    await expect(byTestId(page, "trend-legend")).toHaveAttribute(
      "aria-label",
      split.legendLabel,
    );

    // Side by side: every card now carries one line per cohort value, over the
    // same buckets, and the severity cohort takes the KPI cards' own risk hues.
    for (const card of split.cards) {
      await expectCard(page, split, card.testId);
    }
    expect(await lineFills(page, "trend-volume")).toEqual(
      split.card["trend-volume"].lines.map((line) => line.fill),
    );

    // AC 4's drill: a period, a cohort, and Story 5.5's list.
    const bucketKey = drillableBucket(split, "high");
    await byTestId(page, "trend-period").selectOption(bucketKey);
    await byTestId(page, "trend-legend-link").and(page.locator("[data-key='high']")).click();

    const filters = split.drillFilters(bucketKey, "high");
    await expect(page).toHaveURL(
      new RegExp(drillUrl(filters).replace(/[[\]?]/g, "\\$&")),
    );
    const drill = expectedDrillClaimsFor(ANALYST, filters);
    await expect(byTestId(page, "queue-card").first()).toBeVisible();
    await expect(byTestId(page, "drill-count")).toContainText(drill.count);
    expect(
      await byTestId(page, "queue-card").evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-claim-id") ?? ""),
      ),
    ).toEqual(drill.claimIds.slice(0, drill.pages[0].length));
    // Three chips: the cohort's column and the bucket's two inclusive bounds.
    // The **order is the server's `FILTER_KEYS`**, not the order the analyst
    // chose them in, which is why the oracle carries a list rather than a set.
    await expect(byTestId(page, "drill-chip")).toHaveText(clearable(drill.chips));
    expect(drill.chips).toHaveLength(3);
  });

  test("a grain change replaces the whole bucket population, not just its labels (AC 1)", async ({
    page,
  }) => {
    // The set assertion the 7.1 review's lesson asks for, on the cut this story
    // makes: a window is decided from the calendar and the clock, so a build
    // that anchored it on the *data* would still publish twelve ascending
    // buckets with plausible values — and would quietly describe a different
    // stretch of time than the caption above it claims.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/trends");
    await trendsSettled(page);

    const monthly = expectedTrendsFor(ANALYST, { grain: "month" });
    expect(await lineBuckets(page, "trend-volume", "")).toEqual(monthly.bucketKeys);

    for (const grain of ["quarter", "week"] as TrendGrain[]) {
      await reselect(page, "trend-grain", grain, `grain=${grain}`);
      const expected = expectedTrendsFor(ANALYST, { grain });
      const rendered = await lineBuckets(page, "trend-volume", "");

      // Order, then identity, then that it is a genuinely different vocabulary
      // from the month window — three claims, and the middle one is the one an
      // ordering assertion cannot make.
      expect(rendered, grain).toEqual(expected.bucketKeys);
      expect(new Set(rendered), grain).toEqual(new Set(expected.bucketKeys));
      expect(
        rendered.filter((key) => monthly.bucketKeys.includes(key)),
        `${grain} reused a month bucket key`,
      ).toEqual([]);

      // …and every one of the five cards spans that same window, which is what
      // makes two charts on one screen agree about where March is.
      for (const card of expected.cards) {
        expect(await lineBuckets(page, card.testId, ""), card.testId).toEqual(
          expected.bucketKeys,
        );
      }
      await expect(byTestId(page, "trend-window-caption")).toHaveText(expected.windowCaption);
    }
  });

  test("a sparse window draws gaps rather than zero lines (AC 3)", async ({ page }) => {
    // The seed's FNOL dates run 2026-01-14 → 2026-09-29, so a twelve-week window
    // split nine ways leaves most cells with nothing to average — which is
    // exactly the shape AC 3 is about. The assertion is per bucket **by key**:
    // "the chart has some nulls" would pass on a chart that had put its nulls in
    // the wrong periods, and "no value is zero" would fail on the two metrics
    // where zero is the true answer.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/trends");
    await trendsSettled(page);

    await reselect(page, "trend-grain", "week", "grain=week");
    await reselect(page, "trend-cohort", "sector", "cohort=sector");
    const expected = expectedTrendsFor(ANALYST, { grain: "week", cohort: "sector" });

    // Nine sectors under the ten-hue ceiling, as a set — the population, before
    // anything is said about the lines drawn over it.
    expect(new Set(await lineKeys(page, "trend-settlement"))).toEqual(
      new Set(expected.cohortKeys),
    );

    const settlement = expected.card["trend-settlement"];
    let gapsSeen = 0;
    let markedSeen = 0;
    for (const line of settlement.lines) {
      const buckets = await lineBuckets(page, "trend-settlement", line.cohortKey);
      const points = await linePoints(page, "trend-settlement", line.cohortKey);
      expect(points, line.cohortKey).toEqual(line.points);

      // Every bucket the oracle calls a gap prints an em dash, and **no gap
      // prints a number** — not "0", not "0d". A zero here is the misleading
      // line the criterion forbids, and it is indistinguishable from a real and
      // excellent result on a chart.
      for (const [index, key] of buckets.entries()) {
        const isGap = line.gapBuckets.includes(key);
        expect(points[index].includes("—"), key).toBe(isGap);
        // Said the other way round as well, because the em dash is what the
        // list prints and the zero is what the criterion forbids: a mean with
        // an empty denominator must never reach a reader as a number.
        if (isGap) expect(points[index], key).not.toMatch(/:\s*0/);
      }
      gapsSeen += line.gapBuckets.length;
      markedSeen += line.lowConfidenceBuckets.length;

      // The footnote counts the same gaps this line just drew, rather than
      // leaving a reader to count em dashes.
      expect(settlement.footnote).toContain(
        `${line.label} ${String(line.gapBuckets.length)}/${String(expected.bucketKeys.length)} without data`,
      );
    }
    await expect(byTestId(page, "trend-settlement-footnote")).toHaveText(settlement.footnote);

    // Guards, so the loop above cannot pass by describing nothing: a build whose
    // sparse window came back dense would satisfy every comparison in it.
    expect(gapsSeen, "a twelve-week sector split with no gaps at all").toBeGreaterThan(0);
    expect(markedSeen, "a twelve-week sector split with no thin bucket").toBeGreaterThan(0);

    // The thin buckets say so in the accessible list and are flagged on the
    // point, which is the reader's and the pointer's halves of one verdict.
    const marked = expected.card["trend-volume"].lines
      .flatMap((line) => line.lowConfidenceBuckets.map((key) => [line.cohortKey, key]));
    const [cohortKey, bucketKey] = marked[0];
    const point = byTestId(page, "trend-volume-line")
      .and(page.locator(`[data-cohort-key='${cohortKey}']`))
      .getByTestId("trend-volume-point")
      .and(page.locator(`[data-bucket-key='${bucketKey}']`));
    await expect(point).toHaveAttribute("data-low-confidence", "true");
    await expect(point).toContainText("(low confidence)");
  });

  test("a settlement point drills to the settled claims, not to its whole bucket (AC 6)", async ({
    page,
  }) => {
    // The pointer path, and the only place it can be exercised: the dots live
    // inside an `aria-hidden` figure that jsdom never lays out, so a component
    // test cannot click one. What is under test is that the URL a *settlement*
    // point builds carries the population that point was folded from — the mean
    // is over settled claims, so a drill carrying only the bucket's dates opens
    // a list several times longer than the figure that was clicked, under a
    // heading quoting that figure.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/trends");
    await trendsSettled(page);

    await byTestId(page, "trend-settlement").locator("circle").first().click();
    await expect(byTestId(page, "drill-claims")).toBeVisible();

    // The filters are read back off the URL rather than assumed, so the
    // assertion does not depend on which dot the click landed on — what it
    // depends on is that the stage facet is there and that the list reconciles
    // with it.
    const opened = new URL(page.url());
    const filters: DrillFilters = {
      stage: opened.searchParams.get("filter[stage]") ?? undefined,
      fnolFrom: opened.searchParams.get("filter[fnolFrom]") ?? undefined,
      fnolTo: opened.searchParams.get("filter[fnolTo]") ?? undefined,
    };
    expect(filters.stage).toBe("settled");
    expect(filters.fnolFrom).toBeTruthy();
    expect(filters.fnolTo).toBeTruthy();

    const settled = expectedDrillClaimsFor(ANALYST, filters);
    await expect(byTestId(page, "drill-count")).toContainText(settled.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(clearable(settled.chips));

    // …and the narrowing is a narrowing: the same period without the stage
    // facet is the whole bucket, which is the list this point used to open.
    const whole = expectedDrillClaimsFor(ANALYST, {
      fnolFrom: filters.fnolFrom,
      fnolTo: filters.fnolTo,
    });
    expect(settled.claimIds.length).toBeLessThan(whole.claimIds.length);
  });

  test("a cohort keeps its colour across every chart and across a refetch (AC 2)", async ({
    page,
  }) => {
    // `CATEGORICAL_FILLS`' own docstring records that a hue there means *rank
    // position*. The five metrics rank the same cohorts differently, so a build
    // that coloured a line by its position in its own chart would paint one
    // cohort two colours on one screen — and would repaint the whole section the
    // moment one cohort overtook another.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/trends");
    await trendsSettled(page);

    await reselect(page, "trend-cohort", "disability", "cohort=disability");
    const disability = expectedTrendsFor(ANALYST, { cohort: "disability" });

    for (const card of disability.cards) {
      expect(await lineKeys(page, card.testId), card.testId).toEqual(
        card.lines.map((line) => line.cohortKey),
      );
      expect(await lineFills(page, card.testId), card.testId).toEqual(
        card.lines.map((line) => line.fill),
      );
    }
    // …and the legend swatch is the same hue as the lines it names.
    expect(
      await byTestId(page, "trend-legend-link").evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-fill") ?? ""),
      ),
    ).toEqual(disability.legendFills);

    // Away to a nine-value dimension and back. The slots are assigned over the
    // *vocabulary*, so the return trip must reproduce them exactly rather than
    // re-deal them in whatever order the data now ranks — and the nine sectors
    // in between are what would have re-dealt them.
    await reselect(page, "trend-cohort", "sector", "cohort=sector");
    const sector = expectedTrendsFor(ANALYST, { cohort: "sector" });
    await expect(byTestId(page, "trend-legend-link")).toHaveText(sector.legendRows);
    // No `waitForResponse` on the way back: `useTrends` holds an answer for
    // thirty seconds, so this selection is served from the cache and never
    // reaches the network. The legend text is what says the swap landed.
    await byTestId(page, "trend-cohort").selectOption("disability");
    await expect(byTestId(page, "trend-legend-link")).toHaveText(disability.legendRows);
    expect(await lineFills(page, "trend-paid")).toEqual(
      disability.card["trend-paid"].lines.map((line) => line.fill),
    );

    // …and across a genuine refetch, which is the half a cached round-trip
    // cannot show: a reload throws away every slot the browser was holding, so
    // the hues below are the ones the *server* re-derived from the vocabulary.
    await page.reload();
    await trendsSettled(page);
    await reselect(page, "trend-cohort", "disability", "cohort=disability");
    expect(await lineFills(page, "trend-paid")).toEqual(
      disability.card["trend-paid"].lines.map((line) => line.fill),
    );
    expect(await lineKeys(page, "trend-paid")).toEqual(
      disability.card["trend-paid"].lines.map((line) => line.cohortKey),
    );

    // The severity cohort is the exception the AC names in words: it takes the
    // risk palette, so a High line is the same red as the High Risk KPI card and
    // the severity donut's High arc — three drawings of one `risk` derivation.
    await reselect(page, "trend-cohort", "severity_band", "cohort=severity_band");
    const severity = expectedTrendsFor(ANALYST, { cohort: "severity_band" });
    expect(await lineFills(page, "trend-volume")).toEqual(
      severity.card["trend-volume"].lines.map((line) => line.fill),
    );
    expect(new Set(await lineFills(page, "trend-volume"))).toEqual(
      new Set(["var(--color-error)", "var(--color-warn)", "var(--color-ok)"]),
    );
  });

  test("the keyboard's drill lands on Story 5.5's list with chips that clear (AC 1)", async ({
    page,
  }) => {
    // The period `<select>` plus "View claims" — the accessible path, because
    // every figure in the charts sits inside an `aria-hidden` figure and an SVG
    // circle has no focus and no accessible name. Unsplit, so the drill carries
    // the two date bounds and nothing else, which is the case that proves the
    // bounds are the *bucket's* rather than the window's.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/trends");
    await trendsSettled(page);

    const expected = expectedTrendsFor(ANALYST);
    const bucketKey = drillableBucket(expected);
    await byTestId(page, "trend-period").selectOption(bucketKey);
    await byTestId(page, "trend-view-claims").click();

    const filters = expected.drillFilters(bucketKey);
    const drill = expectedDrillClaimsFor(ANALYST, filters);
    await expect(byTestId(page, "drill-claims")).toBeVisible();
    await expect(byTestId(page, "drill-count")).toContainText(drill.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(clearable(drill.chips));
    // The bounds are the bucket's own inclusive days, so a claim filed on the
    // last of the month is inside the list its own point was counted in.
    const bounds = expected.bucketBounds[bucketKey];
    await expect(byTestId(page, "drill-chip")).toHaveText([
      `FNOL from: ${bounds.from}✕`,
      `FNOL to: ${bounds.to}✕`,
    ]);

    // **Individually** clearable, which is AC 6's word: clearing the upper bound
    // leaves the lower one applied and widens the list to everything filed since
    // the bucket opened, rather than dropping the whole narrowing.
    await byTestId(page, "drill-chip-remove").nth(1).click();
    const widened = expectedDrillClaimsFor(ANALYST, { fnolFrom: bounds.from });
    await expect(byTestId(page, "drill-chip")).toHaveText([`FNOL from: ${bounds.from}✕`]);
    await expect(byTestId(page, "drill-count")).toContainText(widened.count);

    // A row still opens the read-only claim view — the list is 5.5's list, not
    // a second one this section grew.
    await byTestId(page, "queue-card").first().click();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(widened.claimIds[0]);
    await expect(page.getByRole("textbox")).toHaveCount(0);
  });

  test("no query parameter widens the trends scope (AD-1, AD-7)", async ({ page }) => {
    // The seeded analyst is `scope_all`, so no HTTP request can demonstrate a
    // *narrowed* analyst — the gap Story 7.1 recorded rather than papered over,
    // and it stands in `deferred-work.md`. What can be demonstrated is that the
    // endpoint's answer is a function of the session alone: the three typed
    // parameters are the only ones it reads, and a smuggled scope, caller or
    // employer changes nothing about the response.
    await loginAs(page, ANALYST);
    await page.goto("/dashboard/trends");
    await trendsSettled(page);

    const smuggled = await page.evaluate(async () => {
      const honestResponse = await fetch("/api/dashboard/trends");
      const smuggledResponse = await fetch(
        "/api/dashboard/trends?employerId=1&scopeAll=true&as=7&userId=8&handlerId=1&scope=all",
      );
      return {
        honest: await honestResponse.text(),
        smuggled: await smuggledResponse.text(),
        status: smuggledResponse.status,
      };
    });
    expect(smuggled.status).toBe(200);
    expect(smuggled.smuggled).toBe(smuggled.honest);
    // …and byte-identical to *the right answer*, not merely to itself: the
    // window caption on screen was computed from the seed independently, so a
    // pair of matching wrong responses fails here.
    const expected = expectedTrendsFor(ANALYST);
    await expect(byTestId(page, "trend-window-caption")).toHaveText(expected.windowCaption);
  });

  test("a supervisor is refused the trends endpoint and bounced from its URL (AD-7)", async ({
    page,
  }) => {
    // The client guard is a courtesy; the role gate is the control. Asserted
    // through the API as well as the browser, because what is under test is that
    // the *endpoint* is gated and not merely that the route is hidden.
    await loginAs(page, SUPERVISOR);

    const refused = await page.request.get("/api/dashboard/trends");
    expect(refused.status()).toBe(403);
    expect(refused.headers()["cache-control"]).toBe("no-store");
    expect(((await refused.json()) as { type: string }).type).toBe(
      "/problems/fraud-analytics-not-permitted",
    );

    // …and the section is unreachable from the address bar, with the dashboard
    // Epic 5 shipped still on screen underneath.
    await page.goto("/dashboard/trends");
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "trend-analytics")).toHaveCount(0);
    await expect(byTestId(page, "workspace-nav")).toHaveCount(0);
  });
});
