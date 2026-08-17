/**
 * Story 4.2 AC 1-4 — the Notes sub-tab.
 *
 * What is worth asserting here is everything the server cannot: the four states
 * of each of the two lists, that the add-note input clears on success and
 * *keeps its text* on a refusal, that the empty-note refusal is inline and
 * never a dialog, and — the load-bearing one — that the greeting's upcoming
 * count is the **server's field** rather than a length of the summary beneath
 * it. The fixture makes those two numbers differ on purpose.
 *
 * The clock's own boundaries are `lib/clock.test.ts`'s; this file only checks
 * that the card is wired to it.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { NOTE_LENGTH_CAP } from "@/api/fieldLimits";
import { todayIso } from "@/lib/clock";
import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import type { StubRoutes } from "@/test/api-mock";
import {
  DIARY_NOTES,
  DIARY_NOTES_EMPTY,
  DIARY_NOTE_CLAIM_NOT_FOUND,
  DIARY_NOTE_INVALID,
  ME_HANDLER,
  DIARY_NOTE_WRITTEN_NOT_READABLE,
  MEETINGS_EMPTY,
  MEETINGS_TODAY,
  stubApi,
} from "@/test/api-mock";

import { DiaryNavProvider, useDiaryNav } from "./DiaryNav";
import { DiaryTab } from "./DiaryTab";
import { NotesSubTab } from "./NotesSubTab";

const BAD_REQUEST = {
  // A 4xx rather than a 500, so the assertion is about the branch and not
  // about the retry policy: `createQueryClient` retries 5xx twice with
  // backoff, which pushes the error state past the default `findBy` timeout
  // (`MeetingsSubTab.test.tsx`'s note).
  status: 400,
  body: {
    type: "/problems/invalid-cursor",
    title: "Bad Request",
    status: 400,
    detail: "The pagination cursor is not readable.",
  },
};

/** Publishes the router's current query string so a click can be asserted. */
function LocationProbe() {
  return <p data-testid="location-search">{useLocation().search}</p>;
}

/**
 * `NotesSubTab` with its `claimId` taken from the URL, as the shell supplies it.
 *
 * The selection lives in `?claim=` (`useSelectClaim` writes it, `useSelectedClaim`
 * reads it), so a test about the selection *moving* has to read it from there —
 * a hard-coded prop would sit still and the assertion would pass against a
 * component that never froze anything.
 */
function SelectionBoundNotes() {
  const claimId = new URLSearchParams(useLocation().search).get("claim");
  return <NotesSubTab claimId={claimId} workerName="Marcus Webb" injuryType="Laceration" />;
}

