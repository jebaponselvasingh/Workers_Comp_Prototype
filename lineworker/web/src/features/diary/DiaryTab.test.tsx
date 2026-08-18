/**
 * Story 4.1 AC 1 / 4.2 AC 1 / 4.3 AC 4-5 — the diary's three sub-tabs, and the
 * modal that hangs off all three.
 *
 * 4.1 built Meetings and this file asserted the other two named their stories;
 * 4.2 built Notes; 4.3 built Emails, so **there is no seam left in this pane**
 * and the assertion that used to read the placeholder now reads its absence.
 * What the strip still owes is that only the selected sub-tab is mounted — the
 * property the pane depends on for the add-note input's focus key (`DiaryNav`)
 * and for not keeping three lists' worth of queries alive behind tabs nobody is
 * looking at.
 *
 * The composer is mounted *here* rather than inside ✉ Emails, so this is the
 * file that owns the wiring 4.3 added: ✉ on a meeting card opens the modal
 * without moving the sub-tab, and a completed send closes it, toasts, and
 * switches the pane to Emails.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { ToastHost, ToastProvider } from "@/components/ui/toast";
import {
  DIARY_NOTES,
  EMAIL_CREATED,
  EMAIL_LOGS,
  ME_HANDLER,
  MEETINGS,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { DiaryNavProvider } from "./DiaryNav";
import { DiaryTab } from "./DiaryTab";

function renderTab(routes: StubRoutes = {}) {
  stubApi({
    me: ME_HANDLER,
    meetings: MEETINGS,
    diaryNotes: DIARY_NOTES,
    emails: EMAIL_LOGS,
    ...routes,
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ToastProvider>
          <DiaryNavProvider>
            <DiaryTab claimId="WC-20017" workerName="Marcus Delgado" injuryType="Laceration" />
          </DiaryNavProvider>
          <ToastHost />
        </ToastProvider>
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

test("Emails is reachable and mounts its own list — the last seam is gone", async () => {
  renderTab();
  await userEvent.click(await screen.findByTestId("diary-subtab-emails"));

  expect(await screen.findByTestId("emails-subtab")).toBeInTheDocument();
  // The placeholder this file used to assert is not merely empty, it is
  // deleted: the `SEAMS` record, its `Exclude<>` type and the else-branch that
  // rendered it all went with Story 4.3.
  expect(screen.queryByTestId("diary-empty-emails")).not.toBeInTheDocument();
  expect(screen.queryByTestId("notes-subtab")).not.toBeInTheDocument();
  expect(screen.queryByTestId("meetings-subtab")).not.toBeInTheDocument();
});

test("no sub-tab in this pane renders a placeholder any more", async () => {
  renderTab();
  await screen.findByTestId("notes-subtab");

  expect(screen.queryByTestId("diary-empty-notes")).not.toBeInTheDocument();
  expect(screen.queryByTestId("diary-empty-meetings")).not.toBeInTheDocument();
  expect(screen.queryByTestId("diary-empty-emails")).not.toBeInTheDocument();
});

// --- Story 4.3: the composer hangs off the pane, not off one sub-tab ------

test("✉ on a meeting card opens the composer without moving the sub-tab", async () => {
  // The prototype's composer is a page-level modal, and dragging the pane to ✉
  // Emails on a click that is *about* the meeting the handler is looking at
  // would be a sub-tab switch nobody asked for.
  renderTab();
  await userEvent.click(await screen.findByTestId("diary-subtab-meetings"));
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(screen.getAllByTestId("meeting-email")[0]);

  expect(await screen.findByTestId("email-composer")).toBeInTheDocument();
  expect(screen.getByTestId("diary-subtab-meetings")).toHaveAttribute("aria-selected", "true");
  // …and the letter is the server's, merged against that meeting — the subject
  // is not something this browser assembled.
  await waitFor(() =>
    expect(screen.getByTestId("composer-subject")).toHaveValue(
      "Meeting Confirmation: RTW Conference — WC-20017",
    ),
  );
});

test("a completed send closes the modal, toasts, and switches to ✉ Emails", async () => {
  renderTab({ sendEmail: EMAIL_CREATED });
  await userEvent.click(await screen.findByTestId("diary-subtab-meetings"));
  await screen.findAllByTestId("meeting-card");
  await userEvent.click(screen.getAllByTestId("meeting-email")[0]);
  await screen.findByTestId("email-composer");
  await waitFor(() => expect(screen.getByTestId("composer-subject")).not.toHaveValue(""));

  await userEvent.click(screen.getByTestId("composer-send"));

  await waitFor(() => expect(screen.queryByTestId("email-composer")).not.toBeInTheDocument());
  // The prototype's post-send `alert()` in its proper form — non-blocking,
  // polite, and naming who was told.
  expect(screen.getByTestId("toast")).toHaveTextContent("✓ Email logged to: Employee, Employer HR");
  // …and the pane is where the send now is, which is the prototype's behaviour.
  expect(screen.getByTestId("diary-subtab-emails")).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByTestId("emails-subtab")).toBeInTheDocument();
});

// --- the keyboard the ARIA roles promise ---------------------------------

test("the sub-tab strip is a roving tab stop, and the arrows move it", async () => {
  // The strip shipped `role="tablist"`, `role="tab"`, `aria-selected` and
  // `aria-controls` with no key handler and no roving `tabIndex` — which is not
  // an omission but a keyboard *trap*: tab into the strip, land on the selected
  // tab, and the other two are unreachable without a mouse (WCAG 2.1.1).
  // `DetailTabs` records this exact defect from an earlier review, in the file
  // this strip was copied from.
  renderTab();
  await screen.findByTestId("notes-subtab");

  const notes = screen.getByTestId("diary-subtab-notes");
  expect(notes).toHaveAttribute("tabindex", "0");
  expect(screen.getByTestId("diary-subtab-meetings")).toHaveAttribute("tabindex", "-1");

  notes.focus();
  await userEvent.keyboard("{ArrowRight}");

  expect(screen.getByTestId("diary-subtab-meetings")).toHaveAttribute("aria-selected", "true");
  // Focus follows selection, or the caret is left on a tab that has just
  // dropped out of the tab order.
  expect(screen.getByTestId("diary-subtab-meetings")).toHaveFocus();

  await userEvent.keyboard("{End}");
  expect(screen.getByTestId("diary-subtab-emails")).toHaveAttribute("aria-selected", "true");

  // …and it wraps, rather than stopping at the end.
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByTestId("diary-subtab-notes")).toHaveAttribute("aria-selected", "true");

  await userEvent.keyboard("{ArrowLeft}");
  expect(screen.getByTestId("diary-subtab-emails")).toHaveAttribute("aria-selected", "true");

  await userEvent.keyboard("{Home}");
  expect(screen.getByTestId("diary-subtab-notes")).toHaveAttribute("aria-selected", "true");
});
