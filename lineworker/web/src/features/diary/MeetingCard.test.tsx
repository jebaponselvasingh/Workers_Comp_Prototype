/**
 * Story 4.1 AC 3 — the card's only logic, which is `formatWhen`.
 *
 * Everything else on this card is a field rendered where the payload put it,
 * and `MeetingsSubTab.test.tsx` covers that against a two-row fixture. What it
 * cannot cover is the two branches its fixture has no row for: **both** its
 * meetings carry a non-null `meetingTime`, so the all-day meeting — the one
 * the nullable column exists for — and the malformed-date early return are
 * never executed there.
 *
 * They are worth the file because `formatWhen` carries an explicit timezone
 * rationale: the date is parsed as a *local* wall clock rather than through
 * `new Date("2099-09-01")`, which is UTC and renders the 1st as the 31st for
 * every reader west of Greenwich. The all-day branch is exactly where that
 * mistake would show, since a date with no time is the case the string parser
 * treats as UTC. Building the `Date` from local parts is what these assertions
 * pin, and it is why they can name a weekday at all: a UTC parse would make the
 * answer depend on where the suite runs.
 */
import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { Meeting } from "@/api/meetings";

import { MeetingCard, formatWhen } from "./MeetingCard";

/** A meeting with every optional field present — overridden per test. */
function aMeeting(overrides: Partial<Meeting> = {}): Meeting {
  return {
    id: 601,
    claimId: "WC-20017",
    workerName: "Marcus Webb",
    meetingType: "rtw_conference",
    meetingDate: "2099-09-01",
    meetingTime: "10:30:00",
    location: "Phone",
    notes: "Confirm light-duty availability.",
    participants: ["employee"],
    isDone: false,
    version: 1,
    createdAt: "2026-08-17T09:00:00Z",
    status: "upcoming",
    ...overrides,
  };
}

function renderCard(meeting: Meeting) {
  return render(
    <MeetingCard
      meeting={meeting}
      busy={false}
      completing={false}
      deleting={false}
      error={null}
      onComplete={() => {}}
      onDelete={() => {}}
    />,
  );
}

test("a dated-and-timed meeting reads as the day it falls on, in local time", () => {
  // 2099-09-01 is a Tuesday. Naming the weekday is the whole assertion: it is
  // the thing that moves if the date is ever parsed as UTC and rendered in a
  // timezone behind it.
  expect(formatWhen("2099-09-01", "10:30:00")).toBe("Tue, Sep 1, 10:30 AM");
});

test("an all-day meeting renders a date and no invented time", () => {
  // `meetingTime` is nullable because a touchpoint can be "some time that
  // day". The branch must not fall through to midnight — "12:00 AM" is a time
  // nobody entered, and it is what `hour || 0` would print if the `=== null`
  // check were dropped.
  expect(formatWhen("2099-09-01", null)).toBe("Tue, Sep 1");

  renderCard(aMeeting({ meetingTime: null, location: null }));
  const when = screen.getByTestId("meeting-when");
  expect(when).toHaveTextContent("Tue, Sep 1");
  expect(when).not.toHaveTextContent("AM");
  expect(when).not.toHaveTextContent("PM");
  // No location either, and no dangling middot where one would have gone.
  expect(when).not.toHaveTextContent("·");
});

test("a date the formatter cannot read is handed back untouched", () => {
  // The early return exists so a malformed value renders *something* a reader
  // can report, rather than "Invalid Date" or a crash inside a list. Nothing
  // upstream can currently produce one — the column is a `date` — which is
  // precisely why the branch needs a test rather than a claim.
  expect(formatWhen("not-a-date", null)).toBe("not-a-date");
  expect(formatWhen("", "10:30:00")).toBe("");
});

test("an unlinked meeting renders no claim reference at all", () => {
  // `claimId` is nullable (`CLAIM |o--o{ MEETING`), and the row is dropped
  // rather than rendered empty — an empty steel-blue line reads as a claim
  // reference that failed to load.
  renderCard(aMeeting({ claimId: null, workerName: null }));

  expect(screen.queryByTestId("meeting-claim-ref")).not.toBeInTheDocument();
});

test("a meeting with no agenda and no participants renders neither block", () => {
  renderCard(aMeeting({ notes: null, participants: [] }));

  expect(screen.queryByTestId("meeting-notes")).not.toBeInTheDocument();
  expect(screen.queryAllByTestId("meeting-participant-tag")).toHaveLength(0);
});

test("the card renders the status it was sent, whatever the date says", () => {
  // The same trap `MeetingsSubTab.test.tsx` sets, at the level of the one
  // component that would spring it: a 2099 meeting marked `done`. A card that
  // re-derived Upcoming/Done from `meetingDate` renders the wrong glyph.
  renderCard(aMeeting({ isDone: true, status: "done" }));

  const card = screen.getByTestId("meeting-card");
  expect(card).toHaveAttribute("data-status", "done");
  expect(within(card).getByTestId("meeting-title")).toHaveTextContent("✓");
  // …and there is no ✓ Done control, because there is no un-complete command.
  expect(within(card).queryByTestId("meeting-done")).not.toBeInTheDocument();
});
