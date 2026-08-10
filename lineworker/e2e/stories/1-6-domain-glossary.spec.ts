import { PERSONAS, loginAs } from "../fixtures/login";
import { expectedGlossary, glossaryMatching } from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.6 — Domain Glossary.
 *
 * The AC's phrase is "one click away everywhere", so the spec proves the
 * same button in the same shared bar for a handler and for a supervisor,
 * against real HTTP: the browser logs in, `GET /api/glossary` answers from
 * the seeded table, and every expectation is derived from
 * `server/data/seed/glossary_terms.json` (`fixtures/seed.ts`) rather than
 * typed here — no spec in this file knows that the answer is 25.
 */

type PageOf = Parameters<typeof byTestId>[0];

const GIBBERISH = "zzzz";

async function openGlossary(page: PageOf): Promise<void> {
  // The accessible name includes the 📖 glyph, so match on the word.
  await byRole(page, "button", /Glossary/).click();
  await expect(byTestId(page, "glossary-panel")).toBeVisible();
}

async function search(page: PageOf, query: string): Promise<void> {
  await byTestId(page, "glossary-search").fill(query);
}

test.describe("@story:1-6 @epic:1 domain glossary", () => {
  test("@smoke a handler opens the glossary and finds MMI", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    await search(page, "MMI");

    // "MMI" matches three seeded rows, not one: TTD and IME both mention it
    // in their definitions. That is the three-field predicate working
    // rather than a leak, so the expectation comes from the oracle — and
    // the term the story names is asserted present inside it.
    await expect(byTestId(page, "glossary-term-name")).toHaveText(
      glossaryMatching("MMI").map((t) => t.term),
    );
    await expect(
      byTestId(page, "glossary-term").filter({ hasText: "Maximum Medical Improvement" }),
    ).toBeVisible();
  });

  test("every seeded term is listed, in the prototype's order (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    const expected = expectedGlossary();
    // Count *and* order in one assertion: a list that returned the right
    // rows sorted by term would pass a count check and still lose the
    // deliberate FNOL-first ordering the seed's sort_order encodes.
    await expect(byTestId(page, "glossary-term-name")).toHaveText(expected.map((t) => t.term));
    await expect(byTestId(page, "glossary-term-abbr")).toHaveText(
      expected.map((t) => t.abbreviation),
    );
  });

  test("searching an abbreviation filters live, case-insensitively (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    // Typed lower case against an upper-case abbreviation — a glossary you
    // have to shout at is not a glossary.
    await search(page, "havs");

    await expect(byTestId(page, "glossary-term-name")).toHaveText(
      glossaryMatching("havs").map((t) => t.term),
    );
    await expect(byTestId(page, "glossary-term-name")).toHaveText(["Hand-Arm Vibration Syndrome"]);
  });

  test("a word only a definition contains still finds its term (FR-GLOS-1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    // "audiogram" appears in no abbreviation and no term name, so this is
    // the case that proves definition text is searched and not just shown.
    await search(page, "audiogram");

    await expect(byTestId(page, "glossary-term-name")).toHaveText(
      glossaryMatching("audiogram").map((t) => t.term),
    );
    await expect(byTestId(page, "glossary-term-name")).toHaveText(["Noise-Induced Hearing Loss"]);
  });

  test("gibberish gets the no-match state with the query echoed (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    await search(page, GIBBERISH);

    expect(glossaryMatching(GIBBERISH)).toHaveLength(0);
    await expect(byTestId(page, "glossary-empty")).toHaveText(`No matches for "${GIBBERISH}".`);
    await expect(byTestId(page, "glossary-term")).toHaveCount(0);
  });

  test("clearing the query restores the full list (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    await search(page, "havs");
    await expect(byTestId(page, "glossary-term")).toHaveCount(1);

    await search(page, "");
    await expect(byTestId(page, "glossary-term")).toHaveCount(expectedGlossary().length);
  });

  test("✕ closes the panel and returns focus to the button (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);

    await byRole(page, "button", "Close").click();

    await expect(byTestId(page, "glossary-panel")).toHaveCount(0);
    await expect(byRole(page, "button", /Glossary/)).toBeFocused();
  });

  test("reopening resets the search, and the backdrop dismisses it (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openGlossary(page);
    await search(page, "havs");
    await byRole(page, "button", "Close").click();
    await expect(byTestId(page, "glossary-panel")).toHaveCount(0);

    await openGlossary(page);
    // The prototype calls renderGloss("") on open; a remembered filter would
    // look like a glossary that had lost two dozen terms.
    await expect(byTestId(page, "glossary-search")).toHaveValue("");
    await expect(byTestId(page, "glossary-term")).toHaveCount(expectedGlossary().length);

    // The backdrop carries a data-testid because it has no accessible role —
    // the sanctioned second choice in the selector policy, and better than a
    // coordinate click that would silently start hitting the panel if the
    // panel ever got wider.
    await byTestId(page, "glossary-backdrop").click({ position: { x: 10, y: 10 } });
    await expect(byTestId(page, "glossary-panel")).toHaveCount(0);
  });

  test("a supervisor gets the same glossary from the same bar (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await openGlossary(page);

    // Identical to the handler's, deliberately: every other list in this
    // console is scoped per persona, and this one must not be — there is no
    // employer on a glossary term to scope it by.
    await expect(byTestId(page, "glossary-term-name")).toHaveText(
      expectedGlossary().map((t) => t.term),
    );

    await search(page, "havs");
    await expect(byTestId(page, "glossary-term")).toHaveCount(1);
  });

  test("the glossary needs a session, however harmless its contents", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await expect(byTestId(page, "top-bar")).toBeVisible();

    // From inside the authenticated page, so the cookie rides along; then
    // again with it stripped by the server-side logout. `/glossary` is
    // deliberately not in PUBLIC_PATHS.
    const authenticated = await page.evaluate(async () => (await fetch("/api/glossary")).status);
    expect(authenticated).toBe(200);

    const anonymous = await page.evaluate(async () => {
      await fetch("/api/auth/logout", { method: "POST" });
      const resp = await fetch("/api/glossary");
      return { status: resp.status, contentType: resp.headers.get("content-type") };
    });
    expect(anonymous.status).toBe(401);
    expect(anonymous.contentType).toContain("application/problem+json");
  });
});
