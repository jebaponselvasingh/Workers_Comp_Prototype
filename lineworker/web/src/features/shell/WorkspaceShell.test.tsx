import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_INTAKE,
  CLAIM_DETAIL_NOT_FOUND,
  CLAIM_DETAIL_TREATMENT,
  CLAIM_QUEUE,
  CLAIM_QUEUE_PAGE_TWO,
  CLAIM_QUEUE_PAGED,
  ME_HANDLER,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { WorkspaceShell } from "./WorkspaceShell";

/**
 * The three panes, and what the shell still owns now that the centre one is
 * real.
 *
 * **These tests were Story 2.1's, re-pointed rather than deleted.** 2.1's
 * centre pane had to answer "is this claim in my caseload?" from the queue
 * payload it happened to hold, which needed three states — present, absent,
 * unconfirmed — because a filtered or partially-paged queue cannot rule a
 * claim out. Story 2.2's endpoint answers authoritatively, so the guess and
 * its `detail-unlisted` / `detail-unchecked` states are gone.
 *
 * What 2.1 was really guaranteeing survives, and each test below now asserts
 * the *stronger* version of it: a filter that narrows the queue does not
 * disturb the open case file, an unfetched page does not either, and a queue
 * that fails outright no longer stops the case file loading at all.
 */

function renderShell(routes: StubRoutes, path = "/workspace") {
  stubApi({ me: ME_HANDLER, ...routes });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <WorkspaceShell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the selected claim's case file fills the centre pane", async () => {
  renderShell(
    { claimsQueue: CLAIM_QUEUE, claimDetail: CLAIM_DETAIL_TREATMENT },
    "/workspace?claim=WC-20017",
  );

  expect(await screen.findByTestId("case-header-claim-id")).toHaveTextContent("WC-20017");
  expect(screen.getByTestId("case-header-name")).toHaveTextContent("Marcus Delgado");
});

test("a claim the caller cannot see is reported as absent, once, by the server", async () => {
  // The 404 is the whole answer. The shell no longer inspects the queue to
  // decide this, which is why the queue here is the ordinary one.
  renderShell(
    { claimsQueue: CLAIM_QUEUE, claimDetail: CLAIM_DETAIL_NOT_FOUND },
    "/workspace?claim=WC-9999",
  );

  expect(await screen.findByTestId("detail-unknown")).toHaveTextContent("is not in this caseload");
});

test("a filter that narrows the queue leaves the open case file alone", async () => {
  // 2.1's "a claim the filter narrowed away is not called absent", in its
  // stronger form: the case file does not merely avoid being *called*
  // missing, it stays on screen. The detail query is not keyed by filter.
  renderShell({
    claimsQueue: (url) =>
      url.includes("filter=litigation")
        ? {
            status: 200,
            body: {
              ...CLAIM_QUEUE.body,
              groups: {
                ...CLAIM_QUEUE.body.groups,
                intake: { items: [], nextCursor: null, total: 0 },
                treatment: { items: [], nextCursor: null, total: 0 },
              },
            },
          }
        : CLAIM_QUEUE,
    claimDetail: CLAIM_DETAIL_INTAKE,
  });
  expect(await screen.findByTestId("case-header-claim-id")).toHaveTextContent("WC-20003");

  await userEvent.click(screen.getByTestId("queue-filter"));
  await userEvent.click(await screen.findByTestId("queue-filter-option-litigation"));

  await waitFor(() => expect(screen.queryByTestId("queue-card")).not.toBeInTheDocument());
  expect(screen.getByTestId("case-header-claim-id")).toHaveTextContent("WC-20003");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});

test("a claim behind a Show more opens without expanding anything", async () => {
  // 2.1 had to report this claim as "not in the part of your caseload shown
  // here" until the handler clicked Show more. The case file does not depend
  // on the queue having fetched the page the card is on.
  renderShell(
    { claimsQueue: CLAIM_QUEUE_PAGED, claimDetail: CLAIM_DETAIL_TREATMENT },
    "/workspace?claim=WC-20017",
  );

  expect(await screen.findByTestId("case-header-claim-id")).toHaveTextContent("WC-20017");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});

test("a filter change forgets an expansion in the queue", async () => {
  // The consistency the shared expansion state buys. After a filter change
  // the accumulated pages are still in the cache and the queue has to stop
  // rendering them.
  renderShell({
    claimsQueue: (url) => (url.includes("cursor=") ? CLAIM_QUEUE_PAGE_TWO : CLAIM_QUEUE_PAGED),
    claimDetail: CLAIM_DETAIL_TREATMENT,
  });
  await waitFor(() => expect(screen.getByTestId("queue-group-treatment-more")).toBeInTheDocument());
  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));
  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(2));

  await userEvent.click(screen.getByTestId("queue-filter"));
  await userEvent.click(await screen.findByTestId("queue-filter-option-litigation"));
  await userEvent.click(screen.getByTestId("queue-filter"));
  await userEvent.click(await screen.findByTestId("queue-filter-option-all"));

  await waitFor(() => expect(screen.getAllByTestId("queue-card")).toHaveLength(1));
});

test("a queue that failed to load no longer affects the case file", async () => {
  // 2.1's "an unchecked claim, not a missing one" — the concern is now
  // structural rather than a message: the two panes read two endpoints, so a
  // failed queue cannot make the case file say anything at all.
  renderShell(
    {
      claimsQueue: { status: 404, body: { detail: "gone" } },
      claimDetail: CLAIM_DETAIL_TREATMENT,
    },
    "/workspace?claim=WC-20017",
  );

  expect(await screen.findByTestId("queue-error")).toBeInTheDocument();
  expect(screen.getByTestId("case-header-claim-id")).toHaveTextContent("WC-20017");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});

test("a blank claim parameter is no selection at all", async () => {
  // `?claim=` reads as "" rather than null. Left as a value it skipped
  // auto-select *and* had the pane report the empty string as missing.
  renderShell(
    { claimsQueue: CLAIM_QUEUE, claimDetail: CLAIM_DETAIL_INTAKE },
    "/workspace?claim=",
  );

  // Auto-select runs, so the first claim of the first non-empty group wins.
  expect(await screen.findByTestId("case-header-claim-id")).toHaveTextContent("WC-20003");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});

test("nothing selected is its own state, not an empty case file", async () => {
  renderShell({
    claimsQueue: {
      status: 200,
      body: {
        ...CLAIM_QUEUE.body,
        groups: {
          intake: { items: [], nextCursor: null, total: 0 },
          investigation: { items: [], nextCursor: null, total: 0 },
          treatment: { items: [], nextCursor: null, total: 0 },
          settled: { items: [], nextCursor: null, total: 0 },
        },
        unfilteredTotal: 0,
        filteredTotal: 0,
      },
    },
  });

  expect(await screen.findByTestId("detail-none")).toHaveTextContent(
    "Select a claim to open its case file",
  );
});

test("the three panes are all present", async () => {
  renderShell(
    { claimsQueue: CLAIM_QUEUE, claimDetail: CLAIM_DETAIL_TREATMENT },
    "/workspace?claim=WC-20017",
  );

  expect(await screen.findByTestId("queue-pane")).toBeInTheDocument();
  expect(screen.getByTestId("detail-pane")).toBeInTheDocument();
  expect(screen.getByTestId("copilot-pane")).toBeInTheDocument();
});
