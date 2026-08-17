/**
 * One meeting card in the Meetings sub-tab (Story 4.1, AC 3-5, UX-DR9).
 *
 * The prototype's `.mtg-card` (lines 334-348, rendered at 1898): a glyph and
 * the type, the date/time with the location after a middot, the linked claim
 * in steel blue, the agenda in italics, the participant tags, and an action
 * row of ✓ Done · ✉ Email participants · Delete.
 *
 * **This component decides nothing.** Upcoming versus Done is `meeting.status`
 * — one registered derivation on the server (AD-10) — not a comparison written
 * here. The prototype computes `m.date >= today && !m.done` in its renderer,
 * which is the second copy of that rule Story 4.2's today's-meetings summary
 * would have made a third of.
 *
 * **"✉ Email participants" ships disabled with a tooltip** (AC 5), reusing
 * Story 3.5's pattern verbatim: the trigger is a wrapping span rather than the
 * button (a disabled `<button>` fires no pointer events, so Radix would never
 * see the hover), the reason is also the control's `title`, and it is
 * announced through `aria-describedby`. NFR-3 asks for no dead clicks; a
 * control whose explanation only a mouse can reach is a dead click for
 * everybody else. Story 4.3 enables this same control.
 *
 * **A refusal renders on the card that caused it**, keyed by meeting id and
 * derived during render rather than cleared by an effect — `LineItemDialog`'s
 * device. Without the key, "changed by someone else" would follow the handler
 * onto the next card they touched.
 *
 * **Story 4.2 added a `compact` variant rather than a second card.** The
 * today's-meetings summary in the Notes sub-tab renders the same rows with a
 * shorter action row (✓ Done · Open Claim). See the prop's own comment for why
 * a variant and not a copy — the short version is `formatWhen` and the status
 * treatment, which the prototype *does* duplicate and consequently formats two
 * ways for one meeting.
 */
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import type { Meeting } from "@/api/meetings";

import {
  MEETING_STATUS_GLYPH,
  MEETING_STATUS_TONE,
  MEETING_TYPE_LABEL,
  PARTICIPANT_TAG_LABEL,
  PARTICIPANT_TAG_TONE,
} from "./labels";

/** The sentence the ✉ control shows until Story 4.3 builds the composer. */
export const EMAIL_SEAM_REASON = "Email composer arrives in Story 4.3";

const ACTION_CLASS =
  "rounded border px-[9px] py-[4px] text-[10.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50";

/**
 * `2026-09-01` + `10:30` → `Tue, Sep 1, 10:30 AM`.
 *
 * The prototype's `toLocaleString` call, with one change that matters: the
 * date is parsed as a *local* wall clock (`new Date(y, m, d, …)`) rather than
 * through `new Date("2026-09-01T10:30")`, whose behaviour for a bare
 * date-and-time string is implementation-defined and which, for a date with no
 * time, is parsed as UTC — so a meeting on the 1st renders as the 31st for
 * every reader west of Greenwich.
 *
 * Formatting only. Nothing about *when* the meeting falls relative to today is
 * decided here — that is `meeting.status`, and it arrived from the server.
 */
export function formatWhen(isoDate: string, isoTime: string | null): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (!year || !month || !day) return isoDate;
  const [hour, minute] = (isoTime ?? "").split(":").map(Number);
  const when = new Date(year, month - 1, day, hour || 0, minute || 0);

  const date = when.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  return isoTime === null
    ? date
    : `${date}, ${when.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`;
}

