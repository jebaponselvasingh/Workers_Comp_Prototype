import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_QUEUE,
  CLAIM_QUEUE_PAGE_TWO,
  CLAIM_QUEUE_PAGED,
  ME_HANDLER,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { WorkspaceShell } from "./WorkspaceShell";

/**
 * The centre pane's three answers about the selected claim.
 *
 * It holds one page of one filter, so "the claim is not in what I have" is
 * not the same fact as "the claim is not in this caseload". Saying the
 * second when only the first is true sends a handler looking for a claim
 * that is one filter change away — and it happens on the ordinary path,
 * because auto-select picks a claim under `all` and the very next thing a
 * handler does is narrow the list.
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

test("a claim in the queue is named in the centre pane", async () => {
  renderShell({ claimsQueue: CLAIM_QUEUE }, "/workspace?claim=WC-20017");

  expect(await screen.findByTestId("detail-selected")).toHaveTextContent("WC-20017");
});

test("a claim in no complete, unfiltered group is reported as absent", async () => {
  // Every group finished (`nextCursor: null`) and no filter applied — the
  // only state in which the shell can actually rule a claim out.
  renderShell({ claimsQueue: CLAIM_QUEUE }, "/workspace?claim=WC-9999");

  expect(await screen.findByTestId("detail-unknown")).toHaveTextContent("is not in this caseload");
});

test("a claim the filter narrowed away is not called absent", async () => {
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
  });
  expect(await screen.findByTestId("detail-selected")).toHaveTextContent("WC-20003");

  await userEvent.click(screen.getByTestId("queue-filter"));
  await userEvent.click(await screen.findByTestId("queue-filter-option-litigation"));

  await waitFor(() => expect(screen.getByTestId("detail-unlisted")).toBeInTheDocument());
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});

test("a claim behind a Show more is not called absent either", async () => {
  // The treatment group reports a `nextCursor`, so the shell is holding a
  // truncated list and cannot say what is in the rest of it.
  renderShell({ claimsQueue: CLAIM_QUEUE_PAGED }, "/workspace?claim=WC-20044");

  expect(await screen.findByTestId("detail-unlisted")).toHaveTextContent("WC-20044");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});

test("a claim revealed by Show more stops being unlisted", async () => {
  // The other half of the test above, and the state it never reached. The
  // shell read the base query's pages only, so a claim the queue had just
  // rendered — highlighted, clickable, three inches to the left — was still
  // reported here as "not in the part of your caseload shown". One claim,
  // two answers, on screen at once (AD-9).
  renderShell(
    {
      claimsQueue: (url) => (url.includes("cursor=") ? CLAIM_QUEUE_PAGE_TWO : CLAIM_QUEUE_PAGED),
    },
    "/workspace?claim=WC-20044",
  );
  expect(await screen.findByTestId("detail-unlisted")).toHaveTextContent("WC-20044");

  await userEvent.click(screen.getByTestId("queue-group-treatment-more"));

  // The card is now in the queue…
  await waitFor(() =>
    expect(
      screen.getAllByTestId("queue-card").find((card) => card.dataset.claimId === "WC-20044"),
    ).toHaveAttribute("aria-current", "true"),
  );
  // …and the pane beside it agrees.
  expect(screen.getByTestId("detail-selected")).toHaveTextContent("WC-20044");
  expect(screen.queryByTestId("detail-unlisted")).not.toBeInTheDocument();
});

test("a filter change forgets an expansion in both panes at once", async () => {
  // The consistency the shared expansion state buys. After a filter change
  // the accumulated pages are still in the cache; the queue stops rendering
  // them, and the detail pane has to stop counting them, or the two are out
  // of step again in the opposite direction.
  renderShell({
    claimsQueue: (url) => (url.includes("cursor=") ? CLAIM_QUEUE_PAGE_TWO : CLAIM_QUEUE_PAGED),
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

test("a queue that failed to load is an unchecked claim, not a missing one", async () => {
  renderShell(
    { claimsQueue: { status: 500, body: { detail: "boom" } } },
    "/workspace?claim=WC-20017",
  );

  const unchecked = await screen.findByTestId("detail-unchecked", {}, { timeout: 8000 });
  expect(unchecked).toHaveTextContent("could not be checked");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
}, 15000);

test("a blank claim parameter is no selection at all", async () => {
  // `?claim=` reads as "" rather than null. Left as a value it skipped
  // auto-select *and* had the pane report the empty string as missing.
  renderShell({ claimsQueue: CLAIM_QUEUE }, "/workspace?claim=");

  // Auto-select runs, so the first claim of the first non-empty group wins.
  expect(await screen.findByTestId("detail-selected")).toHaveTextContent("WC-20003");
  expect(screen.queryByTestId("detail-unknown")).not.toBeInTheDocument();
});
