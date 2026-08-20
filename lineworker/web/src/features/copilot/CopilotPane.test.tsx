/**
 * The copilot shell — a live pane with **two** working tabs (Story 4.1's AC 1,
 * amended by Story 6.3).
 *
 * Two of the assertions here used to be about a *disabled* control: the ⚡ Actions
 * tab was Epic 6's most visible seam, and "renders disabled with a reason a
 * keyboard can reach" was a property nothing else in the suite checked. Epic 6
 * has arrived, so **those two are amended into their opposites** rather than
 * deleted (AD-15): the tab is enabled, it selects, it mounts a real surface, and
 * the sentence that named the epic is gone from the panel entirely — including
 * from the `title` and the `aria-describedby` target that carried it without a
 * pointer.
 *
 * The third and fourth are unchanged: the claim-context sub-line proves the
 * shell reads the selection from the URL rather than from a prop nobody passes
 * it, and the keyboard test proves the strip honours the WAI-ARIA tabs pattern —
 * which now has somewhere to move to.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { DiaryNavProvider, useDiaryNav } from "@/features/diary/DiaryNav";
import {
  CLAIM_DETAIL_TREATMENT,
  COPILOT_RUN_RTW_DRAFT,
  COPILOT_THREADS,
  COPILOT_TRANSCRIPT,
  copilotRunBodies,
  DIARY_NOTES,
  ME_HANDLER,
  MEETINGS,
  stubApi,
  type StubRoutes,
} from "@/test/api-mock";

import { CopilotPane } from "./CopilotPane";

function renderPane(
  path = "/workspace?claim=WC-20017",
  routes: Partial<StubRoutes> = {},
) {
  stubApi({
    me: ME_HANDLER,
    claimDetail: CLAIM_DETAIL_TREATMENT,
    meetings: MEETINGS,
    // Story 4.2 put the Notes sub-tab behind the Diary tab and made it the
    // one that opens, so the pane issues this request as soon as 📓 Diary is
    // selected.
    diaryNotes: DIARY_NOTES,
    copilotThreads: COPILOT_THREADS,
    copilotTranscript: COPILOT_TRANSCRIPT,
    ...routes,
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <DiaryNavProvider>
          <RtwDeepLink />
          <CopilotPane />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * The centre pane's "Draft RTW Letter →" row, reduced to the one call it makes.
 *
 * `ActionsCard` lives three components away in the *centre* pane and reaches
 * this one through `DiaryNav` — so what the deep link actually is, from the
 * copilot's side, is `requestRtwLetter()` being called from somewhere outside
 * it. Standing in the real row here would drag a case file, a checklist and a
 * detail pane into a test about which tab is showing; calling the context
 * method is the same event with none of that.
 */
function RtwDeepLink() {
  const { requestRtwLetter } = useDiaryNav();
  return (
    <button type="button" data-testid="rtw-deep-link" onClick={requestRtwLetter}>
      Draft RTW Letter
    </button>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the Actions tab is enabled and mounts the copilot when selected", async () => {
  // **The amended assertion.** This test read "the Actions tab renders disabled
  // and names the epic that will enable it" until Story 6.3, and its two
  // assertions were `toBeDisabled()` and a `title` matching the seam sentence.
  // Both are now their opposite (AD-15).
  //
  // It is *enabled* rather than *selected*: 📓 Diary is still the tab the panel
  // opens on, which `CopilotPane` argues — Story 4.1 decided that, this story
  // does not falsify it, and changing it would have invalidated two Epic 4
  // specs that have nothing to do with the copilot.
  renderPane();

  const actions = await screen.findByTestId("copilot-tab-actions");
  expect(actions).toBeEnabled();
  expect(actions).toHaveAttribute("aria-selected", "false");

  await userEvent.click(actions);

  expect(actions).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByTestId("copilot-actions")).toBeInTheDocument();
});

test("no control in the panel still names Epic 6", async () => {
  // **The other amended assertion**, and it is deliberately about an absence
  // over the whole pane rather than about one attribute. The seam was carried
  // three times over — a Radix tooltip, the button's `title`, and an `sr-only`
  // span the button pointed `aria-describedby` at — and a seam removed in two
  // places out of three leaves a handler being told that a working control does
  // not work, by whichever carrier survived.
  renderPane();

  const panel = await screen.findByTestId("copilot");
  expect(panel).not.toHaveTextContent(/Epic 6/);
  const actions = screen.getByTestId("copilot-tab-actions");
  expect(actions).not.toHaveAttribute("title");
  expect(actions).not.toHaveAttribute("aria-describedby");
});