function renderNotes(routes: StubRoutes = {}) {
  stubApi({
    me: ME_HANDLER,
    meetings: MEETINGS_TODAY,
    diaryNotes: DIARY_NOTES,
    ...routes,
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId="WC-20017" workerName="Marcus Webb" injuryType="Laceration" />
          <LocationProbe />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- AC 1: the greeting card ---------------------------------------------

test("the greeting names the session persona by first name", async () => {
  renderNotes();

  // "Kaya Johnson" → "Kaya", the prototype's own reduction. Waited for by text
  // rather than asserted straight off the testid, because the card is mounted
  // from the first frame with the "Handler" fallback in it while `/me` is in
  // flight — a `findByTestId` resolves against that and passes vacuously.
  //
  // Matched on a substring so the test does not depend on the hour it runs at;
  // the three greeting strings are `clock.test.ts`'s business.
  const line = await screen.findByText(/Kaya 👋/);
  expect(line).toHaveAttribute("data-testid", "diary-greeting-line");
});

test("the active-claim line carries the claim, the worker and the injury type", async () => {
  renderNotes();

  const line = await screen.findByTestId("diary-active-claim");
  expect(line).toHaveTextContent("Active: WC-20017 — Marcus Webb (Laceration)");
});

test("with no claim selected the line says so rather than rendering blank", async () => {
  stubApi({ me: ME_HANDLER, meetings: MEETINGS_TODAY, diaryNotes: DIARY_NOTES });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId={null} workerName={null} injuryType={null} />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  // NFR-3: an absent line and "we have not loaded it" look the same.
  expect(await screen.findByTestId("diary-active-claim")).toHaveTextContent("Select a case");
});

test("the upcoming count is the server's field, not the length of the list below it", async () => {
  // The whole reason `upcomingCount` is on the envelope. `MEETINGS_TODAY`
  // carries one item and an `upcomingCount` of three, because the count is
  // over the *whole book* and the summary is one filtered day of it. A
  // component that counted rows renders 1 and fails here.
  renderNotes();

  // Waited for by *state*, not by testid: the line is mounted from the first
  // frame carrying "Counting upcoming meetings…" — the loading state — so a
  // bare `findByTestId` resolves against that and asserts against a card that
  // has not been told anything yet.
  const line = await screen.findByTestId("diary-upcoming-count");
  await waitFor(() => expect(line).toHaveAttribute("data-state", "known"));
  expect(line).toHaveTextContent("📅 3 upcoming meetings — see below / Meetings tab");
  expect(screen.getAllByTestId("meeting-card")).toHaveLength(1);
});

test("the count line is absent when nothing is ahead", async () => {
  renderNotes({ meetings: MEETINGS_EMPTY });

  await screen.findByTestId("diary-today-empty");
  expect(screen.queryByTestId("diary-upcoming-count")).not.toBeInTheDocument();
});

// --- AC 1: today's meetings ----------------------------------------------

test("today's meetings render as compact cards with the server's count", async () => {
  renderNotes();

  expect(await screen.findByTestId("diary-today-heading")).toHaveTextContent(
    "📅 Today's Meetings (1)",
  );
  const card = screen.getByTestId("meeting-card");
  expect(card).toHaveAttribute("data-variant", "compact");
  // ✓ Done and Open Claim, and neither Delete nor the 4.3 email seam.
  expect(within(card).getByTestId("meeting-done")).toBeInTheDocument();
  expect(within(card).getByTestId("meeting-open-claim")).toBeInTheDocument();
  expect(within(card).queryByTestId("meeting-delete")).not.toBeInTheDocument();
  expect(within(card).queryByTestId("meeting-email")).not.toBeInTheDocument();
});

test("an empty day says so in the prototype's own words", async () => {
  renderNotes({ meetings: MEETINGS_EMPTY });

  expect(await screen.findByTestId("diary-today-empty")).toHaveTextContent(
    "📅 No meetings scheduled for today.",
  );
});

test("the summary has a loading state and an error state of its own", async () => {
  const { unmount } = renderNotes({ meetings: "pending" });
  expect(await screen.findByTestId("diary-today-loading")).toBeInTheDocument();
  unmount();
  vi.unstubAllGlobals();

  renderNotes({ meetings: BAD_REQUEST });
  expect(await screen.findByTestId("diary-today-error")).toHaveTextContent("could not be loaded");
  // …and the diary below it is unaffected: two lists, two states, and a
  // failure in one must not blank the other.
  expect(await screen.findAllByTestId("diary-note")).not.toHaveLength(0);
});

test("Open Claim moves the workspace selection to that meeting's claim", async () => {
  // The 4.2 half of AC 1's "wired to … queue selection". The selection lives
  // in the URL (`useSelectClaim`), so what is asserted is the query string —
  // which is also what proves the right pane drives the queue and the case
  // file without any of the three holding a copy of the selection.
  stubApi({
    me: ME_HANDLER,
    diaryNotes: DIARY_NOTES,
    meetings: {
      status: 200,
      body: {
        ...MEETINGS_TODAY.body,
        // A *different* claim from the one the workspace has selected, so the
        // assertion cannot pass on the starting URL.
        items: [{ ...MEETINGS_TODAY.body.items[0], claimId: "WC-20099" }],
      },
    },
  });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId="WC-20017" workerName="Marcus Webb" injuryType="Laceration" />
          <LocationProbe />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("meeting-card");

  // The control names the claim it will select, and that id is the server's —
  // it comes off the meeting row, not from the workspace's current selection.
  const open = screen.getByTestId("meeting-open-claim");
  expect(open).toHaveAttribute("data-claim-id", "WC-20099");
  expect(screen.getByTestId("location-search")).toHaveTextContent("claim=WC-20017");

  await userEvent.click(open);

  expect(screen.getByTestId("location-search")).toHaveTextContent("claim=WC-20099");
});

// --- AC 2, AC 3: the notes list and the input ----------------------------

test("notes render newest-first with their date header and claim tag", async () => {
  renderNotes();

  const notes = await screen.findAllByTestId("diary-note");
  // The order is the server's — `DIARY_NOTES` is newest-first and nothing here
  // sorts. The ids are what make the assertion about order rather than content.
  expect(notes.map((note) => note.getAttribute("data-note-id"))).toEqual(["901", "900"]);

  expect(within(notes[0]).getByTestId("diary-note-tag")).toHaveTextContent("📎 WC-20017");
  expect(within(notes[0]).getByTestId("diary-note-when")).not.toHaveTextContent("Invalid");
  // The untagged note renders no tag rather than `📎 null`.
  expect(within(notes[1]).queryByTestId("diary-note-tag")).not.toBeInTheDocument();
});

test("an empty diary says so rather than rendering nothing", async () => {
  renderNotes({ diaryNotes: DIARY_NOTES_EMPTY });

  expect(await screen.findByTestId("notes-empty")).toHaveTextContent("No notes yet.");
});

test("the notes list has a loading state and an error state of its own", async () => {
  const { unmount } = renderNotes({ diaryNotes: "pending" });
  expect(await screen.findByTestId("notes-loading")).toBeInTheDocument();
  unmount();
  vi.unstubAllGlobals();

  renderNotes({ diaryNotes: BAD_REQUEST });
  expect(await screen.findByTestId("notes-error")).toHaveTextContent("could not be loaded");
  // …and today's meetings are unaffected, for the mirror of the reason above.
  expect(screen.getByTestId("diary-today-heading")).toBeInTheDocument();
});

test("saving a valid note clears the input and announces it politely", async () => {
  renderNotes();
  await screen.findAllByTestId("diary-note");

  const input = screen.getByTestId("note-input");
  await userEvent.type(input, "Called the plant.");
  await userEvent.click(screen.getByTestId("note-save"));

  expect(await screen.findByTestId("notes-status")).toHaveTextContent("Note saved.");
  expect(input).toHaveValue("");
});

test("an empty note is refused inline at the input, and nothing is sent", async () => {
  // The prototype silently ignores an empty input; NFR-3 and UX-DR11 ask for
  // an inline message and never a native dialog. A `window.alert` stub that
  // throws is a stronger statement than asserting a message is visible.
  const alerted = vi.fn();
  vi.stubGlobal("alert", alerted);
  renderNotes();
  await screen.findAllByTestId("diary-note");

  await userEvent.click(screen.getByTestId("note-save"));

  expect(await screen.findByTestId("note-error")).toHaveTextContent(
    "Write something before saving.",
  );
  expect(screen.getByTestId("note-input")).toHaveAttribute("aria-invalid", "true");
  expect(alerted).not.toHaveBeenCalled();
});

test("whitespace alone is the same refusal as an empty box", async () => {
  renderNotes();
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "   ");
  await userEvent.click(screen.getByTestId("note-save"));

  expect(await screen.findByTestId("note-error")).toBeInTheDocument();
});

test("typing clears the refusal — a message about an empty box must not outlive it", async () => {
  renderNotes();
  await screen.findAllByTestId("diary-note");

  await userEvent.click(screen.getByTestId("note-save"));
  await screen.findByTestId("note-error");

  await userEvent.type(screen.getByTestId("note-input"), "n");

  expect(screen.queryByTestId("note-error")).not.toBeInTheDocument();
});

test("a note the server refuses keeps its text in the box", async () => {
  // The worst thing this surface could do is lose a handler's words to a 422
  // or a 404 about a claim tag. The draft is cleared on success only.
  renderNotes({ addDiaryNote: DIARY_NOTE_INVALID });
  await screen.findAllByTestId("diary-note");

  const input = screen.getByTestId("note-input");
  await userEvent.type(input, "Words worth keeping.");
  await userEvent.click(screen.getByTestId("note-save"));

  expect(await screen.findByTestId("note-error")).toBeInTheDocument();
  expect(input).toHaveValue("Words worth keeping.");
});

// --- AC 5: the note *is* the completion, on screen ------------------------

test("saving a note invalidates the tagged claim's action checklist", async () => {
  // The on-screen half of AC 5. The server stops emitting `diary_check_in` the
  // moment the row exists, but the checklist is its own cache key and
  // `refetchOnWindowFocus` is off — so without this the handler follows "Log
  // Diary Entry →", writes the note, watches the row they just satisfied stay
  // exactly where it was, and writes it again.
  const client = createQueryClient();
  client.setQueryData(queryKeys.claims.actions("WC-20017"), { items: [] });
  stubApi({ me: ME_HANDLER, meetings: MEETINGS_TODAY, diaryNotes: DIARY_NOTES });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId="WC-20017" workerName="Marcus Webb" injuryType="Laceration" />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "Weekly check-in done.");
  await userEvent.click(screen.getByTestId("note-save"));
  await screen.findByText("Note saved.");

  expect(client.getQueryState(queryKeys.claims.actions("WC-20017"))?.isInvalidated).toBe(true);
});

// --- AC 2: the claim tag is visible, and it is captured not read ---------

test("the form names the claim the note will be tagged to", async () => {
  // It said nothing before: the note went to whatever the workspace had
  // selected at submit, and the handler had no way to see which claim that was
  // — on a write into a table with no edit and no delete.
  renderNotes();
  await screen.findAllByTestId("diary-note");

  const tag = screen.getByTestId("note-claim-tag");
  expect(tag).toHaveAttribute("data-claim-id", "WC-20017");
  expect(tag).toHaveTextContent("WC-20017");
  expect(tag).toHaveAttribute("data-pinned", "false");
});

test("with no claim selected the form says the note will be untagged", async () => {
  stubApi({ me: ME_HANDLER, meetings: MEETINGS_TODAY, diaryNotes: DIARY_NOTES });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId={null} workerName={null} injuryType={null} />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  const tag = await screen.findByTestId("note-claim-tag");
  expect(tag).toHaveAttribute("data-claim-id", "");
  expect(tag).toHaveTextContent("without a tag");
});

test("Open Claim under a started draft does not move the note's tag", async () => {
  // The defect this pins, end to end and inside one pane: the handler starts a
  // note about WC-20017, clicks Open Claim on a today card for WC-20099 — a
  // control *this component renders* — and the note used to be written against
  // WC-20099. Permanently, and satisfying the wrong claim's weekly check-in.
  stubApi({
    me: ME_HANDLER,
    diaryNotes: DIARY_NOTES,
    meetings: {
      status: 200,
      body: {
        ...MEETINGS_TODAY.body,
        items: [{ ...MEETINGS_TODAY.body.items[0], claimId: "WC-20099" }],
      },
    },
  });

  // What was actually *sent* is the assertion that matters here — the tag on
  // screen could be right while the body carried the moved selection.
  // `openapi-fetch` calls `fetch(new Request(...))`, so the body is on the
  // request rather than in an `init` (`stubApi`'s own note).
  const sent: unknown[] = [];
  const stubbedFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    if (input instanceof Request && input.method === "POST" && input.url.includes("/notes")) {
      sent.push(await input.clone().json());
    }
    return stubbedFetch(input, init);
  });

  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          {/* The selection has to be *live* for this test to mean anything —
              in the shell it comes from the URL through `useSelectedClaim`,
              and a hard-coded prop would make the assertion pass on a
              component that had not changed. */}
          <SelectionBoundNotes />
          <LocationProbe />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("meeting-card");

  await userEvent.type(screen.getByTestId("note-input"), "Spoke to the plant about Marcus.");
  await userEvent.click(screen.getByTestId("meeting-open-claim"));

  // The workspace really did move…
  expect(screen.getByTestId("location-search")).toHaveTextContent("claim=WC-20099");
  // …and the note did not. The form says so, in as many words.
  const tag = screen.getByTestId("note-claim-tag");
  expect(tag).toHaveAttribute("data-claim-id", "WC-20017");
  expect(tag).toHaveAttribute("data-pinned", "true");
  expect(tag).toHaveTextContent("started against");

  await userEvent.click(screen.getByTestId("note-save"));
  await screen.findByText("Note saved.");
  expect(sent).toEqual([{ noteText: "Spoke to the plant about Marcus.", claimId: "WC-20017" }]);
});

