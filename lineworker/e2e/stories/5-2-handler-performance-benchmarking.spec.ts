import { PERSONAS, loginAs } from "../fixtures/login";
import {
  expectedHandlerBenchmarksFor,
  type ExpectedHandlerBenchmarks,
} from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 5.2 — Handler Performance Benchmarking.
 *
 * Nothing here stubs HTTP: the browser logs in for real, `services/worklist`
 * groups the seeded portfolio by handler under the caller's employer scope for
 * real, and the cells are compared against the seed counted independently
 * (`fixtures/seed.ts`). That is the point — the prototype produced this whole
 * table in the browser, so a spec that mocked the response would be testing the
 * half that was never in doubt.
 *
 * Three properties get their own tests beyond "the figures are right":
 *
 * - **Handlers come from claims, not from a roster (AD-7).** Kaya Johnson holds
 *   a John Deere assignment and handles none of its claims, so Ken Stoker's
 *   Deere-and-Lockheed table must not contain her. That is the straddle case
 *   the architecture names, and it is the one an implementation that enumerated
 *   "the handlers in my employers" would fail while passing everything else.
 * - **Scope narrows the desk.** Jennifer Park sees two handlers where David
 *   Bline sees six, and her figures are recomputed inside her book.
 * - **Role gates the surface.** A handler asking the endpoint directly is
 *   refused — this story's own answer to the access question
 *   `/dashboard/summary` left open.
 */

const COLUMN_HEADERS = [
  "#",
  "Handler",
  "Cases",
  "Cycle Speed",
  "Avg Days",
  "RTW %",
  "Complexity",
  "Pending Approvals",
  "Status",
];

async function expectTable(
  page: Parameters<typeof byTestId>[0],
  expected: ExpectedHandlerBenchmarks,
): Promise<void> {
  const rows = byTestId(page, "handler-row");
  await expect(rows).toHaveCount(expected.rows.length);

  // The order is the contract, so it is asserted as a list rather than as N
  // independent lookups — a table that rendered every row in the wrong position
  // would satisfy a per-row loop.
  const rendered = await rows.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-handler")),
  );
  expect(rendered).toEqual(expected.rows.map((row) => row.handler));

  for (const [index, row] of expected.rows.entries()) {
    const cells = await rows.nth(index).locator("td").allTextContents();
    expect(cells, `row ${index + 1} (${row.handler})`).toEqual(row.cells);
  }
}

test.describe("@story:5-2 @epic:5 handler performance benchmarking", () => {
  test("@smoke the full-portfolio supervisor's desk ranks six handlers across nine columns", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    await expect(
      byRole(page, "heading", "Claim handler performance"),
    ).toBeVisible();

    const table = byTestId(page, "handler-benchmarks").locator("table");
    expect(await table.locator("th").allTextContents()).toEqual(COLUMN_HEADERS);

    const bline = expectedHandlerBenchmarksFor(PERSONAS.fullPortfolioSupervisor);
    await expectTable(page, bline);

    // The callout names the two handlers the server picked, not the first and
    // last rows the browser happened to be handed.
    const callout = byTestId(page, "benchmark-callout");
    await expect(callout).toContainText(bline.leader);
    await expect(callout).toContainText(bline.laggard);
    await expect(callout).toContainText("workload check-in");
  });

  test("the cycle-speed bars are filled to the server's peer-relative percentages (AC 1)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    await expect(byTestId(page, "handler-row")).toHaveCount(6);

    const widths = await byTestId(page, "cycle-speed-fill").evaluateAll((nodes) =>
      nodes.map((node) => (node as HTMLElement).style.width),
    );
    expect(widths).toEqual(
      expectedHandlerBenchmarksFor(PERSONAS.fullPortfolioSupervisor).barWidths,
    );
  });

  test("the status footnote quotes the deviation bands the document holds (AC 2)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    const { footnote } = expectedHandlerBenchmarksFor(
      PERSONAS.fullPortfolioSupervisor,
    );
    const bands = byTestId(page, "benchmark-bands");
    await expect(bands).toContainText(`On Track at or below ${footnote.onTrack}%`);
    await expect(bands).toContainText(
      `Attention at or above ${footnote.attention}%`,
    );
    // The portfolio the deviations are percentages *of*, so the column is
    // interpretable without the browser adding three averages of its own.
    await expect(bands).toContainText(footnote.portfolio);
  });

  test("a scoped supervisor sees only the handlers with claims in her employers (AC 4)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const park = expectedHandlerBenchmarksFor(PERSONAS.scopedSupervisor);
    expect(park.rows).toHaveLength(2);
    await expectTable(page, park);

    // The pair is the proof: six rows would mean scoping stopped working and
    // every seed-derived assertion above would still have passed.
    const bline = expectedHandlerBenchmarksFor(PERSONAS.fullPortfolioSupervisor);
    expect(bline.rows.length).toBeGreaterThan(park.rows.length);
    for (const row of park.rows) {
      expect(bline.rows.map((other) => other.handler)).toContain(row.handler);
    }
  });

  test("a handler assigned an employer she handles no claim for is absent (AD-7 straddle)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.passingRtwSupervisor);

    const stoker = expectedHandlerBenchmarksFor(PERSONAS.passingRtwSupervisor);
    await expectTable(page, stoker);

    // Kaya Johnson holds a John Deere assignment in `user_employer_assignment`;
    // every seeded Deere claim is Liam O'Sullivan's. An implementation that
    // enumerated "the handlers assigned to my employers" would list her here.
    expect(stoker.rows.map((row) => row.handler)).not.toContain(
      PERSONAS.handler.name,
    );
    await expect(
      byTestId(page, "handler-row").filter({
        hasText: PERSONAS.handler.name,
      }),
    ).toHaveCount(0);
  });

  test("the analyst reads the same table as the supervisor (Epic 5 preamble)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.analyst);

    // Role gates capability, scope gates visibility, and the analyst persona is
    // David Bline wearing the analyst hat — so the desk is his.
    await expectTable(page, expectedHandlerBenchmarksFor(PERSONAS.analyst));
  });

  test("a handler is refused the endpoint outright (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    // Her own session, her own cookie, asking the API directly — there is no
    // route to this surface in her shell, so the refusal has to be the server's.
    const refusal = await page.evaluate(async () => {
      const resp = await fetch("/api/dashboard/handler-benchmarks");
      return {
        status: resp.status,
        contentType: resp.headers.get("content-type"),
        body: (await resp.json()) as { type?: string },
      };
    });

    expect(refusal.status).toBe(403);
    expect(refusal.contentType).toContain("application/problem+json");
    expect(refusal.body.type).toBe("/problems/benchmarks-not-permitted");
  });

  test("the figures are not something the browser could have computed (AD-1)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await expect(byTestId(page, "handler-benchmarks")).toBeVisible();

    // Ask the API from the page's own session for a scope and an ordering that
    // are not hers. The endpoint takes no parameters at all, so a smuggled one
    // changes nothing — the answer is her two handlers either way.
    const smuggled = await page.evaluate(async () => {
      const resp = await fetch(
        "/api/dashboard/handler-benchmarks?scopeAll=true&sort=-compositeDays",
      );
      return (await resp.json()) as {
        items: { handlerName: string }[];
      };
    });
    const park = expectedHandlerBenchmarksFor(PERSONAS.scopedSupervisor);

    expect(smuggled.items.map((row) => row.handlerName)).toEqual(
      park.rows.map((row) => row.handler),
    );
  });
});
