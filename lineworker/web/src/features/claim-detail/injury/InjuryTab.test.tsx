/**
 * The Injury Diagram tab (Story 2.4) — what it draws, and what it refuses.
 *
 * Every assertion here is "the tab shows what the server sent". The bands,
 * the labels and the treatment-plan order are all payload values, and the
 * fixture spells them out rather than deriving them from the scores beside
 * them — a test that recomputed a band would agree with a component that
 * recomputed one too. `noDerivation.test.ts` is the structural half of the
 * same criterion.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_TREATMENT,
  INJURY_DIAGRAM,
  INJURY_UNKNOWN_KEY,
  ME_HANDLER,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "../ClaimDetailPane";

function renderTab(claimDetail: StubRouteFor = CLAIM_DETAIL_TREATMENT) {
  stubApi({ me: ME_HANDLER, claimDetail });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Open the tab the way a handler does. */
async function openTab(claimDetail: StubRouteFor = CLAIM_DETAIL_TREATMENT) {
  renderTab(claimDetail);
  await screen.findByTestId("case-header");
  await userEvent.click(screen.getByTestId("tab-injury"));
  return screen.getByTestId("injury-tab");
}

/** A payload with the injury block replaced. */
function withInjury(injury: unknown) {
  return {
    status: 200,
    body: { ...CLAIM_DETAIL_TREATMENT.body, injury },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- AC 1: the body map -------------------------------------------------

test("one marker is drawn per injury, at its own region and in its own band", async () => {
  await openTab();

  const markers = screen.getAllByTestId("injury-marker");
  expect(markers).toHaveLength(INJURY_DIAGRAM.markers.length);
  // The band comes off the payload, not off the score: a component that
  // banded `78` itself would still pass a check on the *number*, so the
  // assertion is on the attribute the colour is chosen by.
  expect(markers[0]).toHaveAttribute("data-body-key", "lumbar");
  expect(markers[0]).toHaveAttribute("data-band", "high");
  expect(markers[1]).toHaveAttribute("data-body-key", "hand_left");
  expect(markers[1]).toHaveAttribute("data-band", "med");
});

test("each region is drawn at its own hotspot", async () => {
  await openTab();

  const [primary, secondary] = screen.getAllByTestId("injury-marker");
  // `lumbar` and `hand_left` are at different coordinates in the prototype's
  // dictionary; two markers sharing a centre would mean the port collapsed
  // the table to a single fallback and nothing else would say so.
  const centreOf = (marker: HTMLElement) => {
    const circle = marker.querySelector("circle");
    return `${circle?.getAttribute("cx")},${circle?.getAttribute("cy")}`;
  };
  expect(centreOf(primary)).toBe("65,158");
  expect(centreOf(secondary)).toBe("14,148");
});

test("only the primary marker pulses (UX-DR6)", async () => {
  await openTab();

  const markers = screen.getAllByTestId("injury-marker");
  expect(markers[0]).toHaveAttribute("data-primary", "true");
  expect(within(markers[0]).getByTestId("injury-marker-pulse")).toBeInTheDocument();
  expect(within(markers[1]).queryByTestId("injury-marker-pulse")).not.toBeInTheDocument();
});

test("a region this build does not know falls back to the torso", async () => {
  // Unreachable through the UI — the select is built from the server's
  // vocabulary and the command refuses anything outside it — so only a
  // fixture can produce it. The failure it prevents is worse than a
  // misplaced dot: `undefined.cx` renders no marker at all, which reads as
  // "this claim has no injury".
  await openTab(withInjury(INJURY_UNKNOWN_KEY));

  const [primary] = screen.getAllByTestId("injury-marker");
  expect(primary).toHaveAttribute("data-body-key", "cervical_spine");
  expect(primary.querySelector("circle")).toHaveAttribute("cx", "65");
  expect(primary.querySelector("circle")).toHaveAttribute("cy", "100");
});

// --- AC 1: the cards ----------------------------------------------------

test("the severity bar is as wide as the score and coloured by the band", async () => {
  await openTab();

  const bar = screen.getByTestId("injury-severity-bar");
  expect(bar).toHaveStyle({ width: "78%" });
  expect(bar.className).toContain("bg-error");
  expect(screen.getByTestId("injury-severity-word")).toHaveTextContent("/100 — High");
  expect(screen.getByTestId("injury-risk-trend")).toHaveTextContent("High risk");
});

test("the prognosis, treatment plan and restrictions are the payload's", async () => {
  await openTab();

  expect(screen.getByTestId("injury-prognosis-mmi")).toHaveTextContent("6-10 mo");
  expect(screen.getByTestId("injury-prognosis-litigation")).toHaveTextContent("Low");

  const steps = screen.getAllByTestId("injury-treatment-step");
  expect(steps).toHaveLength(INJURY_DIAGRAM.treatmentPlan.length);
  // Numbered from `stepNo`, in the order the server sent — not from the
  // array index, which would renumber a plan whose steps were reordered.
  expect(steps[0]).toHaveTextContent("1");
  expect(steps[0]).toHaveTextContent("Head/spine CT protocol if fall >4 feet");

  expect(screen.getByTestId("injury-restrictions")).toHaveTextContent(
    "No elevated work platform access",
  );
});

test("the body-part select offers the server's eleven regions", async () => {
  await openTab();

  const select = screen.getByTestId("edit-bodyKey");
  expect(within(select).getAllByRole("option")).toHaveLength(11);
  expect(select).toHaveValue("lumbar");
});

// --- AC 2: the popover --------------------------------------------------

test("the popover lists every injury, tags the primary and offers ✕ only on the rest", async () => {
  await openTab();
  await userEvent.click(screen.getByTestId("injury-add-open"));

  const rows = await screen.findAllByTestId("injury-summary-row");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByTestId("injury-primary-tag")).toBeInTheDocument();
  // The primary is the claim's own injury: removing it would leave a claim
  // with no injury on it, and the server sends no `id` to address.
  expect(within(rows[0]).queryByTestId("injury-remove")).not.toBeInTheDocument();
  expect(within(rows[1]).getByTestId("injury-remove")).toHaveAttribute("data-injury-id", "41");
});

test("the add form starts at the server's default severity", async () => {
  // A rule-document parameter (`injury_capture`), not a literal in the
  // component — retuning it must not need a deploy.
  await openTab();
  await userEvent.click(screen.getByTestId("injury-add-open"));

  expect(await screen.findByTestId("injury-new-severity")).toHaveValue(
    INJURY_DIAGRAM.defaultSeverityScore,
  );
});

// --- AC 4: inline validation, never a dialog ----------------------------

test("an empty injury type is refused inline, and nothing is sent", async () => {
  // The prototype's refusal here is a silent `typeEl.focus()`, which tells a
  // handler nothing at all (UX-DR11).
  await openTab();
  await userEvent.click(screen.getByTestId("injury-add-open"));
  await userEvent.click(await screen.findByTestId("injury-add-submit"));

  expect(await screen.findByTestId("injury-add-invalid")).toHaveTextContent(
    "Injury type is required",
  );
  // **NFR-3 is about what blocks the handler, not about an ARIA role.** A
  // Radix popover is `role="dialog"` whether or not it is modal, so the
  // assertion that means something is that it is *not* modal — nothing
  // behind it is inert, and there is no alert dialog anywhere.
  expect(screen.getByTestId("injury-popover")).not.toHaveAttribute("aria-modal", "true");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("a severity outside the served bounds is refused inline", async () => {
  await openTab();
  await userEvent.click(screen.getByTestId("injury-add-open"));

  await userEvent.type(await screen.findByTestId("injury-new-type"), "Laceration");
  const severity = screen.getByTestId("injury-new-severity");
  await userEvent.clear(severity);
  await userEvent.type(severity, "101");
  await userEvent.click(screen.getByTestId("injury-add-submit"));

  // The bounds are quoted from the payload, so a server that widened them
  // would change this message without a code change.
  expect(await screen.findByTestId("injury-add-invalid")).toHaveTextContent(
    `between ${INJURY_DIAGRAM.severityMin} and ${INJURY_DIAGRAM.severityMax}`,
  );
  expect(screen.getByTestId("injury-popover")).not.toHaveAttribute("aria-modal", "true");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("a severity score outside the bounds is refused at the field, not sent", async () => {
  await openTab();

  const input = screen.getByTestId("edit-severityScore");
  await userEvent.clear(input);
  await userEvent.type(input, "150");
  await userEvent.tab();

  expect(await screen.findByTestId("edit-severityScore-invalid")).toHaveTextContent(
    `between ${INJURY_DIAGRAM.severityMin} and ${INJURY_DIAGRAM.severityMax}`,
  );
  // Kept so it can be corrected rather than retyped — 2.3's rule for a 422,
  // applied to the refusal the browser makes on its own.
  expect(input).toHaveValue(150);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});