test("an empty box follows the selection again, so the freeze is only ever a draft's", async () => {
  // The other half: a handler who has not started typing sees the tag track
  // their clicks, which is what makes the pinned state legible when it happens.
  const { rerender } = renderNotes();
  await screen.findAllByTestId("diary-note");

  expect(screen.getByTestId("note-claim-tag")).toHaveAttribute("data-claim-id", "WC-20017");

  rerender(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20099"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId="WC-20099" workerName="Other Worker" injuryType="Strain" />
          <LocationProbe />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByTestId("note-claim-tag")).toHaveAttribute("data-claim-id", "WC-20099");
});

// --- AC 3: refusals say which kind they are ------------------------------

test("a 422 shows the server's sentence rather than a retry message", async () => {
  // `add.error !== null ? FAILED_MESSAGE` collapsed a 422, a 404 and a dropped
  // connection into "Could not save. Try again in a moment." — true of one of
  // the three.
  renderNotes({ addDiaryNote: DIARY_NOTE_INVALID });
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "Something.");
  await userEvent.click(screen.getByTestId("note-save"));

  expect(await screen.findByTestId("note-error")).toHaveTextContent("noteText cannot be empty");
});

test("a 404 about the claim tag is not announced as a retry", async () => {
  renderNotes({ addDiaryNote: DIARY_NOTE_CLAIM_NOT_FOUND });
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "Something.");
  await userEvent.click(screen.getByTestId("note-save"));

  const error = await screen.findByTestId("note-error");
  expect(error).toHaveTextContent("No claim WC-20017 in your caseload.");
  expect(error).not.toHaveTextContent("Try again in a moment");
});

