import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  DRILL_CLAIMS,
  DRILL_CLAIMS_AGE_BAND,
  DRILL_CLAIMS_EMPTY,
  DRILL_CLAIMS_FILTERED,
  DRILL_CLAIMS_PAGE_TWO,
  DRILL_CLAIMS_REORDERED,
  stubApi,
} from "@/test/api-mock";

import { DrillClaimsPage } from "./DrillClaimsPage";

/**
 * Story 5.5 AC 1 and AC 4 — the list renders what arrived, and the URL is the
 * only state.
 *
 * Two lines are policed here. The first is `noDerivation.test.ts`'s,
 * behaviourally: the page receives a page of a filtered, ranked list plus the
 * population it was cut from, and it may format and lay out; it may not
 * re-order, re-cut or re-count. So the re-ordered contrast fixture renders
 * through the same component — a page that sorted would read the same under
 * both, and one that renders what it was sent cannot.
 *
 * The second is that **the filter set lives in the URL**. Every assertion about
 * chips goes through `initialEntries` rather than through a prop, because a
 * link pasted into a new tab is the case AC 4 is about, and a component that
 * held filters in state would pass a prop-driven test and fail that one.
 */
function renderList(
  routes: Parameters<typeof stubApi>[0],
  entry = "/dashboard/claims?filter%5BseverityBand%5D=high",
) {
  stubApi(routes);
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <QueryClientProvider client={createQueryClient()}>
        <DrillClaimsPage />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** The address bar, as an element — what a chip's ✕ has to move. */
function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">{`${location.pathname}${location.search}`}</span>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function claimIds(): string[] {
  return screen
    .getAllByTestId("queue-card")
    .map((card) => card.getAttribute("data-claim-id") ?? "");
}

// --- the four states (NFR-3) --------------------------------------------

test("a request in flight draws a skeleton and says the section is busy", () => {
  renderList({ drillClaims: "pending" });

  expect(screen.getByTestId("drill-skeleton")).toBeInTheDocument();
  expect(screen.getByTestId("drill-claims")).toHaveAttribute("aria-busy", "true");
  // No count while the figure is unknown: "0 in view" over a spinner is a
  // portfolio with nothing in it, which is a quieter lie than saying nothing.
  expect(screen.queryByTestId("drill-count")).not.toBeInTheDocument();
});

test("a failed request states it inline and draws no list", async () => {
  renderList({ drillClaims: { status: 404, body: {} } });

  const alert = await screen.findByTestId("drill-error");
  expect(alert).toHaveAttribute("role", "alert");
  // In place of the list rather than above an empty one: a headless list reads
  // as "no claims match", which is a different and much quieter lie.
  expect(screen.queryAllByTestId("queue-card")).toHaveLength(0);
});

test("a refused filter stays visible and clearable, and the copy does not promise a retry", async () => {
  // The state a stale link reaches: an enum value the server no longer has.
  // Chips are normally the server's echo of the request, and a 422 has no
  // payload to echo — so without the URL fallback the one control that fixes
  // this disappears in exactly the case where it is the fix, leaving a
  // permanent error whose only exit is the address bar.
  const user = userEvent.setup();
  renderList(
    {
      drillClaims: {
        status: 422,
        body: { type: "/problems/validation-error", title: "Unprocessable Entity" },
      },
    },
    "/dashboard/claims?filter%5Bstage%5D=banana",
  );

  const alert = await screen.findByTestId("drill-error");
  // `createQueryClient` never retries below 500, so "try again in a moment"
  // would promise a recovery the client has already ruled out.
  expect(alert).not.toHaveTextContent("Try again in a moment");
  expect(alert).toHaveTextContent("Clear it above");

  const chips = await screen.findAllByTestId("drill-chip");
  expect(chips).toHaveLength(1);
  expect(chips[0]).toHaveTextContent("Stage: banana");

  await user.click(screen.getByRole("button", { name: /remove/i }));
  expect(screen.getByTestId("location")).toHaveTextContent("/dashboard/claims");
  expect(screen.getByTestId("location")).not.toHaveTextContent("banana");
});

test("a failure that is not about the filters offers no chip and keeps the retry copy", async () => {
  // The discriminator, from the other side: a 404 is not a facet a supervisor
  // can drop, so offering her a ✕ would be offering a fix that is not one.
  renderList({ drillClaims: { status: 404, body: { type: "/problems/not-found" } } });

  const alert = await screen.findByTestId("drill-error");
  expect(alert).toHaveTextContent("Try again in a moment");
  expect(screen.queryAllByTestId("drill-chip")).toHaveLength(0);
});

