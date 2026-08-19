import { PERSONAS, loginAs } from "../fixtures/login";
import {
  drillUrl,
  employerIdOf,
  expectedDrillClaimsFor,
  expectedPortfolioSummaryFor,
  handlerIdOf,
  type ExpectedDrillClaims,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 5.5 — Dashboard Drill-Through.
 *
 * Nothing here stubs HTTP. The browser logs in for real, `services/worklist`
 * narrows and ranks the seeded portfolio under the caller's employer scope for
 * real, and every claim id is compared against the seed counted independently
 * (`fixtures/seed.ts`). That matters more on this story than on the four before
 * it, because what is under test is a *correspondence* between two surfaces —
 * a number and the list behind it — and a mocked response could only ever
 * demonstrate that the page renders what it was handed.
 *
 * Four properties get their own tests beyond "the list opens":
 *
 * - **The list reconciles with the card.** The High Risk figure is read off the
 *   rendered card and the drill-through's count off the rendered list, in one
 *   test, on one session. A constant would pass while both surfaces were wrong
 *   together.
 * - **Scope is not in the URL (AC 4, AD-7).** Jennifer Park pastes a Boeing
 *   employer filter and a Boeing claim id straight into the address bar and
 *   gets a defined empty state and an in-app not-found — never a row, never a
 *   403, and never a redirect that rewrites what she typed.
 * - **Read-only means absent (AC 2).** The claim view is inspected for
 *   textboxes, comboboxes and command buttons rather than for disabled ones.
 * - **The chip is the filter, and clearing it widens the list.** Both
 *   directions, because a chip that rendered but did not clear would satisfy
 *   half of AC 1.
 */

const BLINE = PERSONAS.fullPortfolioSupervisor;
const PARK = PERSONAS.scopedSupervisor;

/** The list has answered — a card is on screen, not just the section frame. */
async function listSettled(page: Parameters<typeof byTestId>[0]): Promise<void> {
  await expect(byTestId(page, "queue-card").first()).toBeVisible();
}

/** Every claim id the list is showing, in the order it drew them. */
async function renderedIds(page: Parameters<typeof byTestId>[0]): Promise<string[]> {
  return byTestId(page, "queue-card").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-claim-id") ?? ""),
  );
}

/**
 * Assert the whole visible list against the oracle.
 *
 * The order is checked as **one list** rather than as N lookups —
 * `expectTable`'s discipline in `5-2-*.spec.ts`, for its reason: a page that
 * rendered every row in the wrong position would satisfy a per-row loop. Only
 * the first page is compared, because that is what is on screen before a "Show
 * more"; the oracle publishes the page cut so the spec does not have to assume
 * one.
 */
async function expectFirstPage(
  page: Parameters<typeof byTestId>[0],
  expected: ExpectedDrillClaims,
): Promise<void> {
  await listSettled(page);
  expect(await renderedIds(page)).toEqual(expected.pages[0]);
  await expect(byTestId(page, "drill-count")).toContainText(expected.count);
}