// --- AC 3: the cap is on the control -------------------------------------

test("the input caps the draft at the server's limit and shows the count", async () => {
  // Pasting a 2400-character summary used to fail with nothing on screen saying
  // what the limit was or which end to trim.
  renderNotes();
  await screen.findAllByTestId("diary-note");

  expect(screen.getByTestId("note-length")).toHaveTextContent("0 of 2000 characters");

  await userEvent.type(screen.getByTestId("note-input"), "abc");
  expect(screen.getByTestId("note-length")).toHaveTextContent("3 of 2000 characters");

  const input = screen.getByTestId("note-input") as HTMLTextAreaElement;
  await userEvent.clear(input);
  await userEvent.paste("x".repeat(NOTE_LENGTH_CAP + 40));
  expect(input.value).toHaveLength(NOTE_LENGTH_CAP);
  expect(screen.getByTestId("note-length")).toHaveTextContent("2000 of 2000 characters");
});

test("the cap counts code points, as the server does — not UTF-16 units", async () => {
  // `maxLength` counts UTF-16 code units and `len()` on the other side counts
  // code points, so a note of emoji hit the browser's cap at half the number
  // the counter was displaying: "2000 of 2000" for a thousand characters the
  // server would happily have taken. The attribute cannot express a code-point
  // cap, so it is gone and the clamp is in `onChange`.
  renderNotes();
  await screen.findAllByTestId("diary-note");

  const input = screen.getByTestId("note-input") as HTMLTextAreaElement;
  await userEvent.click(input);
  // Two code points, four UTF-16 units.
  await userEvent.paste("👷🏭");

  expect(screen.getByTestId("note-length")).toHaveTextContent("2 of 2000 characters");
  expect(input.value).toBe("👷🏭");
});

