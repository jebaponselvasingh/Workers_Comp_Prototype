import { PERSONAS, loginAs } from "../fixtures/login";
import {
  claimIdsOutsideScopeOf,
  expectedChecklistFor,
  expectedRecentTimelineFor,
  expectedRiskFor,
  expectedStepperFor,
  expectedTimelineFor,
  firstClaimInStage,
} from "../fixtures/seed";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 2.2 — Case Header & Stage-Adaptive Overview.
 *
 * Nothing here stubs HTTP. The browser logs in for real, the API scopes,
 * derives and assembles for real against the seeded database, and every
 * expectation is computed independently in `fixtures/seed.ts` from the same
 * two seed files the stack was migrated with. That independence is what
 * makes these assertions capable of disagreeing with the implementation.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

/** Open one claim's case file directly, without going through the queue. */
async function openClaim(
  page: Parameters<typeof byTestId>[0],
  claimId: string,
): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
}

test.describe("@story:2-2 @epic:2 case header and stage-adaptive overview", () => {
  test("@smoke a treatment claim opens on a header, a gauge, a stepper and its own variant", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openClaim(page, claimId);

    // --- the header (AC 1) ---------------------------------------------
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(claimId);
    // The worker's name and the employer are joins the detail query makes;
    // an empty one is how a broken join shows up.
    await expect(byTestId(page, "case-header-name")).not.toBeEmpty();
    await expect(byTestId(page, "case-header-employer")).not.toBeEmpty();
    await expect(byTestId(page, "case-header-injury")).toContainText("ICD-10");
    await expect(byTestId(page, "badge-stage")).toHaveText("Treatment");

    // --- the gauge, coloured by the band the queue also uses (AC 1) -----
    await expect(byTestId(page, "risk-gauge")).toHaveAttribute(
      "data-risk",
      expectedRiskFor(claimId),
    );

    // --- the stepper, first thing in Overview (AC 2) --------------------
    const steps = byTestId(page, "stepper-step");
    await expect(steps).toHaveCount(4);
    for (const [index, step] of expectedStepperFor("treatment").entries()) {
      await expect(steps.nth(index)).toHaveAttribute("data-stage", step.stage);
      await expect(steps.nth(index)).toHaveAttribute("data-state", step.state);
    }

    // --- the derived values, server-computed (AC 3, AC 5) --------------
    await expect(byTestId(page, "treatment-phase-banner")).toBeVisible();
    // Present and non-empty rather than compared to a string: which phase a
    // claim is in depends on the day the request is served, and pinning a
    // phase here would make the spec fail on a Tuesday. That the *rule* is
    // right is `test_case_file_derivations.py`'s job; that it reaches the
    // screen is this one's.
    await expect(byTestId(page, "treatment-phase-label")).not.toBeEmpty();
    await expect(byTestId(page, "treatment-phase-note")).not.toBeEmpty();
    await expect(byTestId(page, "treatment-phase-day")).toContainText("of claim");
    await expect(byTestId(page, "treatment-coordination-label")).not.toBeEmpty();
    await expect(byTestId(page, "treatment-coordination-note")).not.toBeEmpty();

    // --- the Bills jump-link switches tabs (AC 4) -----------------------
    // Story 3.3 filled the tab in, so this asserts the *switch* and that the
    // panel is real; the figures it lands on are 3.3's own spec's subject.
    await byTestId(page, "treatment-bills-link").click();
    await expect(byTestId(page, "tab-bills")).toHaveAttribute("aria-selected", "true");
    await expect(byTestId(page, "bills-tab")).toBeVisible();
    await expect(byTestId(page, "tab-empty-bills")).toHaveCount(0);
  });

  test("the intake variant shows its checklist against the claim's documents (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    await openClaim(page, claimId);

    await expect(byTestId(page, "intake-summary")).toBeVisible();
    await expect(byTestId(page, "intake-injury")).toBeVisible();

    const expected = expectedChecklistFor(claimId);
    const rows = byTestId(page, "checklist-row");
    await expect(rows).toHaveCount(expected.length);
    for (const [index, row] of expected.entries()) {
      await expect(rows.nth(index)).toHaveAttribute("data-doc-type", row.docType);
      await expect(rows.nth(index)).toHaveAttribute("data-received", String(row.received));
      await expect(rows.nth(index)).toContainText(row.received ? "Received" : "Missing");
    }

    // The whole timeline, not the treatment slice.
    await expect(byTestId(page, "timeline-entry")).toHaveCount(
      expectedTimelineFor(claimId).length,
    );
  });

  test("the investigation variant renders its two cards (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    await openClaim(page, claimId);

    const injury = byTestId(page, "investigation-injury");
    await expect(injury).toBeVisible();
    // **Amended by Story 2.3** (AD-15: a later story that changes earlier
    // behaviour updates the spec in its own PR, rather than deleting the
    // assertion). This used to require *no* input on the injury card,
    // because shipping the prototype's editors before the audited command
    // existed would have been a form that discards what a handler types.
    // 2.3 built the command, so what this story still guarantees is the
    // other half: the injury card renders, and the financials beside it
    // stay read-only — the severity score is 2.4's and the money is Epic
    // 3's. The editing behaviour itself is `2-3-audited-inline-field-
    // editing.spec.ts`.
    await expect(byTestId(page, "investigation-financials")).toBeVisible();
    await expect(
      byTestId(page, "investigation-financials").locator("input, select, textarea"),
    ).toHaveCount(0);
    await expect(byTestId(page, "investigation-severity")).toContainText("/100");
  });

  test("the settled variant shows the payout, the outcome and the action summary (AC 3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "settled");
    await openClaim(page, claimId);

    await expect(byTestId(page, "settled-banner")).toContainText("reached final settlement");
    // No settlement date on any seeded claim: the prototype writes `Closed`
    // where the date belongs, so the clause is omitted rather than printing
    // a non-date. Asserted so that a future data change is a visible test
    // failure rather than a silent new sentence.
    await expect(byTestId(page, "settled-date")).toHaveCount(0);
    await expect(byTestId(page, "settled-payout")).toBeVisible();
    await expect(byTestId(page, "settled-outcome")).toBeVisible();
    await expect(byTestId(page, "settled-timeline")).toContainText("Summary of actions taken");
    await expect(byTestId(page, "timeline-entry")).toHaveCount(
      expectedTimelineFor(claimId).length,
    );
  });

  test("the treatment timeline is the slice the server cut (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openClaim(page, claimId);

    const expected = expectedRecentTimelineFor(claimId);
    const entries = byTestId(page, "timeline-entry");
    await expect(entries).toHaveCount(expected.length);
    for (const [index, entry] of expected.entries()) {
      await expect(entries.nth(index)).toContainText(entry.description);
    }
  });

  test("every stage in the handler's book opens on its own variant (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    // One assertion per stage that the *right* variant rendered — and, just
    // as importantly, that the others did not. A payload with four optional
    // blocks could satisfy the first half and not the second.
    const signatures = {
      intake: "intake-summary",
      investigation: "investigation-financials",
      treatment: "treatment-phase-banner",
      settled: "settled-banner",
    } as const;

    for (const [stage, signature] of Object.entries(signatures)) {
      await openClaim(page, firstClaimInStage(KAYA.name, KAYA.role, stage as "intake"));
      await expect(byTestId(page, signature)).toBeVisible();
      for (const [other, otherSignature] of Object.entries(signatures)) {
        if (other === stage) continue;
        await expect(byTestId(page, otherSignature)).toHaveCount(0);
      }
    }
  });

  test("all six tabs exist and the unbuilt ones say which story fills them (UX-DR5, NFR-3)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    await openClaim(page, firstClaimInStage(KAYA.name, KAYA.role, "treatment"));

    // Scoped to the **case file's** strip since Story 4.1 (which put a second
    // and third tablist on the page — the copilot's two tabs and the diary's
    // three). A page-wide `role="tab"` count was only ever right while the
    // case file was the sole tabbed surface, and it counted 11 the day the
    // right pane arrived. Addressing the strip by its accessible name is what
    // the assertion always meant.
    await expect(byRole(page, "tablist", "Case file sections").getByRole("tab")).toHaveCount(6);

    // **Story 2.4 built the Injury Diagram tab, 2.5 the Documents & ID tab,
    // 2.6 the Photos tab and 3.3 the Bills & Payments tab, so their rows are
    // gone from this table and their panels are asserted below instead.**
    // Re-pointed rather than deleted (1.5, 1.6 and 2.1's precedent): what the
    // rows were really guaranteeing is that a tab is either built or honest
    // about not being, and both halves of that are still asserted here.
    //
    // One row left, and it names an *epic*. That is the state Epic 3's
    // financial engine closes in — the only seam still standing is the
    // copilot's.
    for (const [tab, mentions] of [["insights", "copilot"]] as const) {
      await byTestId(page, `tab-${tab}`).click();
      await expect(byTestId(page, `tab-empty-${tab}`)).toContainText(mentions);
      // The Overview content is gone, so the seam is a real panel switch
      // rather than a message appended under the case file.
      await expect(byTestId(page, "stage-stepper")).toHaveCount(0);
    }

    // The tabs that are built show their content rather than a seam.
    for (const [tab, panel] of [
      ["injury", "injury-tab"],
      ["bills", "bills-tab"],
      ["documents", "documents-tab"],
      ["photos", "photos-tab"],
    ] as const) {
      await byTestId(page, `tab-${tab}`).click();
      await expect(byTestId(page, panel)).toBeVisible();
      await expect(byTestId(page, `tab-empty-${tab}`)).toHaveCount(0);
    }

    // …and Overview comes back.
    await byTestId(page, "tab-overview").click();
    await expect(byTestId(page, "stage-stepper")).toBeVisible();
  });

  test("the tab strip is operable with the keyboard alone (WCAG 2.1.1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await openClaim(page, firstClaimInStage(KAYA.name, KAYA.role, "treatment"));

    // Roving tabindex puts one tab in the document's tab order, so the
    // arrow keys are the *only* way to reach the other five. Asserted in a
    // real browser rather than jsdom because that is where focus, tab order
    // and `preventDefault` actually behave — this shipped with the
    // `tabIndex={-1}` half and no handler (code review, 2026-08-12).
    await byTestId(page, "tab-overview").focus();
    await page.keyboard.press("ArrowRight");
    await expect(byTestId(page, "tab-injury")).toBeFocused();
    // Re-pointed from the seam panel to the panel that replaced it (Story
    // 2.4). The assertion is the same one it always was: the *panel*
    // followed the selection, which is the half of the tabs pattern that
    // would otherwise pass while Overview's content stayed on screen.
    await expect(byTestId(page, "injury-tab")).toBeVisible();

    await page.keyboard.press("End");
    await expect(byTestId(page, "tab-insights")).toBeFocused();
    await expect(byTestId(page, "tab-empty-insights")).toBeVisible();

    await page.keyboard.press("Home");
    await expect(byTestId(page, "tab-overview")).toBeFocused();
    await expect(byTestId(page, "stage-stepper")).toBeVisible();
  });

  test("a claim outside the caller's scope is refused, not described (AD-7)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedHandler);

    // A claim that really exists, that another handler can open, and whose
    // id this persona could plausibly have been handed in a link. The
    // server must answer exactly as it does for an id nobody has — anything
    // else turns this route into an oracle for enumerating the portfolio.
    const someoneElses = claimIdsOutsideScopeOf(SARAH.name, SARAH.role)[0];
    await openClaim(page, someoneElses);
    await expect(byTestId(page, "detail-unknown")).toContainText("not in this caseload");
    await expect(byTestId(page, "case-header")).toHaveCount(0);

    // The API's own answer, without the SPA in the way: a 404, not a 403.
    const response = await page.request.get(`/api/claims/${someoneElses}`);
    expect(response.status()).toBe(404);
    const invented = await page.request.get("/api/claims/WC-99999");
    expect(invented.status()).toBe(404);
    expect((await response.json()).type).toBe((await invented.json()).type);
  });

  test("the case file loads even when the queue does not (NFR-3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");

    // Story 2.1's centre pane had to say "we could not check" here, because
    // the queue payload was the only evidence it had. Two endpoints, two
    // fates: the case file is unaffected.
    await page.route("**/api/claims/queue*", (route) => route.abort());
    await openClaim(page, claimId);

    await expect(byTestId(page, "queue-error")).toBeVisible();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(claimId);
  });

  test("selecting a card in the queue opens that claim's case file (2.1 → 2.2)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    // The seam Story 2.1 built the selection for: the queue writes
    // `?claim=`, this pane reads it, and neither owns the other.
    const target = firstClaimInStage(KAYA.name, KAYA.role, "settled");
    const card = page.locator(`[data-testid="queue-card"][data-claim-id="${target}"]`);
    await expect(card).toBeVisible();
    await card.click();

    await expect(page).toHaveURL(new RegExp(`claim=${target}`));
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(target);
    await expect(byTestId(page, "settled-banner")).toBeVisible();
  });
});
