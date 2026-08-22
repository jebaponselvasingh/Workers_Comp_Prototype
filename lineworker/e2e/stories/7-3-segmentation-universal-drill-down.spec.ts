import type { Page } from "@playwright/test";

import { PERSONAS, loginAs } from "../fixtures/login";
import {
  expectedDimensionValuesFor,
  expectedDrillClaimsFor,
  expectedSegmentedFor,
  workspaceUrl,
  type Segmentation,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 7.3 — Segmentation & Universal Drill-Down.
 *
 * Nothing here stubs HTTP. The browser logs in for real, `services/worklist`
 * narrows and folds the seeded portfolio under the caller's employer scope for
 * real, and every figure on screen is compared against the seed filtered
 * independently (`fixtures/seed.ts`, the Story 7.3 banner).
 *
 * **What this spec is defending against is an oracle that agrees.** A filter is
 * a *cut*, and Story 7.1's review found `seed.ts::topRate` carrying the same
 * sort-then-cut defect as the implementation — so the one property that was
 * wrong was the one the independent check could not see. Two things follow:
 *
 * - The oracle shares **no predicate** with the drill oracle above it in that
 *   file, although six of the ten dimensions overlap. An oracle that routed both
 *   surfaces through one restatement could not notice the day the two
 *   vocabularies came apart, which is precisely the property this story claims.
 * - The assertions are **set identity** — which claim ids survive — rather than
 *   counts. Two populations of the same size are exactly the failure a count
 *   cannot see, and on this surface they are one plausible predicate apart.
 *
 * **The URL is the state, so the URL is what is asserted.** Every check below
 * either reads `page.url()` or reloads the page and reads the screen again: a
 * control that held its filter in component state would satisfy every rendering
 * assertion in this file and fail AC 3.
 *
 * **One `@smoke`.** The happy path is the whole of AC 1 and AC 2 in one gesture:
 * narrow by two dimensions, watch both sections recompute, click a chart
 * segment, and land on a list carrying the segmentation *and* the slice — three
 * independently clearable chips over exactly the intersection.
 */

const ANALYST = PERSONAS.analyst;
const SUPERVISOR = PERSONAS.fullPortfolioSupervisor;

const FRAUD = "/dashboard/fraud";
const TRENDS = "/dashboard/trends";

/**
 * Two dimensions, and deliberately one stored column and one **derived band**.
 *
 * The pair is what makes "folded over the intersection" a real intersection
 * rather than two readings of one column — and `severityBand` is the half a SQL
 * push-down could not express, which is why the whole filter is applied in the
 * service over rows a scoped read returned.
 *
 * `med` rather than `high`, and the choice is about the *drill* three assertions
 * later: the smoke path narrows, then clicks the high fraud band, then opens a
 * claim — so the three-way intersection has to be non-empty on the seeded book
 * or the last step would be asserting an empty list. `Aerospace` × `med` × the
 * high fraud band is three claims; `Aerospace` × `high` is two claims and
 * neither scores into the high band. The oracle checks the emptiness rather than
 * this comment being trusted.
 */
const TWO: Segmentation = { sector: "Aerospace", severityBand: "med" };

/** The Fraud section has answered — a figure is on screen, not just a frame. */
async function fraudSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "fraud-kpi-flagged-value")).toBeVisible();
}

/** The segmentation bar has answered — its pickers are populated. */
async function barSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "segmentation-count")).toBeVisible();
}

/** One picker's options, as a reader sees them. */
async function optionsOf(page: Page, dimension: string): Promise<string[]> {
  return byTestId(page, `segmentation-picker-${dimension}`)
    .locator("option")
    .evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim() ?? ""));
}

