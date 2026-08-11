import { PERSONAS, loginAs } from "../fixtures/login";
import { STAGES, claimIdsOutsideScopeOf, expectedQueueFor } from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 2.1 — Prioritized, Filterable Claim Queue.
 *
 * Nothing here stubs HTTP. The browser logs in for real, the API scopes,
 * derives, scores and pages for real against the seeded database, and every
 * expectation is computed independently in `fixtures/seed.ts` from the same
 * seed file the stack was migrated with. That independence is what makes
 * these assertions capable of disagreeing with the implementation — an
 * oracle that asked the server what to expect would agree with any answer.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

/**
 * The claim ids rendered in one stage group, top to bottom.
 *
 * `evaluateAll` takes a snapshot and does **not** auto-wait, which is right
 * — several callers assert an empty group, and a helper that waited for a
 * card could never observe one. The cost is that every caller owes an
 * auto-retrying assertion first (`queueLoaded` below, or a `toHaveText` on a
 * count chip), or it reads the queue before the query has answered and
 * compares an empty set against the oracle.
 */
async function renderedIds(
  page: Parameters<typeof byTestId>[0],
  stage: string,
): Promise<string[]> {
  return byTestId(page, `queue-group-${stage}`)
    .getByTestId("queue-card")
    .evaluateAll((cards) => cards.map((card) => (card as HTMLElement).dataset.claimId ?? ""));
}

/** Wait for the queue to have answered — see `renderedIds`. */
async function queueLoaded(page: Parameters<typeof byTestId>[0]): Promise<void> {
  await expect(page.locator('[data-testid="queue-card"]').first()).toBeVisible();
}

