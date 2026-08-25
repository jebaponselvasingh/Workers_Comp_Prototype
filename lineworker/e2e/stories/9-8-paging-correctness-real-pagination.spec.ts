import { PERSONAS, loginAs } from "../fixtures/login";
import { expectedPriorityClaimsFor } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 9.8 — Paging Correctness & Real Pagination.
 *
 * Nothing here stubs HTTP. The browser logs in for real, `services/worklist`
 * ranks the seeded portfolio under the caller's employer scope for real, and
 * the cursors under test are the ones the server minted a moment earlier.
 *
 * What this story changed is invisible on a single screenshot: three ranked
 * lists moved from offset resumption to keyset resumption on
 * `priority.order_key`, two fake-paginated endpoints became genuinely pageable,
 * the queue's incremental page stopped computing three groups it discards, and
 * every date-sensitive payload now states the `asOf` it resolved. Each of those
 * is a property of a **sequence** of requests, so every test below is a walk
 * rather than a render check.
 *
 * Four properties get their own test:
 *
 * - **A walk visits every claim exactly once** (`@smoke`). The worklist is
 *   walked to its cap through the real "Show more" and the ids are compared
 *   against the seed counted independently. Under offsets this passed too —
 *   which is the point of the second test.
 * - **A cursor from before the change is refused, not reinterpreted.** An
 *   offset-shaped token is replayed against the live API and must come back 400
 *   `/problems/invalid-cursor`. A silent page one is the failure that turns a
 *   client bug into an infinite "Show more".
 * - **A narrowed queue request is narrowed and still truthful.** The response
 *   omits the three groups nobody asked for, and both totals still describe the
 *   whole queue. Driven through the API rather than "Show more": no seeded group
 *   reaches the fifty-row page size, so that affordance is unreachable in the
 *   browser against this data. The SPA half — that the incremental request
 *   carries `groups` — is asserted in `web/src/features/queue/QueuePane.test.tsx`
 *   against a paged fixture.
 * - **`/glossary` pages for real.** A `LIMIT`, a non-null cursor, a `total`
 *   from a `COUNT(*)`, and a walk that visits every term once — the endpoint
 *   whose envelope asserted a pagination that did not exist.
 */

const BLINE = PERSONAS.fullPortfolioSupervisor;
const KAYA = PERSONAS.handler;

/** Every claim id the worklist is showing, in the order it drew them. */
async function renderedClaimIds(
  page: Parameters<typeof byTestId>[0],
): Promise<string[]> {
  return byTestId(page, "priority-row").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-claim-id") ?? ""),
  );
}

