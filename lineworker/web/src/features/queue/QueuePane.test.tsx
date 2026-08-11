import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import type { QueueFilter } from "@/api/claims";
import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_QUEUE,
  CLAIM_QUEUE_EMPTY,
  CLAIM_QUEUE_NO_MATCH,
  CLAIM_QUEUE_PAGE_TWO,
  CLAIM_QUEUE_PAGED,
  INTAKE_CARD,
  LOUD_CARD,
  QUIET_CARD,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { QueuePane } from "./QueuePane";
import { useStageExpansion } from "./useStageExpansion";

/**
 * Story 2.1 AC 1/2/3/6 — the pane renders a server payload and moves a
 * selection.
 *
 * Two lines these tests police. First, nothing here is derived: the stub
 * decides the groups, the order and the flags, and the pane is only allowed
 * to draw them. Second, changing the filter must *ask the server again* —
 * asserted by watching the request URLs, because a component that narrowed
 * a cached list in the browser would look identical on screen.
 */

/**
 * Reports the URL so a test can assert the selection moved into it — and
 * the location key, which changes on every history entry and so is how a
 * test sees a navigation that changed nothing.
 */
function LocationProbe() {
  const location = useLocation();
  return (
    <>
      <span data-testid="location">{location.search}</span>
      <span data-testid="location-key">{location.key}</span>
    </>
  );
}

/**
 * The pane plus the state `WorkspaceShell` owns on its behalf — the filter
 * and the stage expansion, expired together in one handler. Mirroring the
 * shell rather than inventing a second arrangement is deliberate: these
 * tests are about the pane under the wiring it actually ships with, and the
 * "changing the filter forgets an expansion" cases below are only meaningful
 * if the reset lives where the real one does.
 */
function Harness({ initialFilter = "all" as QueueFilter }) {
  const [filter, setFilter] = useState<QueueFilter>(initialFilter);
  const expansion = useStageExpansion();
  return (
    <>
      <QueuePane
        filter={filter}
        onFilterChange={(next) => {
          setFilter(next);
          expansion.reset();
        }}
        expandedStages={expansion.expanded}
        onExpandStage={expansion.expand}
        onCollapseStage={expansion.collapse}
      />
      <LocationProbe />
    </>
  );
}

function renderPane(routes: StubRoutes, path = "/workspace") {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The pane under a fixed filter — for the cases where the filter is the input. */
function renderPaneWithFilter(routes: StubRoutes, filter: QueueFilter) {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <Harness initialFilter={filter} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The `/claims/queue` URLs the stub was asked for, in order. */
function queueRequests(): string[] {
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls
    .map((call: unknown[]) => {
      const input = call[0] as RequestInfo | URL;
      return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    })
    .filter((url: string) => url.includes("/api/claims/queue"));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- structure ----------------------------------------------------------

test("all four stage groups render with their icon, label and count", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });

  await waitFor(() => expect(screen.getByTestId("queue-group-intake")).toBeInTheDocument());

  for (const [stage, label, count] of [
    ["intake", "Intake", "1"],
    ["investigation", "Investigation", "0"],
    ["treatment", "Treatment", "2"],
    ["settled", "Settled", "0"],
  ]) {
    const header = screen.getByTestId(`queue-group-${stage}-header`);
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(header).toHaveTextContent(label);
    // The count on its own testid: the header text also contains the
    // label, and a substring check on the section would match anything.
    expect(screen.getByTestId(`queue-group-${stage}-count`)).toHaveTextContent(count);
  }
});

test("a stage the persona has nothing in says so, in its own words", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });

  await waitFor(() =>
    expect(screen.getByTestId("queue-group-investigation-empty")).toHaveTextContent(
      "No claims in this stage.",
    ),
  );
  // …and the populated ones do not.
  expect(screen.queryByTestId("queue-group-treatment-empty")).not.toBeInTheDocument();
});

test("the count chip reports the whole group, not the page in hand", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE_PAGED });

  await waitFor(() =>
    expect(screen.getByTestId("queue-group-treatment-count")).toHaveTextContent("2"),
  );
  expect(screen.getAllByTestId("queue-card")).toHaveLength(1);
});

