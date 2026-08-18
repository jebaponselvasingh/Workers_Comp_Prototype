import { PERSONAS, loginAs } from "../fixtures/login";
import {
  NEXT_BEST_ACTION_COLUMN,
  expectedPriorityClaimsFor,
  type ExpectedPriorityClaims,
} from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 5.4 — Priority Claims Worklist (Top 30).
 *
 * Nothing here stubs HTTP: the browser logs in for real, `services/worklist`
 * scores the seeded portfolio with Story 2.1's scorer under the caller's
 * employer scope for real, Story 3.5's generator fills the action column for
 * real, and every cell is compared against the seed counted independently
 * (`fixtures/seed.ts`). That is the point — the prototype produced this whole
 * table in the browser, from a global claim array, so a spec that mocked the
 * response would be testing the half that was never in doubt.
 *
 * Four properties get their own tests beyond "the cells are right":
 *
 * - **The order is the scorer's, not the dataset's.** The prototype cuts its
 *   top thirty with `.slice(0, 30)` over a filter in dataset order; the oracle
 *   sorts by `(-score, claimId)` and the two sequences differ from the first
 *   row, so a table that had ported the defect fails on row one.
 * - **Scope narrows the population (AD-7).** Jennifer Park's eight rows are all
 *   Toyota/GM/3M, and her book carries no litigated claim at all — so the
 *   absence of a LITIG chip on her table is a fact about her data rather than
 *   about the renderer, which is why the LITIG assertions are split across two
 *   personas.
 * - **The cursor walks to thirty without repeats.** Three pages of ten under a
 *   cap of thirty, appended and deduped, and the button disappears when the
 *   server stops offering a cursor — nothing in the browser counts rows against
 *   the cap.
 * - **No parameter she can add changes any of it (AD-1).** The route declares
 *   exactly one — `cursor` — so `limit`, `cap`, `scopeAll` and `sort` are all
 *   ignored rather than rejected.
 *
 * **What is deliberately not asserted here: the action column's text.** Its
 * oracle is Story 3.5's generator itself, and restating eleven trigger rules in
 * TypeScript would be a fourth implementation that agrees with the third
 * exactly as often as it was copied from it. `test_priority_claims.py` asserts
 * every row against `generate_actions(...)[0].label` for the same claim, inputs
 * and `as_of`; this spec asserts that the column is populated and non-empty,
 * which is the part a browser can see.
 */

const COLUMN_HEADERS = [
  "Claim ID",
  "Worker",
  "Employer",
  "Injury Type",
  "Severity",
  "Fraud Score",
  "Handler",
  "Days Open",
  "Priority Next Best Action",
  "Status",
];

/** Every rendered row's claim id, in the order the table drew them. */
async function renderedClaimIds(
  page: Parameters<typeof byTestId>[0],
): Promise<(string | null)[]> {
  return byTestId(page, "priority-row").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-claim-id")),
  );
}

/**
 * Wait for the table's first page to land.
 *
 * **Waiting on a row rather than on the section**, and the difference is a real
 * defect this spec had: the `priority-claims` section renders its heading and
 * its ten column labels while the request is still in flight — that is the
 * skeleton state NFR-3 asks for — so `toBeVisible()` on the section resolves
 * immediately and a `evaluateAll` behind it reads an empty list. Both order
 * tests passed vacuously against `[]` before this helper existed.
 */
async function settled(page: Parameters<typeof byTestId>[0]): Promise<void> {
  await expect(byTestId(page, "priority-row").first()).toBeVisible();
}

/**
 * Assert one page of the table against the oracle.
 *
 * The order is checked as **one list** rather than as N independent lookups —
 * `expectTable`'s discipline in `5-2-*.spec.ts`, for its reason: a table that
 * rendered every row in the wrong position would satisfy a per-row loop. The
 * per-row comparison then drops the action column, which this oracle
 * deliberately does not predict (`ExpectedPriorityRow`).
 */
async function expectRows(
  page: Parameters<typeof byTestId>[0],
  expected: ExpectedPriorityClaims,
  count: number,
): Promise<void> {
  const rows = byTestId(page, "priority-row");
  await expect(rows).toHaveCount(count);

  const ids = await renderedClaimIds(page);
  expect(ids).toEqual(expected.rows.slice(0, count).map((row) => row.claimId));

  for (const [index, row] of expected.rows.slice(0, count).entries()) {
    const cells = await rows.nth(index).locator("td").allTextContents();
    const action = cells[NEXT_BEST_ACTION_COLUMN];
    // The generator's answer is asserted server-side; here the only claim about
    // it a browser can make is that the cell was filled at all — an empty one
    // would mean the column never reached the wire.
    expect(
      action.trim(),
      `row ${String(index + 1)} (${row.claimId}) action`,
    ).not.toBe("");
    const predictable = cells.filter(
      (_cell, at) => at !== NEXT_BEST_ACTION_COLUMN,
    );
    expect(predictable, `row ${String(index + 1)} (${row.claimId})`).toEqual(
      row.cells,
    );
  }
}

