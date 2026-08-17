/**
 * The 📅 Meetings sub-tab — the list, its four states, and the scheduler
 * (Story 4.1, AC 1-5, UX-DR9).
 *
 * **Loading, error, empty and populated are four branches, not three** (NFR-3).
 * A list that rendered nothing while it loaded would make "no meetings
 * scheduled" and "we have not asked yet" the same picture, and the empty state
 * is the one a first-time reader is most likely to see.
 *
 * **The order is the server's.** `useMeetings` returns `items` already sorted
 * by `(meetingDate, id)`; nothing here sorts, filters or counts. `total` comes
 * off the payload for the same reason — `items.length` is the page's size, not
 * the diary's — and it is *rendered*, because a list that silently showed the
 * first fifty of a longer diary would be indistinguishable from a complete
 * one. "Show more" is the other half of that: the sort is ascending, so the
 * rows beyond the page are the newest ones, and a handler whose diary outgrows
 * a page would otherwise lose the meeting they just scheduled off the end.
 *
 * **Feedback is a polite live region plus inline refusals, not a toast.** The
 * story text asks for a "non-blocking success toast" and this console has no
 * toast provider, queue or live-region host; Story 3.4 deferred building one
 * with the note that the first story owning *two* surfaces should. This story
 * owns one (the save) and 4.3 adds the second (the email send), so the
 * primitive stays deferred and this follows the shipped precedent — an
 * always-mounted `role="status"` carrying at most one sentence, and a
 * `role="alert"` on the card that was refused. Recorded in `deferred-work.md`.
 *
 * The region is **always mounted** rather than rendered with its message: a
 * `role="status"` element that appears at the same moment as its content is
 * frequently not announced at all (Story 3.5's note), and one sentence rather
 * than concatenated ternaries because `isSuccess` is sticky until its own
 * mutation runs again.
 */
import { useState } from "react";

import type { Meeting } from "@/api/meetings";
import {
  useCompleteMeeting,
  useDeleteMeeting,
  useMeetingWriteInFlight,
  useMeetings,
} from "@/api/meetings";
import { isConflict, isNotFound } from "@/api/errors";
import { useDiaryNav } from "@/features/diary/DiaryNav";
import { todayIso } from "@/lib/clock";

import { MeetingCard } from "./MeetingCard";
import { MeetingSchedulerDialog } from "./MeetingSchedulerDialog";

const CONFLICT_MESSAGE = "Changed by someone else — showing the latest.";
const FAILED_MESSAGE = "Could not save. Try again in a moment.";
/**
 * A 404 is not a retry, so it must not be worded as one.
 *
 * The meeting was deleted in another session; the list refetches on this path
 * (`afterFailedWrite`) so the card is about to leave the screen on its own.
 * "Try again in a moment" would invite a click that answers 404 for ever.
 */
const GONE_MESSAGE = "This meeting is no longer in your diary — the list has been refreshed.";

