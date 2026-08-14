import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { formatCents } from "@/lib/money";
import {
  CLAIM_DETAIL_INTAKE,
  CLAIM_DETAIL_INVESTIGATION,
  CLAIM_DETAIL_INVESTIGATION_UNPAID,
  CLAIM_DETAIL_NOT_FOUND,
  CLAIM_DETAIL_NO_TIMELINE,
  CLAIM_DETAIL_SETTLED,
  CLAIM_DETAIL_SETTLED_DATED,
  CLAIM_DETAIL_TREATMENT,
  CLAIM_FINANCIALS,
  ME_HANDLER,
  RESERVE_CHECK,
  type StubRoute,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "./ClaimDetailPane";
import { RESERVE_VERDICT_LABEL } from "./labels";

/**
 * The case file, rendered from a payload.
 *
 * Every assertion here is "the pane shows what the server sent" — there is
 * no expectation in this file that was computed from another field, because
 * a test that recomputed the phase or the cost split would agree with a
 * component that recomputed it too. `noDerivation.test.ts` is the structural
 * half of the same criterion; this is the behavioural half.
 */

function renderPane(claimDetail: StubRouteFor, claim: string | null = "WC-20017") {
  stubApi({ me: ME_HANDLER, claimDetail });
  const path = claim === null ? "/workspace" : `/workspace?claim=${claim}`;
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- NFR-3: the four states ---------------------------------------------

test("no selection is its own message, not an empty case file", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT, null);

  expect(await screen.findByTestId("detail-none")).toBeInTheDocument();
  expect(screen.queryByTestId("case-header")).not.toBeInTheDocument();
});

test("a request in flight draws a skeleton, not a blank pane", async () => {
  renderPane("pending" as StubRoute);

  expect(await screen.findByTestId("detail-skeleton")).toBeInTheDocument();
  expect(screen.queryByTestId("case-header")).not.toBeInTheDocument();
});

test("a 404 is reported as absence, not as failure", async () => {
  // The distinction matters: "not in this caseload" is a fact a stale link
  // deserves plainly; "could not be loaded" invites a retry that will never
  // work. They are different elements so a test can tell them apart.
  renderPane(CLAIM_DETAIL_NOT_FOUND, "WC-9999");

  expect(await screen.findByTestId("detail-unknown")).toHaveTextContent("WC-9999");
  expect(screen.queryByTestId("detail-error")).not.toBeInTheDocument();
});

test("any other failure is an alert that says so", async () => {
  // A 403 rather than a 500: the shared client retries 5xx twice with
  // backoff, so a 500 would race the retry timer instead of testing the
  // branch (Story 1.4's lesson).
  renderPane({
    status: 403,
    body: { type: "/problems/forbidden", title: "Forbidden", status: 403, detail: "no" },
  });

  const alert = await screen.findByTestId("detail-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(alert).toHaveTextContent("could not be loaded");
});

// --- AC 1: the header ---------------------------------------------------

test("the header carries the worker, the meta line and the injury summary", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("case-header-name")).toHaveTextContent("Marcus Delgado");
  expect(screen.getByTestId("case-header-claim-id")).toHaveTextContent("WC-20017");
  expect(screen.getByTestId("case-header-employer")).toHaveTextContent("Caterpillar Inc.");
  const injury = screen.getByTestId("case-header-injury");
  expect(injury).toHaveTextContent("Fall from Height — Lower Back");
  expect(injury).toHaveTextContent("ICD-10 S39.012A");
  expect(injury).toHaveTextContent("High severity");
});

test("every conditional badge renders when its flag is set", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("badge-stage")).toHaveTextContent("Treatment");
  expect(screen.getByTestId("badge-fraud")).toHaveTextContent("Fraud Score: 62");
  expect(screen.getByTestId("badge-litigation")).toBeInTheDocument();
  expect(screen.getByTestId("badge-surgery")).toBeInTheDocument();
  expect(screen.getByTestId("badge-osha")).toBeInTheDocument();
});

test("a claim with no flags carries only its stage pill", async () => {
  // The half a "renders the badges" test misses: absence has to be
  // observable, or a component that always drew all four would pass.
  renderPane(CLAIM_DETAIL_INTAKE, "WC-20003");

  expect(await screen.findByTestId("badge-stage")).toHaveTextContent("Intake");
  expect(screen.queryByTestId("badge-fraud")).not.toBeInTheDocument();
  expect(screen.queryByTestId("badge-litigation")).not.toBeInTheDocument();
  expect(screen.queryByTestId("badge-surgery")).not.toBeInTheDocument();
  expect(screen.queryByTestId("badge-osha")).not.toBeInTheDocument();
});