test.describe("@story:2-1 @epic:2 prioritized, filterable claim queue", () => {
  test("@smoke a handler's queue groups, ranks, auto-selects, filters and follows a click", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    // --- four groups over the seeded caseload (AC 1) --------------------
    const expected = expectedQueueFor(KAYA.name, KAYA.role);
    for (const stage of STAGES) {
      await expect(byTestId(page, `queue-group-${stage}-count`)).toHaveText(
        String(expected[stage].length),
      );
    }
    // One deliberate literal, the house exception: Kaya's caseload is 45
    // claims across intake 3 / investigation 1 / treatment 15 / settled 26.
    // If the oracle and the seed drifted together, every assertion above
    // would still pass and this one would not.
    await expect(byTestId(page, "queue-group-treatment-count")).toHaveText("15");

    // --- ranked within the group (AC 4) --------------------------------
    expect(await renderedIds(page, "treatment")).toEqual(
      expected.treatment.map((card) => card.claimId),
    );

    // --- the first claim is selected (AC 2) ----------------------------
    const first = expected.intake[0].claimId;
    await expect(page).toHaveURL(new RegExp(`claim=${first}`));
    await expect(byTestId(page, "detail-selected")).toHaveText(first);

    // --- a filter re-filters, server-side (AC 3) -----------------------
    await byTestId(page, "queue-filter").click();
    await byTestId(page, "queue-filter-option-litigation").click();

    const litigated = expectedQueueFor(KAYA.name, KAYA.role, "litigation");
    for (const stage of STAGES) {
      await expect(byTestId(page, `queue-group-${stage}-count`)).toHaveText(
        String(litigated[stage].length),
      );
    }
    // A *premise*, asserted before the conclusion — two oracle computations
    // compared to each other say nothing about the page. It is here so that
    // a seed in which every treatment claim is litigated fails with a
    // readable reason instead of making the assertion below vacuous.
    expect(litigated.treatment.length).toBeLessThan(expected.treatment.length);
    // And this is the conclusion, which touches the page: the cards that
    // are actually rendered are the narrower list, in the narrower order.
    expect(await renderedIds(page, "treatment")).toEqual(
      litigated.treatment.map((card) => card.claimId),
    );

    // --- clicking a card moves the highlight and the URL (AC 6) --------
    await byTestId(page, "queue-filter").click();
    await byTestId(page, "queue-filter-option-all").click();
    await expect(byTestId(page, "queue-group-treatment-count")).toHaveText("15");

    const target = expected.treatment[1].claimId;
    const card = page.locator(`[data-testid="queue-card"][data-claim-id="${target}"]`);
    await card.click();

    await expect(page).toHaveURL(new RegExp(`claim=${target}`));
    await expect(card).toHaveAttribute("aria-current", "true");
    await expect(byTestId(page, "detail-selected")).toHaveText(target);
  });

  test("a stage the handler has nothing in says so (AC 1, NFR-3)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedHandler);

    const expected = expectedQueueFor(SARAH.name, SARAH.role);
    // The oracle asserts the premise before the conclusion: if the seed
    // ever gives Sarah an intake claim, this fails here with a readable
    // reason instead of failing below as a missing element.
    expect(expected.intake).toHaveLength(0);
    expect(expected.settled.length).toBeGreaterThan(0);

    await expect(byTestId(page, "queue-group-intake-empty")).toHaveText(
      "No claims in this stage.",
    );
    await expect(byTestId(page, "queue-group-intake-count")).toHaveText("0");
    // …and the populated group beside it is not showing the same message.
    await expect(byTestId(page, "queue-group-settled-empty")).toHaveCount(0);
  });

  test("the priority marker lands on the group's top-scoring claims (AC 4)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const expected = expectedQueueFor(KAYA.name, KAYA.role);
    const marked = expected.treatment.filter((card) => card.priorityMarker);
    expect(marked.length).toBeGreaterThan(0);

    for (const card of marked) {
      await expect(
        page.locator(
          `[data-testid="queue-card"][data-claim-id="${card.claimId}"] [data-testid="queue-card-marker"]`,
        ),
      ).toBeVisible();
    }
    // The half of the rule a naive `score > threshold` gets wrong: the
    // fourth-ranked claim carries no marker however high it scored.
    const unmarked = expected.treatment.find((card) => !card.priorityMarker);
    expect(unmarked).toBeDefined();
    await expect(
      page.locator(
        `[data-testid="queue-card"][data-claim-id="${unmarked!.claimId}"] [data-testid="queue-card-marker"]`,
      ),
    ).toHaveCount(0);

    // No settled claim in this portfolio carries it. The penalty is a bias
    // rather than a floor — the categorical weights outweigh it, so a
    // settled claim carrying every flag would clear the threshold — but
    // none of Kaya's 26 is that loud, and the oracle says so independently.
    expect(expected.settled.every((card) => !card.priorityMarker)).toBe(true);
  });

  test("a scoped handler never sees a claim outside her book (AD-7)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedHandler);
    await queueLoaded(page);

    const mine = expectedQueueFor(SARAH.name, SARAH.role);
    const rendered = new Set(
      (
        await Promise.all(STAGES.map((stage) => renderedIds(page, stage)))
      ).flat(),
    );
    expect(rendered).toEqual(new Set(STAGES.flatMap((s) => mine[s].map((c) => c.claimId))));

    // The pair is the proof: an unscoped queue would still satisfy the
    // assertion above if the oracle were also unscoped.
    const forbidden = claimIdsOutsideScopeOf(SARAH.name, SARAH.role);
    expect(forbidden.length).toBeGreaterThan(0);
    for (const claimId of forbidden.slice(0, 5)) {
      expect(rendered.has(claimId)).toBe(false);
    }

    // And the endpoint itself, asked directly from her own session with a
    // scope smuggled into the query string, answers her book anyway.
    const smuggled = await page.evaluate(async () => {
      const resp = await fetch("/api/claims/queue?employerId=1&scopeAll=true");
      return (await resp.json()) as { groups: Record<string, { items: { claimId: string }[] }> };
    });
    const smuggledIds = Object.values(smuggled.groups).flatMap((g) =>
      g.items.map((i) => i.claimId),
    );
    expect(new Set(smuggledIds)).toEqual(rendered);
  });

  test("a card carries the flags and figures the server computed (AC 5)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const expected = expectedQueueFor(KAYA.name, KAYA.role);
    const top = expected.treatment[0];
    const card = page.locator(`[data-testid="queue-card"][data-claim-id="${top.claimId}"]`);

    await expect(card.getByTestId("queue-card-id")).toContainText(top.claimId);
    await expect(card.getByTestId("queue-card-days-open")).toHaveText(`${top.daysOpen}d`);
    await expect(card.getByTestId("queue-card-risk")).toHaveAttribute("data-risk", top.risk);
    // Present and non-empty rather than compared to a string: the worker's
    // name and the employer's short name are joins the queue query makes,
    // and an empty one is how a broken join shows up.
    await expect(card.getByTestId("queue-card-worker")).not.toBeEmpty();
    await expect(card.getByTestId("queue-card-employer")).not.toBeEmpty();
    await expect(card.getByTestId("queue-card-stage")).toContainText("Treatment");
  });

  test("a stale selection is explained rather than redirected away", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await page.goto("/workspace?claim=WC-9999");

    await expect(byTestId(page, "detail-unknown")).toContainText("not in this caseload");
    // No card is highlighted, and the URL is left exactly as typed — a
    // redirect to the first claim would hide the broken link it came from.
    await expect(page.locator('[data-testid="queue-card"][aria-current="true"]')).toHaveCount(0);
    await expect(page).toHaveURL(/claim=WC-9999/);
  });

  test("a group pages through the browser's own session without repeats or gaps", async ({
    page,
  }) => {
    /**
     * The cursor machinery, exercised at browser level (AD-15).
     *
     * The SPA cannot reach it on its own: the biggest seeded handler group
     * is 26 against a `pageLimit` of 50, and the client never sends
     * `limit`, so "Show more" cannot appear no matter what a spec clicks.
     * That left the whole round trip — encode, hand back, decode, continue
     * — proven only by pytest, against an ASGI transport rather than
     * through nginx, a cookie jar and a real HTTP stack.
     *
     * `page.request` is the same session the shell is logged into (the
     * pattern 1-3 uses for `/api/me`), so this is the caller's own scoped
     * book, not an unauthenticated peek.
     */
    await loginAs(page, PERSONAS.handler);

    const expected = expectedQueueFor(KAYA.name, KAYA.role).settled.map((card) => card.claimId);
    expect(expected.length).toBeGreaterThan(10);

    const collected: string[] = [];
    let url = "/api/claims/queue?limit=5";
    let total: number | null = null;

    for (let request = 0; request < 20; request += 1) {
      const resp = await page.request.get(url);
      expect(resp.status()).toBe(200);
      const group = (await resp.json()).groups.settled as {
        items: { claimId: string }[];
        nextCursor: string | null;
        total: number;
      };

      // The count beside the stage header is the whole group on every page,
      // never the page in hand.
      total ??= group.total;
      expect(group.total).toBe(total);

      collected.push(...group.items.map((item) => item.claimId));
      if (group.nextCursor === null) break;
      url = `/api/claims/queue?limit=5&stage=settled&cursor=${encodeURIComponent(group.nextCursor)}`;
    }

    // No repeats, no gaps, and the same order the oracle ranks them in.
    expect(collected).toEqual(expected);
    expect(new Set(collected).size).toBe(collected.length);
    expect(total).toBe(expected.length);
  });

  test("a cursor replayed against a different filter is refused", async ({ page }) => {
    // Serving it would silently mix two orderings: the caller would get a
    // page that overlaps or skips the one before it with nothing to say
    // why. A 400 problem document says why.
    await loginAs(page, PERSONAS.handler);

    const first = await (await page.request.get("/api/claims/queue?limit=5")).json();
    const cursor = first.groups.settled.nextCursor as string;
    expect(cursor).not.toBeNull();

    const replayed = await page.request.get(
      `/api/claims/queue?limit=5&filter=litigation&stage=settled&cursor=${encodeURIComponent(cursor)}`,
    );

    expect(replayed.status()).toBe(400);
    expect(replayed.headers()["content-type"]).toContain("application/problem+json");
    expect(await replayed.json()).toMatchObject({ type: "/problems/invalid-cursor", status: 400 });
  });

  test("the three panes and the shared top bar are all present (UX-DR3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    await expect(byRole(page, "region", "Claim workspace")).toBeVisible();
    await expect(byTestId(page, "queue-pane")).toBeVisible();
    await expect(byTestId(page, "detail-pane")).toBeVisible();
    // The third pane, which the test's own title had been promising and not
    // checking. It is the one that matters most here: the copilot column is
    // the only pane behind a breakpoint (`xl:block`), so it is the only one
    // a layout change can remove without any other assertion noticing. The
    // project's viewport is Desktop Chrome's 1280×720, which is exactly
    // Tailwind's `xl` — so this also pins the breakpoint choice.
    await expect(byTestId(page, "copilot-pane")).toBeVisible();
    // Story 1.4's and 1.5's seams still occupied — the workspace gained a
    // queue without losing the bar every role shares.
    await expect(byTestId(page, "top-bar")).toBeVisible();
    await expect(byTestId(page, "sla-strip")).toBeVisible();
  });

  test("a group collapses without losing its count (AC 1, UX-DR3)", async ({ page }) => {
    // The collapsible half of AC 1, which existed only in Vitest against a
    // stub. The count chip is the point: it reports the whole group, so
    // closing the drawer must not change it — a chip that followed the
    // rendered cards would read zero on every closed section.
    await loginAs(page, PERSONAS.handler);
    await queueLoaded(page);

    const expected = expectedQueueFor(KAYA.name, KAYA.role);
    const header = byTestId(page, "queue-group-treatment-header");
    await expect(header).toHaveAttribute("aria-expanded", "true");
    expect(await renderedIds(page, "treatment")).toHaveLength(expected.treatment.length);

    await header.click();

    await expect(header).toHaveAttribute("aria-expanded", "false");
    expect(await renderedIds(page, "treatment")).toHaveLength(0);
    await expect(byTestId(page, "queue-group-treatment-count")).toHaveText(
      String(expected.treatment.length),
    );
    // The stage beside it is untouched — collapsing is per section.
    expect(await renderedIds(page, "settled")).toHaveLength(expected.settled.length);

    await header.click();
    await expect(header).toHaveAttribute("aria-expanded", "true");
    expect(await renderedIds(page, "treatment")).toEqual(
      expected.treatment.map((card) => card.claimId),
    );
  });

  test("a filter that matches nothing says so in its own words (NFR-3)", async ({ page }) => {
    /**
     * The filter-miss pane state, against the real stack.
     *
     * NFR-3 asks for three distinguishable messages and this is the one the
     * server has to disambiguate: the groups come back empty either way, and
     * only `unfilteredTotal` separates "nothing matches this filter" from
     * "your caseload is empty". Sarah has eight 3M claims and none of them
     * litigated, so the filtered payload is empty over a book that is not.
     *
     * The third state — an empty book — is deliberately absent: every seeded
     * persona has claims, so reaching it would need either a seed change
     * (Story 1.2's) or an HTTP stub, and this file stubs nothing. It is
     * covered in `QueuePane.test.tsx` and by `unfilteredTotal`'s server
     * tests, and recorded in the story's Review Findings.
     */
    await loginAs(page, PERSONAS.scopedHandler);

    const book = expectedQueueFor(SARAH.name, SARAH.role);
    const litigated = expectedQueueFor(SARAH.name, SARAH.role, "litigation");
    expect(STAGES.flatMap((s) => book[s]).length).toBeGreaterThan(0);
    expect(STAGES.flatMap((s) => litigated[s])).toHaveLength(0);

    await byTestId(page, "queue-filter").click();
    await byTestId(page, "queue-filter-option-litigation").click();

    await expect(byTestId(page, "queue-empty-filter")).toContainText(
      "No claims match this filter.",
    );
    // Not the empty-caseload sentence, and not four stacked stage messages:
    // the pane collapses to one line on purpose (see `QueuePane`'s note).
    await expect(byTestId(page, "queue-empty-scope")).toHaveCount(0);
    await expect(byTestId(page, "queue-group-treatment")).toHaveCount(0);
  });
});
