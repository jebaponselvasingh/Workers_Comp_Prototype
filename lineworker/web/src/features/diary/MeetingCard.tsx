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
 * **"✉ Email participants" is live as of Story 4.3.** It shipped disabled in
 * 4.1 behind a `TooltipProvider`/`Tooltip`/`TooltipTrigger` wrapper with a
 * `tabIndex={0}` span, a `title`, an sr-only reason and an `aria-describedby`
 * pointing at it — the house's seam pattern, and enabling the control is the
 * *deletion* of all of it. What is left is an ordinary button that hands the
 * meeting to `onEmail`, disabled only while a meeting command is in flight like
 * every other action on the card. Full variant only: the compact card in the
 * today's-meetings summary stays a two-action card (✓ Done · Open Claim), which
 * is now a real guard rather than a seam echo.
 *
 * **Delete asks first, inline** (Story 4.3). There is no `update_meeting`, so
 * delete-and-recreate is the sanctioned correction path and a mis-click deletes
 * an audited PHI row with no undo — `deferred-work.md` assigned the two-step to
 * whoever built the feedback primitive, and that is this story. The first click
 * swaps the button for "Delete?" beside "Cancel"; nothing is sent until the
 * second. No `confirm()`, no dialog, no new primitive (NFR-3, UX-DR11).
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
import { useState } from "react";

import type { Meeting } from "@/api/meetings";

import {
  MEETING_STATUS_GLYPH,
  MEETING_STATUS_TONE,
  MEETING_TYPE_LABEL,
  PARTICIPANT_TAG_LABEL,
  PARTICIPANT_TAG_TONE,
} from "./labels";

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
  onEmail,
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
   * with no Delete and no ✉. Deleting from a one-day summary is a destructive
   * action a long way from the list that shows what else is scheduled, and the
   * composer belongs where the meeting's whole context is — the Meetings
   * sub-tab. Both absences shipped as seam echoes in 4.1 and 4.2 and are real
   * guards now that the ✉ works.
   */
  compact?: boolean;
  onComplete: (meeting: Meeting) => void;
  onDelete: (meeting: Meeting) => void;
  /**
   * Open the composer pre-filled from this meeting (Story 4.3, AC 5).
   *
   * Required rather than optional, unlike `onOpenClaim`: the full card always
   * renders the ✉, so a consumer that forgot the prop would ship a dead click.
   * The compact variant renders no ✉ at all and passes a `() => {}` that is
   * honest about there being nothing to do — `onDelete`'s arrangement.
   */
  onEmail: (meeting: Meeting) => void;
  /** Drive the workspace to this meeting's claim. Compact cards only. */
  onOpenClaim?: (claimId: string) => void;
}) {
  const claimId = meeting.claimId;
  /**
   * Whether Delete has been asked once — the inline two-step (Story 4.3).
   *
   * Local to the card, because it is about *this* control on *this* row: a
   * confirmation that outlived the card, or followed the handler onto the next
   * one, would be worse than none. Every other action on the card lowers it in
   * its own handler, so a handler who thought better of it and pressed ✓ or ✉
   * instead does not leave an armed Delete behind them.
   */
  const [confirming, setConfirming] = useState(false);

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
            onClick={() => {
              // Any other action disarms Delete — see `confirming`.
              setConfirming(false);
              onComplete(meeting);
            }}
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
              // **Disabled while a meeting command is in flight**, with the ✓
              // and the Delete beside it. It was the one action on any card
              // without this, and it is not merely an inconsistency: the
              // consumer's `onOpenClaim` calls `clearFeedback()`, which resets
              // the in-flight mutation — so clicking it mid-✓ turned "Saving…"
              // back into "✓ Done" while the request was still outstanding, and
              // the handler had a button that looked ready and a write they
              // could no longer see the outcome of.
              disabled={busy}
              className={`${ACTION_CLASS} border-steel bg-steel-soft text-steel hover:bg-surface`}
            >
              Open Claim
            </button>
          )
        ) : (
          <>
            <button
              type="button"
              data-testid="meeting-email"
              onClick={() => {
                setConfirming(false);
                onEmail(meeting);
              }}
              // Disabled only while a meeting command is in flight, with the ✓
              // and the Delete beside it. Story 4.1's seam wrapper — the
              // tooltip, the `tabIndex` span, the `title` and the sr-only
              // reason — is deleted rather than adjusted; there is nothing left
              // to explain about a control that works.
              disabled={busy}
              className={`${ACTION_CLASS} border-steel bg-steel-soft text-steel hover:bg-surface disabled:cursor-not-allowed`}
            >
              ✉ Email participants
            </button>

            {/* **The two-step, inline.** The first press arms; the second is
                what calls the command. Two separate controls rather than one
                that changes meaning under the pointer, so the confirmation and
                the way out are both reachable and neither is where "Delete"
                just was. */}
            {confirming ? (
              <>
                <button
                  type="button"
                  data-testid="meeting-delete-confirm"
                  onClick={() => {
                    setConfirming(false);
                    onDelete(meeting);
                  }}
                  disabled={busy}
                  className={`${ACTION_CLASS} border-error bg-error text-white hover:opacity-90`}
                >
                  {/* No in-flight label here, and there cannot be one: the
                      handler above lowers `confirming` before it calls
                      `onDelete`, so this pair unmounts in the same commit the
                      mutation starts and a `deleting` branch on it was
                      unreachable. The plain Delete below is what renders
                      "Deleting…", which is where the handler is looking. */}
                  Delete?
                </button>
                <button
                  type="button"
                  data-testid="meeting-delete-cancel"
                  onClick={() => setConfirming(false)}
                  // **Never disabled.** `busy` is list-wide — `MeetingsSubTab`
                  // hands every card the same flag — so arming Delete on one
                  // meeting and ticking ✓ Done on another left an armed
                  // destructive control that could not be lowered until somebody
                  // else's command settled. Cancel sends nothing and touches no
                  // row; the only thing it can do is make the card safer.
                  className={`${ACTION_CLASS} border-border bg-surface-2 text-muted-text hover:bg-surface`}
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                data-testid="meeting-delete"
                onClick={() => setConfirming(true)}
                disabled={busy}
                className={`${ACTION_CLASS} border-border bg-surface-2 text-muted-text hover:bg-surface`}
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
            )}
          </>
        )}
      </div>

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