test.each([
  ["WC-20017", CLAIM_DETAIL_TREATMENT, "high", 115],
  ["WC-20051", CLAIM_DETAIL_INVESTIGATION, "med", 75],
  ["WC-20003", CLAIM_DETAIL_INTAKE, "low", 35],
])("the gauge is coloured by the band the server sent (%s)", async (claim, payload, band, arc) => {
  renderPane(payload, claim);

  const gauge = await screen.findByTestId("risk-gauge");
  expect(gauge).toHaveAttribute("data-risk", band);
  // The arc length is the prototype's drawing for each band; what matters is
  // that it follows the *payload's* band rather than a score this component
  // banded itself.
  expect(screen.getByTestId("risk-gauge-arc")).toHaveAttribute(
    "stroke-dasharray",
    `${arc} 132`,
  );
});

// --- AC 2: the stepper and the tabs -------------------------------------

test.each([
  ["WC-20003", CLAIM_DETAIL_INTAKE, "intake", 0],
  ["WC-20051", CLAIM_DETAIL_INVESTIGATION, "investigation", 1],
  ["WC-20017", CLAIM_DETAIL_TREATMENT, "treatment", 2],
  ["WC-20068", CLAIM_DETAIL_SETTLED, "settled", 3],
])(
  "the stepper marks done and current for a %s claim",
  async (claim, payload, stage, doneCount) => {
    renderPane(payload, claim);

    const steps = await screen.findAllByTestId("stepper-step");
    expect(steps).toHaveLength(4);
    expect(steps.filter((step) => step.dataset.state === "done")).toHaveLength(doneCount);
    const current = steps.find((step) => step.dataset.state === "current");
    expect(current?.dataset.stage).toBe(stage);
    expect(current).toHaveAttribute("aria-current", "step");
  },
);

test("the stepper is the first element of the Overview tab", async () => {
  // AC 2 says "always begins with"; asserting only that it renders would
  // pass with it at the bottom of the pane.
  renderPane(CLAIM_DETAIL_TREATMENT);

  const panel = await screen.findByRole("tabpanel");
  expect(panel.firstElementChild).toBe(screen.getByTestId("stage-stepper"));
});

test("all six tabs render, with Overview selected", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  const tabs = await screen.findAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent)).toEqual([
    "Overview",
    "Injury Diagram",
    "Bills & Payments",
    "Documents & ID",
    // Story 2.6 gave this label the count the prototype's has. It is the
    // fixture's `photos.count`, not the length of its grid — the two are the
    // same here, and `PhotosTab.test.tsx` is where they are made to disagree.
    "Photos (3)",
    "AI Insights",
  ]);
  expect(screen.getByTestId("tab-overview")).toHaveAttribute("aria-selected", "true");
});

test("the Photos tab's label counts what the payload says it counts", async () => {
  // Story 2.2 asserted the *absence* of a count here, because there was no
  // `photo` table to count. 2.6 created it, so the assertion becomes the
  // stronger one it was standing in for — re-pointed rather than deleted, on
  // 1.5/1.6/2.1/2.3/2.4/2.5's precedent.
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("tab-photos")).toHaveTextContent(/^Photos \(3\)$/);
});

// Story 2.4 removed the `injury` row, 2.5 the `documents` one, 2.6 the
// `photos` one and 3.3 the `bills` one: the tabs they named are built, so an
// assertion that any of them still says "arrives with Story 2.x" would be
// asserting the seam rather than the shipped surface. What the rows were
// really guaranteeing — every unbuilt tab is honest about being unbuilt — is
// asserted by the one that remains, and each built tab's content is asserted
// in its own suite (`InjuryTab.test.tsx`, `DocumentsTab.test.tsx`,
// `PhotosTab.test.tsx`, `BillsTab.test.tsx`).
//
// One row left, and it names an *epic* rather than a story. That is the state
// Epic 3's financial engine closes in: the only seam still standing belongs to
// the copilot.
test.each([["insights", "copilot"]])(
  "the %s tab shows an explicit empty state naming its story",
  async (tab, mentions) => {
    renderPane(CLAIM_DETAIL_TREATMENT);
    await screen.findByTestId("case-header");

    await userEvent.click(screen.getByTestId(`tab-${tab}`));

    expect(screen.getByTestId(`tab-empty-${tab}`)).toHaveTextContent(mentions);
    expect(screen.queryByTestId("stage-stepper")).not.toBeInTheDocument();
  },
);