test.describe("@story:7-3 @epic:7 segmentation and universal drill-down", () => {
  test("@smoke an analyst narrows the workspace and drills into the intersection", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto(FRAUD);
    await fraudSettled(page);
    await barSettled(page);

    // The pickers offer exactly what the caller's book carries — folded from her
    // own scoped rows, so a control cannot offer a value with no claims behind
    // it and cannot name anything outside the book. Compared as a list because
    // the order is the server's: free text sorts by what is on screen.
    expect(await optionsOf(page, "sector")).toEqual([
      "Any",
      ...expectedDimensionValuesFor(ANALYST, "sector"),
    ]);
    // …and the age band reads as a *range*, composed in the browser from the
    // three edges the server published. The wire values carry no numbers at all,
    // deliberately, so that moving an edge in `derivation_thresholds` cannot
    // leave a member name asserting the old one.
    expect(await optionsOf(page, "ageGroup")).toEqual([
      "Any",
      ...expectedDimensionValuesFor(ANALYST, "ageGroup"),
    ]);

    // Two dimensions, applied one at a time through the control — which is how
    // an analyst actually composes one, and which is what proves the second does
    // not clear the first.
    await byTestId(page, "segmentation-picker-sector").selectOption("Aerospace");
    await byTestId(page, "segmentation-picker-severityBand").selectOption("med");

    const expected = expectedSegmentedFor(ANALYST, TWO);
    // AC 1: every figure in the section is folded over the intersection,
    // server-side. The bar's own sentence is a *rendered string*, so a figure
    // published on the wrong scale fails here rather than passing a comparison
    // of integers.
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(
      expected.chips.map((chip) => `${chip}✕`),
    );
    // AC 3: the state is in the URL and nowhere else, in the drill list's own
    // `filter[…]` spelling — which is what makes the next click a merge rather
    // than a translation.
    //
    // Asserted parameter by parameter rather than against a whole query string,
    // because the *order* of the two in the URL is the order the analyst chose
    // them in and is deliberately not canonical: the hook edits the current
    // parameters rather than rebuilding them, which is what stops a filter
    // change from clearing a section control. Nothing downstream depends on it —
    // the chip row is drawn in the vocabulary's order (asserted above) and the
    // cache key goes through `toFilterKey`, which serialises in that same order
    // — so two links naming the same two dimensions are one resource whichever
    // way round they were typed.
    for (const [dimension, value] of Object.entries(TWO)) {
      expect(page.url()).toContain(
        `${encodeURIComponent(`filter[${dimension}]`)}=${encodeURIComponent(value)}`,
      );
    }

    // The other section recomputes under the same filter, from the same URL —
    // which is the difference between a filter that belongs to the workspace and
    // one that belongs to a card.
    await byTestId(page, "nav-trends").click();
    await expect(byTestId(page, "trend-window-caption")).toBeVisible();
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
    await expect(byTestId(page, "trend-window-caption")).toContainText(
      `${String(expected.claimIds.length)} claims`,
    );

    // AC 2: a chart segment opens the claims behind it, carrying the active
    // segmentation *and* the clicked slice. Keyed on the segment rather than on
    // its position, `7-1`'s ruling: a positional click would open a different
    // band's list the moment the vocabulary changed.
    await byTestId(page, "nav-fraud").click();
    await fraudSettled(page);
    await byTestId(page, "fraud-band-distribution-legend-link")
      .and(page.locator("[data-key='high']"))
      .click();

    const drilled = expectedDrillClaimsFor(ANALYST, {
      sector: "Aerospace",
      severityBand: "med",
      fraudBand: "high",
    });
    // The three-way intersection has to hold claims, or the row click below
    // would be asserting an empty list — checked rather than assumed.
    expect(drilled.claimIds.length).toBeGreaterThan(0);
    await expect(byTestId(page, "drill-count")).toContainText(drilled.count);
    // Three chips, in `FILTER_KEYS` order — so the fraud band is drawn *between*
    // the severity band and the sector although the analyst chose the sector
    // first, and each is independently clearable.
    await expect(byTestId(page, "drill-chip")).toHaveText([
      "Severity: Medium✕",
      "Fraud band: High✕",
      "Sector: Aerospace✕",
    ]);
    expect(
      await byTestId(page, "queue-card").evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-claim-id") ?? ""),
      ),
    ).toEqual(drilled.claimIds);

    // …and a row opens the read-only claim view: the case header renders and
    // nothing on the page can write.
    await byTestId(page, "queue-card").first().click();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(drilled.claimIds[0]);
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("combobox")).toHaveCount(0);
  });

  test("a shared URL restores the whole workspace state (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    // A link somebody sent: two dimensions *and* a section control, which is the
    // pair that proves one query string carries both schemes. `grain` is a bare
    // named parameter under the route's own name; the filters are `filter[…]`.
    await page.goto(`${workspaceUrl(TRENDS, TWO)}&grain=quarter&cohort=disability`);
    await barSettled(page);

    const expected = expectedSegmentedFor(ANALYST, TWO);
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(
      expected.chips.map((chip) => `${chip}✕`),
    );
    // The controls restore too, and they restore *from the server's echo* — the
    // payload carries the grain and the cohort it actually applied, so a control
    // showing them is showing the answer rather than its own reading of the URL.
    await expect(byTestId(page, "trend-grain")).toHaveValue("quarter");
    await expect(byTestId(page, "trend-cohort")).toHaveValue("disability");

    // …and a reload re-resolves scope server-side and lands on the same screen.
    await page.reload();
    await barSettled(page);
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
    await expect(byTestId(page, "trend-grain")).toHaveValue("quarter");
  });

  test("an impossible combination is a zero-result state, not an error (AC 4)", async ({
    page,
  }) => {
    // A sector and a state no claim carries together — checked against the
    // oracle rather than assumed, because a combination that turned out to be
    // satisfiable would make this test assert nothing at all.
    const impossible: Segmentation = { sector: "Aerospace", state: "MI" };
    const expected = expectedSegmentedFor(ANALYST, impossible);
    expect(expected.claimIds).toEqual([]);

    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(FRAUD, impossible));
    await barSettled(page);

    // Distinct from loading and from error: a sentence, a zero, and the chips
    // still on screen — an analyst stranded on an empty workspace with no
    // clearable chip has only the address bar.
    await expect(byTestId(page, "segmentation-empty")).toBeVisible();
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(
      expected.chips.map((chip) => `${chip}✕`),
    );
    await expect(byTestId(page, "segmentation-error")).toHaveCount(0);
    // The section underneath renders its own zero state rather than a broken
    // layout: the band distribution is a *rule's* vocabulary, so all three
    // segments are still published and all three read zero.
    await expect(byTestId(page, "fraud-kpi-flagged-value")).toHaveText("0");

    // **AC 4 is per surface, not per bar.** Every card, chart and table on the
    // section says the same thing, and it says "these filters" — not "this
    // portfolio", which is a statement about the book that a zero-claim
    // *intersection* of the book does not support. "No claim in this portfolio
    // is under SIU review" on this screen would be false about the one thing an
    // analyst might repeat out loud.
    for (const surface of [
      "fraud-band-distribution",
      "siu-pipeline-stage",
      "siu-pipeline-handler",
      "fraud-rate-injury",
      "fraud-rate-employer",
      "fraud-rate-handler",
    ]) {
      await expect(byTestId(page, `${surface}-empty`)).toHaveText(
        "No claims match these filters.",
      );
    }
    await expect(byTestId(page, "fraud-flagged-empty")).toHaveText(
      "No claims match these filters.",
    );
    // …and the red-flag card, which is the one that *misattributes* rather than
    // merely overstates: its coverage sentence blames a cold AI cache for what
    // the filter did, and the two have nothing in common as fixes.
    await expect(byTestId(page, "fraud-red-flag-card-empty")).toContainText(
      "matching these filters",
    );

    // The other section too, where an empty series has a second plausible cause
    // — the period — and therefore a second wrong control to send the reader to.
    await byTestId(page, "nav-trends").click();
    await expect(byTestId(page, "trend-window-caption")).toBeVisible();
    await expect(byTestId(page, "trend-volume-empty")).toHaveText(
      "No claims match these filters in this window.",
    );
    await expect(byTestId(page, "trend-settlement-empty")).toHaveText(
      "No claims matching these filters have settled in this window.",
    );
    await byTestId(page, "nav-fraud").click();
    await barSettled(page);

    // …and clearing one chip widens the view rather than stranding it, which is
    // the half of AC 4 a rendered zero cannot demonstrate on its own.
    await byTestId(page, "drill-chip-remove").first().click();
    const widened = expectedSegmentedFor(ANALYST, { sector: "Aerospace" });
    await expect(byTestId(page, "segmentation-count")).toHaveText(widened.count);
    await expect(byTestId(page, "segmentation-empty")).toHaveCount(0);
  });

  test("an age-band chip reads the same range on the workspace and on the list (AC 2)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(FRAUD, { ageGroup: "older" }));
    await barSettled(page);

    // The label is a *range* composed in the browser from the three edges the
    // server publishes — the wire value carries no numbers at all, deliberately.
    const composed = expectedSegmentedFor(ANALYST, { ageGroup: "older" }).chips[0];
    await expect(byTestId(page, "drill-chip")).toHaveText([`${composed}✕`]);

    // …and it is the *same string* one click later, on Story 5.5's list. It was
    // not: the drill list drew "Age group: older" because only the workspace bar
    // had fetched the edges, so one value rendered under two names one click
    // apart — which is what AC 2's "survives the click as a clearable chip"
    // denies. The list now publishes the same three edges from the same
    // document, composed through the same function.
    await byTestId(page, "fraud-flagged-view-all").click();
    await expect(byTestId(page, "drill-count")).toBeVisible();
    // Picked out by its own dimension rather than by position, because the list
    // carries the flagged rule's chip as well — the merge every drill target on
    // this workspace goes through.
    await expect(
      byTestId(page, "drill-chip").filter({ hasText: "Age group" }),
    ).toHaveText(`${composed}✕`);
  });

  test("a URL carrying a refused value clears that dimension and keeps the rest (AC 5)", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);
    // A stale link: `filter[ageGroup]=25_34` is exactly the spelling a reader
    // would guess from the label, and the band's members are ordinal words
    // precisely so a range never becomes a wire value. The server refuses it with
    // a 422 that names the parameter.
    await page.goto(`${workspaceUrl(FRAUD, { sector: "Aerospace" })}&filter[ageGroup]=25_34`);
    await barSettled(page);

    // The refused dimension is gone from the URL…
    expect(page.url()).not.toContain("ageGroup");
    // …an inline message names it, by the word the analyst would have chosen it
    // under rather than by the parameter it was sent as…
    await expect(byTestId(page, "segmentation-refused")).toContainText("Age group");
    // …and the rest of the filter still applies, over the right claims.
    const expected = expectedSegmentedFor(ANALYST, { sector: "Aerospace" });
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
    await expect(byTestId(page, "drill-chip")).toHaveText(["Sector: Aerospace✕"]);
    // The page renders — the section beneath is not an error state.
    await fraudSettled(page);
  });

  test("no query parameter widens the segmented scope (AD-1, AD-7)", async ({
    page,
  }) => {
    // The seeded analyst is `scope_all`, so no HTTP request can demonstrate a
    // *narrowed* analyst — the gap Stories 7.1 and 7.2 recorded rather than
    // papered over, and it stands in `deferred-work.md`. What can be
    // demonstrated is that the answer is a function of the session plus the ten
    // declared dimensions: a smuggled scope, caller or employer changes nothing.
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(FRAUD, TWO));
    await barSettled(page);

    const smuggled = await page.evaluate(async () => {
      const honestResponse = await fetch(
        "/api/dashboard/fraud?filter[sector]=Aerospace&filter[severityBand]=med",
      );
      const smuggledResponse = await fetch(
        "/api/dashboard/fraud?filter[sector]=Aerospace&filter[severityBand]=med" +
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
    const expected = expectedSegmentedFor(ANALYST, TWO);
    await expect(byTestId(page, "segmentation-count")).toHaveText(expected.count);
  });

  test("a supervisor is refused the values endpoint and sees no bar", async ({
    page,
  }) => {
    // The client guard is a courtesy; the role gate is the control. Asserted
    // through the API as well as the browser, because what is under test is that
    // the *endpoint* is gated and not merely that the bar is hidden.
    await loginAs(page, SUPERVISOR);

    const refused = await page.request.get("/api/dashboard/segmentation/values");
    expect(refused.status()).toBe(403);
    expect(refused.headers()["cache-control"]).toBe("no-store");
    expect(((await refused.json()) as { type: string }).type).toBe(
      "/problems/fraud-analytics-not-permitted",
    );

    // …and the supervisor's dashboard is the element tree Epic 5 shipped, with
    // nothing inserted into it: no nav, and no filter bar over a portfolio whose
    // endpoints take no segmentation at all.
    await page.goto("/dashboard");
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "segmentation-bar")).toHaveCount(0);
    await expect(byTestId(page, "workspace-nav")).toHaveCount(0);
  });

  test("the bar is on the analyst's sections and not on their portfolio", async ({
    page,
  }) => {
    await loginAs(page, ANALYST);

    // The portfolio view is Epic 5's dashboard, scoped for the analyst rather
    // than borrowed from the supervisor, and its endpoints take no segmentation
    // — so a bar there would be a control that narrowed nothing and said so only
    // by leaving every figure unchanged.
    await page.goto("/dashboard");
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "segmentation-bar")).toHaveCount(0);

    await byTestId(page, "nav-fraud").click();
    await expect(byTestId(page, "segmentation-bar")).toBeVisible();

    await byTestId(page, "nav-trends").click();
    await expect(byTestId(page, "segmentation-bar")).toBeVisible();
  });
});
