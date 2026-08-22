import type { Page } from "@playwright/test";

import { PERSONAS, loginAs } from "../fixtures/login";
import {
  expectedAdequacyFor,
  expectedBreakdownFor,
  expectedDrillClaimsFor,
  expectedFinancialsFor,
  expectedSegmentedFor,
  workspaceUrl,
  type Segmentation,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 7.4 — Financial Decomposition.
 *
 * Nothing here stubs HTTP. The browser logs in for real, `services/worklist`
 * folds the seeded portfolio under the caller's employer scope for real,
 * `services/financials` reaches every reserve verdict for real, and every figure
 * on screen is compared against the seed folded independently
 * (`fixtures/seed.ts`, the Story 7.4 banner).
 *
 * **What this spec is defending against is a number that is plausible.** Three
 * of the surfaces here publish money, and money is the one thing on this console
 * a reader cannot sanity-check by eye: `$1,670,497` and `$2,140,998` are equally
 * believable portfolio figures, and they are the paid total and the projected
 * total. So:
 *
 * - Every money assertion is against a **rendered string** produced by a second
 *   implementation of `formatCents`, because comparing integers would pass
 *   against a card that printed cents as dollars.
 * - Every drill assertion is **set identity** rather than a count. Two
 *   populations of the same size are exactly what a count cannot see, and on this
 *   section they are one predicate apart — `surgery_required` splits the seeded
 *   book at 39 and `osha_recordable` at 41.
 * - The reserve distribution is checked bucket by bucket against a verdict this
 *   file computes from the seed with its own schedule projection, which is Story
 *   3.2's oracle. AC 2's "never a re-derivation" is a claim about *agreement*,
 *   and agreement is what an oracle can check.
 *
 * **The URL is the state, so the URL is what is asserted.** The `groupBy`
 * control and the four kinds of drill target are all read back off `page.url()`
 * or off the list they open: a control that held its grouping in component state
 * would satisfy every rendering assertion in this file and fail AC 7.
 *
 * **One `@smoke`.** The happy path is the whole of AC 1 and AC 3 in one gesture:
 * open the section, read the three totals and the adequacy distribution, narrow
 * by two dimensions, watch every figure recompute over the intersection, click a
 * cost-driver cohort, and land on the list holding exactly those claims.
 */

const ANALYST = PERSONAS.analyst;
const SUPERVISOR = PERSONAS.fullPortfolioSupervisor;

const FINANCIAL = "/dashboard/financials";

/**
 * Two dimensions, and deliberately one stored column and one **derived band**.
 *
 * `test_segmentation.py`'s pair and the 7.3 spec's, reused so the two suites
 * narrow on the same intersection: it is what makes "folded over the
 * intersection" a real intersection rather than two readings of one column, and
 * `severityBand` is the half a SQL push-down could not express.
 *
 * `med` rather than `high` for the same reason the 7.3 spec chose it — the smoke
 * path narrows, then clicks a cohort, then opens a claim, so the intersection
 * has to be non-empty on the seeded book. The oracle checks the emptiness rather
 * than this comment being trusted.
 */
const TWO: Segmentation = { sector: "Aerospace", severityBand: "med" };

/** The section has answered — a figure is on screen, not just a frame. */
async function financialsSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "financial-kpi-paid-value")).toBeVisible();
}

/** The adequacy donut has answered — its legend is drawn. */
async function adequacySettled(page: Page): Promise<void> {
  await expect(
    byTestId(page, "reserve-adequacy-distribution-legend-row").first(),
  ).toBeVisible();
}

/** The claim ids the drill list is showing, in the order it drew them. */
async function drilledClaimIds(page: Page): Promise<string[]> {
  return byTestId(page, "queue-card").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-claim-id") ?? ""),
  );
}

