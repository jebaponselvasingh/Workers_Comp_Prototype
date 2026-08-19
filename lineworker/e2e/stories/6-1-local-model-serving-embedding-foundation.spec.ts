import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  STAGES,
  claimIdsOutsideScopeOf,
  expectedQueueFor,
  firstClaimInStage,
} from "../fixtures/seed";
import { expect, test } from "../fixtures/test";

/**
 * Story 6.1 — Local Model Serving & Embedding Foundation.
 *
 * This story ships no UI, so the assertions are stack-level by design — the
 * shape Story 1.2's spec set for a schema-and-seed story. What is being proved
 * here is not a screen; it is that the *composed stack* does the thing:
 *
 * 1. **The api can reach a model server on the internal network.** Nothing in
 *    this file asserts that directly, because there is no way to and no need:
 *    `rowsEmbedded > 0` is only possible if the api container made an HTTP
 *    request to `model-stub:11434` and got 1024-dimension vectors back, through
 *    the *real* `OllamaEmbeddingClient` with its real dimension check. Nothing
 *    is swapped in Python for the e2e profile; only the model behind the API is
 *    deterministic (`deploy/model-stub/`).
 * 2. **Scope holds through the vector path.** A `scope_all` supervisor embeds
 *    the whole portfolio; a handler scoped to four employers then asks for
 *    similar cases and gets back only her own book — from a table that now
 *    contains every claim's vector, so an unscoped query would visibly leak.
 *    That ordering is deliberate: running the refresh as the handler first
 *    would leave nothing outside her book to leak, and the test would pass
 *    against a repository with no filter at all.
 * 3. **Freshness travels with retrieval** (AD-12) — every item carries a
 *    non-null `embeddedAt`, which Story 6.4's staleness disclosure reads.
 *
 * **How the refresh is triggered.** Through `POST /api/admin/embedding-refresh`,
 * served only under `ENV=e2e` (`api/routers/admin.py`) and calling the *real*
 * `refresh_stale_embeddings` — there is no second code path. The scheduler is
 * off in this profile by design, so without an explicit trigger a spec would
 * wait fifteen minutes for a tick; AD-15 forbids waiting on a wall clock, which
 * is the same reason the payment batch has a trigger of its own.
 *
 * **What this spec deliberately does not assert.** That any particular claim is
 * *similar* to any other. The stub's vectors are hashes, so nearness here is
 * arbitrary by construction — and even against a real model, "bge-m3 thinks
 * these two shoulder strains are alike" would be a test of the model rather
 * than of this codebase. Every assertion below is structural: membership,
 * exclusion, ordering, counts, freshness.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

/** The fifteen seeded labour-law chunks — three each for OH, MN, IL, WA, TX. */
const EXPECTED_CHUNKS = 15;

/** Every claim id in the persona's book, from the independent seed oracle. */
function bookOf(name: string, role: string): Set<string> {
  const queue = expectedQueueFor(name, role);
  return new Set(STAGES.flatMap((stage) => queue[stage].map((card) => card.claimId)));
}

interface RefreshRun {
  rowsEmbedded: number;
  chunksEmbedded: number;
  rowsFailed: number;
  chunksFailed: number;
}

interface SimilarItem {
  claimId: string;
  employerShortName: string;
  injuryType: string;
  severityScore: number;
  distance: number;
  embeddedAt: string | null;
  stale: boolean;
}

type Page = Parameters<typeof loginAs>[0];

/** Trigger the real refresh command under the logged-in persona's scope. */
async function runRefresh(page: Page): Promise<RefreshRun> {
  const response = await page.request.post("/api/admin/embedding-refresh");
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as RefreshRun;
}

async function similarTo(page: Page, claimId: string, k: number) {
  return await page.request.get(`/api/claims/${claimId}/similar?k=${k}`);
}

/**
 * Embed the whole portfolio, as a persona whose scope covers it.
 *
 * Every test below the smoke test needs an embedded table, and until this
 * existed they got one by running *after* the smoke test — which the file did
 * not say and which is not true under `--grep`, `test.only`, or a reordering.
 * The reset fixture rebuilds the database once per spec *file*, not per test,
 * so a shared prerequisite has to be requested rather than assumed.
 *
 * Cheap to call repeatedly: the refresh is idempotent by construction, so
 * every call after the first selects nothing and returns zeroes. It logs in as
 * the `scope_all` persona because the refresh runs under the caller's scope —
 * a scoped handler could only ever embed their own book, which is the property
 * "the refresh runs under the caller's own scope" exists to assert.
 */
async function embedEverything(page: Page): Promise<void> {
  await loginAs(page, PERSONAS.fullPortfolioSupervisor);
  const run = await runRefresh(page);
  expect(run.rowsFailed).toBe(0);
  expect(run.chunksFailed).toBe(0);
  await switchPersona(page);
}