test("an empty result names the filter that produced it", async () => {
  renderList({ drillClaims: DRILL_CLAIMS_EMPTY }, "/dashboard/claims?filter%5Blitigation%5D=true");

  const empty = await screen.findByTestId("drill-empty");
  // "No claims" over a screen a supervisor reached by clicking Litigation is a
  // sentence she cannot act on. The active filter is the one thing she needs.
  expect(empty).toHaveTextContent("Litigation: Yes");
  expect(screen.getByTestId("drill-count")).toHaveTextContent("0 in view");
});

test("the rows are the server's, in the server's order", async () => {
  renderList({ drillClaims: DRILL_CLAIMS });

  await screen.findAllByTestId("queue-card");

  expect(claimIds()).toEqual(DRILL_CLAIMS.body.items.map((row) => row.claimId));
  // The population, quoted verbatim — never `items.length`, which is a page and
  // would shrink the sentence as the reader scrolled.
  expect(screen.getByTestId("drill-count")).toHaveTextContent("32 in view");
});

test("a re-ordered payload renders re-ordered — the list never sorts", async () => {
  renderList({ drillClaims: DRILL_CLAIMS_REORDERED });

  await screen.findAllByTestId("queue-card");

  // The reverse of the server's order is the one permutation every plausible
  // client-side sort would *undo*, so a list that re-imposed the ranking would
  // produce `DRILL_CLAIMS`' order here and fail.
  expect(claimIds()).toEqual(
    DRILL_CLAIMS_REORDERED.body.items.map((row) => row.claimId),
  );
});

// --- the chips (AC 1) ----------------------------------------------------

test("one applied filter draws one chip, from the server's own reading", async () => {
  renderList({ drillClaims: DRILL_CLAIMS });

  const chips = await screen.findAllByTestId("drill-chip");
  expect(chips).toHaveLength(1);
  expect(chips[0]).toHaveTextContent("Severity: High");
  // Drawn from `appliedFilters` rather than from a second parse of the URL: the
  // server ignored what it did not recognise, so a client re-parsing would draw
  // a chip for a narrowing that never happened.
  expect(screen.queryByTestId("drill-clear-all")).not.toBeInTheDocument();
});

test("an id-valued chip reads the name the server resolved", async () => {
  renderList(
    { drillClaims: DRILL_CLAIMS_FILTERED },
    "/dashboard/claims?filter%5BseverityBand%5D=high&filter%5BemployerId%5D=2",
  );

  const chips = await screen.findAllByTestId("drill-chip");
  expect(chips).toHaveLength(2);
  expect(chips[0]).toHaveTextContent("Severity: High");
  // Nothing in the browser can turn `employerId=2` into a name on a cold load —
  // the chart that published the id may never have been rendered — so the
  // server resolves it off rows it had already read.
  expect(chips[1]).toHaveTextContent("Employer: Boeing");
});

test("an age-band chip reads the same range here as on the workspace it came from", async () => {
  renderList(
    { drillClaims: DRILL_CLAIMS_AGE_BAND },
    "/dashboard/claims?filter%5BageGroup%5D=older",
  );

  // AC 2 is that the segmentation survives the click as a clearable chip, and a
  // chip that renames itself in transit barely survives: this read "Age group:
  // older" one click after reading "Age group: 45–54" on the bar that produced
  // it. One value, two names, on two screens of one workspace.
  //
  // The fix is not a label decided server-side — that would be a second copy of
  // a value derived from a rule document, free to disagree with the workspace's
  // the first time an edge moved. It is the three **edges**, published on this
  // payload from the document this route already loads, composed through the
  // same `ageBandLabels` the bar calls.
  const chips = await screen.findAllByTestId("drill-chip");
  expect(chips[0]).toHaveTextContent("Age group: 45–54");
  // …and the wire value is untouched: it is what the URL carries and what the
  // ✕ removes.
  expect(chips[0]).toHaveAttribute("data-filter-key", "ageGroup");
  // The empty state names the same filter by the same words, rather than by the
  // ordinal word underneath it.
  expect(screen.queryByTestId("drill-empty")).toBeNull();
});

test("an age-band chip falls back to the ordinal word when there are no edges to compose from", async () => {
  // A 422 has no payload to publish edges on, and the chips are drawn from the
  // URL instead (`appliedFromFilters`). "Age group: older" is honest there — the
  // alternative is a second request for a caption on a screen whose only useful
  // control is a ✕.
  renderList(
    {
      drillClaims: {
        status: 422,
        body: {
          type: "/problems/validation-error",
          title: "Unprocessable Content",
          status: 422,
          detail: "no",
        },
      },
    },
    "/dashboard/claims?filter%5BageGroup%5D=older",
  );

  const chips = await screen.findAllByTestId("drill-chip");
  expect(chips[0]).toHaveTextContent("Age group: older");
});