test("the Diary tab is still the one the panel opens on, and still mounts the diary", async () => {
  // Epic 6 filled the other tab **without touching the diary**, which is the
  // whole reason the shell lives in `features/copilot/` and the diary is a
  // tenant of it. This is that claim, asserted — and it is the same assertion
  // Story 4.1 made, unchanged, which is the point.
  renderPane();

  expect(await screen.findByTestId("copilot-tab-diary")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByTestId("copilot-tab-actions")).toHaveAttribute("aria-selected", "false");
  expect(await screen.findByTestId("diary-tab")).toBeInTheDocument();
});

test("the sub-line names the claim the URL selected, with its worker", async () => {
  // Waited for by text rather than asserted straight off the testid, because
  // the element is mounted from the first frame with only the claim id in it —
  // a `findByTestId` resolves against the loading state and passes vacuously.
  renderPane();

  const subLine = await screen.findByTestId("copilot-context");
  expect(await within(subLine).findByText("WC-20017 — Marcus Delgado")).toBeInTheDocument();
});

test("with no claim selected the sub-line says so rather than rendering blank", async () => {
  renderPane("/workspace");

  // NFR-3: an empty sub-line and "we have not loaded it yet" would look the
  // same, and the prototype's own wording is a sentence.
  expect(await screen.findByTestId("copilot-context")).toHaveTextContent("Select a case");
});

test("the arrows move between the two tabs, and only the selected one is a stop", async () => {
  // The strip ships `role="tablist"`, `role="tab"` and `aria-selected`, so it
  // owes the keyboard pattern that goes with them. While ⚡ Actions was disabled
  // this was a well-formed no-op over one tab; now there are two and the
  // pattern that was already correct starts doing something. Roving `tabIndex`
  // is the half that is easy to get wrong: two tab stops in one strip is the
  // defect the pattern exists to prevent.
  renderPane();

  const diary = await screen.findByTestId("copilot-tab-diary");
  const actions = screen.getByTestId("copilot-tab-actions");
  expect(diary).toHaveAttribute("tabindex", "0");
  expect(actions).toHaveAttribute("tabindex", "-1");

  diary.focus();
  await userEvent.keyboard("{ArrowLeft}");

  expect(actions).toHaveAttribute("aria-selected", "true");
  expect(actions).toHaveAttribute("tabindex", "0");
  expect(diary).toHaveAttribute("tabindex", "-1");

  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByTestId("copilot-tab-diary")).toHaveAttribute("aria-selected", "true");
});

test("the RTW deep link works from the tab the panel opens on", async () => {
  // **The dead click** (review of Story 6.5). 📓 Diary is the tab the panel
  // opens on, so the ordinary journey is: the checklist raises the counter,
  // this shell switches to ⚡ Actions, and `ActionsTab` *mounts* — at which
  // point a `useRef(rtwRequestSession)` inside it initialises to the value it
  // was supposed to react to, its effect sees no change, and nothing happens.
  // The tab was selected and the letter never drafted. From ⚡ Actions, where
  // the component was already mounted, it worked — which is why it looked fine.
  //
  // Asserted on the *run* rather than on the tab, because selecting the tab was
  // never the broken half.
  renderPane("/workspace?claim=WC-20017", {
    copilotRun: { sse: COPILOT_RUN_RTW_DRAFT },
  });

  expect(await screen.findByTestId("copilot-tab-diary")).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await userEvent.click(screen.getByTestId("rtw-deep-link"));

  expect(screen.getByTestId("copilot-tab-actions")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await waitFor(() => expect(copilotRunBodies).toHaveLength(1));
  expect(copilotRunBodies[0]).toMatchObject({ quickAction: "rtw" });
  // …and the modal the link is really asking for opens on the draft.
  expect(await screen.findByTestId("rtw-body")).toHaveTextContent(
    "Modified duty is available.",
  );
});

test("returning to the Actions tab by hand does not replay the deep link", async () => {
  // The other half, and the reason the marker cannot simply be initialised to
  // zero: a remount cannot tell "I have not handled this" from "I handled it
  // before I was unmounted", so a ref inside `ActionsTab` would make every
  // later visit to ⚡ Actions re-run the draft — a network run, and a modal
  // opening over whatever the handler had come back to do.
  renderPane("/workspace?claim=WC-20017", {
    copilotRun: { sse: COPILOT_RUN_RTW_DRAFT },
  });

  await screen.findByTestId("copilot-tab-diary");
  await userEvent.click(screen.getByTestId("rtw-deep-link"));
  await waitFor(() => expect(copilotRunBodies).toHaveLength(1));

  // The modal the first run opened is a focus-trapped dialog, so the strip
  // behind it is genuinely unclickable until it is dismissed — which is the
  // handler's own route back to the case file, and Escape is one of the three
  // ways out `DocumentViewerDialog` argues for.
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByTestId("rtw-body")).toBeNull());

  await userEvent.click(screen.getByTestId("copilot-tab-diary"));
  await userEvent.click(screen.getByTestId("copilot-tab-actions"));
  await screen.findByTestId("copilot-actions");

  expect(copilotRunBodies).toHaveLength(1);
});
