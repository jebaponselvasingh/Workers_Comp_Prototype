import { PERSONAS, loginAs } from "../fixtures/login";
import { psqlQuery, purgeClaim } from "../fixtures/reset";
import {
  claimIdsInStage,
  expectedPortfolioSummaryFor,
  expectedQueueFor,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 8.1 — PHI Purge Cascade & Retention.
 *
 * **An ops story with no UI, proved through the browser anyway.** There is no
 * purge screen and there is deliberately not going to be one while the identity
 * provider is a deferred decision — the deliverable is a `services/audit`
 * command and two scheduled jobs. AD-15 still wants the story's effect shown
 * against the real stack, and a purge *has* a browser-observable effect: the
 * claim stops existing. So this spec drives the console around a real
 * `docker compose exec` of the real management command, which is the same
 * host-side mechanism `resetDb` uses and the only invocation surface the story
 * ships.
 *
 * **What only this file can check.** The server suite purges through a Python
 * function on a session it built; this runs `python -m scripts.purge_claim`
 * inside the api image, as the application role, against the composed stack —
 * so it is the only place that exercises the CLI's own composition root: the
 * engine, the psycopg pool, the real `AsyncPostgresSaver`, the `SET ROLE
 * audit_redactor` on the container's `ALEMBIC_DATABASE_URL`, and the exit code.
 * A grant that migration 0049 forgot, or an env var the compose file does not
 * set, fails here and nowhere else.
 *
 * **The second half is the one that matters as much as the first.** A cascade
 * that deleted rather more than it was asked to would satisfy "the claim is
 * gone" perfectly, so the smoke path also asserts that the neighbouring claim's
 * case file still loads, that the queue still ranks, and that a supervisor's
 * dashboard still comes up. `audit_event` is read with `psqlQuery` because
 * nothing in the product exposes it (Story 2.3's ruling) and inventing an
 * endpoint so a spec could look would be building a product surface for a test.
 *
 * **The purge is destructive and irreversible**, and the AD-15 fixture resets
 * per spec *file* rather than per test — so the claim purged below is chosen
 * from the far end of the sorted settled group and no other test in this file
 * opens it. `test_photos_tab.py` and Story 2.5 both record the version of that
 * lesson where a destructive test emptied a claim two other tests were reading.
 */

const KAYA = PERSONAS.handler;
const SUPERVISOR = PERSONAS.fullPortfolioSupervisor;

type Page = Parameters<typeof byTestId>[0];

/** The queue has answered — cards are drawn. */
async function queueLoaded(page: Page): Promise<void> {
  await expect(page.locator('[data-testid="queue-card"]').first()).toBeVisible();
}

/** One claim's card in the queue, by business id. */
function cardFor(page: Page, claimId: string) {
  return page.locator(`[data-testid="queue-card"][data-claim-id="${claimId}"]`);
}

/**
 * Rows in one table for one claim, straight from the database — **by the
 * numeric primary key, captured before the purge.**
 *
 * The obvious spelling is a subquery: `WHERE claim_id = (SELECT id FROM claim
 * WHERE claim_id = '<wc>')`. After the purge that subquery returns NULL,
 * `column = NULL` is never true, and every post-purge count is zero whether or
 * not a single row was deleted. Seven table assertions passed vacuously that
 * way, and would have gone on passing against a cascade that deleted nothing at
 * all — which is the one thing this spec exists to rule out.
 *
 * So the pk is read once while the claim still exists and every count after it
 * is a literal comparison against that number. A surrogate key is not reused in
 * this schema, so the number keeps meaning the same rows after the row that
 * minted it is gone.
 */
function rowsForPk(table: string, column: string, claimPk: number): number {
  return Number(psqlQuery(`SELECT count(*) FROM ${table} WHERE ${column} = ${claimPk}`)[0]);
}

/** The numeric `claim.id` behind a business id, or 0 if the claim is gone. */
function claimPkOf(claimId: string): number {
  return Number(psqlQuery(`SELECT id FROM claim WHERE claim_id = '${claimId}'`)[0] ?? 0);
}

/** Every claim-keyed store the cascade is asserted to have emptied. */
const PURGED_TABLES = [
  ["document", "claim_id"],
  ["photo", "claim_id"],
  ["timeline_event", "claim_id"],
  ["bill", "claim_id"],
  ["expense", "claim_id"],
  ["payment_schedule_week", "claim_id"],
  ["claim_embedding", "claim_id"],
] as const;

test.describe("@story:8-1 @epic:8 PHI purge cascade & retention", () => {
  test("@smoke a purged claim leaves the console and the rest of it still works", async ({
    page,
  }) => {
    // **The intake group, and the choice is about paging rather than taste.**
    // The queue pages within a stage, and a group is ranked by priority score
    // rather than by business id — so "the last settled claim" is a card at an
    // unpredictable rank in a group of twenty-six, and might legitimately be on
    // page two, which would make "it is no longer drawn" true before the purge
    // as well as after. Kaya's intake group holds three claims and is therefore
    // drawn whole, so every card assertion below is a statement about the
    // console rather than about the cursor.
    //
    // The **last** by business id, for `lastClaimInStage`'s reason: this test
    // destroys what it opens, and the first of a stage is what every other spec
    // in this suite reaches for.
    const intake = claimIdsInStage(KAYA.name, KAYA.role, "intake");
    expect(intake.length, "the intake group must be small enough to draw whole").toBeLessThan(10);
    const doomed = intake[intake.length - 1];
    const survivor = intake[0];
    expect(doomed).not.toBe(survivor);

    const expectedIntake = expectedQueueFor(KAYA.name, KAYA.role).intake.length;

    await loginAs(page, KAYA);
    await queueLoaded(page);

    // --- the claim is really there, in the queue and in the case file ----
    // The premise, asserted before it is destroyed: "it is gone" says nothing
    // about a claim that was never drawn.
    await expect(byTestId(page, "queue-group-intake-count")).toHaveText(String(expectedIntake));
    await expect(cardFor(page, doomed)).toBeVisible();

    await page.goto(`/workspace?claim=${doomed}`);
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(doomed);

    // …and it has the PHI the cascade is about. Read from the database rather
    // than from a tab, because the assertion after the purge is about rows
    // rather than about what is rendered — and **captured now**, because the
    // primary key that finds them stops resolving the moment the claim goes.
    const claimPk = claimPkOf(doomed);
    expect(claimPk, "the doomed claim must exist before it is purged").toBeGreaterThan(0);
    const before = Object.fromEntries(
      PURGED_TABLES.map(([table, column]) => [table, rowsForPk(table, column, claimPk)]),
    );
    // The premise for all seven assertions after the purge. Without it "no rows
    // remain" is satisfied by a store that never had any, and the whole block
    // below says nothing about whether the cascade ran.
    for (const [table] of PURGED_TABLES) {
      expect(before[table], `${table} has no rows to lose, so the sweep is vacuous`).toBeGreaterThan(
        0,
      );
    }

    // --- the purge, through the shipped command -------------------------
    purgeClaim(doomed);

    // --- the console no longer knows the claim --------------------------
    await page.goto("/workspace");
    await queueLoaded(page);
    await expect(byTestId(page, "queue-group-intake-count")).toHaveText(
      String(expectedIntake - 1),
    );
    await expect(cardFor(page, doomed)).toHaveCount(0);

    // The detail route yields the **not-found state, not an error page**: a
    // stale link to a purged claim is a fact to report, and a console that
    // showed a red failure banner would be telling a handler something had
    // broken when something had worked.
    await page.goto(`/workspace?claim=${doomed}`);
    await expect(byTestId(page, "detail-unknown")).toContainText("not in this caseload");
    await expect(byTestId(page, "detail-error")).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`claim=${doomed}`));

    // --- and nothing else moved -----------------------------------------
    // The half a "did it delete enough?" test cannot see. A cascade with a
    // missing `WHERE` empties the book and passes every assertion above.
    await page.goto(`/workspace?claim=${survivor}`);
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(survivor);
    await expect(byTestId(page, "tab-documents")).toBeVisible();

    // --- the rows are gone, in every store the cascade names ------------
    // Against the pk captured above, not against a subquery that now returns
    // NULL — see `rowsForPk`.
    for (const [table, column] of PURGED_TABLES) {
      expect(rowsForPk(table, column, claimPk), `${table} still has rows`).toBe(0);
    }
    expect(Number(psqlQuery(`SELECT count(*) FROM claim WHERE claim_id = '${doomed}'`)[0])).toBe(0);

    // --- the audit log records the purge and carries no content ---------
    // Read with `psqlQuery` because nothing in the product reads this table.
    const purged = psqlQuery(
      "SELECT entity || '|' || coalesce(before::text, 'null') || '|' || " +
        "coalesce(after::text, 'null') FROM audit_event " +
        `WHERE action = 'purge' AND entity_id = '${doomed}' ORDER BY id`,
    );
    expect(purged.length).toBeGreaterThan(0);
    for (const row of purged) {
      expect(row.endsWith("|null|null")).toBe(true);
    }
  });

  test("a supervisor's portfolio comes up after a purge and is one claim smaller", async ({
    page,
  }) => {
    // A different persona and a different shell, because the smoke path only
    // proves the handler's three panes survived. The dashboard folds every
    // claim in the portfolio, so it is the surface a dangling foreign key or an
    // orphaned child row would break first — and it is one of the four screens
    // Story 8.3's restore drill has to pass, which is what makes it worth
    // asserting here rather than assuming.
    //
    // **It also has to assert something the purge changed.** The first version
    // of this test only checked that two regions rendered, which is true of an
    // unpurged database and of a purged one alike — it would have passed
    // against a cascade that did nothing, against a `purgeClaim` helper that
    // silently no-opped, and against this file with the smoke test deleted. So
    // the count is asserted: David Bline sees the whole hundred-claim
    // portfolio, and after the purge above he must see ninety-nine. That
    // number is the same aggregate the handler's queue count moved by, read
    // through a different persona, a different route and a different query, so
    // it fails if the claim was hidden from one surface rather than deleted.
    //
    // The spec file's reset runs once per *file* and Playwright is configured
    // `workers: 1, fullyParallel: false`, so this runs after the smoke test's
    // purge by construction rather than by luck.
    const portfolio = expectedPortfolioSummaryFor(SUPERVISOR).totalClaims;
    const intake = claimIdsInStage(KAYA.name, KAYA.role, "intake");
    const purged = intake[intake.length - 1];
    expect(claimPkOf(purged), "the smoke test's purge must have happened first").toBe(0);

    await loginAs(page, SUPERVISOR);
    await expect(byTestId(page, "kpi-total-claims")).toBeVisible();
    await expect(byTestId(page, "sla-strip")).toBeVisible();
    await expect(byTestId(page, "kpi-total-claims-value")).toHaveText(String(portfolio - 1));
  });

  test("purging a claim that does not exist fails loudly and changes nothing", async () => {
    // The CLI's refusal path, which no server test can reach: `main()` returns
    // a non-zero exit code and `compose()` lets that throw. A command that
    // exited 0 on a typo would be the worst possible failure mode for an
    // irreversible operation — the operator would believe the wrong claim had
    // been purged and go looking for it.
    const before = Number(psqlQuery("SELECT count(*) FROM claim")[0]);
    expect(() => purgeClaim("WC-99999")).toThrow();
    expect(Number(psqlQuery("SELECT count(*) FROM claim")[0])).toBe(before);
  });
});