export function MeetingsSubTab({
  claimId,
  workerName,
}: {
  /** The claim the workspace has selected, or `null`. */
  claimId: string | null;
  workerName: string | null;
}) {
  // **The viewer's day, sent so the server judges this list at the reader's
  // clock rather than its own.** Without it the unfiltered read had no clock —
  // the Notes summary one sub-tab away passes `day`, which does double duty —
  // and the same meeting came back `upcoming` there and `done` here for any
  // handler whose local date differs from UTC's. Read during render exactly as
  // the summary reads it, so the two cannot disagree inside one paint.
  const meetings = useMeetings({ asOf: todayIso(new Date()) });
  const complete = useCompleteMeeting();
  const remove = useDeleteMeeting();
  const busy = useMeetingWriteInFlight();
  // The scheduler's open state lives in the pane's context rather than here,
  // because the centre pane's `meetings` deep link has to be able to open it
  // (AC 5) — see `DiaryNav` on why the context holds the state rather than an
  // event a `useEffect` would have to react to.
  const { schedulerOpen, schedulerSession, openScheduler, closeScheduler } = useDiaryNav();

  const [scheduled, setScheduled] = useState(false);
  // A refusal belongs to one card. Stored with the meeting it was raised
  // about and read back by comparison, `LineItemDialog`'s device: without the
  // key, "changed by someone else" would follow the handler onto the next
  // card they touched.
  const [refusal, setRefusal] = useState<{ meetingId: number; message: string } | null>(null);

  function refusalFor(error: unknown, meetingId: number): void {
    setRefusal({
      meetingId,
      message: isConflict(error)
        ? CONFLICT_MESSAGE
        : isNotFound(error)
          ? GONE_MESSAGE
          : FAILED_MESSAGE,
    });
  }

  /**
   * Empty the live region and every refusal, before anything new starts.
   *
   * **Both mutations, on every path**, which is what makes it a function.
   * TanStack keeps `error` and `isSuccess` until that same mutation runs
   * again, so resetting only the *sibling* left the region sticky in a way
   * nobody clicking could clear: tick ✓, then open and cancel the scheduler,
   * and "Meeting marked done." was still what a screen reader read out. The
   * asymmetry was the bug — the reset had three call sites and each one
   * cleared a different subset (`ActionsCard`'s logged defect, again).
   */
  function clearFeedback(): void {
    complete.reset();
    remove.reset();
    setRefusal(null);
    setScheduled(false);
  }

  function onComplete(meeting: Meeting): void {
    clearFeedback();
    complete.mutate(
      { meetingId: meeting.id, expectedVersion: meeting.version },
      { onError: (error) => refusalFor(error, meeting.id) },
    );
  }

  function onDelete(meeting: Meeting): void {
    clearFeedback();
    remove.mutate(
      { meetingId: meeting.id, expectedVersion: meeting.version },
      { onError: (error) => refusalFor(error, meeting.id) },
    );
  }

  return (
    <div data-testid="meetings-subtab" className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto p-[8px_10px]">
        {/* **Four branches, and `isError` is not the second of them.** TanStack
            keeps `data` when a *refetch* or a later infinite page fails, so
            testing `isError` before `data` blanked a populated list on a
            transient failure: sixty loaded meetings vanished behind "could not
            be loaded" when "Show more" timed out, and — because Story 4.2 made
            ✓ Done refetch on 200 — a blip in the moment after a successful tick
            wiped the list the handler had just acted in. With rows in hand the
            list stays and the failure is a strip above it, which is the honest
            report: this is what we have, and the last attempt to refresh it did
            not work. */}
        {meetings.isPending ? (
          <p data-testid="meetings-loading" className="text-[11.5px] text-faint">
            Loading meetings…
          </p>
        ) : meetings.isError && meetings.data === undefined ? (
          <p role="alert" data-testid="meetings-error" className="text-[11.5px] text-error">
            ⚠ Your meetings could not be loaded. Try again in a moment.
          </p>
        ) : meetings.data === undefined ? null : meetings.data.items.length === 0 ? (
          /* The prototype's own sentence. A pane that rendered an empty list
             would read as "we have not checked", which is a different fact. */
          <p data-testid="meetings-empty" className="text-[11.5px] text-faint">
            No meetings scheduled. Use the button below to schedule one.
          </p>
        ) : (
          <>
            {/* The stale strip: rows in hand, and the last refresh failed. */}
            {meetings.isError && (
              <p role="alert" data-testid="meetings-stale" className="mb-[6px] text-[11px] text-error">
                ⚠ Could not refresh — showing the meetings last loaded.
              </p>
            )}
            {/* The server's `total`, not `items.length`: what is on screen is
                one page, and the two numbers differ exactly when the "Show
                more" below matters. */}
            <p
              data-testid="meetings-count"
              className="mb-[6px] font-display text-[9.5px] font-bold tracking-[0.3px] text-muted-text uppercase"
            >
              {meetings.data.total === 1 ? "1 meeting" : `${meetings.data.total} meetings`}
            </p>

            <div className="flex flex-col gap-[6px]">
              {meetings.data.items.map((meeting) => (
                <MeetingCard
                  key={meeting.id}
                  meeting={meeting}
                  busy={busy}
                  completing={complete.isPending && complete.variables?.meetingId === meeting.id}
                  deleting={remove.isPending && remove.variables?.meetingId === meeting.id}
                  error={refusal?.meetingId === meeting.id ? refusal.message : null}
                  onComplete={onComplete}
                  onDelete={onDelete}
                />
              ))}
            </div>

            {/* Present exactly while the server says there is another page —
                `hasNextPage` follows `nextCursor`, so this carries no count
                the browser worked out for itself (`StageGroup`'s rule). */}
            {meetings.hasNextPage && (
              <button
                type="button"
                data-testid="meetings-more"
                disabled={meetings.isFetchingNextPage}
                onClick={() => void meetings.fetchNextPage()}
                className="mt-[6px] w-full rounded border border-border py-[5px] text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
              >
                {meetings.isFetchingNextPage ? "Loading…" : "Show more"}
              </button>
            )}
          </>
        )}
      </div>

      <div className="border-t border-border p-[8px_10px]">
        <button
          type="button"
          data-testid="meeting-schedule-open"
          onClick={() => {
            clearFeedback();
            openScheduler();
          }}
          className="w-full rounded border border-dashed border-border bg-surface-2 p-[8px] text-[12px] font-semibold text-muted-text hover:border-brand hover:text-brand"
        >
          ＋ Schedule Meeting
        </button>
      </div>

      {/* Polite, in place, and never a dialog (UX-DR11). Always mounted so a
          screen reader is subscribed before anything is written into it. */}
      <p role="status" data-testid="meetings-status" className="sr-only">
        {scheduled
          ? "Meeting scheduled."
          : complete.isSuccess
            ? "Meeting marked done."
            : remove.isSuccess
              ? "Meeting deleted."
              : ""}
      </p>

      {/* Keyed on the session, so every open is a fresh mount and the draft is
          re-seeded — today's date, 10:00, Employee checked. Without it a
          handler who cancelled halfway through would re-open onto their own
          half-typed agenda, which is the prototype's behaviour (it clears only
          `notes` and `location`, line 1878) and not one worth porting. */}
      <MeetingSchedulerDialog
        key={schedulerSession}
        open={schedulerOpen}
        claimId={claimId}
        workerName={workerName}
        onClose={closeScheduler}
        onScheduled={() => {
          clearFeedback();
          setScheduled(true);
        }}
      />
    </div>
  );
}