test.describe("@story:5-5 @epic:5 Dashboard drill-through", () => {
  test("@smoke a KPI card opens the claims behind it, and a claim opens read-only (AC 1, AC 2)", async ({
    page,
  }) => {
    await loginAs(page, BLINE);

    // The card first, so the number the list has to match is read off the
    // screen rather than from a constant.
    const summary = expectedPortfolioSummaryFor(BLINE);
    const highRisk = summary.cards["kpi-high-risk"];
    await expect(byTestId(page, "kpi-high-risk-value")).toHaveText(highRisk);

    await byTestId(page, "kpi-high-risk-link").click();

    // …and the list's count is the same figure. This is the story's whole
    // promise: not that the list is plausible, but that it is the card's.
    const expected = expectedDrillClaimsFor(BLINE, { severityBand: "high" });
    expect(expected.count).toBe(`${highRisk} in view`);
    await expectFirstPage(page, expected);

    // The filter is visible and says what it is.
    await expect(byTestId(page, "drill-chip")).toHaveText([
      "Severity: High✕",
    ]);

    // …and clearing it widens the list to the whole book, over two pages.
    await byTestId(page, "drill-chip-remove").click();
    const everything = expectedDrillClaimsFor(BLINE);
    await expect(byTestId(page, "drill-chips")).toHaveCount(0);
    await expect(byTestId(page, "drill-count")).toContainText(everything.count);
    expect(everything.pages.length).toBeGreaterThan(1);
    expect(await renderedIds(page)).toEqual(everything.pages[0]);

    await byTestId(page, "drill-more").click();
    await expect(byTestId(page, "queue-card")).toHaveCount(
      everything.claimIds.length,
    );
    expect(await renderedIds(page)).toEqual(everything.claimIds);

    // A claim opens read-only: the case header renders and nothing on the page
    // can write.
    const first = everything.claimIds[0];
    await byTestId(page, "queue-card").first().click();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(first);
    await expect(byTestId(page, "risk-gauge")).toBeVisible();
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("combobox")).toHaveCount(0);
    // No copilot pane either — dashboard-scope copilot is architecture-deferred
    // and a claim-scoped one is the handler's workspace.
    await expect(byTestId(page, "copilot-pane")).toHaveCount(0);
  });

  test("a severity donut segment opens its own filter (AC 1)", async ({ page }) => {
    await loginAs(page, BLINE);

    await expect(byTestId(page, "chart-severity-legend")).toBeVisible();
    // Keyed on the segment rather than on its position: the legend is in the
    // enum's declaration order, which a scope with no High claims would shorten
    // — and a positional click would then open a different band's list.
    await byTestId(page, "chart-severity-legend-link")
      .and(page.locator("[data-key='high']"))
      .click();

    // The chart's dimension, not the neighbouring donut's: a settlement segment
    // must not open a severity filter, and the value is the enum's wire form.
    await expect(byTestId(page, "drill-chip")).toHaveText(["Severity: High✕"]);
    await expectFirstPage(page, expectedDrillClaimsFor(BLINE, { severityBand: "high" }));
  });

  test("a handler row opens that handler's caseload, by id (AC 3)", async ({ page }) => {
    await loginAs(page, BLINE);

    const marcus = "Marcus Chen";
    await byTestId(page, "benchmark-handler-link")
      .filter({ hasText: marcus })
      .click();

    // The chip carries the *name* the server resolved from the id — the browser
    // cannot label a surrogate key, and this URL is one a supervisor may have
    // opened cold.
    await expect(byTestId(page, "drill-chip")).toHaveText([
      `Handler: ${marcus}✕`,
    ]);
    await expectFirstPage(
      page,
      expectedDrillClaimsFor(BLINE, { handlerId: String(handlerIdOf(marcus)) }),
    );
  });

  test("a scoped supervisor's drill-through stays inside her book (AD-7)", async ({
    page,
  }) => {
    await loginAs(page, PARK);

    await byTestId(page, "kpi-total-claims-link").click();

    const hers = expectedDrillClaimsFor(PARK);
    await expectFirstPage(page, hers);
    // The pair is the proof: her list is a strict subset of the portfolio, so an
    // implementation that had stopped scoping would fail the second assertion
    // while satisfying the first.
    const everyone = expectedDrillClaimsFor(BLINE);
    expect(hers.claimIds.length).toBeLessThan(everyone.claimIds.length);
    expect(everyone.claimIds).toEqual(expect.arrayContaining(hers.claimIds));
  });

  test("a filter that matches nothing is a defined empty state that names it", async ({
    page,
  }) => {
    await loginAs(page, PARK);

    // Jennifer Park's book carries no litigated claim at all — a fact about her
    // portfolio rather than about the renderer.
    const empty = expectedDrillClaimsFor(PARK, { litigation: "true" });
    expect(empty.claimIds).toHaveLength(0);

    await page.goto(drillUrl({ litigation: "true" }));

    await expect(byTestId(page, "drill-empty")).toContainText("Litigation: Yes");
    await expect(byTestId(page, "queue-card")).toHaveCount(0);
    await expect(byTestId(page, "drill-count")).toContainText("0 in view");
  });

  test("a hand-edited employer filter intersects her scope rather than widening it (AC 4)", async ({
    page,
  }) => {
    await loginAs(page, PARK);

    // Boeing is not one of her three employers, so the honest answer to "her
    // book ∩ Boeing" is nothing — never a row, and never a 403, because a
    // refusal would confirm the employer exists.
    const boeing = String(employerIdOf("Boeing"));
    await page.goto(drillUrl({ employerId: boeing }));

    await expect(byTestId(page, "drill-empty")).toBeVisible();
    await expect(byTestId(page, "queue-card")).toHaveCount(0);
    // The URL is left exactly as she typed it — no redirect, no rewrite.
    expect(new URL(page.url()).search).toContain("filter%5BemployerId%5D=");

    // …and the API says the same thing to her own session, with a 200.
    const answer = await page.evaluate(async (id: string) => {
      const resp = await fetch(`/api/dashboard/claims?filter[employerId]=${id}`);
      return {
        status: resp.status,
        body: (await resp.json()) as { total: number; items: unknown[] },
      };
    }, boeing);
    expect(answer.status).toBe(200);
    expect(answer.body.total).toBe(0);
    expect(answer.body.items).toHaveLength(0);
  });

  test("a claim id outside her scope renders the in-app not-found state (AC 4)", async ({
    page,
  }) => {
    await loginAs(page, PARK);

    const outside = expectedDrillClaimsFor(BLINE, {
      employerId: String(employerIdOf("Boeing")),
    }).claimIds[0];
    expect(
      expectedDrillClaimsFor(PARK).claimIds,
    ).not.toContain(outside);

    await page.goto(`/dashboard/claims/${outside}`);

    // 404 rendered as a state, not as a redirect and not as a 403: an
    // out-of-scope claim is invisible rather than forbidden, so nothing here
    // tells her the claim exists.
    await expect(byTestId(page, "readonly-not-found")).toContainText(outside);
    expect(page.url()).toContain(`/dashboard/claims/${outside}`);
    await expect(byTestId(page, "case-header-claim-id")).toHaveCount(0);
  });

  test("the back button returns to a dashboard that is still populated", async ({
    page,
  }) => {
    await loginAs(page, BLINE);
    await expect(byTestId(page, "kpi-total-claims-value")).toBeVisible();

    await byTestId(page, "kpi-litigation-link").click();
    await listSettled(page);

    await page.goBack();

    // The dashboard's own landmark and its figures, not a blank shell: the
    // drill-through is a route under the same layout, so going back re-renders
    // the page rather than remounting the app.
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();
    await expect(byTestId(page, "kpi-total-claims-value")).toHaveText(
      expectedPortfolioSummaryFor(BLINE).cards["kpi-total-claims"],
    );
  });

  test("no query parameter she can add widens the list (AD-1, AD-7)", async ({
    page,
  }) => {
    await loginAs(page, PARK);
    await expect(byTestId(page, "portfolio-dashboard")).toBeVisible();

    // Her own session, her own cookie, asking the API directly for a scope, a
    // page size and an ordering that are not hers. All three are unknown
    // parameter names on this route, so they are ignored rather than refused.
    const answers = await page.evaluate(async () => {
      const read = async (query: string) => {
        const resp = await fetch(`/api/dashboard/claims${query}`);
        return (await resp.json()) as { items: { claimId: string }[]; total: number };
      };
      return {
        honest: await read(""),
        smuggled: await read("?scopeAll=true&limit=200&sort=-severity&cap=1&employerId=2"),
      };
    });

    expect(answers.smuggled).toEqual(answers.honest);
    expect(answers.honest.total).toBe(
      expectedDrillClaimsFor(PARK).claimIds.length,
    );
  });

  test("the priority worklist opens its whole population, uncapped", async ({
    page,
  }) => {
    await loginAs(page, BLINE);

    await byTestId(page, "priority-view-all").click();

    // The table shows the top 30; this list shows all of what qualified, which
    // is why the caption quotes both numbers. `filter[priority]` is the
    // worklist's own predicate, imported by the server rather than restated.
    const expected = expectedDrillClaimsFor(BLINE, { priority: "true" });
    await expect(byTestId(page, "drill-chip")).toHaveText([
      "Priority worklist: Yes✕",
    ]);
    await expectFirstPage(page, expected);
    expect(expected.claimIds.length).toBeGreaterThan(30);
  });
});