test("collapsing a group hides its cards without changing its count", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(3));

  const header = screen.getByTestId("queue-group-treatment-header");
  await userEvent.click(header);

  expect(header).toHaveAttribute("aria-expanded", "false");
  expect(screen.getAllByTestId("queue-card")).toHaveLength(1); // intake only
  expect(screen.getByTestId("queue-group-treatment-count")).toHaveTextContent("2");
});

// --- the three states ---------------------------------------------------

test("the pane draws skeletons while the queue is in flight", () => {
  renderPane({ claimsQueue: "pending" });

  expect(screen.getAllByTestId("queue-skeleton").length).toBeGreaterThan(0);
  expect(screen.getByTestId("queue-pane")).toHaveAttribute("aria-busy", "true");
  expect(screen.queryByTestId("queue-card")).not.toBeInTheDocument();
});

test("a failed request is an alert, never an empty caseload", async () => {
  renderPane({ claimsQueue: { status: 500, body: { detail: "boom" } } });

  const alert = await screen.findByRole("alert", {}, { timeout: 8000 });
  expect(alert).toHaveTextContent(/could not be loaded/i);
  // The distinction that matters: "we could not fetch your claims" must
  // never render as "you have no claims".
  expect(screen.queryByTestId("queue-empty-scope")).not.toBeInTheDocument();
}, 15000);

test("an empty book is reported as an empty book, not as a filter miss", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE_EMPTY });

  await waitFor(() =>
    expect(screen.getByTestId("queue-empty-scope")).toHaveTextContent(
      "No claims in your caseload.",
    ),
  );
  expect(screen.queryByTestId("queue-empty-filter")).not.toBeInTheDocument();
});

test("a filter that matches nothing is reported as a filter miss", async () => {
  // The groups are byte-identical to the payload above. What differs is
  // `unfilteredTotal` — the server's count of the book behind the filter,
  // and the only thing that can decide which sentence a handler reads.
  renderPaneWithFilter({ claimsQueue: CLAIM_QUEUE_NO_MATCH }, "litigation");

  await waitFor(() =>
    expect(screen.getByTestId("queue-empty-filter")).toHaveTextContent(
      "No claims match this filter.",
    ),
  );
  expect(screen.queryByTestId("queue-empty-scope")).not.toBeInTheDocument();
});

test("an empty pane collapses to one sentence instead of four empty sections", async () => {
  // Recorded as a decision, not an oversight. AC 1 asks for four sections
  // with counts and this replaces all four with one line — because NFR-3
  // wants three *distinguishable* messages, and four stacked "No claims in
  // this stage." repetitions under four zero chips say the same thing four
  // times without telling a handler which of the three situations they are
  // in. The AC's wording holds for the populated case (see the story's Dev
  // Notes); this is what the empty case does instead.
  renderPane({ claimsQueue: CLAIM_QUEUE_EMPTY });

  await waitFor(() => expect(screen.getByTestId("queue-empty-scope")).toBeInTheDocument());
  for (const stage of ["intake", "investigation", "treatment", "settled"]) {
    expect(screen.queryByTestId(`queue-group-${stage}`)).not.toBeInTheDocument();
  }
  expect(screen.queryAllByText("No claims in this stage.")).toHaveLength(0);
});

test("a filter miss collapses the same way, with the filter's sentence", async () => {
  renderPaneWithFilter({ claimsQueue: CLAIM_QUEUE_NO_MATCH }, "litigation");

  await waitFor(() => expect(screen.getByTestId("queue-empty-filter")).toBeInTheDocument());
  expect(screen.queryByTestId("queue-group-treatment")).not.toBeInTheDocument();
});

test("an empty book with a filter applied is still reported as an empty book", async () => {
  // The regression this pair exists for: deciding scope-empty from
  // `filter === "all"` told a handler with no assignment that their filter
  // was wrong — the precise mis-diagnosis the pane's own note disclaims.
  renderPaneWithFilter({ claimsQueue: CLAIM_QUEUE_EMPTY }, "litigation");

  await waitFor(() =>
    expect(screen.getByTestId("queue-empty-scope")).toHaveTextContent("No claims in your caseload."),
  );
  expect(screen.queryByTestId("queue-empty-filter")).not.toBeInTheDocument();
});

