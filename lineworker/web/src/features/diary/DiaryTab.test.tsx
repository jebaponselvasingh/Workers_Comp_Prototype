/**
 * Story 4.1 AC 1 / Story 4.2 AC 1 — the diary's three sub-tabs.
 *
 * 4.1 built Meetings and this file asserted the other two named their stories.
 * 4.2 built Notes, so what is left to assert is the *shape of the strip*: three
 * tabs, two of them live, one still naming Story 4.3 — and that only the
 * selected one is mounted, which is the property the pane depends on for the
 * add-note input's focus key (`DiaryNav`) and for not keeping two lists' worth
 * of queries alive behind a tab nobody is looking at.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { DIARY_NOTES, ME_HANDLER, MEETINGS, stubApi } from "@/test/api-mock";

import { DiaryNavProvider } from "./DiaryNav";
import { DiaryTab } from "./DiaryTab";

function renderTab() {
  stubApi({ me: ME_HANDLER, meetings: MEETINGS, diaryNotes: DIARY_NOTES });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <DiaryTab claimId="WC-20017" workerName="Marcus Delgado" injuryType="Laceration" />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("Notes opens selected — it is the first tab and the one carrying the greeting", async () => {
  renderTab();

  expect(await screen.findByTestId("diary-subtab-notes")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("notes-subtab")).toBeInTheDocument();
});

test("Meetings is reachable and mounts its own list", async () => {
  renderTab();
  await userEvent.click(await screen.findByTestId("diary-subtab-meetings"));

  expect(screen.getByTestId("meetings-subtab")).toBeInTheDocument();
  // …and Notes is unmounted, not merely hidden. Both sub-tabs hold queries and
  // Notes holds a draft; keeping either alive behind an unselected tab is the
  // thing `DetailTabs` avoids for the same reason.
  expect(screen.queryByTestId("notes-subtab")).not.toBeInTheDocument();
});

test("the emails sub-tab is the one seam left, and it names Story 4.3", async () => {
  renderTab();
  await userEvent.click(await screen.findByTestId("diary-subtab-emails"));

  const placeholder = screen.getByTestId("diary-empty-emails");
  expect(placeholder).toHaveAttribute("data-story", "Story 4.3");
  expect(placeholder).toHaveTextContent("Story 4.3");
  expect(screen.queryByTestId("notes-subtab")).not.toBeInTheDocument();
  expect(screen.queryByTestId("meetings-subtab")).not.toBeInTheDocument();
});

test("there is no notes placeholder left anywhere — Story 4.2 removed the seam", async () => {
  renderTab();
  await screen.findByTestId("notes-subtab");

  expect(screen.queryByTestId("diary-empty-notes")).not.toBeInTheDocument();
});