test.describe("@story:7-4 @epic:7 financial decomposition", () => {
  test("@smoke an analyst decomposes the book, narrows it, and drills a cost driver", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto(FINANCIAL);
    await financialsSettled(page);
    await adequacySettled(page);

    // AC 1: paid, reserve and projected across the scoped portfolio, every one
    // of them folded server-side. Rendered strings, because the defect a reader
    // cannot see is a scale error rather than a wrong claim set.
    const whole = expectedFinancialsFor(ANALYST);
    await expect(byTestId(page, "financial-kpi-paid-value")).toHaveText(whole.money.paid);
    await expect(byTestId(page, "financial-kpi-reserve-value")).toHaveText(
      whole.money.reserve,
    );
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      whole.money.projected,
    );

    // …and the breakdown by the default dimension, ranked by projected cost and
    // labelled with the employer names the server resolved — an id is the one
    // value on this payload a browser could not read.
    const byEmployer = expectedBreakdownFor(ANALYST, "employerId", 12);
    await expect(byTestId(page, "financial-breakdown-bars-value-link")).toHaveText(
      byEmployer.rows.map((row) => `${row.label}: ${row.value}`),
    );

    // AC 2: the reserve distribution, bucket by bucket, against a verdict this
    // file computes with its own schedule projection and its own band
    // arithmetic. All five, including the ones at zero — a rule's vocabulary is
    // complete for every book, and a chart missing an empty bucket cannot be
    // told from one that forgot to draw it.
    const adequacy = expectedAdequacyFor(ANALYST);
    await expect(byTestId(page, "reserve-adequacy-distribution-legend-row")).toHaveText(
      adequacy.buckets.map((bucket) => `${bucket.label}:${String(bucket.count)}`),
    );
    // The centre figure, **with its accessible caption**, which is one string in
    // the DOM: the donut renders an `sr-only` label beside the number precisely
    // so a screen reader is told what the figure counts. Asserting the pair
    // rather than the digits is what makes the population claim checkable —
    // "Claims in this portfolio" over a nine-dimension subset is exactly the
    // false sentence Story 7.3's review found twice.
    await expect(byTestId(page, "reserve-adequacy-distribution-total")).toHaveText(
      `Claims in this portfolio: ${String(adequacy.total)}`,
    );

    // AC 3: both cost-driver comparisons, quantified. The **average** is the
    // headline, because there are three litigated claims and ninety-seven
    // without: a comparison of sums says litigation is a rounding error, which
    // is true of the total and false of the claim.
    await expect(byTestId(page, "cost-driver-surgery-with-average")).toHaveText(
      whole.surgery.withDriver.average ?? "—",
    );
    await expect(byTestId(page, "cost-driver-surgery-without-average")).toHaveText(
      whole.surgery.withoutDriver.average ?? "—",
    );
    // …and the litigated cohort's *paid* figure is exactly zero, because every
    // litigated claim on this book is still open. A card comparing paid alone
    // would report that litigation costs nothing.
    await expect(byTestId(page, "cost-driver-litigation-with-paid")).toHaveText(
      whole.litigation.withDriver.money.paid,
    );

    // Two dimensions, applied through the segmentation bar the section inherits
    // from Story 7.3 — which is what proves the bar is mounted here at all.
    await byTestId(page, "segmentation-picker-sector").selectOption("Aerospace");
    await byTestId(page, "segmentation-picker-severityBand").selectOption("med");

    const narrowed = expectedFinancialsFor(ANALYST, TWO);
    expect(narrowed.claimsInScope).toBeGreaterThan(0);
    // AC 1 under a filter: every figure recomputes over the intersection.
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      narrowed.money.projected,
    );
    await expect(byTestId(page, "cost-driver-surgery-with-average")).toHaveText(
      narrowed.surgery.withDriver.average ?? "—",
    );
    const narrowedAdequacy = expectedAdequacyFor(ANALYST, TWO);
    // …and the caption changes with it: under a filter the centre figure counts
    // an intersection, and saying "portfolio" here would be a statement about
    // the book that the book does not support.
    await expect(byTestId(page, "reserve-adequacy-distribution-total")).toHaveText(
      `Claims matching these filters: ${String(narrowedAdequacy.total)}`,
    );
    // …and the caption names the population rather than calling it the
    // portfolio, which on a money surface would be a false statement quoted in
    // dollars.
    await expect(byTestId(page, "financial-kpi-paid")).toContainText(
      `${String(narrowed.claimsInScope)} matching claims`,
    );

    // AC 3's second half: a cohort is a click target, and the list it opens
    // carries the segmentation *and* the cohort as independently clearable
    // chips — three of them, in `FILTER_KEYS` order, so `surgery` is drawn
    // between the severity band and the sector.
    await byTestId(page, "cost-driver-surgery-with").click();
    const drilled = expectedDrillClaimsFor(ANALYST, {
      sector: "Aerospace",
      severityBand: "med",
      surgery: "true",
    });
    expect(drilled.claimIds.length).toBeGreaterThan(0);
    await expect(byTestId(page, "drill-count")).toContainText(drilled.count);
    await expect(byTestId(page, "drill-chip")).toHaveText([
      "Severity: Medium✕",
      "Surgery required: Yes✕",
      "Sector: Aerospace✕",
    ]);
    // **Set identity, not a count** — the whole discipline of this file: two
    // populations of the same size are one predicate apart on this card.
    expect(await drilledClaimIds(page)).toEqual(drilled.claimIds);
    // …and the cohort card's own claim count is that same population.
    expect(drilled.claimIds).toEqual(narrowed.surgery.withDriver.claimIds.filter(
      (id) => drilled.claimIds.includes(id),
    ));
    expect(drilled.claimIds.length).toBe(narrowed.surgery.withDriver.claimCount);

    // …and a row opens the read-only claim view: the case header renders and
    // nothing on the page can write.
    await byTestId(page, "queue-card").first().click();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(drilled.claimIds[0]);
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("combobox")).toHaveCount(0);
  });

  test("a verdict segment opens exactly the claims it counted (AC 2)", async ({ page }) => {
    await loginAs(page, ANALYST);
    await page.goto(FINANCIAL);
    await adequacySettled(page);

    // The bucket with the most claims that is *not* `closed_final`, so the drill
    // is a real narrowing of the open book rather than the settled majority —
    // and picked from the oracle rather than named here, so a retune of the
    // reserve bands moves which bucket is asserted instead of failing this test
    // for the wrong reason.
    const adequacy = expectedAdequacyFor(ANALYST);
    const bucket = adequacy.buckets
      .filter((entry) => entry.verdict !== "closed_final" && entry.count > 0)
      .sort((a, b) => b.count - a.count)[0];
    expect(bucket).toBeDefined();

    await byTestId(page, "reserve-adequacy-distribution-legend-link")
      .and(page.locator(`[data-key='${bucket.verdict}']`))
      .click();

    // The list's `total` is the segment's count — the drill-through's founding
    // promise, on the one facet whose value is not a column and cannot become
    // one. `filter[reserveVerdict]` is matched through the same computation the
    // segment was counted with, which is what makes the two agree.
    await expect(byTestId(page, "drill-count")).toContainText(
      `${String(bucket.count)} in view`,
    );
    await expect(byTestId(page, "drill-chip")).toHaveText([
      `Reserve: ${bucket.label}✕`,
    ]);
    // Set identity: a plausible re-derivation would produce a list of the right
    // size holding different claims.
    expect((await drilledClaimIds(page)).sort()).toEqual([...bucket.claimIds].sort());
  });

  test("a breakdown group opens its own dimension's facet (AC 3 pattern)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto(FINANCIAL);
    await financialsSettled(page);

    // Regroup by a dimension whose values are the stored strings, so the group
    // key, the chip's value and the drill facet are visibly one word. `sector`
    // rather than `employerId`, because an id would let a mislabelled group pass
    // — the label and the key differ there, and the whole point of the facet
    // being the group's key is that they do not have to be reconciled.
    await byTestId(page, "financial-group-by").selectOption("sector");

    const bySector = expectedBreakdownFor(ANALYST, "sector", 12);
    await expect(byTestId(page, "financial-breakdown-bars-value-link")).toHaveText(
      bySector.rows.map((row) => `${row.label}: ${row.value}`),
    );
    // The control's state is in the URL under the route's own bare name, so a
    // grouping is as shareable as the filter beside it.
    expect(page.url()).toContain("groupBy=sector");

    await byTestId(page, "financial-breakdown-bars-value-link").first().click();

    const drilled = expectedDrillClaimsFor(ANALYST, { sector: bySector.rows[0].key });
    await expect(byTestId(page, "drill-count")).toContainText(drilled.count);
    expect(await drilledClaimIds(page)).toEqual(drilled.claimIds);
  });

  test("a shared URL restores the filter and the grouping (AC 7)", async ({ page }) => {
    await loginAs(page, ANALYST);
    // A link somebody sent: two dimensions *and* the section control, which is
    // the pair that proves one query string carries both schemes — `filter[…]`
    // for the segmentation and a bare named parameter for the control.
    await page.goto(`${workspaceUrl(FINANCIAL, TWO)}&groupBy=state`);
    await financialsSettled(page);

    const expected = expectedFinancialsFor(ANALYST, TWO);
    const segmented = expectedSegmentedFor(ANALYST, TWO);
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      expected.money.projected,
    );
    await expect(byTestId(page, "segmentation-count")).toHaveText(segmented.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(
      segmented.chips.map((chip) => `${chip}✕`),
    );
    // The control restores **from the server's echo**: the payload carries the
    // dimension it actually grouped by, so a control showing it is showing the
    // answer rather than its own reading of the URL.
    await expect(byTestId(page, "financial-group-by")).toHaveValue("state");

    // …and a reload re-resolves scope server-side and lands on the same screen.
    await page.reload();
    await financialsSettled(page);
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      expected.money.projected,
    );
    await expect(byTestId(page, "financial-group-by")).toHaveValue("state");
  });

  test("an impossible combination empties every card without erroring (AC 5)", async ({
    page,
  }) => {
    // A sector and a state no claim carries together — checked against the
    // oracle rather than assumed, because a combination that turned out to be
    // satisfiable would make this test assert nothing at all.
    const impossible: Segmentation = { sector: "Aerospace", state: "MI" };
    expect(expectedFinancialsFor(ANALYST, impossible).claimsInScope).toBe(0);

    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(FINANCIAL, impossible));
    await financialsSettled(page);

    // The three tiles render their zeros — a total of zero over an empty segment
    // is an answer rather than an absence — and every card below says the same
    // sentence, which is "these filters" and not "this portfolio". The second
    // would be a false statement about the book, in dollars, on the one screen
    // an analyst might quote out loud.
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText("$0");
    for (const surface of [
      "financial-breakdown-bars",
      "reserve-adequacy-distribution",
      "cost-driver-surgery",
      "cost-driver-litigation",
    ]) {
      await expect(byTestId(page, `${surface}-empty`)).toHaveText(
        "No claims match these filters.",
      );
    }
    // …the cohort averages are an em dash rather than `$0`: a mean over an empty
    // set is not zero, and `$0` would say surgical claims cost nothing.
    await expect(byTestId(page, "cost-driver-surgery-with-average")).toHaveCount(0);
    // …nothing errored…
    await expect(byTestId(page, "financial-totals-error")).toHaveCount(0);
    // …and the chips are still clearable, which is the half a rendered zero
    // cannot demonstrate: an analyst stranded on an empty section with no chip
    // has only the address bar.
    await byTestId(page, "drill-chip-remove").first().click();
    const widened = expectedFinancialsFor(ANALYST, { sector: "Aerospace" });
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      widened.money.projected,
    );
  });

  test("no query parameter widens the segmented book (AD-1, AD-7)", async ({ page }) => {
    // The seeded analyst is `scope_all`, so no HTTP request can demonstrate a
    // *narrowed* analyst — the gap Stories 7.1, 7.2 and 7.3 each recorded rather
    // than papered over, and it stands in `deferred-work.md`. What can be
    // demonstrated is that the answer is a function of the session plus the
    // declared parameters: a smuggled scope, caller or employer changes nothing.
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(FINANCIAL, TWO));
    await financialsSettled(page);

    const smuggled = await page.evaluate(async () => {
      const query = "filter[sector]=Aerospace&filter[severityBand]=med";
      const honestResponse = await fetch(`/api/dashboard/financials?${query}`);
      const smuggledResponse = await fetch(
        `/api/dashboard/financials?${query}` +
          "&employerId=1&scopeAll=true&as=7&userId=8&handlerId=1&scope=all",
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
    // figure on screen was computed from the seed independently, so a pair of
    // matching wrong responses fails here.
    const expected = expectedFinancialsFor(ANALYST, TWO);
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      expected.money.projected,
    );
  });

  test("a supervisor is refused both financial routes and sees no section", async ({
    page,
  }) => {
    // The client guard is a courtesy; the role gate is the control. Asserted
    // through the API as well as the browser, because what is under test is that
    // *both* endpoints are gated — a gate added to one route of a section and
    // forgotten on its sibling is the ordinary way a workspace half-opens.
    await loginAs(page, SUPERVISOR);

    for (const path of [
      "/api/dashboard/financials",
      "/api/dashboard/financials/reserve-adequacy",
    ]) {
      const refused = await page.request.get(path);
      expect(refused.status(), path).toBe(403);
      expect(refused.headers()["cache-control"]).toBe("no-store");
      expect(((await refused.json()) as { type: string }).type).toBe(
        "/problems/fraud-analytics-not-permitted",
      );
    }

    // …and typing the URL lands on the supervisor's own dashboard rather than on
    // an error page: the server said this caller is a supervisor, so their own
    // dashboard is the correct destination.
    await page.goto(FINANCIAL);
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "financial-decomposition")).toHaveCount(0);
  });

  test("the analyst's navigation carries the filter into the Financial section", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl("/dashboard/fraud", TWO));
    await expect(byTestId(page, "segmentation-count")).toBeVisible();

    // A plain link would drop the query string, which is a filter silently
    // widening back to the whole portfolio the moment an analyst moves between
    // two views of one filtered book — and it looks like a correct page, because
    // every figure on it is right for a filter nobody cancelled.
    await byTestId(page, "nav-financial").click();
    await financialsSettled(page);

    const expected = expectedFinancialsFor(ANALYST, TWO);
    await expect(byTestId(page, "financial-kpi-projected-value")).toHaveText(
      expected.money.projected,
    );
    await expect(byTestId(page, "nav-financial")).toHaveAttribute("aria-current", "page");
  });
});