// --- selection (AC 2, 6) ------------------------------------------------

test("the first claim of the first non-empty group is selected on arrival", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });

  // Intake comes first in the lifecycle order, so its single claim wins —
  // not the treatment group's higher-scoring one.
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("claim=WC-20003"));
  const cards = screen.getAllByTestId("queue-card");
  expect(cards[0]).toHaveAttribute("aria-current", "true");
  expect(cards[1]).not.toHaveAttribute("aria-current");
});

test("a selection already in the URL is left alone", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE }, "/workspace?claim=WC-20044");

  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(3));
  expect(screen.getByTestId("location")).toHaveTextContent("claim=WC-20044");
  expect(
    screen.getAllByTestId("queue-card").find((card) => card.dataset.claimId === "WC-20044"),
  ).toHaveAttribute("aria-current", "true");
});

test("an empty caseload selects nothing rather than navigating in a loop", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE_EMPTY });

  await waitFor(() => expect(screen.getByTestId("queue-empty-scope")).toBeInTheDocument());
  expect(screen.getByTestId("location")).toHaveTextContent("");
});

test("clicking a card moves both the highlight and the URL", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("claim=WC-20003"));

  const target = screen
    .getAllByTestId("queue-card")
    .find((card) => card.dataset.claimId === "WC-20017")!;
  await userEvent.click(target);

  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("claim=WC-20017"));
  expect(target).toHaveAttribute("aria-current", "true");
  expect(
    screen.getAllByTestId("queue-card").find((card) => card.dataset.claimId === "WC-20003"),
  ).not.toHaveAttribute("aria-current");
});

test("clicking the card that is already selected is not a navigation", async () => {
  // Pushing an identical entry stacks history: Back then appears to do
  // nothing, once per stray click the handler made on the open claim.
  renderPane({ claimsQueue: CLAIM_QUEUE }, "/workspace?claim=WC-20017");
  const selected = await waitFor(() =>
    screen.getAllByTestId("queue-card").find((card) => card.dataset.claimId === "WC-20017")!,
  );
  const before = screen.getByTestId("location-key").textContent;

  await userEvent.click(selected);
  await userEvent.click(selected);

  expect(screen.getByTestId("location-key")).toHaveTextContent(before!);
  expect(screen.getByTestId("location")).toHaveTextContent("claim=WC-20017");
});

// --- filtering (AC 3) ---------------------------------------------------

test("choosing a filter asks the server again instead of narrowing what is cached", async () => {
  renderPane({
    // Branching on the URL is the whole point: a stub that answered the
    // same bytes whatever was asked could not tell a refetch from a
    // client-side filter.
    claimsQueue: (url) => (url.includes("filter=litigation") ? CLAIM_QUEUE_NO_MATCH : CLAIM_QUEUE),
  });
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(3));
  expect(queueRequests()).toHaveLength(1);

  await userEvent.click(screen.getByTestId("queue-filter"));
  await userEvent.click(await screen.findByTestId("queue-filter-option-litigation"));

  await waitFor(() => expect(screen.getByTestId("queue-empty-filter")).toBeInTheDocument());
  const requests = queueRequests();
  expect(requests).toHaveLength(2);
  expect(requests[0]).toContain("filter=all");
  expect(requests[1]).toContain("filter=litigation");
});

test("the announcement tells a screen reader how many claims are in view", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });

  await waitFor(() =>
    expect(screen.getByTestId("queue-announcement")).toHaveTextContent("3 claims in your caseload."),
  );
});

test("one claim is announced as one claim", async () => {
  // Never seen by a sighted user, heard every time by everyone else.
  const empty = { items: [], nextCursor: null, total: 0 };
  renderPane({
    claimsQueue: {
      status: 200,
      body: {
        groups: {
          intake: { items: [INTAKE_CARD], nextCursor: null, total: 1 },
          investigation: empty,
          treatment: empty,
          settled: empty,
        },
        rulesVersion: 1,
        thresholdsVersion: 1,
        unfilteredTotal: 1,
        filteredTotal: 1,
      },
    },
  });

  await waitFor(() =>
    expect(screen.getByTestId("queue-announcement")).toHaveTextContent("1 claim in your caseload."),
  );
});

