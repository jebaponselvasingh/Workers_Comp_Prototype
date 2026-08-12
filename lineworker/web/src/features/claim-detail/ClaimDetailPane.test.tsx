import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_INTAKE,
  CLAIM_DETAIL_INVESTIGATION,
  CLAIM_DETAIL_INVESTIGATION_UNPAID,
  CLAIM_DETAIL_NOT_FOUND,
  CLAIM_DETAIL_NO_TIMELINE,
  CLAIM_DETAIL_SETTLED,
  CLAIM_DETAIL_SETTLED_DATED,
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  type StubRoute,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "./ClaimDetailPane";

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
    "Photos",
    "AI Insights",
  ]);
  expect(screen.getByTestId("tab-overview")).toHaveAttribute("aria-selected", "true");
});

test("the Photos tab carries no count until Story 2.6 has a table to count", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("tab-photos")).toHaveTextContent(/^Photos$/);
});

// Story 2.4 removed the `injury` row: the tab it named is built, so an
// assertion that it still says "arrives with Story 2.4" would be asserting
// the seam rather than the shipped surface. What the row was really
// guaranteeing — every unbuilt tab is honest about being unbuilt — is
// asserted by the four that remain, and the injury tab's own content is
// asserted in `InjuryTab.test.tsx`.
test.each([
  ["bills", "financial engine"],
  ["documents", "Story 2.5"],
  ["photos", "Story 2.6"],
  ["insights", "copilot"],
])("the %s tab shows an explicit empty state naming its story", async (tab, mentions) => {
  renderPane(CLAIM_DETAIL_TREATMENT);
  await screen.findByTestId("case-header");

  await userEvent.click(screen.getByTestId(`tab-${tab}`));

  expect(screen.getByTestId(`tab-empty-${tab}`)).toHaveTextContent(mentions);
  expect(screen.queryByTestId("stage-stepper")).not.toBeInTheDocument();
});

test("the Bills jump-link lands on the Bills tab's empty state", async () => {
  // The @smoke path's last step. The link is a tab switch, not a route, and
  // the tab it switches to is honest about being unbuilt.
  renderPane(CLAIM_DETAIL_TREATMENT);

  await userEvent.click(await screen.findByTestId("treatment-bills-link"));

  expect(screen.getByTestId("tab-bills")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("tab-empty-bills")).toHaveTextContent("financial engine");
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

test("the treatment reserve check is an explicit placeholder, not a verdict", async () => {
  // Story 3.2 owns the rule. A card that guessed one from the paid columns
  // would be a different rule wearing the prototype's words.
  renderPane(CLAIM_DETAIL_TREATMENT);

  expect(await screen.findByTestId("treatment-reserve-check")).toHaveTextContent(
    "Verdict arrives with the financial engine",
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
  expect(screen.getByTestId("tab-empty-documents")).toBeInTheDocument();
  unmount();

  vi.unstubAllGlobals();
  renderPane(CLAIM_DETAIL_INTAKE, "WC-20003");

  expect(await screen.findByTestId("tab-overview")).toHaveAttribute("aria-selected", "true");
});
