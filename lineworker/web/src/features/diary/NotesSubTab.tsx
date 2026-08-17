/**
 * The 📓 Notes sub-tab — greeting, today's meetings, the diary (Story 4.2).
 *
 * The prototype's second `renderDiary` (line 2045; the first is dead code with
 * a different empty state), in the same three bands: a greeting card, a
 * today's-meetings section, and the reverse-chronological note list with the
 * add-note input pinned under it.
 *
 * **Almost nothing here is decided in the browser, and the exceptions are
 * exactly three.** The greeting bucket, the "Today is …" sentence and the
 * `day` this component sends to the server are the *viewer's own clock* — a
 * server has never been told the reader's timezone, so it cannot know whether
 * it is morning where they are sitting. All three live in `lib/clock.ts`,
 * outside the roots `noDerivation.test.ts` walks, precisely so that guard stays
 * blunt over this directory. Everything else — which meetings fall today, in
 * what order, whether each is upcoming or done, how many are ahead, how the
 * notes are ordered, how many there are — arrives decided (AD-1, AD-10).
 *
 * **One request serves the summary and the greeting's count.** `useMeetings`
 * is called with today's local date; the server filters and orders that day and
 * puts `upcomingCount` (whole book, filter-independent) on the same envelope.
 * The alternative — reading the unfiltered list and picking today's rows out of
 * it — is the fifty-row truncation bug Story 4.1's review already caught once,
 * in a new place: the sort is ascending, so today's meetings are the *last*
 * thing a page reaches.
 *
 * **Feedback is a polite live region plus inline refusals, not a toast**, which
 * is the shipped precedent (`MeetingsSubTab`, `ActionsCard`) and the standing
 * deferral: Story 3.4 put the toast primitive with the first story owning *two*
 * surfaces that need one, 4.1 owned one and this owns one, so 4.3 — with the
 * email send — owes it. The region is **always mounted** rather than rendered
 * with its message, because a `role="status"` element that appears at the same
 * moment as its content is frequently not announced at all.
 *
 * **No `useEffect` anywhere**, which is the rule this whole feature keeps.
 * Focusing the add-note input after Story 3.5's deep link is a `key` on the
 * input taken from `noteFocusSession` plus a `noteFocusPending` flag the tab
 * strip lowers, so a fresh mount *is* the focus and an ordinary click on 📓
 * Notes is not — see `DiaryNav`.
 *
 * **The draft is not this component's state, and neither is the claim it will
 * be tagged to.** `DiaryTab` mounts only the selected sub-tab, so a `useState`
 * here is destroyed by a click on 📅 Meetings; and the tag has to be captured
 * when the note *starts* rather than read when it is submitted, because "Open
 * Claim" on a card two elements up can move the selection underneath a draft.
 * Both live in `DiaryNav` — see `NoteDraft` there for the whole argument. What
 * stays local is the two pieces of feedback, which belong to this render.
 */
import { useState } from "react";

import { useMe } from "@/api/auth";
import type { Meeting } from "@/api/meetings";
import { useCompleteMeeting, useMeetingWriteInFlight, useMeetings } from "@/api/meetings";
import {
  MAX_NOTE_LENGTH,
  useAddDiaryNote,
  useDiaryNoteWriteInFlight,
  useDiaryNotes,
} from "@/api/diaryNotes";
import { isConflict } from "@/api/errors";
import { feedbackFromError } from "@/features/claim-detail/useInlineEdits";
import { useDiaryNav } from "@/features/diary/DiaryNav";
import { useSelectClaim } from "@/features/queue/useSelectedClaim";
import { formatLongDate, formatNotedAt, greetingFor, todayIso } from "@/lib/clock";

import { MeetingCard } from "./MeetingCard";

const CONFLICT_MESSAGE = "Changed by someone else — showing the latest.";
const FAILED_MESSAGE = "Could not save. Try again in a moment.";
const EMPTY_NOTE_MESSAGE = "Write something before saving.";

/** The prototype's hint line, verbatim. [Source: prototype line 2078] */
const HINT = "Log notes below · Schedule meetings · Email stakeholders via tabs above.";

/**
 * The greeting card — `.dgreet` in the prototype.
 *
 * `firstName` is `name.split(" ")[0]`, the prototype's own reduction. It is a
 * *display* choice about a string the session already holds, not a derivation
 * over claim data.
 */