// --- the greeting's third state ------------------------------------------

test("a failed day request says the count is unknown rather than dropping the line", async () => {
  // `upcomingCount` is a whole-book fact on the day-filtered envelope, so a
  // failed request took the greeting's line down with it — and an absent line
  // is exactly how this card says "nothing ahead".
  renderNotes({ meetings: BAD_REQUEST });

  const line = await screen.findByTestId("diary-upcoming-count");
  await waitFor(() => expect(line).toHaveAttribute("data-state", "unknown"));
  expect(line).toHaveTextContent("could not be counted");
});

test("the count line says it is loading rather than rendering nothing", async () => {
  // The state this card is in on *every* mount, and it used to look exactly
  // like "nothing is ahead" — the same absence-versus-zero conflation the
  // unknown state above exists to fix, in the branch a handler sees first.
  renderNotes({ meetings: "pending" });

  const line = await screen.findByTestId("diary-upcoming-count");
  expect(line).toHaveAttribute("data-state", "loading");
  expect(line).toHaveTextContent("Counting upcoming meetings…");
});

// --- feedback is scoped to the control it belongs to ----------------------

test("typing a note does not wipe a meeting's refusal", async () => {
  // "Changed by someone else — showing the latest." is rendered on a today card
  // a few lines above the textarea, and it is the one message on this pane that
  // reports somebody else's write. Typing is not a reason to decide it has been
  // read.
  renderNotes({
    completeMeeting: {
      status: 409,
      body: {
        type: "/problems/stale-write",
        title: "Conflict",
        status: 409,
        detail: "changed",
        meeting: { ...MEETINGS_TODAY.body.items[0], isDone: true, version: 2, status: "done" },
      },
    },
  });
  await screen.findByTestId("meeting-card");

  await userEvent.click(screen.getByTestId("meeting-done"));
  expect(await screen.findByTestId("meeting-error")).toHaveTextContent("Changed by someone else");

  await userEvent.type(screen.getByTestId("note-input"), "Unrelated note.");

  expect(screen.getByTestId("meeting-error")).toBeInTheDocument();
});

