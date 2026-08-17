/**
 * Story 4.1 AC 1 — the diary's three sub-tabs, two of which name their story.
 *
 * The whole value of this file is the pair of placeholders. Notes and Emails
 * are not built, and NFR-3 asks that a not-yet-built surface say so rather
 * than render blank — so what is asserted is that each one is reachable and
 * that it carries the story that fills it. A test that only checked the
 * Meetings tab would pass against a pane with one tab in it.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { ME_HANDLER, MEETINGS, stubApi } from "@/test/api-mock";

import { DiaryNavProvider } from "./DiaryNav";
import { DiaryTab } from "./DiaryTab";

function renderTab() {
  stubApi({ me: ME_HANDLER, meetings: MEETINGS });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <DiaryTab claimId="WC-20017" workerName="Marcus Delgado" />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("Meetings is the sub-tab this story builds, and it opens selected", async () => {
  renderTab();

  expect(await screen.findByTestId("diary-subtab-meetings")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByTestId("meetings-subtab")).toBeInTheDocument();
});

test.each([
  ["notes", "Story 4.2"],
  ["emails", "Story 4.3"],
])("the %s sub-tab names the story that delivers it", async (tab, story) => {
  renderTab();
  await userEvent.click(screen.getByTestId(`diary-subtab-${tab}`));

  const placeholder = screen.getByTestId(`diary-empty-${tab}`);
  expect(placeholder).toHaveAttribute("data-story", story);
  expect(placeholder).toHaveTextContent(story);
  // …and the built sub-tab is unmounted, not merely hidden: the meetings list
  // holds a query and a modal, and keeping it alive behind an unselected tab
  // is the thing `DetailTabs` avoids for the same reason.
  expect(screen.queryByTestId("meetings-subtab")).not.toBeInTheDocument();
});