test("the Photos tab is built, and renders its grid rather than a seam", async () => {
  // The stronger half of the row that was just removed: a seam assertion goes
  // on passing while a tab renders nothing at all, so what replaces it has to
  // check that the *panel* is there (Story 2.5's rule for the Documents seam).
  renderPane(CLAIM_DETAIL_TREATMENT);
  await screen.findByTestId("case-header");

  await userEvent.click(screen.getByTestId("tab-photos"));

  expect(screen.getByTestId("photos-tab")).toBeVisible();
  expect(screen.queryByTestId("tab-empty-photos")).not.toBeInTheDocument();
});

test("the Bills jump-link lands on the built Bills tab", async () => {
  // The @smoke path's last step, and the assertion Story 3.3 promoted: the
  // link is still a tab switch rather than a route, but the tab it switches to
  // now renders the financial picture instead of naming the epic that would
  // bring it. Checking the *panel* rather than only the selected state, on
  // Story 2.5's rule for the Documents seam — a tab can be selected and empty.
  renderPane(CLAIM_DETAIL_TREATMENT);

  await userEvent.click(await screen.findByTestId("treatment-bills-link"));

  expect(screen.getByTestId("tab-bills")).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByTestId("bills-tab")).toBeVisible();
  expect(screen.queryByTestId("tab-empty-bills")).not.toBeInTheDocument();
});

test("the Overview's medical-paid row is the bills' figure, not the empty column", async () => {
  // The defect the second review of Story 3.3 found: this row read
  // `overview.paidMedicalCents` — `claim.paid_medical`, 0 on all 38 open
  // seeded claims — directly above a live indemnity figure and directly above
  // the link to a tab showing the same claim's paid bills as a real number.
  //
  // The fixture makes it a real check rather than a coincidence: the case-file
  // block's `paidMedicalCents` and the reserve block's `disbursedMedicalCents`
  // are deliberately different numbers, so a component reading the wrong one
  // has something visibly wrong to be.
  renderPane(CLAIM_DETAIL_TREATMENT);

  await expect(screen.findByTestId("treatment-medical-paid")).resolves.toHaveTextContent(
    formatCents(RESERVE_CHECK.disbursedMedicalCents),
  );
});

test("the Bills tab and the Overview card state the same indemnity figures", async () => {
  // AC 4, at the component level: the jump-link's whole point is that the
  // handler lands on the same numbers they just left. Both surfaces read the
  // *same* `reserveCheck` block — the case file's on one side, the financials
  // payload's on the other — so this fails if either component starts
  // computing rather than rendering, or reads a different pair of fields.
  //
  // The fixtures make that a real check rather than a tautology: `RESERVE_CHECK`
  // is the object `CLAIM_FINANCIALS` publishes, so the two payloads agree the
  // way the server's do.
  renderPane(CLAIM_DETAIL_TREATMENT);

  const overviewPaid = (await screen.findByTestId("treatment-indemnity-paid")).textContent;

  await userEvent.click(screen.getByTestId("treatment-bills-link"));
  await screen.findByTestId("bills-tab");

  const disbursed = screen.getByTestId("schedule-disbursed").textContent;
  const scheduled = screen.getByTestId("schedule-scheduled").textContent;
  expect(overviewPaid).toBe(`${disbursed} of ${scheduled}`);

  // And the verdict itself, which is the other thing both cards render.
  expect(screen.getByTestId("summary-reserve-check")).toHaveTextContent(
    RESERVE_VERDICT_LABEL[CLAIM_FINANCIALS.body.reserveCheck.verdict],
  );
});

// --- AC 3: one variant per stage ----------------------------------------

test("the intake variant renders its two cards and the checklist", async () => {
  renderPane(CLAIM_DETAIL_INTAKE, "WC-20003");

  expect(await screen.findByTestId("intake-summary")).toHaveTextContent("EMP-1042");
  expect(screen.getByTestId("intake-summary")).toHaveTextContent(
    "Need for Additional Information",
  );
  expect(screen.getByTestId("intake-injury")).toHaveTextContent("Repetitive Strain");
  expect(screen.getByTestId("intake-severity")).toHaveTextContent("Low (22/100)");
  expect(screen.queryByTestId("investigation-financials")).not.toBeInTheDocument();
});

test("the checklist shows Received and Missing per required document", async () => {
  renderPane(CLAIM_DETAIL_INTAKE, "WC-20003");

  const rows = await screen.findAllByTestId("checklist-row");
  expect(rows.map((row) => [row.dataset.docType, row.dataset.received])).toEqual([
    ["froi", "true"],
    ["incident", "false"],
    ["medauth", "true"],
    ["wage", "false"],
  ]);
  expect(rows[1]).toHaveTextContent("Incident Investigation Report");
  expect(rows[1]).toHaveTextContent("Missing");
  expect(rows[0]).toHaveTextContent("Received");
});