function GreetingCard({
  now,
  handlerName,
  claimId,
  workerName,
  injuryType,
  upcomingCount,
  upcomingUnknown,
}: {
  now: Date;
  handlerName: string | null;
  claimId: string | null;
  workerName: string | null;
  injuryType: string | null;
  /** The server's count of meetings still ahead, or `null` while it loads. */
  upcomingCount: number | null;
  /**
   * The request that carries the count failed.
   *
   * A third state rather than a second reason to render nothing.
   * `upcomingCount` is a whole-book fact delivered on the day-filtered
   * envelope, so a failed day request took the greeting's line down with it —
   * and an absent line is exactly how this card says "nothing is ahead". A
   * handler with three meetings this week read "no meetings ahead" from a
   * network error, which is the one thing NFR-3's explicit-empty-state rule
   * exists to prevent.
   */
  upcomingUnknown: boolean;
}) {
  const firstName = (handlerName ?? "Handler").split(" ")[0];

  return (
    <section
      data-testid="diary-greeting"
      className="mb-[12px] rounded border border-steel/30 bg-steel-soft p-[10px_12px]"
    >
      <h3
        data-testid="diary-greeting-line"
        className="font-display text-[13px] font-bold text-text"
      >
        {greetingFor(now)}, {firstName} 👋
      </h3>
      <p className="mt-[3px] text-[11.5px] leading-[1.6] text-text">
        Today is <strong data-testid="diary-today">{formatLongDate(now)}</strong>.
      </p>
      {/* "Select a case" rather than nothing, `CopilotPane`'s three-state
          treatment: an absent line reads as a missing feature, and the handler
          may genuinely have no claim selected. */}
      <p data-testid="diary-active-claim" className="text-[11.5px] leading-[1.6] text-text">
        {claimId === null ? (
          <span className="text-faint">Select a case</span>
        ) : (
          <>
            Active:{" "}
            <strong>
              {claimId}
              {workerName === null ? "" : ` — ${workerName}`}
            </strong>
            {injuryType === null ? "" : ` (${injuryType})`}
          </>
        )}
      </p>
      {/* Three states, not two. Rendered with a number only when the server's
          count is above zero — the prototype's own rule; rendered as an
          explicit unknown when the request that carries it failed; and absent
          only when the answer really is "nothing ahead". The comparisons are
          against `null` (still loading) and zero, and the count is *rendered*,
          never combined with anything: `upcomingCount` is on `noDerivation`'s
          list, and pluralising is presentation. */}
      {upcomingUnknown ? (
        <p
          data-testid="diary-upcoming-count"
          data-state="unknown"
          className="text-[11.5px] leading-[1.6] text-faint"
        >
          📅 Upcoming meetings could not be counted — see the Meetings tab.
        </p>
      ) : (
        upcomingCount !== null &&
        upcomingCount !== 0 && (
          <p
            data-testid="diary-upcoming-count"
            data-state="known"
            className="text-[11.5px] leading-[1.6]"
          >
            <strong className="text-brand">
              📅 {upcomingCount} upcoming meeting
              {upcomingCount === 1 ? "" : "s"} — see below / Meetings tab
            </strong>
          </p>
        )
      )}
      <p className="text-[11px] leading-[1.6] text-faint">{HINT}</p>
    </section>
  );
}