test("under a filter the announcement counts what the filter left, not the caseload", async () => {
  renderPaneWithFilter({ claimsQueue: CLAIM_QUEUE }, "litigation");

  await waitFor(() =>
    expect(screen.getByTestId("queue-announcement")).toHaveTextContent("3 claims match this filter."),
  );
});

// --- paging -------------------------------------------------------------

test("Show more appends the next page the server hands back", async () => {
  renderPane({
    claimsQueue: (url) => (url.includes("cursor=") ? CLAIM_QUEUE_PAGE_TWO : CLAIM_QUEUE_PAGED),
  });
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(1));

  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));

  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(2));
  // The cursor came off the first response; nothing in the browser
  // computed an offset.
  expect(queueRequests()[1]).toContain("cursor=cursor-page-2");
  expect(screen.queryByTestId("queue-group-treatment-more")).not.toBeInTheDocument();
});

test("a group the server says is complete offers no Show more", async () => {
  renderPane({ claimsQueue: CLAIM_QUEUE });

  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(3));
  expect(screen.queryByTestId("queue-group-treatment-more")).not.toBeInTheDocument();
});

test("the button stays put while the page it asked for is in flight", async () => {
  // It used to unmount on the click that triggered the fetch —
  // `hasNextPage` reads false until the infinite query answers — which made
  // its own `disabled` and "Loading…" states unreachable, and made the
  // control vanish under the pointer for the length of a request.
  renderPane({
    claimsQueue: (url) => (url.includes("cursor=") ? "pending" : CLAIM_QUEUE_PAGED),
  });
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());

  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));

  const button = await screen.findByTestId("queue-group-treatment-more");
  expect(button).toBeDisabled();
  expect(button).toHaveTextContent("Loading…");
});

test("Show more carries no count the browser worked out for itself", async () => {
  // It used to read "Show more (N)" with N computed as `group.total -
  // items.length` — a subtraction over two server numbers, done in the
  // browser, which is the derivation `noDerivation.test.ts` now fails a
  // build over. It also went wrong on its own terms: after a refetch
  // overlap the accumulated pages could outnumber the total the base query
  // last reported, and the button offered "Show more (-1)".
  //
  // The payload below is that exact overlap — the base query says two, the
  // pages in hand say three — and the label is a plain invitation either
  // way. The server does not know how many pages this client is holding, so
  // no honest number is available to send instead.
  renderPane({
    claimsQueue: (url) =>
      url.includes("cursor=")
        ? {
            status: 200,
            body: {
              ...CLAIM_QUEUE_PAGE_TWO.body,
              groups: {
                ...CLAIM_QUEUE_PAGE_TWO.body.groups,
                treatment: { items: [QUIET_CARD, INTAKE_CARD], nextCursor: "c3", total: 2 },
              },
            },
          }
        : CLAIM_QUEUE_PAGED,
  });
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());

  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));

  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(3));
  expect(screen.getByTestId("queue-group-treatment-more")).toHaveTextContent("Show more");
  expect(screen.getByTestId("queue-group-treatment-more").textContent).not.toMatch(/\d/);
});

test("a refused cursor leaves a way back rather than a group that cannot reload", async () => {
  // The server refuses a cursor whose rules version has been superseded, and
  // every retry replays the same rejected cursor — so a plain "try again"
  // could never succeed. "Reload this stage" drops the accumulated pages,
  // collapses the group and re-asks the base query, which is the only thing
  // that can produce a cursor the server will accept.
  let refuse = true;
  renderPane({
    claimsQueue: (url) =>
      url.includes("cursor=")
        ? refuse
          ? { status: 400, body: { detail: "That page was ranked by priority_weights v1" } }
          : CLAIM_QUEUE_PAGE_TWO
        : CLAIM_QUEUE_PAGED,
  });
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());

  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));
  const alert = await screen.findByTestId("queue-group-treatment-error", {}, { timeout: 8000 });
  expect(alert).toHaveTextContent(/could not be loaded/i);

  refuse = false;
  await userEvent.click(screen.getByTestId("queue-group-treatment-reload"));

  // Back to the base page, the error gone, and the base query asked again.
  await waitFor(() =>
    expect(screen.queryByTestId("queue-group-treatment-error")).not.toBeInTheDocument(),
  );
  expect(screen.getAllByTestId("queue-card")).toHaveLength(1);
  expect(queueRequests().filter((url) => !url.includes("cursor=")).length).toBeGreaterThan(1);
}, 15000);