test.describe("@story:5-4 @epic:5 priority claims worklist", () => {
  test("@smoke the full-portfolio supervisor's worklist ranks thirty claims across ten columns", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    const section = byTestId(page, "priority-claims");
    await expect(byRole(page, "heading", /Priority claims/)).toBeVisible();
    await settled(page);

    const table = section.locator("table");
    expect(await table.locator("th").allTextContents()).toEqual(COLUMN_HEADERS);

    const bline = expectedPriorityClaimsFor(PERSONAS.fullPortfolioSupervisor);
    // The cap is genuinely exercised: thirty-eight of the hundred seeded claims
    // qualify and thirty are shown. Asserted rather than assumed, because a
    // seed change that dropped the population below the cap would make every
    // assertion below pass while testing nothing about the cut.
    expect(bline.total).toBeGreaterThan(bline.cap);
    await expect(byTestId(page, "priority-caption")).toHaveText(bline.caption);

    // The first page, in the scorer's order.
    await expectRows(page, bline, 10);

    // The three litigation claims carry LITIG chips, and they are the only rows
    // that do. `bline.rows` is the capped list, so this is a statement about
    // what is on the table rather than about the whole portfolio.
    const litigated = bline.rows.slice(0, 10).filter((row) => row.litig);
    expect(litigated.length).toBeGreaterThan(0);
    await expect(byTestId(page, "priority-litig")).toHaveCount(
      litigated.length,
    );
  });

  test("the order is the priority scorer's, not the dataset's (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    await settled(page);

    const bline = expectedPriorityClaimsFor(PERSONAS.fullPortfolioSupervisor);
    const rendered = await renderedClaimIds(page);

    // Non-empty first, or every comparison below holds trivially between two
    // empty lists — which is exactly how this test passed while asserting
    // nothing (the section is visible during the skeleton state).
    expect(rendered.length).toBeGreaterThan(0);
    expect(rendered).toEqual(
      bline.rows.slice(0, rendered.length).map((row) => row.claimId),
    );
    // The prototype's defect, spelled out: it takes the qualifying claims in
    // *dataset* order, which is `claim_id` ascending. If the table had ported
    // that, the first ten ids would be the ten lowest — and they are not.
    const datasetOrder = [...bline.rows]
      .map((row) => row.claimId)
      .sort((a, b) => a.localeCompare(b))
      .slice(0, rendered.length);
    expect(rendered).not.toEqual(datasetOrder);
  });

  test("a scoped supervisor's rows stay inside her book and carry no LITIG chip (AD-7)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await settled(page);

    const park = expectedPriorityClaimsFor(PERSONAS.scopedSupervisor);
    const bline = expectedPriorityClaimsFor(PERSONAS.fullPortfolioSupervisor);

    // Her whole population fits inside one page, so there is nothing to walk —
    // which is itself the assertion: `nextCursor` is null exactly when the list
    // is finished, so the button must be absent rather than disabled.
    await expectRows(page, park, park.rows.length);
    await expect(byTestId(page, "priority-claims-more")).toHaveCount(0);
    // Her eight qualifying claims are under the cap, so the caption must say so
    // rather than quoting a thirty that cut nothing. The oracle decides which
    // sentence; the server publishes the same decision as `truncated`.
    await expect(byTestId(page, "priority-caption")).toHaveText(park.caption);
    await expect(byTestId(page, "priority-caption")).not.toContainText(
      "showing top",
    );

    // Not one litigated claim in her three employers' books, so not one chip.
    // The pair with Bline's table is what makes this meaningful: the same
    // component draws chips there and none here.
    expect(park.rows.some((row) => row.litig)).toBe(false);
    await expect(byTestId(page, "priority-litig")).toHaveCount(0);
    expect(park.total).toBeLessThan(bline.total);
  });

  test("the analyst reads the same worklist as the supervisor", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.analyst);
    await settled(page);

    // David Bline holds both personas over the same `scope_all` book, so any
    // difference between the two tables could only come from a role branch —
    // and there is none anywhere on the path.
    await expectRows(
      page,
      expectedPriorityClaimsFor(PERSONAS.fullPortfolioSupervisor),
      10,
    );
  });

  test("Show more walks to the cap without repeating a claim (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    await settled(page);

    const bline = expectedPriorityClaimsFor(PERSONAS.fullPortfolioSupervisor);
    const more = byTestId(page, "priority-claims-more");
    // The button has to be there before the loop, or `isVisible()` answers
    // false on a table that has simply not landed and the walk never starts —
    // which is a pass with no clicks in it.
    await expect(more).toBeVisible();

    // Two clicks under a page size of ten and a cap of thirty. Driven by the
    // button's own presence rather than by a count written here, so a retuned
    // page size changes how many clicks this takes and not whether it passes.
    // The row count is what settles between clicks: the button unmounts on the
    // last page, so waiting on *it* would race the unmount.
    let seen = (await renderedClaimIds(page)).length;
    while (await more.isVisible()) {
      await more.click();
      await expect(byTestId(page, "priority-row")).not.toHaveCount(seen, {
        timeout: 15_000,
      });
      seen = (await renderedClaimIds(page)).length;
    }

    await expectRows(page, bline, bline.cap);
    const ids = await renderedClaimIds(page);
    expect(new Set(ids).size).toBe(ids.length);
    // The caption still names the population, not the number of rows on screen:
    // `total` is counted before the cap and does not move as the table grows.
    await expect(byTestId(page, "priority-caption")).toHaveText(bline.caption);
  });

  test("the rows are not something the browser could have computed (AD-1)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await settled(page);

    // Ask the API from the page's own session for a scope, a page size, a cap
    // and an ordering that are not hers. The route declares exactly one
    // parameter — `cursor` — so every one of these is unknown and ignored, and
    // the answer is her eight claims at the server's cap either way.
    const smuggled = await page.evaluate(async () => {
      const resp = await fetch(
        "/api/dashboard/priority-claims?limit=200&cap=100&scopeAll=true&sort=-severity",
      );
      return (await resp.json()) as {
        items: { claimId: string }[];
        total: number;
        cap: number;
      };
    });
    const park = expectedPriorityClaimsFor(PERSONAS.scopedSupervisor);

    expect(smuggled.items.map((row) => row.claimId)).toEqual(
      park.rows.map((row) => row.claimId),
    );
    expect(smuggled.total).toBe(park.total);
    expect(smuggled.cap).toBe(park.cap);
  });
});