test.describe("@story:9-8 @epic:9 Paging correctness and real pagination", () => {
  test("@smoke a walked worklist visits every claim exactly once and states the day it aged them (AC 1, AC 2)", async ({
    page,
  }) => {
    await loginAs(page, BLINE);
    await expect(byTestId(page, "priority-row").first()).toBeVisible();

    const expected = expectedPriorityClaimsFor(BLINE);
    const more = byTestId(page, "priority-claims-more");
    // The button has to be on screen before the loop, or `isVisible()` answers
    // false on a table that has simply not landed and the walk never starts —
    // a pass with no clicks in it.
    await expect(more).toBeVisible();

    // Driven by the button's own presence rather than by a click count written
    // here, so a retuned page size changes how long this takes and not whether
    // it passes. The row count is what settles between clicks: the button
    // unmounts on the last page, so waiting on *it* would race the unmount.
    let seen = (await renderedClaimIds(page)).length;
    while (await more.isVisible()) {
      await more.click();
      await expect(byTestId(page, "priority-row")).not.toHaveCount(seen, {
        timeout: 15_000,
      });
      seen = (await renderedClaimIds(page)).length;
    }

    const ids = await renderedClaimIds(page);
    // The whole walk, as one ordered list against the independently counted
    // seed. Compared as a list rather than as a set, because a keyset that
    // resumed one row early would repeat rather than skip and a set comparison
    // would miss it; the duplicate check beside it is the other direction.
    expect(ids).toEqual(expected.rows.map((row) => row.claimId));
    expect(new Set(ids).size).toBe(ids.length);

    // AC 2, on the payload the walk was cut from: the endpoint states the day
    // it aged the claims against, and every page of one walk states the same
    // day. Read off the API rather than the screen — the field rides unrendered,
    // exactly like `rulesVersion` beside it, and exists so a client (or an
    // oracle) can stop guessing at the server's clock.
    const firstPage = await page.request.get("/api/dashboard/priority-claims");
    expect(firstPage.status()).toBe(200);
    const body = (await firstPage.json()) as {
      asOf: string;
      nextCursor: string | null;
    };
    expect(body.asOf).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(body.nextCursor).not.toBeNull();

    const secondPage = await page.request.get(
      `/api/dashboard/priority-claims?cursor=${encodeURIComponent(body.nextCursor ?? "")}`,
    );
    expect(secondPage.status()).toBe(200);
    expect(((await secondPage.json()) as { asOf: string }).asOf).toBe(body.asOf);
  });

  test("a cursor from before the keyset change is refused rather than reinterpreted (AC 1)", async ({
    page,
  }) => {
    await loginAs(page, BLINE);
    await expect(byTestId(page, "priority-row").first()).toBeVisible();

    // The offset shape this endpoint used to mint: `o` and no `k`. Built here
    // rather than fetched, because the server can no longer produce one — which
    // is exactly why a client holding a bookmarked or in-flight token must get
    // an answer it can act on.
    const stale = Buffer.from(
      JSON.stringify({
        o: 10,
        l: 10,
        v: 1,
        t: 5,
        a: 2,
        d: new Date().toISOString().slice(0, 10),
      }),
    )
      .toString("base64url")
      .replace(/=+$/, "");

    const refused = await page.request.get(
      `/api/dashboard/priority-claims?cursor=${encodeURIComponent(stale)}`,
    );

    // 400 and a problem document, never a 200 carrying page one: a silent reset
    // turns a client bug into a "Show more" that re-appends the same claims for
    // ever, and never a 500, which is what an unhandled decode would be.
    expect(refused.status()).toBe(400);
    expect(refused.headers()["content-type"]).toContain("application/problem+json");
    expect((await refused.json()) as { type: string }).toMatchObject({
      type: "/problems/invalid-cursor",
    });
  });

  test("a narrowed queue request omits the groups nobody asked for and still counts the book (AC 5)", async ({
    page,
  }) => {
    await loginAs(page, KAYA);
    await expect(byTestId(page, "queue-group-treatment")).toBeVisible();

    // **Driven through the API rather than through "Show more", and the reason
    // is a property of the seeded book rather than a shortcut.** The queue's
    // page size is `priority_weights.pageLimit` (fifty) and no seeded group
    // holds more than fifteen claims, so the "Show more" affordance is
    // unreachable in the browser against this data — `2-1-*.spec.ts` records
    // the same constraint. The SPA half of AC 5 (that the incremental request
    // carries `groups`) is asserted in `QueuePane.test.tsx`, where a paged
    // fixture can be handed to the pane; what is left for the live stack is the
    // server's half, which is here.
    //
    // The narrowing is invisible in a rendered page — three groups computed and
    // discarded and three groups never computed draw identically — so the
    // payload is what has to be read.
    const full = (await (
      await page.request.get("/api/claims/queue")
    ).json()) as QueueBody;
    const narrowed = (await (
      await page.request.get("/api/claims/queue?groups=treatment")
    ).json()) as QueueBody;

    expect(narrowed.groups.treatment).not.toBeNull();
    expect(narrowed.groups.intake).toBeNull();
    expect(narrowed.groups.investigation).toBeNull();
    expect(narrowed.groups.settled).toBeNull();
    expect(narrowed.unfilteredTotal).toBe(full.unfilteredTotal);
    expect(narrowed.filteredTotal).toBe(full.filteredTotal);
    // The one group that came back is the one the unnarrowed response holds:
    // `groups` decides what is computed, never what any of it says.
    expect(narrowed.groups.treatment?.total).toBe(full.groups.treatment?.total);
    // AC 2 on this surface too.
    expect(narrowed.asOf).toBe(full.asOf);
  });

  test("the glossary is genuinely paged and a walk visits every term once (AC 4)", async ({
    page,
  }) => {
    await loginAs(page, KAYA);

    const first = (await (
      await page.request.get("/api/glossary?limit=7")
    ).json()) as GlossaryBody;

    // A real `LIMIT`, a real cursor, and a `total` that is not `len(items)` —
    // the three halves of the envelope this endpoint used to publish without
    // honouring. The inequality is the load-bearing one: before, `total` agreed
    // with itself whatever happened to the table, so a truncation could never
    // have been detected from the payload.
    expect(first.items).toHaveLength(7);
    expect(first.nextCursor).not.toBeNull();
    expect(first.total).toBeGreaterThan(first.items.length);

    const walked: string[] = first.items.map((item) => item.abbreviation);
    let cursor = first.nextCursor;
    let nulls = 0;
    while (cursor !== null) {
      const next = (await (
        await page.request.get(
          `/api/glossary?limit=7&cursor=${encodeURIComponent(cursor)}`,
        )
      ).json()) as GlossaryBody;
      walked.push(...next.items.map((item) => item.abbreviation));
      cursor = next.nextCursor;
      if (cursor === null) nulls += 1;
      expect(walked.length).toBeLessThan(200);
    }

    // Every term once, and the walk's length is the count the first page
    // published — which is what makes `total` a fact about the table rather
    // than about the page it arrived on.
    expect(new Set(walked).size).toBe(walked.length);
    expect(walked).toHaveLength(first.total);
    // Null exactly once, on the last page. A page size that does not divide the
    // vocabulary, deliberately: an exact divisor is where an off-by-one in the
    // "is there more?" test produces a phantom empty final page unnoticed.
    expect(nulls).toBe(1);
  });
});

/** The queue envelope, narrowed to what this spec reads. */
interface QueueBody {
  groups: Record<string, { total: number } | null>;
  unfilteredTotal: number;
  filteredTotal: number;
  asOf: string;
}

/** The glossary envelope, narrowed to what this spec reads. */
interface GlossaryBody {
  items: { abbreviation: string }[];
  nextCursor: string | null;
  total: number;
}