test("the investigation variant renders its values and the cost bar", async () => {
  renderPane(CLAIM_DETAIL_INVESTIGATION, "WC-20051");

  // Story 2.2 asserted the injury card had no inputs at all, because the
  // audited command behind them did not exist yet. Story 2.3 built it, so
  // the assertion is re-pointed rather than deleted: the *financials* card
  // is the one that must still be read-only here — the reserve and the
  // severity score belong to Epic 3 and Story 2.4, and an input over either
  // would be a form with nothing behind it.
  const injury = await screen.findByTestId("investigation-injury");
  expect((screen.getByTestId("edit-injuryType") as HTMLInputElement).value).toBe("Laceration");
  expect((screen.getByTestId("edit-disability") as HTMLSelectElement).value).toBe("temporary");
  expect(injury).toHaveTextContent("AWW");

  const financials = screen.getByTestId("investigation-financials");
  expect(financials.querySelectorAll("input, select, textarea")).toHaveLength(0);

  expect(screen.getByTestId("investigation-total")).toHaveTextContent("$10,000");
  expect(screen.getByTestId("cost-bar-indemnity")).toHaveStyle({ width: "40%" });
  expect(screen.getByTestId("cost-bar-medical")).toHaveStyle({ width: "55%" });
});

test("an investigation claim with no payments says so instead of drawing a bar", async () => {
  renderPane(CLAIM_DETAIL_INVESTIGATION_UNPAID, "WC-20051");

  expect(await screen.findByTestId("investigation-unpaid")).toHaveTextContent(
    "Active — payments pending",
  );
  expect(screen.queryByTestId("cost-bar")).not.toBeInTheDocument();
  expect(screen.getByTestId("investigation-total")).toHaveTextContent("Active");
});

test("the treatment variant renders both derived states as sent", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("treatment-phase-label")).toHaveTextContent(
    "Approaching MMI / RTW Planning",
  );
  expect(screen.getByTestId("treatment-phase-note")).toHaveTextContent(
    "Nearing maximum medical improvement",
  );
  expect(screen.getByTestId("treatment-phase-day")).toHaveTextContent("Day 140 of claim");
  expect(screen.getByTestId("treatment-coordination-label")).toHaveTextContent(
    "⚠ Coordination Gap",
  );
  expect(screen.getByTestId("treatment-coordination-note")).toHaveTextContent(
    "RTW follow-up is overdue",
  );
});

test("the treatment reserve check states the server's verdict and its sentence", async () => {
  // Story 3.2. The label is the UI's (the wire carries `adequate`), the
  // sentence is the server's, and the component compares nothing to arrive at
  // either — `ReserveCheckCard.test.tsx` covers the variants and the absence
  // of client-side arithmetic.
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("treatment-reserve-check")).toHaveTextContent(
    "Reserve Adequate",
  );
  expect(screen.getByTestId("treatment-reserve-rationale")).toHaveTextContent(
    "Reserve ($45,000) is well aligned with projected remaining exposure ($40,500).",
  );
});

test("the settled variant renders the banner, payout and outcome", async () => {
  renderPane(CLAIM_DETAIL_SETTLED, "WC-20068");

  expect(await screen.findByTestId("settled-total")).toHaveTextContent("$31,000");
  expect(screen.getByTestId("settled-payout")).toHaveTextContent("$15,000");
  expect(screen.getByTestId("settled-outcome")).toHaveTextContent("Permanent");
  expect(screen.getByTestId("settled-outcome")).toHaveTextContent("212");
  // Three segments here, unlike investigation's two.
  expect(screen.getByTestId("cost-bar-expense")).toHaveStyle({ width: "7%" });
});

test("the settled banner omits the date it does not have", async () => {
  // Every seeded settlement event carries `Closed` where a date belongs, so
  // `settlementDate` is null. The prototype prints "settled on Closed".
  renderPane(CLAIM_DETAIL_SETTLED, "WC-20068");

  const banner = await screen.findByTestId("settled-banner");
  expect(banner).toHaveTextContent("This claim reached final settlement.");
  expect(screen.queryByTestId("settled-date")).not.toBeInTheDocument();
});

test("the settled banner names the date when there is one", async () => {
  // The other half of the test above, and the branch no fixture reached
  // until the code review: `settlementDate` is null on every seeded claim,
  // so the clause the component promises to "gain without a change" was
  // rendered by nothing.
  renderPane(CLAIM_DETAIL_SETTLED_DATED, "WC-20068");

  expect(await screen.findByTestId("settled-date")).toHaveTextContent("2026-07-29");
  expect(screen.getByTestId("settled-banner")).toHaveTextContent(
    "reached final settlement on 2026-07-29",
  );
});