// --- the draft survives the sub-tab, and focus is not sticky --------------

/** The centre pane's "Log Diary Entry →", with none of the centre pane. */
function DeepLink() {
  const { requestNotes } = useDiaryNav();
  return (
    <button type="button" data-testid="deep-link" onClick={requestNotes}>
      Log Diary Entry →
    </button>
  );
}

function renderDiaryTab() {
  stubApi({ me: ME_HANDLER, meetings: MEETINGS_TODAY, diaryNotes: DIARY_NOTES });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <DeepLink />
          <DiaryTab claimId="WC-20017" workerName="Marcus Webb" injuryType="Laceration" />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("the deep link still focuses the input, and a second follow still works", async () => {
  // The behaviour the focus intent must not have broken. Asserted through a
  // real mount rather than through the counter, because the counter was never
  // the thing that was wrong.
  renderDiaryTab();
  await screen.findByTestId("notes-subtab");

  await userEvent.click(screen.getByTestId("deep-link"));
  expect(screen.getByTestId("note-input")).toHaveFocus();

  await userEvent.click(screen.getByTestId("diary-subtab-meetings"));
  await userEvent.click(screen.getByTestId("deep-link"));
  expect(screen.getByTestId("note-input")).toHaveFocus();
});

test("a manual return to Notes after a deep link leaves the caret alone", async () => {
  // The failure the intent flag fixes, in its own test: once the deep link had
  // been followed, `noteFocusSession` stayed non-zero for ever and every later
  // arrival on 📓 Notes re-took focus.
  renderDiaryTab();
  await screen.findByTestId("notes-subtab");

  await userEvent.click(screen.getByTestId("deep-link"));
  expect(screen.getByTestId("note-input")).toHaveFocus();

  await userEvent.click(screen.getByTestId("diary-subtab-meetings"));
  await userEvent.click(screen.getByTestId("diary-subtab-notes"));

  expect(await screen.findByTestId("note-input")).not.toHaveFocus();
  expect(screen.getByTestId("diary-subtab-notes")).toHaveFocus();
});

test("a half-typed note survives a trip to the Meetings sub-tab", async () => {
  // `DiaryTab` mounts only the selected sub-tab, deliberately — so a draft held
  // inside `NotesSubTab` was destroyed by a click on 📅 Meetings, with nothing
  // saying so.
  renderDiaryTab();
  await screen.findByTestId("notes-subtab");

  await userEvent.type(screen.getByTestId("note-input"), "Three sentences, mid-thought.");

  await userEvent.click(screen.getByTestId("diary-subtab-meetings"));
  expect(screen.queryByTestId("note-input")).not.toBeInTheDocument();

  await userEvent.click(screen.getByTestId("diary-subtab-notes"));
  expect(await screen.findByTestId("note-input")).toHaveValue("Three sentences, mid-thought.");
});

test("selecting Notes by hand does not steal focus, even after a deep link has", async () => {
  // `autoFocus={noteFocusSession !== 0}` stayed true for the life of the
  // provider once the deep link had been followed once, so every later click on
  // 📓 Notes remounted the textarea and yanked focus out of the tablist. The
  // assertion is about the *remount*, which is what a counter-only test missed.
  renderDiaryTab();
  await screen.findByTestId("notes-subtab");

  // No deep link has been followed, so the first mount leaves focus alone.
  expect(screen.getByTestId("note-input")).not.toHaveFocus();

  await userEvent.click(screen.getByTestId("diary-subtab-meetings"));
  await userEvent.click(screen.getByTestId("diary-subtab-notes"));

  const tab = screen.getByTestId("diary-subtab-notes");
  expect(screen.getByTestId("note-input")).not.toHaveFocus();
  expect(tab).toHaveFocus();
});

test("the live region is mounted from the first frame and starts empty", async () => {
  // A `role="status"` element that appears at the same moment as its content
  // is frequently not announced at all (Story 3.5's note).
  renderNotes();

  const region = await screen.findByTestId("notes-status");
  expect(region).toBeInTheDocument();
  expect(region).toHaveTextContent("");
});

// --- the request itself, which nothing used to assert --------------------

/** Every URL the stub was asked for, in order. */
function requestedUrls(): string[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) =>
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
    );
}

