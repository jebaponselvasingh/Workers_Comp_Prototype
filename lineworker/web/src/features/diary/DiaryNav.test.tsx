/**
 * Story 4.1 AC 5 — the cross-pane deep link, at the level of the state that
 * makes it work.
 *
 * `DiaryNav` is the only piece of this feature with no markup of its own, and
 * so the only one nothing else was testing: `DiaryTab.test.tsx` exercises the
 * sub-tabs through the provider and `MeetingsSubTab.test.tsx` the scheduler,
 * but neither calls `requestMeetings`, which is the whole point of the context
 * existing. What is pinned here is the pair of guarantees the centre pane
 * relies on and cannot check for itself:
 *
 * - `requestMeetings()` selects the Meetings sub-tab **and** opens the
 *   scheduler, in one event handler — the scheduler is rendered by
 *   `MeetingsSubTab`, so opening it while Notes is showing would set a flag
 *   nothing is mounted to read;
 * - `schedulerSession` advances on every open, because the modal is keyed on
 *   it and a number that stood still would re-open onto the last handler's
 *   half-typed agenda.
 *
 * Story 4.2 adds the same pair for the diary target: `requestNotes()` selects
 * Notes **and** advances `noteFocusSession`, which `NotesSubTab` uses as the
 * add-note input's React `key` so that a fresh mount with `autoFocus` *is* the
 * focus. The counter is what makes a *second* click work — a boolean intent
 * flag would already be true and the input would not remount.
 *
 * The default context value is asserted too. It is a working no-op so that
 * `ClaimDetailPane` and `ActionsCard` can be rendered outside any shell by
 * their own vitest files, and a later "obvious" change to a
 * provider-or-throw would break those two files rather than this one — which
 * is a failure with the wrong name on it.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { DiaryNavProvider, useDiaryNav } from "./DiaryNav";

/** A consumer that shows the whole context and can drive it. */
function Probe() {
  const nav = useDiaryNav();

  return (
    <div>
      <p data-testid="sub-tab">{nav.subTab}</p>
      <p data-testid="open">{String(nav.schedulerOpen)}</p>
      <p data-testid="session">{String(nav.schedulerSession)}</p>
      <p data-testid="note-session">{String(nav.noteFocusSession)}</p>
      <p data-testid="note-pending">{String(nav.noteFocusPending)}</p>
      <p data-testid="draft-text">{nav.noteDraft.text}</p>
      <p data-testid="draft-claim">{String(nav.noteDraft.claimId)}</p>
      <button
        type="button"
        data-testid="type-a"
        onClick={() => nav.typeNoteDraft(`${nav.noteDraft.text}a`, "WC-1")}
      >
        type against WC-1
      </button>
      <button
        type="button"
        data-testid="type-b"
        onClick={() => nav.typeNoteDraft(`${nav.noteDraft.text}b`, "WC-2")}
      >
        type against WC-2
      </button>
      <button type="button" data-testid="clear-draft" onClick={nav.clearNoteDraft}>
        clear
      </button>
      <button type="button" data-testid="request" onClick={nav.requestMeetings}>
        deep link
      </button>
      <button type="button" data-testid="open-scheduler" onClick={nav.openScheduler}>
        open
      </button>
      <button type="button" data-testid="close-scheduler" onClick={nav.closeScheduler}>
        close
      </button>
      <button type="button" data-testid="select-notes" onClick={() => nav.selectSubTab("notes")}>
        notes
      </button>
      <button
        type="button"
        data-testid="select-meetings"
        onClick={() => nav.selectSubTab("meetings")}
      >
        meetings
      </button>
      <button type="button" data-testid="request-notes" onClick={nav.requestNotes}>
        diary deep link
      </button>
    </div>
  );
}

test("the deep link selects Meetings and opens the scheduler together", async () => {
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  // From Notes, which is the state the failure mode needs: a `requestMeetings`
  // that only opened the scheduler would leave the flag set on a sub-tab that
  // does not render one.
  await userEvent.click(screen.getByTestId("select-notes"));
  expect(screen.getByTestId("sub-tab")).toHaveTextContent("notes");
  expect(screen.getByTestId("open")).toHaveTextContent("false");

  await userEvent.click(screen.getByTestId("request"));

  expect(screen.getByTestId("sub-tab")).toHaveTextContent("meetings");
  expect(screen.getByTestId("open")).toHaveTextContent("true");
});

test("every open is a new session, so the modal re-mounts on a fresh draft", async () => {
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  const session = screen.getByTestId("session").textContent;

  await userEvent.click(screen.getByTestId("open-scheduler"));
  const first = screen.getByTestId("session").textContent;
  expect(first).not.toBe(session);

  await userEvent.click(screen.getByTestId("close-scheduler"));
  expect(screen.getByTestId("open")).toHaveTextContent("false");
  // Closing does not advance it — the re-seed happens on the *open*, and a
  // counter that moved on both would be describing something else.
  expect(screen.getByTestId("session")).toHaveTextContent(first!);

  await userEvent.click(screen.getByTestId("open-scheduler"));
  expect(screen.getByTestId("session")).not.toHaveTextContent(first!);
});