test("a chip's ✕ removes exactly one key from the URL", async () => {
  renderList(
    { drillClaims: DRILL_CLAIMS_FILTERED },
    "/dashboard/claims?filter%5BseverityBand%5D=high&filter%5BemployerId%5D=2",
  );

  await screen.findAllByTestId("drill-chip");
  const remove = screen
    .getAllByTestId("drill-chip-remove")
    .find((button) => button.getAttribute("data-filter-key") === "employerId");
  await userEvent.click(remove!);

  // One narrowing widened, the other left alone — a "clear filters" that threw
  // away both would be the wrong control for the gesture.
  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/claims?filter%5BseverityBand%5D=high",
    ),
  );
});

test("the ✕ is a named control rather than a bare glyph", async () => {
  renderList({ drillClaims: DRILL_CLAIMS });

  // "✕" is not an accessible name. The button says which filter it removes.
  expect(
    await screen.findByRole("button", { name: "Remove the Severity: High filter" }),
  ).toBeInTheDocument();
});

test("Clear all appears with more than one chip and widens to the whole book", async () => {
  renderList(
    { drillClaims: DRILL_CLAIMS_FILTERED },
    "/dashboard/claims?filter%5BseverityBand%5D=high&filter%5BemployerId%5D=2",
  );

  await userEvent.click(await screen.findByTestId("drill-clear-all"));

  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent("/dashboard/claims"),
  );
  expect(screen.getByTestId("location")).not.toHaveTextContent("filter");
});

test("no filter draws no chip row at all", async () => {
  renderList({ drillClaims: { status: 200, body: { ...DRILL_CLAIMS.body, appliedFilters: [] } } }, "/dashboard/claims");

  await screen.findAllByTestId("queue-card");

  // An unfiltered list is the whole book; a row saying "no filters" would be a
  // control with no purpose above a list that is already complete.
  expect(screen.queryByTestId("drill-chips")).not.toBeInTheDocument();
});

// --- paging --------------------------------------------------------------

test("Show more appends the next page and then disappears", async () => {
  renderList({
    drillClaims: (url) =>
      url.includes("cursor=") ? DRILL_CLAIMS_PAGE_TWO : DRILL_CLAIMS,
  });

  await screen.findAllByTestId("queue-card");
  expect(claimIds()).toHaveLength(DRILL_CLAIMS.body.items.length);

  await userEvent.click(screen.getByTestId("drill-more"));

  await waitFor(() =>
    expect(claimIds()).toEqual([
      ...DRILL_CLAIMS.body.items.map((row) => row.claimId),
      ...DRILL_CLAIMS_PAGE_TWO.body.items.map((row) => row.claimId),
    ]),
  );
  // `nextCursor: null` on page two, so the button goes rather than offering a
  // third page the server would refuse.
  expect(screen.queryByTestId("drill-more")).not.toBeInTheDocument();
  // …and the count is still the population, unchanged by the page that landed.
  expect(screen.getByTestId("drill-count")).toHaveTextContent("32 in view");
});

test("a rejected second page keeps the loaded rows and offers a list reload", async () => {
  renderList({
    drillClaims: (url) =>
      url.includes("cursor=") ? { status: 400, body: {} } : DRILL_CLAIMS,
  });

  await screen.findAllByTestId("queue-card");
  await userEvent.click(screen.getByTestId("drill-more"));

  const alert = await screen.findByTestId("drill-page-error");
  expect(alert).toHaveAttribute("role", "alert");
  // The rows that did arrive stay: a failure to extend a list is not a reason
  // to throw away what the reader is looking at.
  expect(claimIds()).toHaveLength(DRILL_CLAIMS.body.items.length);
  // And the only recovery offered reloads the list rather than replaying the
  // cursor the server just refused — which would fail identically for ever.
  expect(screen.getByTestId("drill-reload")).toBeInTheDocument();
  expect(screen.queryByTestId("drill-more")).not.toBeInTheDocument();
});

// --- navigation ----------------------------------------------------------

test("a row opens that claim's read-only view", async () => {
  renderList({ drillClaims: DRILL_CLAIMS });

  const cards = await screen.findAllByTestId("queue-card");
  await userEvent.click(cards[0]);

  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/claims/${DRILL_CLAIMS.body.items[0].claimId}`,
    ),
  );
});

test("a back control returns to the dashboard", async () => {
  renderList({ drillClaims: DRILL_CLAIMS });

  expect(await screen.findByTestId("drill-back")).toHaveAttribute(
    "href",
    "/dashboard",
  );
});
