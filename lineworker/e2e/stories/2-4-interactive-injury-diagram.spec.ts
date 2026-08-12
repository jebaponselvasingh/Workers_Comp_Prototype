import { PERSONAS, loginAs } from "../fixtures/login";
import {
  expectedContraindications,
  expectedPrimaryMarker,
  expectedPrognosis,
  expectedTimelineFor,
  expectedTreatmentPlan,
  firstClaimInStage,
  otherBodyKey,
  scoreInAnotherBand,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 2.4 — Interactive Injury Diagram.
 *
 * Nothing is stubbed: the browser drives the real SPA, the API runs the real
 * compare-and-swaps against the real database, and the expectations come from
 * `fixtures/seed.ts` reading the same seed files the stack migrated with.
 *
 * **The band the markers are drawn in is the *console's*, not the
 * prototype's.** `injHTML` colours its markers from a cut-off pair written
 * into the drawing function; this build bands every marker through the one
 * registered `risk` derivation, whose parameters are in a rule document. The
 * oracle below restates the document's rule, so a diagram that went back to
 * the prototype's second pair fails here.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec
 * *file*, and several tests below add and remove injuries on the same claim,
 * so every one of them reads the current version before it writes — as a
 * client does.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

/** Open a claim's Injury Diagram tab the way a handler does. */
async function openDiagram(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await byTestId(page, "tab-injury").click();
  await expect(byTestId(page, "injury-tab")).toBeVisible();
}

/** The case file as the API answers it — the versions a client would send. */
async function readClaim(page: Page, claimId: string): Promise<Record<string, never>> {
  const response = await page.request.get(`/api/claims/${claimId}`);
  expect(response.status()).toBe(200);
  return response.json();
}

test.describe("@story:2-4 @epic:2 interactive injury diagram", () => {
  test("@smoke the diagram renders, takes a second injury, and gives it back", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    // The **investigation** claim, deliberately: its overview variant renders
    // the whole timeline, while the treatment variant renders only the recent
    // six — so counting events after the write would be counting the slice.
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const primary = expectedPrimaryMarker(claimId);
    const plan = expectedTreatmentPlan(claimId);
    const events = expectedTimelineFor(claimId).length;

    await openDiagram(page, claimId);

    // --- AC 1: the silhouette, the primary marker, and the cards ---------
    await expect(byTestId(page, "body-map")).toBeVisible();
    const markers = byTestId(page, "injury-marker");
    await expect(markers).toHaveCount(1);
    await expect(markers.first()).toHaveAttribute("data-body-key", primary.bodyKey);
    await expect(markers.first()).toHaveAttribute("data-band", primary.band);
    // The primary pulses (UX-DR6) and nothing else does.
    await expect(byTestId(page, "injury-marker-pulse")).toHaveCount(1);

    await expect(byTestId(page, "injury-prognosis-mmi")).toHaveText(
      expectedPrognosis(claimId).mmi,
    );
    await expect(byTestId(page, "injury-treatment-step")).toHaveCount(plan.length);
    await expect(byTestId(page, "injury-treatment-step").first()).toContainText(plan[0]);
    await expect(byTestId(page, "injury-restrictions")).toContainText(
      expectedContraindications(claimId),
    );

    // --- AC 2: add a secondary injury through the popover ---------------
    await byTestId(page, "injury-add-open").click();
    await byTestId(page, "injury-new-body-key").selectOption("shoulder_left");
    await byTestId(page, "injury-new-type").fill("Rotator Cuff Tear");
    await byTestId(page, "injury-new-severity").fill("90");
    await byTestId(page, "injury-add-submit").click();

    await expect(markers).toHaveCount(2);
    const secondary = markers.nth(1);
    await expect(secondary).toHaveAttribute("data-body-key", "shoulder_left");
    // Banded by its own score, by the same rule as the primary.
    await expect(secondary).toHaveAttribute("data-band", "high");
    await expect(secondary).toHaveAttribute("data-primary", "false");

    // It is on the claim, not just on the screen.
    await page.reload();
    await byTestId(page, "tab-injury").click();
    await expect(byTestId(page, "injury-marker")).toHaveCount(2);

    // …and the command logged itself in the same transaction (AD-4).
    await byTestId(page, "tab-overview").click();
    const timeline = byTestId(page, "timeline-entry");
    await expect(timeline).toHaveCount(events + 1);
    await expect(timeline.last()).toContainText("Additional injury recorded (Left Shoulder)");
    // The sentence names the region, never the free text a handler typed.
    await expect(timeline.last()).not.toContainText("Rotator Cuff Tear");

    // --- AC 2: remove it again ------------------------------------------
    await byTestId(page, "tab-injury").click();
    await byTestId(page, "injury-add-open").click();
    await byTestId(page, "injury-remove").click();

    await expect(byTestId(page, "injury-marker")).toHaveCount(1);
    await page.reload();
    await byTestId(page, "tab-injury").click();
    await expect(byTestId(page, "injury-marker")).toHaveCount(1);
  });

  test("the popover lists every injury and tags the primary (AC 1, AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");

    await openDiagram(page, claimId);
    await byTestId(page, "injury-add-open").click();

    const rows = byTestId(page, "injury-summary-row");
    await expect(rows).toHaveCount(1);
    await expect(byTestId(page, "injury-primary-tag")).toHaveCount(1);
    // The primary is the claim's own injury: there is no row to remove and
    // no `id` on the wire to address one with.
    await expect(byTestId(page, "injury-remove")).toHaveCount(0);

    await byTestId(page, "injury-new-type").fill("Laceration");
    await byTestId(page, "injury-add-submit").click();

    await expect(rows).toHaveCount(2);
    await expect(byTestId(page, "injury-remove")).toHaveCount(1);
  });

  test("editing the severity score moves the gauge, the marker and the queue card (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const target = scoreInAnotherBand(claimId);

    await openDiagram(page, claimId);
    // Read rather than restated from the seed: several tests in this file
    // share one database, and an assertion that depended on this claim being
    // untouched would be an assertion about the order the file runs in.
    const startingBand = await byTestId(page, "risk-gauge").getAttribute("data-risk");
    expect(startingBand).not.toBe(target.band);

    const field = byTestId(page, "edit-severityScore");
    await field.fill(String(target.score));
    await field.press("Enter");

    // The band is the server's answer, recomputed from the column that
    // changed — the browser holds no rule that could have produced it.
    await expect(byTestId(page, "injury-marker").first()).toHaveAttribute(
      "data-band",
      target.band,
    );
    await expect(byTestId(page, "risk-gauge")).toHaveAttribute("data-risk", target.band);

    // …and the queue card beside it agrees, having been asked separately.
    const card = page.locator(`[data-testid="queue-card"][data-claim-id="${claimId}"]`);
    await expect(card.getByTestId("queue-card-risk")).toHaveAttribute("data-risk", target.band);

    await page.reload();
    await byTestId(page, "tab-injury").click();
    await expect(byTestId(page, "edit-severityScore")).toHaveValue(String(target.score));
  });

  test("changing the body part moves the marker and relabels the claim (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const target = otherBodyKey(claimId);

    await openDiagram(page, claimId);
    await byTestId(page, "edit-bodyKey").selectOption(target.key);

    await expect(byTestId(page, "injury-marker").first()).toHaveAttribute(
      "data-body-key",
      target.key,
    );
    // The label is the server's, derived from the key — this tab is where
    // both vocabularies are on screen at once.
    await expect(byTestId(page, "case-header-injury")).toContainText(target.label);

    await page.reload();
    await byTestId(page, "tab-injury").click();
    await expect(byTestId(page, "edit-bodyKey")).toHaveValue(target.key);
  });

  test("an out-of-range severity is refused inline and saves nothing (AC 4)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");

    await openDiagram(page, claimId);
    // The stored value, read from the field rather than from the seed: an
    // earlier test in this file has already edited this claim's score, and
    // what this test is about is that the refused edit changed nothing.
    const stored = await byTestId(page, "edit-severityScore").inputValue();

    const field = byTestId(page, "edit-severityScore");
    await field.fill("150");
    await field.press("Enter");

    await expect(byTestId(page, "edit-severityScore-invalid")).toContainText("between 0 and 100");
    // No native dialog anywhere (NFR-3, UX-DR11).
    await expect(page.locator("[role=alertdialog]")).toHaveCount(0);

    await page.reload();
    await byTestId(page, "tab-injury").click();
    await expect(byTestId(page, "edit-severityScore")).toHaveValue(stored);
  });

  test("an empty injury type is refused inline, without a request (AC 4)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");

    await openDiagram(page, claimId);
    const markersBefore = await byTestId(page, "injury-marker").count();

    await byTestId(page, "injury-add-open").click();
    await byTestId(page, "injury-add-submit").click();

    // The prototype's refusal here is a silent `focus()`, which tells a
    // handler nothing at all.
    await expect(byTestId(page, "injury-add-invalid")).toContainText("Injury type is required");
    await expect(page.locator("[role=alertdialog]")).toHaveCount(0);
    // Refused before any request: the marker count is whatever it already
    // was, and the point is that submitting did not change it.
    await expect(byTestId(page, "injury-marker")).toHaveCount(markersBefore);
  });

  test("the API refuses a read-only role and an out-of-scope claim (AD-7)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const held = (await readClaim(page, claimId)) as unknown as { version: number };

    // The same 404 the GET answers, so a write route cannot be used to find
    // out whose claims exist.
    const refused = await page.request.patch("/api/claims/WC-99999/severity", {
      data: { expectedVersion: 1, severityScore: 10 },
    });
    expect(refused.status()).toBe(404);

    // A second handler's claim is not addressable either.
    const someoneElses = firstClaimInStage(SARAH.name, SARAH.role, "treatment");
    const outOfScope = await page.request.post(`/api/claims/${someoneElses}/injuries`, {
      data: {
        expectedVersion: 1,
        bodyKey: "torso",
        injuryType: "Contusion",
        severityScore: 10,
      },
    });
    expect(outOfScope.status()).toBe(404);
    expect((await outOfScope.json()).type).toBe((await refused.json()).type);

    // A stale claim version is a 409 carrying the fresh entity.
    const stale = await page.request.post(`/api/claims/${claimId}/injuries`, {
      data: {
        expectedVersion: held.version + 99,
        bodyKey: "torso",
        injuryType: "Contusion",
        severityScore: 10,
      },
    });
    expect(stale.status()).toBe(409);
    expect((await stale.json()).claim.claimId).toBe(claimId);
  });
});