test.describe("@story:6-1 @epic:6 local model serving and embedding foundation", () => {
  test("@smoke the refresh embeds the portfolio and similar-case search stays in scope", async ({
    page,
  }) => {
    // A `scope_all` persona first, so the whole 100-claim portfolio ends up
    // embedded. Kaya's assertion below is only meaningful against a table that
    // contains other people's claims.
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    const first = await runRefresh(page);
    expect(first.rowsFailed).toBe(0);
    // Migration 0041 pre-creates one pending row per claim and per chunk,
    // because a migration cannot reach a model server. This is the run that
    // does the work, and it is the only code path in the build that ever writes
    // a vector.
    expect(first.rowsEmbedded).toBe(100);
    expect(first.chunksEmbedded).toBe(EXPECTED_CHUNKS);

    // Idempotent by construction, not by bookkeeping: the pending query is
    // "stale or never embedded", so a second call selects nothing.
    expect(await runRefresh(page)).toEqual({
      rowsEmbedded: 0,
      chunksEmbedded: 0,
      rowsFailed: 0,
      chunksFailed: 0,
    });

    await switchPersona(page);
    await loginAs(page, PERSONAS.handler);

    const book = bookOf(KAYA.name, KAYA.role);
    const outside = new Set(claimIdsOutsideScopeOf(KAYA.name, KAYA.role));
    expect(outside.size).toBeGreaterThan(0);

    const target = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const response = await similarTo(page, target, 5);
    expect(response.status(), await response.text()).toBe(200);
    const payload = (await response.json()) as { items: SimilarItem[]; k: number };

    expect(payload.k).toBe(5);
    expect(payload.items.length).toBe(5);
    for (const item of payload.items) {
      // AD-7 through the vector path: never another employer's claim.
      expect(book.has(item.claimId), `${item.claimId} is outside the handler's book`).toBe(true);
      expect(outside.has(item.claimId)).toBe(false);
      // AD-12: retrieval carries freshness, from day one.
      expect(item.embeddedAt).not.toBeNull();
      expect(item.stale).toBe(false);
      // The target is excluded before the LIMIT, not filtered out afterwards —
      // otherwise a claim's own neighbour list would be one short.
      expect(item.claimId).not.toBe(target);
    }

    // Cosine distance, ascending. Published raw rather than as a similarity
    // percentage, so the ordering is assertable without asserting a score.
    const distances = payload.items.map((item) => item.distance);
    expect(distances).toEqual([...distances].sort((a, b) => a - b));
  });

  test("an out-of-scope claim answers exactly as an unknown one does", async ({ page }) => {
    // The single-answer rule (AD-7). Two different answers would make this
    // route an oracle a caller can walk WC-20000…WC-20999 through to enumerate
    // a portfolio they cannot read — so the two bodies are compared, not just
    // the two status codes.
    await loginAs(page, PERSONAS.handler);

    const stranger = claimIdsOutsideScopeOf(KAYA.name, KAYA.role)[0];
    const outOfScope = await similarTo(page, stranger, 5);
    const missing = await similarTo(page, "WC-99999", 5);

    expect(outOfScope.status()).toBe(404);
    expect(missing.status()).toBe(404);

    // Same status, same problem `type`, same title — and a `detail` that
    // differs only by echoing back the id the caller sent. Nothing in either
    // body distinguishes "somebody else's claim" from "no such claim", which
    // is the property; comparing the bodies whole would only assert that the
    // route ignores its own parameter.
    const refused = (await outOfScope.json()) as Record<string, unknown>;
    const unknown = (await missing.json()) as Record<string, unknown>;
    expect({ ...refused, detail: null }).toEqual({ ...unknown, detail: null });
    expect(refused.detail).toBe(`No claim ${stranger} in your caseload.`);
    expect(unknown.detail).toBe("No claim WC-99999 in your caseload.");
  });

  test("the neighbour count is bounded by k and by the caller's book", async ({ page }) => {
    await embedEverything(page);
    await loginAs(page, PERSONAS.scopedHandler);

    const book = bookOf("Sarah Williams", "handler");
    const target = firstClaimInStage("Sarah Williams", "handler", "treatment");

    const one = await similarTo(page, target, 1);
    expect(one.status()).toBe(200);
    expect(((await one.json()) as { items: SimilarItem[] }).items.length).toBe(1);

    // Sarah is scoped to 3M alone. Asking for more neighbours than her book
    // holds returns her book minus the target — the bound is the partition,
    // never k.
    const many = await similarTo(page, target, 25);
    expect(many.status()).toBe(200);
    const items = ((await many.json()) as { items: SimilarItem[] }).items;
    expect(items.length).toBe(Math.min(25, book.size - 1));
    expect(items.every((item) => book.has(item.claimId))).toBe(true);
  });

  test("the refresh runs under the caller's own scope", async ({ page }) => {
    // The admin trigger takes the requesting persona's context, exactly as the
    // payment batch's does — and here that is a live assertion rather than a
    // convenience: the two *write* paths (mark-stale and store-vector) are the
    // ones nobody would think to scope, so a run under a scoped handler that
    // embedded the whole portfolio would show up as a count.
    //
    // The portfolio is embedded first, then this test makes work for itself: it
    // edits one of Sarah's claims, which marks that claim's embedding stale in
    // the same transaction (AD-12, retro-wired into Story 2.3's command), and
    // then refreshes as Sarah. Asserting `rowsEmbedded === 1` only means
    // anything against a table where everything *else* is already current.
    await embedEverything(page);
    await loginAs(page, PERSONAS.scopedHandler);

    const claimId = firstClaimInStage("Sarah Williams", "handler", "investigation");
    const detail = await page.request.get(`/api/claims/${claimId}`);
    expect(detail.status()).toBe(200);
    const version = ((await detail.json()) as { version: number }).version;

    const edit = await page.request.patch(`/api/claims/${claimId}`, {
      data: { expectedVersion: version, cause: "Struck by falling stock (e2e)" },
    });
    expect(edit.status(), await edit.text()).toBe(200);

    const run = await runRefresh(page);
    expect(run.rowsFailed).toBe(0);
    // Exactly the one claim the edit touched: the flag was set inside the
    // edit's own transaction, and nothing else in the portfolio became pending.
    expect(run.rowsEmbedded).toBe(1);
    expect(run.chunksEmbedded).toBe(0);
    expect(run.chunksFailed).toBe(0);
  });
});
