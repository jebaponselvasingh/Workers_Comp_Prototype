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
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

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

function renderCard(
  meeting: Meeting,
  extra: {
    compact?: boolean;
    onOpenClaim?: (id: string) => void;
    onEmail?: (meeting: Meeting) => void;
    onDelete?: (meeting: Meeting) => void;
  } = {},
) {
  return render(
    <MeetingCard
      meeting={meeting}
      busy={false}
      completing={false}
      deleting={false}
      error={null}
      onComplete={() => {}}
      onDelete={() => {}}
      onEmail={() => {}}
      {...extra}
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

// --- Story 4.2: the compact variant --------------------------------------

test("the compact variant offers ✓ Done and Open Claim, and nothing else", async () => {
  // A *variant* rather than a second component, because `formatWhen` and the
  // Upcoming/Done treatment are exactly what a copy would have duplicated —
  // the prototype does copy them, and formats one meeting two ways as a
  // result. What differs is the action row, and this is that difference.
  renderCard(aMeeting(), { compact: true, onOpenClaim: () => {} });

  const card = screen.getByTestId("meeting-card");
  expect(card).toHaveAttribute("data-variant", "compact");
  expect(within(card).getByTestId("meeting-done")).toBeInTheDocument();
  expect(within(card).getByTestId("meeting-open-claim")).toBeInTheDocument();
  // No Delete: a destructive action a long way from the list that shows what
  // else is scheduled. No ✉ either: the composer belongs where the meeting's
  // whole context is. Both shipped as seam echoes and are **real guards** now
  // that Story 4.3 has made the control on the full card work — the summary
  // card stays a two-action card.
  expect(within(card).queryByTestId("meeting-delete")).not.toBeInTheDocument();
  expect(within(card).queryByTestId("meeting-email")).not.toBeInTheDocument();
});

test("the compact variant drops the agenda and the participant tags", () => {
  renderCard(aMeeting(), { compact: true });

  expect(screen.queryByTestId("meeting-notes")).not.toBeInTheDocument();
  expect(screen.queryByTestId("meeting-participant-tag")).not.toBeInTheDocument();
  // …but keeps the two lines that identify the meeting, formatted by the one
  // `formatWhen` both variants share.
  expect(screen.getByTestId("meeting-when")).toHaveTextContent("10:30");
  expect(screen.getByTestId("meeting-claim-ref")).toHaveTextContent("WC-20017");
});

test("the full variant carries Delete and a working ✉, and no Open Claim", () => {
  renderCard(aMeeting());

  const card = screen.getByTestId("meeting-card");
  expect(card).toHaveAttribute("data-variant", "full");
  expect(within(card).getByTestId("meeting-delete")).toBeInTheDocument();
  // **Enabled**, which is the whole of AC 5 on this side. Story 4.1 shipped it
  // disabled inside a tooltip wrapper with a `title`, an sr-only reason and an
  // `aria-describedby`; enabling it was the deletion of all of that, so what is
  // asserted is that none of it survives.
  const email = within(card).getByTestId("meeting-email");
  expect(email).toBeEnabled();
  expect(email).not.toHaveAttribute("title");
  expect(email).not.toHaveAttribute("aria-describedby");
  expect(screen.queryByTestId("meeting-email-seam")).not.toBeInTheDocument();
  expect(within(card).queryByTestId("meeting-open-claim")).not.toBeInTheDocument();
});

test("✉ hands the whole meeting back, so the caller can name it to the server", async () => {
  const emailed: number[] = [];
  renderCard(aMeeting({ id: 777 }), { onEmail: (meeting) => emailed.push(meeting.id) });

  await userEvent.click(screen.getByTestId("meeting-email"));

  expect(emailed).toEqual([777]);
});

test("the ✉ disables itself while a meeting command is in flight", () => {
  render(
    <MeetingCard
      meeting={aMeeting()}
      busy
      completing
      deleting={false}
      error={null}
      onComplete={() => {}}
      onDelete={() => {}}
      onEmail={() => {}}
    />,
  );

  expect(screen.getByTestId("meeting-email")).toBeDisabled();
});

// --- Story 4.3: Delete asks first ----------------------------------------

test("Delete arms rather than deletes, and the second press is what sends", async () => {
  // There is no `update_meeting`, so delete-and-recreate is the correction path
  // and a mis-click removes an audited PHI row with no undo. `deferred-work.md`
  // assigned the two-step to whoever built the feedback primitive.
  const deleted: number[] = [];
  const confirm = vi.fn();
  vi.stubGlobal("confirm", confirm);
  renderCard(aMeeting({ id: 501 }), { onDelete: (meeting) => deleted.push(meeting.id) });

  await userEvent.click(screen.getByTestId("meeting-delete"));

  // Nothing sent, and the plain Delete has been replaced by the pair.
  expect(deleted).toEqual([]);
  expect(screen.queryByTestId("meeting-delete")).not.toBeInTheDocument();
  expect(screen.getByTestId("meeting-delete-confirm")).toHaveTextContent("Delete?");
  expect(screen.getByTestId("meeting-delete-cancel")).toHaveTextContent("Cancel");
  // …and never a native dialog (NFR-3, UX-DR11).
  expect(confirm).not.toHaveBeenCalled();

  await userEvent.click(screen.getByTestId("meeting-delete-confirm"));
  expect(deleted).toEqual([501]);

  vi.unstubAllGlobals();
});

test("Cancel disarms Delete and sends nothing", async () => {
  const deleted: number[] = [];
  renderCard(aMeeting(), { onDelete: (meeting) => deleted.push(meeting.id) });

  await userEvent.click(screen.getByTestId("meeting-delete"));
  await userEvent.click(screen.getByTestId("meeting-delete-cancel"));

  expect(deleted).toEqual([]);
  expect(screen.getByTestId("meeting-delete")).toBeInTheDocument();
  expect(screen.queryByTestId("meeting-delete-confirm")).not.toBeInTheDocument();
});

test("Cancel disarms Delete even while another card's command is in flight", async () => {
  // `busy` is **list-wide**: `MeetingsSubTab` hands every card the same flag. So
  // a Cancel disabled by it meant arming Delete on meeting A and then ticking ✓
  // Done on meeting B left A holding an armed destructive control that could not
  // be lowered until somebody else's write settled. Cancel sends nothing and
  // touches no row; the only thing it can do is make the card safer.
  const deleted: number[] = [];
  const card = (busy: boolean) => (
    <MeetingCard
      meeting={aMeeting()}
      busy={busy}
      completing={busy}
      deleting={false}
      error={null}
      onComplete={() => {}}
      onDelete={(meeting) => deleted.push(meeting.id)}
      onEmail={() => {}}
    />
  );

  const { rerender } = render(card(false));
  await userEvent.click(screen.getByTestId("meeting-delete"));

  rerender(card(true));
  // The confirm is disabled with everything else — it *writes*.
  expect(screen.getByTestId("meeting-delete-confirm")).toBeDisabled();
  const cancel = screen.getByTestId("meeting-delete-cancel");
  expect(cancel).toBeEnabled();

  await userEvent.click(cancel);

  expect(screen.queryByTestId("meeting-delete-confirm")).not.toBeInTheDocument();
  expect(deleted).toEqual([]);
});

test("the in-flight label belongs to the plain Delete, never to the confirm", () => {
  // The confirm's own handler lowers `confirming` before it calls `onDelete`, so
  // the pair unmounts in the same commit the mutation starts and a `deleting`
  // branch on the confirm button was unreachable — a state a reader would take
  // for a state the card can be in.
  render(
    <MeetingCard
      meeting={aMeeting()}
      busy
      completing={false}
      deleting
      error={null}
      onComplete={() => {}}
      onDelete={() => {}}
      onEmail={() => {}}
    />,
  );

  expect(screen.getByTestId("meeting-delete")).toHaveTextContent("Deleting…");
  expect(screen.queryByTestId("meeting-delete-confirm")).not.toBeInTheDocument();
});

test("any other action on the card disarms an armed Delete", async () => {
  // A confirmation left armed behind a handler who thought better of it and
  // pressed ✓ or ✉ instead is a loaded control on a row they have moved on from.
  renderCard(aMeeting());

  await userEvent.click(screen.getByTestId("meeting-delete"));
  expect(screen.getByTestId("meeting-delete-confirm")).toBeInTheDocument();

  await userEvent.click(screen.getByTestId("meeting-email"));
  expect(screen.queryByTestId("meeting-delete-confirm")).not.toBeInTheDocument();
  expect(screen.getByTestId("meeting-delete")).toBeInTheDocument();
});

test("a compact card for an untagged meeting offers no Open Claim", () => {
  // `claimId` is nullable (the ERD's `CLAIM |o--o{ MEETING`), and a button that
  // navigated nowhere would be the dead click NFR-3 forbids.
  renderCard(aMeeting({ claimId: null, workerName: null }), { compact: true });

  expect(screen.queryByTestId("meeting-open-claim")).not.toBeInTheDocument();
  expect(screen.getByTestId("meeting-done")).toBeInTheDocument();
});

test("a done meeting's compact card offers Open Claim but no ✓", () => {
  // The ✓ is hidden once done in both variants — there is no un-complete
  // command, so the button's only outcome would be a 409.
  renderCard(aMeeting({ isDone: true, status: "done" }), {
    compact: true,
    onOpenClaim: () => {},
  });

  expect(screen.queryByTestId("meeting-done")).not.toBeInTheDocument();
  expect(screen.getByTestId("meeting-open-claim")).toBeInTheDocument();
});

test("a compact card with no `onOpenClaim` renders no Open Claim", () => {
  // The prop is optional and the click used to go through `onOpenClaim?.()`, so
  // a consumer that forgot it shipped an enabled control whose click did
  // nothing — the dead click NFR-3 forbids, and the same thing this file
  // already asserts about an untagged meeting. Both are the same rule: the
  // button exists when there is a claim to open *and* somewhere to open it.
  renderCard(aMeeting(), { compact: true });

  expect(screen.queryByTestId("meeting-open-claim")).not.toBeInTheDocument();
  expect(screen.getByTestId("meeting-done")).toBeInTheDocument();
});

test("Open Claim hands back the meeting's own claim id", async () => {
  const opened: string[] = [];
  renderCard(aMeeting({ claimId: "WC-20099" }), {
    compact: true,
    onOpenClaim: (id) => opened.push(id),
  });

  screen.getByTestId("meeting-open-claim").click();

  expect(opened).toEqual(["WC-20099"]);
});

test("the compact Open Claim disables itself with the other actions", () => {
  // It was the only action on any card without `disabled={busy}`, and it is not
  // a cosmetic inconsistency: the consumer's `onOpenClaim` resets the meeting
  // mutation, so a click mid-✓ turned "Saving…" back into "✓ Done" while the
  // request was still outstanding — a control that looked ready over a write
  // whose outcome the handler could no longer see.
  renderCard(aMeeting(), { compact: true, onOpenClaim: () => {} });
  expect(screen.getByTestId("meeting-open-claim")).toBeEnabled();

  cleanup();
  render(
    <MeetingCard
      meeting={aMeeting()}
      busy
      completing
      deleting={false}
      error={null}
      compact
      onComplete={() => {}}
      onDelete={() => {}}
      onEmail={() => {}}
      onOpenClaim={() => {}}
    />,
  );
  expect(screen.getByTestId("meeting-open-claim")).toBeDisabled();
  expect(screen.getByTestId("meeting-done")).toBeDisabled();
});
