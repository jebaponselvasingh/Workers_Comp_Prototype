/**
 * Story 4.1 AC 1 — the copilot shell: a live pane with one working tab and one
 * that says why it is not.
 *
 * Two of the three assertions here are about a *disabled* control, which is
 * the point: the ⚡ Actions tab is the epic's most visible seam, and "renders
 * disabled with a reason a keyboard can reach" is a property nothing else in
 * the suite checks. The third is the claim-context sub-line, which exists to
 * prove the shell reads the selection from the URL rather than from a prop
 * nobody passes it.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { DiaryNavProvider } from "@/features/diary/DiaryNav";
import { CLAIM_DETAIL_TREATMENT, ME_HANDLER, MEETINGS, stubApi } from "@/test/api-mock";

import { ACTIONS_SEAM_REASON, CopilotPane } from "./CopilotPane";

function renderPane(path = "/workspace?claim=WC-20017") {
  stubApi({ me: ME_HANDLER, claimDetail: CLAIM_DETAIL_TREATMENT, meetings: MEETINGS });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <DiaryNavProvider>
          <CopilotPane />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the Actions tab renders disabled and names the epic that will enable it", async () => {
  renderPane();

  const actions = await screen.findByTestId("copilot-tab-actions");
  expect(actions).toBeDisabled();
  // The reason is reachable without a pointer: a `title` and an
  // `aria-describedby` target, because a disabled button takes neither hover
  // nor focus and a tooltip alone is a dead control for everybody else
  // (NFR-3, Story 3.5's pattern).
  expect(actions).toHaveAttribute("title", ACTIONS_SEAM_REASON);
  const describedBy = actions.getAttribute("aria-describedby");
  expect(describedBy).not.toBeNull();
  expect(document.getElementById(describedBy!)).toHaveTextContent(ACTIONS_SEAM_REASON);
});

test("the Diary tab is the selected one and the diary is mounted behind it", async () => {
  renderPane();

  expect(await screen.findByTestId("copilot-tab-diary")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByTestId("diary-tab")).toBeInTheDocument();
});

test("the sub-line names the claim the URL selected, with its worker", async () => {
  renderPane();

  // Found by its text rather than by testid, because the element is mounted
  // from the first frame with only the claim id in it — a `findByTestId`
  // resolves against the loading state and passes vacuously.
  expect(await screen.findByText("WC-20017 — Marcus Delgado")).toHaveAttribute(
    "data-testid",
    "copilot-context",
  );
});

test("with no claim selected the sub-line says so rather than rendering blank", async () => {
  renderPane("/workspace");

  // NFR-3: an empty sub-line and "we have not loaded it yet" would look the
  // same, and the prototype's own wording is a sentence.
  expect(await screen.findByTestId("copilot-context")).toHaveTextContent("Select a case");
});
