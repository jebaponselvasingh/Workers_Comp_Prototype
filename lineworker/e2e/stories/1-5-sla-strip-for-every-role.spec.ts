import { PERSONAS, loginAs } from "../fixtures/login";
import { expectedSlaFor } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.5 — SLA Strip for Every Role.
 *
 * The clause under test is "every role": the prototype recomputed this
 * strip on handler flows only, so supervisors and analysts read the
 * markup's placeholder numbers and had no way to tell. Nothing here stubs
 * HTTP — each persona logs in for real and the tiles are compared against
 * the seed counted independently (`fixtures/seed.ts`).
 */

const TILES = {
  pick: "sla-pick",
  approve: "sla-approve",
  settle: "sla-settle",
  rtwRate: "sla-rtw-rate",
} as const;

/**
 * Server-decided verdict → the token the tile is drawn in (UX notes).
 *
 * `no_data` is here because the oracle can legitimately predict it: no
 * seeded persona has an empty segment today, but a seed change could give
 * one, and the spec should then expect the muted em dash the component
 * correctly renders rather than fail pointing at the component.
 */
const TONE = {
  pick: { pass: "bg-ok", warn: "bg-warn", no_data: "bg-surface-2" },
  approve: { pass: "bg-ok", warn: "bg-warn", no_data: "bg-surface-2" },
  settle: { pass: "bg-ok", warn: "bg-warn", no_data: "bg-surface-2" },
  // A missed return-to-work rate is the prototype's one error-toned tile.
  rtwRate: { pass: "bg-ok", warn: "bg-error", no_data: "bg-surface-2" },
} as const;

async function expectStrip(
  page: Parameters<typeof byTestId>[0],
  persona: { name: string; role: string },
): Promise<void> {
  const expected = expectedSlaFor(persona.name, persona.role);

  for (const [metric, testId] of Object.entries(TILES)) {
    const tile = expected[metric];
    // `toHaveText` on the value element rather than `toContainText` on the
    // tile: "2.7d" contains "2", and the target line beside it contains
    // the target's digits too.
    await expect(byTestId(page, `${testId}-value`)).toHaveText(tile.text);
    await expect(byTestId(page, testId)).toHaveClass(
      new RegExp(TONE[metric as keyof typeof TONE][tile.status]),
    );
  }
}

test.describe("@story:1-5 @epic:1 SLA strip for every role", () => {
  test("@smoke David Bline's strip renders four computed tiles", async ({ page }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    await expect(byTestId(page, "sla-strip")).toBeVisible();
    await expectStrip(page, PERSONAS.fullPortfolioSupervisor);
    // No tile fell back to an em dash: the full portfolio has data for all
    // four metrics, so a dash here would mean the aggregation dropped a
    // segment rather than that the book was empty.
    await expect(byTestId(page, "sla-strip")).not.toContainText("—");
  });

  test("a handler gets the strip over their own book (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    await expectStrip(page, PERSONAS.handler);
  });

  test("an analyst gets it too — the endpoint has no role branch (FR-SLA-1)", async ({ page }) => {
    await loginAs(page, PERSONAS.analyst);

    await expectStrip(page, PERSONAS.analyst);
  });

  test("a scoped supervisor's strip differs from the portfolio's (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await expectStrip(page, PERSONAS.scopedSupervisor);

    // The pair is the proof of scoping: identical figures would satisfy
    // every per-persona assertion above even if scope had stopped applying.
    const park = expectedSlaFor("Jennifer Park", "supervisor");
    const bline = expectedSlaFor("David Bline", "supervisor");
    expect(park.settle.text).not.toBe(bline.settle.text);
  });

  test("a met target is styled and annotated as met (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.passingRtwSupervisor);

    // Ken Stoker's book clears the return-to-work target where every other
    // persona's misses it, so this is the suite's one live `pass` tile.
    await expect(byTestId(page, "sla-rtw-rate")).toHaveClass(/bg-ok/);
    await expect(byTestId(page, "sla-rtw-rate-target")).toHaveText("✓ >80%");
    // …beside a missed one, annotated with the operator flipped.
    await expect(byTestId(page, "sla-pick-target")).toHaveText("⚠ >1d");
  });

  test("each tile explains itself on hover and on focus (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    /**
     * Asserted through `aria-describedby` rather than by finding "the"
     * tooltip on the page. Two reasons, and the second is the important
     * one: an opened tooltip does not always close before the next one
     * opens, so a bare `getByRole("tooltip")` is ambiguous; and the
     * description *being attached to its tile* is the accessibility claim
     * this AC makes — a floating box with the right words in it that no
     * assistive technology associates with the number is not a tooltip,
     * it is decoration.
     */
    const describes = async (testId: string): Promise<string> => {
      const tile = byTestId(page, testId);
      await expect(tile).toHaveAttribute("aria-describedby", /.+/);
      const id = await tile.getAttribute("aria-describedby");
      return (await page.locator(`#${id}`).textContent()) ?? "";
    };

    await byTestId(page, "sla-settle").hover();
    expect(await describes("sla-settle")).toBe(
      "Avg days from FROI to Settlement — target <30 days",
    );

    // Keyboard-reachable, not hover-only: the tooltip carries the metric's
    // definition, which is not optional information.
    await byTestId(page, "sla-rtw-rate").focus();
    expect(await describes("sla-rtw-rate")).toBe(
      "Percentage of settled claims with successful RTW",
    );
  });

  test("the strip is served per session and cannot be asked for someone else's", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    // The same smuggling attempt Story 1.4 makes against /stats/topbar,
    // from inside the authenticated page so the session cookie rides along.
    const [honest, smuggled] = await page.evaluate(async () => {
      const get = async (query: string) => (await fetch(`/api/stats/sla${query}`)).json();
      return [await get(""), await get("?employerId=1&scopeAll=true&role=supervisor")];
    });

    expect(smuggled).toEqual(honest);
  });
});