test("the summary asks the server for the viewer's own day", async () => {
  // The one parameter that makes this a *summary* rather than a page of the
  // whole book — and nothing asserted it: the stub ignored the query string, so
  // deleting `{ day: today }` from the component left every test here green
  // while the pane rendered fifty past meetings under "Today's Meetings".
  renderNotes();
  await screen.findAllByTestId("diary-note");

  const today = todayIso(new Date());
  const meetingRequests = requestedUrls().filter((url) => url.includes("/claims-diary/meetings"));
  expect(meetingRequests).not.toHaveLength(0);
  for (const url of meetingRequests) {
    expect(url).toContain(`day=${today}`);
  }
});

// --- the errored-with-data branch ----------------------------------------

test("a failed refresh keeps the notes already on screen", async () => {
  // TanStack keeps `data` when a *refetch* fails, so testing `isError` before
  // the cache blanked a populated list: a "Show more" that timed out took the
  // fifty notes above it with it, and — because 4.2 made ✓ Done refetch on 200
  // — so did a blip in the second after a successful tick.
  let calls = 0;
  renderNotes({
    diaryNotes: () => {
      calls += 1;
      return calls === 1 ? DIARY_NOTES : BAD_REQUEST;
    },
  });
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "Weekly check-in.");
  await userEvent.click(screen.getByTestId("note-save"));

  // The save invalidates the list, the refetch fails — and the notes stay.
  expect(await screen.findByTestId("notes-stale")).toBeInTheDocument();
  expect(screen.getAllByTestId("diary-note")).not.toHaveLength(0);
  expect(screen.queryByTestId("notes-error")).not.toBeInTheDocument();
});

test("a failed refresh keeps today's summary and its count", async () => {
  let calls = 0;
  renderNotes({
    meetings: () => {
      calls += 1;
      return calls === 1 ? MEETINGS_TODAY : BAD_REQUEST;
    },
  });
  await screen.findByTestId("meeting-card");

  // ✓ Done refetches on 200 (`upcomingCount` is a whole-book aggregate), and
  // this is the refetch that fails.
  await userEvent.click(screen.getByTestId("meeting-done"));

  expect(await screen.findByTestId("diary-today-stale")).toBeInTheDocument();
  expect(screen.getByTestId("meeting-card")).toBeInTheDocument();
  // …and the greeting keeps the number it was told rather than claiming the
  // count is unknown.
  const line = screen.getByTestId("diary-upcoming-count");
  expect(line).toHaveAttribute("data-state", "known");
});

// --- the note refusals that are not failures ------------------------------