export function MeetingCard({
  meeting,
  busy,
  completing,
  deleting,
  error,
  compact = false,
  onComplete,
  onDelete,
  onOpenClaim,
}: {
  meeting: Meeting;
  /** Any meeting command is in flight — what *disables* the controls. */
  busy: boolean;
  /** **This card's own** ✓ is in flight — what *relabels* it. */
  completing: boolean;
  /** **This card's own** ✕ is in flight. */
  deleting: boolean;
  /** The refusal raised about this meeting, or `null`. */
  error: string | null;
  /**
   * The today's-meetings summary's variant (Story 4.2, AC 1).
   *
   * **A variant rather than a second component**, which is the story's own
   * instruction and the right call for one reason above the others: the
   * date/time formatting and the Upcoming/Done treatment are the two things
   * that must not exist twice, and `formatWhen` plus `MEETING_STATUS_*` are
   * exactly what a copy would have duplicated. The prototype does copy them —
   * its `renderDiary` writes its own `toLocaleTimeString` call at line 2062 —
   * and its two cards format the same meeting differently.
   *
   * What the variant changes is the *action row*: ✓ Done and "Open Claim",
   * with no Delete and no email seam. Deleting from a one-day summary is a
   * destructive action a long way from the list that shows what else is
   * scheduled, and the ✉ seam belongs where Story 4.3 will enable it — the
   * Meetings sub-tab — rather than being a second disabled control to explain.
   */
  compact?: boolean;
  onComplete: (meeting: Meeting) => void;
  onDelete: (meeting: Meeting) => void;
  /** Drive the workspace to this meeting's claim. Compact cards only. */
  onOpenClaim?: (claimId: string) => void;
}) {
  const emailReasonId = `meeting-${meeting.id}-email-seam`;
  const claimId = meeting.claimId;

  return (
    <article
      data-testid="meeting-card"
      data-meeting-id={meeting.id}
      data-meeting-type={meeting.meetingType}
      data-status={meeting.status}
      data-variant={compact ? "compact" : "full"}
      className={`rounded border border-border bg-surface p-[9px_11px] ${MEETING_STATUS_TONE[meeting.status]}`}
    >
      <h4 data-testid="meeting-title" className="text-[12px] font-semibold text-text">
        {MEETING_STATUS_GLYPH[meeting.status]} {MEETING_TYPE_LABEL[meeting.meetingType]}
      </h4>

      <p data-testid="meeting-when" className="mt-[2px] text-[11px] text-faint">
        🕐 {formatWhen(meeting.meetingDate, meeting.meetingTime)}
        {meeting.location === null ? "" : ` · ${meeting.location}`}
      </p>

      {meeting.claimId !== null && (
        <p data-testid="meeting-claim-ref" className="mt-[2px] text-[11px] text-steel">
          📋 {meeting.claimId}
          {meeting.workerName === null ? "" : ` — ${meeting.workerName}`}
        </p>
      )}

      {/* The agenda and the participant tags are the two things a one-day
          summary drops: the card sits under a greeting in a 320px pane, and
          the full detail is one sub-tab away on the same rows. */}
      {!compact && meeting.notes !== null && (
        <p data-testid="meeting-notes" className="mt-[3px] text-[11px] text-faint italic">
          {meeting.notes}
        </p>
      )}

      {/* `!== 0` rather than `> 0`: `noDerivation.test.ts` refuses a
          comparison against a numeric literal anywhere it scans, and it is
          right to — the cheapest way to smuggle a threshold in is to write one
          next to a `.length`. */}
      {!compact && meeting.participants.length !== 0 && (
        <p className="mt-[5px] flex flex-wrap gap-[4px]">
          {meeting.participants.map((participant) => (
            <span
              key={participant}
              data-testid="meeting-participant-tag"
              data-participant={participant}
              className={`rounded-full px-[6px] py-px text-[9.5px] font-semibold ${PARTICIPANT_TAG_TONE[participant]}`}
            >
              {PARTICIPANT_TAG_LABEL[participant]}
            </span>
          ))}
        </p>
      )}

      <div className="mt-[7px] flex flex-wrap items-center gap-[5px]">
        {/* Hidden once done, the prototype's own rule: there is no un-complete
            command, so a ✓ on a finished meeting would be a button whose only
            outcome is a 409. */}
        {!meeting.isDone && (
          <button
            type="button"
            data-testid="meeting-done"
            onClick={() => onComplete(meeting)}
            disabled={busy}
            className={`${ACTION_CLASS} border-brand bg-brand text-white hover:bg-brand-strong`}
          >
            {completing ? "Saving…" : "✓ Done"}
          </button>
        )}

        {compact ? (
          /* The 4.2 half of AC 1's "wired to the meeting store and queue
             selection": the ✓ above is the meeting store, and this is the
             selection. Present only when the meeting names a claim **and a
             consumer supplied somewhere to send it** — the column is nullable
             and the prop is optional, and a button that navigated nowhere
             would be the dead click NFR-3 forbids. The two conditions are the
             same rule: rendering on `claimId` alone while calling through
             `onOpenClaim?.()` shipped an enabled control whose click did
             nothing, which is precisely what the first half of this comment
             says must not happen. */
          claimId !== null &&
          onOpenClaim !== undefined && (
            <button
              type="button"
              data-testid="meeting-open-claim"
              data-claim-id={claimId}
              onClick={() => onOpenClaim(claimId)}
              className={`${ACTION_CLASS} border-steel bg-steel-soft text-steel hover:bg-surface`}
            >
              Open Claim
            </button>
          )
        ) : (
          <>
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span data-testid="meeting-email-seam" tabIndex={0}>
                    <button
                      type="button"
                      data-testid="meeting-email"
                      disabled
                      // Duplicated as a native tooltip so the reason survives
                      // without a pointer — Story 3.5's note.
                      title={EMAIL_SEAM_REASON}
                      aria-describedby={emailReasonId}
                      className={`${ACTION_CLASS} border-steel bg-steel-soft text-steel`}
                    >
                      ✉ Email participants
                    </button>
                  </span>
                </TooltipTrigger>
                <TooltipContent data-testid="meeting-email-reason">
                  {EMAIL_SEAM_REASON}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <button
              type="button"
              data-testid="meeting-delete"
              onClick={() => onDelete(meeting)}
              disabled={busy}
              className={`${ACTION_CLASS} border-border bg-surface-2 text-muted-text hover:bg-surface`}
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </>
        )}
      </div>

      {!compact && (
        <span id={emailReasonId} className="sr-only">
          {EMAIL_SEAM_REASON}
        </span>
      )}

      {error !== null && (
        <p
          role="alert"
          data-testid="meeting-error"
          className="mt-[6px] text-[11px] font-semibold text-error"
        >
          {error}
        </p>
      )}
    </article>
  );
}
