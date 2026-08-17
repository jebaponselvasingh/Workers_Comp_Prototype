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
  // subjects with no shell around them. Meetings is still the initial sub-tab,
  // so a consumer reading it outside a provider sees the same value it would
  // inside one.
  render(<Probe />);

  expect(screen.getByTestId("sub-tab")).toHaveTextContent("meetings");
  await userEvent.click(screen.getByTestId("request"));
  expect(screen.getByTestId("open")).toHaveTextContent("false");
});