export function NotesSubTab({
  claimId,
  workerName,
  injuryType,
}: {
  /** The claim the workspace has selected, or `null`. */
  claimId: string | null;
  workerName: string | null;
  injuryType: string | null;
}) {
  // The persona's own name, read here rather than threaded down from
  // `CopilotPane`: it is identity state, not claim state, and `useMe` is the
  // single reader of it (AD-9) — a prop would have put a fact about the
  // session on two components that render meetings.
  const me = useMe();
  // Resolved once per render rather than per call site, so the greeting, the
  // long date and the `day` sent to the server cannot straddle midnight and
  // disagree with each other inside one paint.
  const now = new Date();
  const today = todayIso(now);

  const notes = useDiaryNotes();
  const add = useAddDiaryNote();
  const savingNote = useDiaryNoteWriteInFlight();

  const todaysMeetings = useMeetings({ day: today });
  const complete = useCompleteMeeting();
  const meetingBusy = useMeetingWriteInFlight();

  const { noteFocusSession, noteFocusPending, noteDraft, typeNoteDraft, clearNoteDraft } =
    useDiaryNav();
  const selectClaim = useSelectClaim();

  const [refusal, setRefusal] = useState<{
    meetingId: number;
    message: string;
  } | null>(null);
  const [emptyNote, setEmptyNote] = useState(false);

  /**
   * The claim this note will be tagged to — **captured, not current**.
   *
   * An empty box has captured nothing, so it follows the workspace selection
   * and the handler sees the tag move as they click around. The first keystroke
   * freezes it (`DiaryNav.typeNoteDraft`), and from then on this is the claim
   * the note was *started* against however far the selection has since moved.
   *
   * That freeze is the whole fix. "Open Claim" on a today's-meeting card — a
   * control this same pane renders, two elements up — changes the selection
   * while a draft survives underneath it, and the note would have been written
   * against a claim it does not describe: permanently, into a table with no
   * edit and no delete, and satisfying the wrong claim's weekly check-in on the
   * way. Reading the selection at submit time is what made that possible, and
   * showing nothing about the tag is what made it invisible.
   */
  const tagClaimId = noteDraft.text === "" ? claimId : noteDraft.claimId;
  /** The selection has moved out from under a started note. */
  const tagIsPinned = noteDraft.text !== "" && tagClaimId !== claimId;

  /**
   * Empty the live region and every refusal, before anything new starts.
   *
   * **Both mutations and the local flag**, which is what makes it a function:
   * TanStack keeps `error` and `isSuccess` until that same mutation runs again,
   * so resetting only a subset leaves the region sticky in a way nobody
   * clicking can clear. `MeetingsSubTab` learned this as a defect (code review,
   * 2026-08-17) and this is the same reset with one more member.
   *
   * **Used by the meeting paths only.** The note form has
   * `clearNoteFeedback` below — see it.
   */
  function clearFeedback(): void {
    add.reset();
    complete.reset();
    setRefusal(null);
    setEmptyNote(false);
  }

  /**
   * The note form's own reset — this control's feedback and nothing else.
   *
   * Scoped, because the full `clearFeedback` on every keystroke wiped a
   * *meeting's* refusal the handler was still reading. "Changed by someone else
   * — showing the latest." is rendered on a today card a few lines above the
   * textarea, and it is the one message on this pane that reports somebody
   * else's write; typing a sentence about a phone call is not a reason to
   * decide it has been read.
   */
  function clearNoteFeedback(): void {
    add.reset();
    setEmptyNote(false);
  }

  function onComplete(meeting: Meeting): void {
    clearFeedback();
    complete.mutate(
      { meetingId: meeting.id, expectedVersion: meeting.version },
      {
        onError: (error) =>
          setRefusal({
            meetingId: meeting.id,
            message: isConflict(error) ? CONFLICT_MESSAGE : FAILED_MESSAGE,
          }),
      },
    );
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    clearNoteFeedback();
    const text = noteDraft.text.trim();
    if (text === "") {
      // Refused here *and* by the command (`normalise_note_text`), which is not
      // duplication of a rule so much as of a courtesy: the server is the gate
      // and answers 422, and this saves the round trip on the one input in the
      // console a handler is most likely to submit blank by pressing Enter.
      setEmptyNote(true);
      return;
    }
    add.mutate(
      // `tagClaimId`, never the live `claimId`: the note goes to the claim it
      // was started against, which is also the one the form has been showing.
      { noteText: text, claimId: tagClaimId },
      // Cleared on success only. A note the server refused stays in the box —
      // it is the handler's words, and losing them to a 404 about a claim tag
      // would be the worst thing this surface could do.
      { onSuccess: () => clearNoteDraft() },
    );
  }

  /**
   * What the input says went wrong — the server's sentence, not a category.
   *
   * `add.error !== null ? FAILED_MESSAGE` collapsed every refusal into "Could
   * not save. Try again in a moment.", which is true of a dropped connection
   * and false of all three refusals this command actually has: a 422
   * `/problems/invalid-patch` (the text cannot be stored), a 422
   * `/problems/validation-error` (it is over the cap) and a 404 (the claim tag
   * is not in this caseload, or the note was written and cannot be read back).
   * None of the three is retriable and all three were inviting a retry.
   * `feedbackFromError` is the shared mapper every other refusal on the case
   * file goes through, and `MeetingSchedulerDialog` already reads it — so this
   * is one import rather than a fourth opinion about what a 409 looks like.
   */
  const saveRefusal = emptyNote
    ? EMPTY_NOTE_MESSAGE
    : add.error !== null
      ? feedbackFromError(add.error).message
      : null;

  return (
    <div data-testid="notes-subtab" className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto p-[8px_10px]">
        <GreetingCard
          now={now}
          handlerName={me.data?.name ?? null}
          claimId={claimId}
          workerName={workerName}
          injuryType={injuryType}
          upcomingCount={todaysMeetings.data?.upcomingCount ?? null}
          upcomingUnknown={todaysMeetings.isError}
        />

        {/* --- today's meetings ------------------------------------------ */}
        <section data-testid="diary-today-meetings" className="mb-[14px]">
          {todaysMeetings.isPending ? (
            <p data-testid="diary-today-loading" className="text-[11.5px] text-faint">
              Loading today's meetings…
            </p>
          ) : todaysMeetings.isError ? (
            <p role="alert" data-testid="diary-today-error" className="text-[11.5px] text-error">
              ⚠ Today's meetings could not be loaded. Try again in a moment.
            </p>
          ) : todaysMeetings.data.items.length === 0 ? (
            <p data-testid="diary-today-empty" className="text-[11.5px] text-faint">
              📅 No meetings scheduled for today.
            </p>
          ) : (
            <>
              {/* The server's `total` for the filtered day, not `items.length`
                  — the two differ exactly when the day has more than one page,
                  and the heading must describe the day rather than the page. */}
              <h3
                data-testid="diary-today-heading"
                className="mb-[6px] font-display text-[9.5px] font-bold tracking-[0.3px] text-faint uppercase"
              >
                📅 Today's Meetings ({todaysMeetings.data.total})
              </h3>
              <div className="flex flex-col gap-[6px]">
                {todaysMeetings.data.items.map((meeting) => (
                  <MeetingCard
                    key={meeting.id}
                    meeting={meeting}
                    compact
                    busy={meetingBusy}
                    completing={complete.isPending && complete.variables?.meetingId === meeting.id}
                    deleting={false}
                    error={refusal?.meetingId === meeting.id ? refusal.message : null}
                    onComplete={onComplete}
                    /* Unreachable: the compact variant renders no Delete. It is
                       required by the shared prop type, and a `() => {}` here
                       is honest about there being nothing to do. */
                    onDelete={() => {}}
                    onOpenClaim={(id) => {
                      clearFeedback();
                      selectClaim(id);
                    }}
                  />
                ))}
              </div>
              {todaysMeetings.hasNextPage && (
                <button
                  type="button"
                  data-testid="diary-today-more"
                  disabled={todaysMeetings.isFetchingNextPage}
                  onClick={() => void todaysMeetings.fetchNextPage()}
                  className="mt-[6px] w-full rounded border border-border py-[5px] text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
                >
                  {todaysMeetings.isFetchingNextPage ? "Loading…" : "Show more"}
                </button>
              )}
            </>
          )}
        </section>

        {/* --- the diary ------------------------------------------------- */}
        {notes.isPending ? (
          <p data-testid="notes-loading" className="text-[12px] text-faint">
            Loading notes…
          </p>
        ) : notes.isError ? (
          <p role="alert" data-testid="notes-error" className="text-[12px] text-error">
            ⚠ Your notes could not be loaded. Try again in a moment.
          </p>
        ) : notes.data.items.length === 0 ? (
          /* The prototype's own sentence. A pane that rendered an empty list
             would read as "we have not checked", which is a different fact. */
          <p data-testid="notes-empty" className="py-[8px] text-[12px] text-faint">
            No notes yet.
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-[6px]">
              {notes.data.items.map((note) => (
                <article
                  key={note.id}
                  data-testid="diary-note"
                  data-note-id={note.id}
                  data-claim-id={note.claimId ?? ""}
                  className="rounded border border-border bg-surface p-[8px_10px]"
                >
                  <p data-testid="diary-note-when" className="text-[10px] text-faint">
                    {formatNotedAt(note.notedAt)}
                  </p>
                  {/* `whitespace-pre-line` because the command stores newlines
                      — a note is prose and a handler presses Enter. */}
                  <p
                    data-testid="diary-note-text"
                    className="mt-[2px] text-[11.5px] whitespace-pre-line text-text"
                  >
                    {note.noteText}
                  </p>
                  {note.claimId !== null && (
                    <p data-testid="diary-note-tag" className="mt-[3px] text-[10.5px] text-steel">
                      📎 {note.claimId}
                    </p>
                  )}
                </article>
              ))}
            </div>
            {notes.hasNextPage && (
              <button
                type="button"
                data-testid="notes-more"
                disabled={notes.isFetchingNextPage}
                onClick={() => void notes.fetchNextPage()}
                className="mt-[6px] w-full rounded border border-border py-[5px] text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
              >
                {notes.isFetchingNextPage ? "Loading…" : "Show more"}
              </button>
            )}
          </>
        )}
      </div>

      {/* --- the pinned input ------------------------------------------- */}
      <form
        data-testid="note-form"
        onSubmit={onSubmit}
        className="flex flex-col gap-[5px] border-t border-border p-[8px_10px]"
      >
        {/* **What this note will be tagged to, said out loud.** The tag was
            invisible before: the form sent whatever the workspace had selected
            at submit time and showed nothing about it, so a handler could not
            see either the ordinary case or the one where the selection had
            moved under their draft. `tagIsPinned` is the second sentence, and
            it is the only place the console explains a value it is deliberately
            *not* updating. */}
        <p
          data-testid="note-claim-tag"
          data-claim-id={tagClaimId ?? ""}
          data-pinned={tagIsPinned}
          className={`text-[10.5px] ${tagIsPinned ? "text-steel" : "text-faint"}`}
        >
          {tagClaimId === null ? (
            "No claim selected — this note will be saved without a tag."
          ) : (
            <>
              📎 Tagging <strong>{tagClaimId}</strong>
              {tagIsPinned
                ? " — the claim this note was started against, not the one now open."
                : ""}
            </>
          )}
        </p>

        <textarea
          // **The key is the remount and `noteFocusPending` is the focus.**
          // `requestNotes()` bumps the counter *and* raises the flag, so the
          // centre pane's "Log Diary Entry →" remounts this input and the mount
          // takes the caret — no ref, no effect, and it works on the second
          // click as well as the first. Selecting 📓 Notes by hand lowers the
          // flag, so an ordinary click on the tab strip leaves focus where the
          // handler put it. See `DiaryNav`.
          key={noteFocusSession}
          autoFocus={noteFocusPending}
          data-testid="note-input"
          aria-label="Add a diary note"
          aria-invalid={saveRefusal !== null}
          aria-describedby="note-length"
          // The column's width, declared on the control (AC 3's quiet half): a
          // 2400-character summary pasted in is stopped here with the cap on
          // screen, rather than sent and refused by a message that names
          // neither the limit nor which end to trim.
          maxLength={MAX_NOTE_LENGTH}
          value={noteDraft.text}
          onChange={(event) => {
            // The current selection travels with the keystroke: the provider
            // captures it on the *first* one and keeps it after that, which is
            // what freezes the tag. See `DiaryNav.NoteDraft`.
            typeNoteDraft(event.target.value, claimId);
            // Typing clears this control's refusal — a message about an empty
            // box must not survive the box stopping being empty — and nothing
            // else's. See `clearNoteFeedback`.
            if (saveRefusal !== null) clearNoteFeedback();
          }}
          disabled={savingNote}
          rows={2}
          placeholder="Add a diary note for today…"
          className="w-full resize-none rounded-[3px] border border-border bg-surface px-[6px] py-[4px] text-[11.5px] text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60"
        />
        {/* The cap, visible before it is reached rather than only when it
            bites. A count of the local draft, which is nothing the server
            decided. */}
        <p id="note-length" data-testid="note-length" className="text-right text-[10px] text-faint">
          {noteDraft.text.length} of {MAX_NOTE_LENGTH} characters
        </p>
        {saveRefusal !== null && (
          <p role="alert" data-testid="note-error" className="text-[11px] font-semibold text-error">
            {saveRefusal}
          </p>
        )}
        <button
          type="submit"
          data-testid="note-save"
          disabled={savingNote}
          className="w-full rounded border border-brand bg-brand py-[5px] text-[11.5px] font-semibold text-white hover:bg-brand-strong disabled:opacity-60"
        >
          {savingNote ? "Saving…" : "Save"}
        </button>
      </form>

      {/* Polite, in place, and never a dialog (UX-DR11). Always mounted so a
          screen reader is subscribed before anything is written into it.
          **One sentence, not two concatenated** — `isSuccess` is sticky until
          its own mutation runs again, and `clearFeedback` resets both, so at
          most one of these is true. */}
      <p role="status" data-testid="notes-status" className="sr-only">
        {add.isSuccess ? "Note saved." : complete.isSuccess ? "Meeting marked done." : ""}
      </p>
    </div>
  );
}