// --- the tab strip is operable without a mouse (WCAG 2.1.1) -------------

test("arrow keys move across the tab strip", async () => {
  // `tabIndex={-1}` on the unselected tabs is only half of roving tabindex.
  // Without the handler this shipped without, a keyboard user reaches
  // Overview and nothing else.
  renderPane(CLAIM_DETAIL_TREATMENT);
  await screen.findByTestId("case-header");

  screen.getByTestId("tab-overview").focus();
  await userEvent.keyboard("{ArrowRight}");

  expect(screen.getByTestId("tab-injury")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("tab-injury")).toHaveFocus();
  // Re-pointed from the seam panel to the panel that replaced it (Story
  // 2.4): what this line is checking is that the *panel* followed the
  // selection, which is the half of the tabs pattern that would otherwise
  // pass while showing Overview's content under the Injury tab.
  expect(screen.getByTestId("injury-tab")).toBeInTheDocument();
});

test("the arrow keys wrap at both ends, and Home/End jump", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);
  await screen.findByTestId("case-header");

  screen.getByTestId("tab-overview").focus();
  // Left from the first tab wraps to the last.
  await userEvent.keyboard("{ArrowLeft}");
  expect(screen.getByTestId("tab-insights")).toHaveAttribute("aria-selected", "true");
  // …and right from the last wraps back to the first.
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByTestId("tab-overview")).toHaveAttribute("aria-selected", "true");

  await userEvent.keyboard("{End}");
  expect(screen.getByTestId("tab-insights")).toHaveAttribute("aria-selected", "true");
  await userEvent.keyboard("{Home}");
  expect(screen.getByTestId("tab-overview")).toHaveAttribute("aria-selected", "true");
});

test("only the selected tab is in the tab order", async () => {
  // The invariant that makes the arrow keys necessary rather than optional.
  renderPane(CLAIM_DETAIL_TREATMENT);
  await screen.findByTestId("case-header");

  const tabs = screen.getAllByRole("tab");
  const inOrder = tabs.filter((tab) => tab.getAttribute("tabindex") === "0");
  expect(inOrder).toHaveLength(1);
  expect(inOrder[0]).toHaveAttribute("data-testid", "tab-overview");
});

// --- the timeline -------------------------------------------------------

test("the timeline renders the entries the server sent, in order", async () => {
  renderPane(CLAIM_DETAIL_SETTLED, "WC-20068");

  const entries = await screen.findAllByTestId("timeline-entry");
  expect(entries).toHaveLength(3);
  expect(entries[0]).toHaveTextContent("FNOL received");
  expect(entries[2]).toHaveTextContent("Handler assigned");
});

test("a claim with no history says so rather than rendering nothing", async () => {
  renderPane(CLAIM_DETAIL_NO_TIMELINE, "WC-20068");

  expect(await screen.findByTestId("timeline-empty")).toHaveTextContent(
    "No timeline events on file.",
  );
});

test("a truncated treatment timeline is headed 'recent'", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("timeline-card")).toHaveTextContent("Recent case timeline");
});

test("an untruncated one is not — the heading follows the server's flag", async () => {
  // Two tests rather than two renders in one: the second render would mount
  // beside the first, and `findAllByTestId` would then be asserting about
  // whichever card the query happened to return.
  renderPane({
    status: 200,
    body: {
      ...CLAIM_DETAIL_TREATMENT.body,
      overview: { ...CLAIM_DETAIL_TREATMENT.body.overview, timelineTruncated: false },
    },
  });

  const card = await screen.findByTestId("timeline-card");
  expect(card).toHaveTextContent("Case timeline");
  expect(card).not.toHaveTextContent("Recent case timeline");
});

// --- selection ----------------------------------------------------------

test("switching claims resets the tab, because it is a different case file", async () => {
  const { unmount } = renderPane(CLAIM_DETAIL_TREATMENT);
  await userEvent.click(await screen.findByTestId("tab-documents"));
  // Re-pointed at the built tab (Story 2.5), and stronger for it: asserting a
  // seam panel would have gone on passing while the tab it stands in for
  // rendered nothing at all.
  expect(screen.getByTestId("documents-tab")).toBeInTheDocument();
  unmount();

  vi.unstubAllGlobals();
  renderPane(CLAIM_DETAIL_INTAKE, "WC-20003");

  expect(await screen.findByTestId("tab-overview")).toHaveAttribute("aria-selected", "true");
});