test("a card served twice is rendered once", async () => {
  // The base page and an accumulated page can overlap after a refetch. Two
  // React children with one key is a warning at best and a dropped row at
  // worst, so the list is deduped by claim id.
  renderPane({
    claimsQueue: (url) =>
      url.includes("cursor=")
        ? {
            status: 200,
            body: {
              ...CLAIM_QUEUE_PAGE_TWO.body,
              groups: {
                ...CLAIM_QUEUE_PAGE_TWO.body.groups,
                treatment: { items: [LOUD_CARD, QUIET_CARD], nextCursor: null, total: 2 },
              },
            },
          }
        : CLAIM_QUEUE_PAGED,
  });
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(1));

  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));

  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(2));
  expect(
    screen.getAllByTestId("queue-card").filter((card) => card.dataset.claimId === LOUD_CARD.claimId),
  ).toHaveLength(1);
});

test("changing the filter does not carry an expansion into the new list", async () => {
  // "Show more" is a statement about one list of claims, and a filter
  // change makes it a different list. Remembered across the change, the
  // group fetched page 2 of the new filter with nobody having clicked
  // anything — the eagerness paging exists to avoid.
  //
  // The route back to `all` is the one that exposes it: its payload is
  // cached, so the pane does not fall back to skeletons and the group is
  // never unmounted. The expansion, if it survived, is applied to a list
  // the user has not asked to expand.
  renderPane({
    claimsQueue: (url) => (url.includes("cursor=") ? CLAIM_QUEUE_PAGE_TWO : CLAIM_QUEUE_PAGED),
  });
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());

  const pick = async (option: string) => {
    await userEvent.click(screen.getByTestId("queue-filter"));
    await userEvent.click(await screen.findByTestId(`queue-filter-option-${option}`));
  };

  await pick("litigation");
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());
  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(2));

  await pick("all");

  // One page of the restored filter, and no cursor request behind it.
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(1));
  expect(
    queueRequests().filter((url) => url.includes("filter=all") && url.includes("cursor=")),
  ).toHaveLength(0);
});

test("an expansion does not survive a round trip back to the filter it was made under", async () => {
  // The ordering the previous test cannot reach, and the one the old
  // implementation got wrong. Expansion was held as *which filter it was
  // asked for* (`expandedFilter === filter`), which does not expire — it
  // sleeps. Expand under `all`, switch away, switch back, and the group is
  // expanded again with nobody having clicked anything: the infinite query
  // re-enables against a cached page 2, and past its `staleTime` it
  // refetches. The previous test only ever expands under the filter it
  // *leaves*, where the memory is inert.
  renderPane({
    claimsQueue: (url) =>
      url.includes("cursor=") ? CLAIM_QUEUE_PAGE_TWO : CLAIM_QUEUE_PAGED,
  });
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());

  const pick = async (option: string) => {
    await userEvent.click(screen.getByTestId("queue-filter"));
    await userEvent.click(await screen.findByTestId(`queue-filter-option-${option}`));
  };

  // A → expand.
  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(2));

  // A → B → A.
  await pick("litigation");
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(1));
  await pick("all");

  // One page again: the expansion belonged to the list, and this is a new
  // reading of it. `all`'s payload is cached, so the pane never falls back
  // to skeletons and the group is never unmounted — if the state survived
  // anywhere, the second page would be on screen here.
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());
  expect(screen.getAllByTestId("queue-card")).toHaveLength(1);
});