test("outside a provider the context is a working no-op, not a throw", async () => {
  // `ClaimDetailPane.test.tsx` and `ActionsCard.test.tsx` render their
  // subjects with no shell around them. The default value has to agree with
  // the provider's *initial* state — Notes since Story 4.2 — or a consumer
  // reading it outside a provider would see a sub-tab the shell never opens on.
  render(<Probe />);

  expect(screen.getByTestId("sub-tab")).toHaveTextContent("notes");
  await userEvent.click(screen.getByTestId("request"));
  expect(screen.getByTestId("open")).toHaveTextContent("false");
});

test("the diary deep link selects Notes and advances the focus session", async () => {
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  // From Meetings, which is the state the failure mode needs: a `requestNotes`
  // that only bumped the counter would advance a number on a sub-tab that
  // renders no input, and the input would later mount unfocused.
  await userEvent.click(screen.getByTestId("select-meetings"));
  expect(screen.getByTestId("sub-tab")).toHaveTextContent("meetings");

  const before = screen.getByTestId("note-session").textContent;
  await userEvent.click(screen.getByTestId("request-notes"));

  expect(screen.getByTestId("sub-tab")).toHaveTextContent("notes");
  expect(screen.getByTestId("note-session")).not.toHaveTextContent(before!);
});

test("following the same deep link twice advances the counter twice", async () => {
  // The reason focus is a counter rather than a boolean: the handler is
  // already on Notes, clicks "Log Diary Entry →" again, and expects the caret
  // back in the input. A flag that was already set would remount nothing.
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  await userEvent.click(screen.getByTestId("request-notes"));
  const first = screen.getByTestId("note-session").textContent;
  await userEvent.click(screen.getByTestId("request-notes"));

  expect(screen.getByTestId("note-session")).not.toHaveTextContent(first!);
});

test("selecting a sub-tab by hand lowers the focus intent the deep link raised", async () => {
  // The counter is monotonic and the *intent* is not, which is the whole
  // separation: `autoFocus={noteFocusSession !== 0}` was true for the rest of
  // the provider's life once the deep link had been followed once.
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  expect(screen.getByTestId("note-pending")).toHaveTextContent("false");

  await userEvent.click(screen.getByTestId("request-notes"));
  expect(screen.getByTestId("note-pending")).toHaveTextContent("true");

  await userEvent.click(screen.getByTestId("select-meetings"));
  expect(screen.getByTestId("note-pending")).toHaveTextContent("false");
  // …and the counter did *not* rewind, so the next deep link still remounts.
  expect(screen.getByTestId("note-session")).not.toHaveTextContent("0");
});

test("the draft's claim is captured at the first keystroke and kept after it", async () => {
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  // Nothing typed: no claim captured, so the form falls back to the selection.
  expect(screen.getByTestId("draft-claim")).toHaveTextContent("null");

  await userEvent.click(screen.getByTestId("type-a"));
  expect(screen.getByTestId("draft-text")).toHaveTextContent("a");
  expect(screen.getByTestId("draft-claim")).toHaveTextContent("WC-1");

  // The selection moved under the draft — the note keeps the claim it started
  // against, which is the whole point.
  await userEvent.click(screen.getByTestId("type-b"));
  expect(screen.getByTestId("draft-text")).toHaveTextContent("ab");
  expect(screen.getByTestId("draft-claim")).toHaveTextContent("WC-1");

  // Cleared after a save: the next note captures afresh.
  await userEvent.click(screen.getByTestId("clear-draft"));
  expect(screen.getByTestId("draft-claim")).toHaveTextContent("null");
  await userEvent.click(screen.getByTestId("type-b"));
  expect(screen.getByTestId("draft-claim")).toHaveTextContent("WC-2");
});

test("the scheduler session and the note session move independently", async () => {
  // Two counters, not one: opening the scheduler must not remount the
  // add-note input (it would discard a half-typed note), and asking for the
  // input must not re-seed the modal's draft.
  render(
    <DiaryNavProvider>
      <Probe />
    </DiaryNavProvider>,
  );

  const note = screen.getByTestId("note-session").textContent;
  await userEvent.click(screen.getByTestId("open-scheduler"));
  expect(screen.getByTestId("note-session")).toHaveTextContent(note!);

  const scheduler = screen.getByTestId("session").textContent;
  await userEvent.click(screen.getByTestId("request-notes"));
  expect(screen.getByTestId("session")).toHaveTextContent(scheduler!);
});