test("a committed-but-unreadable note clears the draft instead of inviting a retry", async () => {
  // `/problems/note-not-readable` is answered *after* the row is committed and
  // audited, and it says "Do not write it again." — while the draft stayed in
  // the box under an enabled Save, the list went unrefreshed and the checklist
  // row stayed put. Every affordance pointed at the duplicate the wording
  // forbids, into a table with no edit and no delete.
  const client = createQueryClient();
  client.setQueryData(queryKeys.claims.actions("WC-20017"), { items: [] });
  stubApi({
    me: ME_HANDLER,
    meetings: MEETINGS_TODAY,
    diaryNotes: DIARY_NOTES,
    addDiaryNote: DIARY_NOTE_WRITTEN_NOT_READABLE,
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <DiaryNavProvider>
          <NotesSubTab claimId="WC-20017" workerName="Marcus Webb" injuryType="Laceration" />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "Weekly check-in: spoke to the NCM.");
  await userEvent.click(screen.getByTestId("note-save"));

  const error = await screen.findByTestId("note-error");
  expect(error).toHaveTextContent("Do not write it again");
  // The box is empty, so nothing is one click from being sent twice…
  expect(screen.getByTestId("note-input")).toHaveValue("");
  // …and the two lists the row belongs to are told to re-read.
  expect(client.getQueryState(queryKeys.claims.actions("WC-20017"))?.isInvalidated).toBe(true);
});

test("a 404 with no problem type is a failure, not a permanent refusal", async () => {
  // A proxy 404 or a deploy-skew 404 carries no envelope, so `api/client.ts`
  // synthesises `detail = "The server answered 404."` — a machine sentence that
  // was being rendered at the input as an answer that will never succeed.
  renderNotes({ addDiaryNote: { status: 404, body: {} } });
  await screen.findAllByTestId("diary-note");

  await userEvent.type(screen.getByTestId("note-input"), "Something.");
  await userEvent.click(screen.getByTestId("note-save"));

  const error = await screen.findByTestId("note-error");
  expect(error).toHaveTextContent("Could not save. Try again in a moment.");
  expect(error).not.toHaveTextContent("The server answered 404.");
});

// --- feedback scoping, the other direction --------------------------------

test("✓ Done does not wipe a note refusal the handler is mid-read", async () => {
  // The mirror of the narrowing pass one made. `clearFeedback` called
  // `add.reset()` and was wired to ✓ Done and to Open Claim, so either erased
  // the 404 or 422 explaining why the note in the box had not been saved.
  renderNotes({ addDiaryNote: DIARY_NOTE_CLAIM_NOT_FOUND });
  await screen.findByTestId("meeting-card");

  await userEvent.type(screen.getByTestId("note-input"), "Words worth keeping.");
  await userEvent.click(screen.getByTestId("note-save"));
  await screen.findByTestId("note-error");

  await userEvent.click(screen.getByTestId("meeting-done"));

  expect(screen.getByTestId("note-error")).toBeInTheDocument();
  expect(screen.getByTestId("note-input")).toHaveValue("Words worth keeping.");
});

test("Open Claim does not wipe a note refusal either", async () => {
  renderNotes({ addDiaryNote: DIARY_NOTE_CLAIM_NOT_FOUND });
  await screen.findByTestId("meeting-card");

  await userEvent.type(screen.getByTestId("note-input"), "Words worth keeping.");
  await userEvent.click(screen.getByTestId("note-save"));
  await screen.findByTestId("note-error");

  await userEvent.click(screen.getByTestId("meeting-open-claim"));

  expect(screen.getByTestId("note-error")).toBeInTheDocument();
});

// --- the pinned tag's null corner ----------------------------------------

test("a draft started with no claim says the tag is pinned, not that none exists", async () => {
  // The corner the tag message could not describe: start typing with nothing
  // selected, then select a claim — from the queue or from this pane's own Open
  // Claim — and the capture stays `null` for ever. The note really is written
  // untagged, which is correct; what was wrong is that the form said "No claim
  // selected" while the greeting directly above it named the active claim and
  // `data-pinned` was already true. The one sentence explaining a pinned tag was
  // unreachable in exactly the case where the divergence is hardest to see.
  stubApi({
    me: ME_HANDLER,
    diaryNotes: DIARY_NOTES,
    meetings: {
      status: 200,
      body: {
        ...MEETINGS_TODAY.body,
        items: [{ ...MEETINGS_TODAY.body.items[0], claimId: "WC-20099" }],
      },
    },
  });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace"]}>
        <DiaryNavProvider>
          <SelectionBoundNotes />
          <LocationProbe />
        </DiaryNavProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("meeting-card");

  // Nothing selected, nothing typed: the ordinary untagged sentence.
  expect(screen.getByTestId("note-claim-tag")).toHaveTextContent("without a tag");

  await userEvent.type(screen.getByTestId("note-input"), "A general note.");
  await userEvent.click(screen.getByTestId("meeting-open-claim"));

  // The workspace moved…
  expect(screen.getByTestId("location-search")).toHaveTextContent("claim=WC-20099");
  const tag = screen.getByTestId("note-claim-tag");
  // …the note did not, and the form says *that* rather than the sentence for a
  // console with no claim open at all.
  expect(tag).toHaveAttribute("data-claim-id", "");
  expect(tag).toHaveAttribute("data-pinned", "true");
  expect(tag).toHaveTextContent("started against no claim");
  expect(tag).not.toHaveTextContent("No claim selected —");
});
