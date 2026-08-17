/**
 * The 📅 Schedule Meeting modal (Story 4.1, AC 1 and AC 2, UX-DR10).
 *
 * The prototype's `#meetingModal` (lines 536-579) with a command behind it:
 * the ten types, the read-only linked claim in the steel-blue treatment, a
 * required date defaulted to today, a time defaulted to 10:00, the six
 * participant checkboxes with Employee pre-checked, an agenda and a location.
 *
 * **Radix `Dialog`, for `LineItemDialog`'s reasons** — focus-trapped,
 * `role="dialog"`, closable by ✕, backdrop and Escape. The prototype's own
 * modal closes on ✕ and backdrop but not Escape and traps no focus (UX-DR11).
 * Open state is *derived* from the `open` prop, and every close path funnels
 * through `close()` — the three Radix ones by way of `onOpenChange`, and
 * Cancel by calling it directly, because Radix does not fire `onOpenChange`
 * for a prop change driven from outside and Cancel is the likeliest close of
 * the four. One place to reset local state, and one place to miss it.
 *
 * **The date refusal is inline and it is never a native dialog** (AC 2,
 * NFR-3). The prototype calls `alert('Please select a date.')`, which blocks
 * the page and cannot be styled, announced or dismissed by anything but a
 * click. Here the check is a `useState` message rendered at the field, and the
 * server's own 422 lands in the same place through `feedbackFromError` — so a
 * handler who somehow gets past the client check reads the refusal where they
 * were already looking.
 *
 * **Hand-rolled `useState`, no react-hook-form and no zod**, which is this
 * codebase's form idiom (`AddInjuryPopover` is the exemplar). Six controls and
 * one required field do not earn a schema library, and adding one would make
 * this the only form in the app that has one.
 *
 * **The draft is seeded at mount, and the caller re-mounts on every open.**
 * `MeetingsSubTab` keys this component on `DiaryNav`'s `schedulerSession`, so
 * there is no effect here re-seeding state when `open` flips — which is both
 * the cheaper arrangement and the one `react-hooks/set-state-in-effect`
 * allows. The consequence is worth having in its own right: a handler who
 * cancels halfway through and re-opens gets an empty form, where the prototype
 * clears only `notes` and `location` (line 1878) and pre-fills the next
 * meeting from the last one.
 *
 * **The linked claim cannot be retargeted, and since the 4.2 follow-up review
 * that is true of the *value* as well as of the control.** It is a `readonly`
 * input showing `WC-nnnn — Worker Name`, exactly as the prototype has it,
 * because the modal opens *from* a claim: a select here would publish "schedule
 * against any claim in my book" as an interaction nothing in the story asks
 * for, and the server would then have to refuse choices the UI had offered. But
 * the submit used to read the live `?claim=` prop rather than what the field had
 * been showing, so the workspace moving underneath an open modal — auto-select
 * landing, a Back or a Forward — retargeted it silently. The claim is captured
 * into the draft at mount; see `Draft.claimId`.
 *
 * **And a save with no claim is refused here** rather than written as an orphan
 * row — see `MISSING_CLAIM_MESSAGE`.
 */
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";

import {
  MEETING_LOCATION_LENGTH_CAP,
  MEETING_NOTES_LENGTH_CAP,
} from "@/api/fieldLimits";
import type { MeetingParticipant, MeetingType, NewMeeting } from "@/api/meetings";
import { useMeetingWriteInFlight, useScheduleMeeting } from "@/api/meetings";
import { feedbackFromError } from "@/features/claim-detail/useInlineEdits";
import type { FieldFeedback } from "@/features/claim-detail/InlineEditField";
import { todayIso } from "@/lib/clock";

import {
  MEETING_TYPE_LABEL,
  MEETING_TYPE_ORDER,
  PARTICIPANT_LABEL,
  PARTICIPANT_ORDER,
} from "./labels";

/** The shared control class every inline edit in this console already uses. */
const FIELD_CLASS =
  "w-full rounded-[3px] border border-border bg-surface px-[5px] py-[3px] text-[11px] text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60";

const LABEL_CLASS =
  "mb-[3px] block font-display text-[9.5px] font-bold tracking-[0.3px] text-muted-text uppercase";

/**
 * The message the missing-date check shows.
 *
 * A constant so the vitest file asserts the same string the component renders
 * rather than a paraphrase of it — and so the sentence is one edit away from
 * being reworded, which is what it is: prose, not logic.
 */
export const MISSING_DATE_MESSAGE = "Pick a date for this meeting.";

/**
 * The refusal for a save with no claim behind it.
 *
 * The only client check used to be the empty date, so saving while the
 * read-only Linked Claim field said "No claim selected" answered 201 with
 * `claimId: null` — an orphan meeting that can never be attached to anything,
 * because there is no `update_meeting` and the only correction is
 * delete-and-recreate. The column is nullable so the *server* accepts one
 * (a touchpoint genuinely about no case file is a real thing to record), but
 * this modal has no control for choosing a claim, so from here an untagged
 * meeting is never a decision — it is the selection not having arrived.
 */
export const MISSING_CLAIM_MESSAGE =
  "Select a case before scheduling — this meeting has no claim to attach to.";

/** The id the alert carries, and the only thing that ever points at it. */
const ERROR_ID = "meeting-scheduler-error";

/**
 * A refusal, and the control it is about.
 *
 * The field is carried because `aria-invalid` is a statement about *one* input
 * and most refusals are not: a 422 about the agenda's length, a 404 for a claim
 * that left the book, a network failure — all three used to mark the **date**
 * invalid and point its `aria-describedby` at the shared alert, which tells a
 * screen-reader user to go and fix the one field that was fine. `null` is the
 * ordinary case: the message is rendered where it always was, and no input
 * claims it.
 */
interface Refusal {
  field: "meetingDate" | "meetingClaim" | "meetingNotes" | "meetingLocation" | null;
  feedback: FieldFeedback;
}

/**
 * Today, as an `<input type="date">` wants it — the **handler's** today.
 *
 * `lib/clock.ts` owns the definition since Story 4.2, because the Notes
 * sub-tab needs the identical string to send as `day` and two local
 * definitions of "what day is it" would be one bug away from a modal that
 * pre-fills a date the summary above it does not consider today.
 */
function today(): string {
  return todayIso(new Date());
}

/**
 * The form's own state — everything the modal collects, in one object.
 *
 * One `useState` over a record rather than seven, so `reset()` is one call and
 * cannot leave a field behind. The prototype clears only `notes` and
 * `location` after a save (line 1878) and leaves the type, the time and every
 * checkbox as the last handler left them, which is a form that quietly
 * pre-fills the next meeting from the previous one.
 */
interface Draft {
  meetingType: MeetingType;
  meetingDate: string;
  meetingTime: string;
  location: string;
  notes: string;
  participants: ReadonlySet<MeetingParticipant>;
  /**
   * The claim this meeting will be linked to — **captured at mount**.
   *
   * The docstring above says the linked claim "cannot be retargeted", and it
   * was not true: the submit read the `claimId` prop, which is `?claim=` as it
   * stands *then*. Open the scheduler before the queue's auto-select lands, or
   * press Back or Forward while it is open, and the read-only field silently
   * changes under the handler — and the meeting is written against a claim they
   * never chose, into a table with no `update_meeting`. Story 4.2 made exactly
   * this capture for the note draft and for the same reason; this is the modal's
   * version, and the capture is what makes the read-only field honest.
   */
  claimId: string | null;
}

function emptyDraft(claimId: string | null): Draft {
  return {
    claimId,
    // The prototype's first option is the default because it is first, not
    // because it is a sensible default; kept, so the modal opens the way the
    // one this replaces did.
    meetingType: "three_point_contact_initial",
    meetingDate: today(),
    meetingTime: "10:00",
    location: "",
    notes: "",
    // Employee pre-checked, per UX-DR10 — the one participant almost every
    // touchpoint has.
    participants: new Set<MeetingParticipant>(["employee"]),
  };
}

export function MeetingSchedulerDialog({
  open,
  claimId,
  workerName,
  onClose,
  onScheduled,
}: {
  open: boolean;
  /** The selected claim, or `null` when the workspace has none. */
  claimId: string | null;
  workerName: string | null;
  onClose: () => void;
  /** Called after a successful save, so the list can announce it. */
  onScheduled: () => void;
}) {
  const schedule = useScheduleMeeting();
  const busy = useMeetingWriteInFlight();
  // The lazy initialiser runs once per mount, and `MeetingsSubTab` keys this
  // component on `schedulerSession` so every *open* is a mount — which is what
  // makes "captured at mount" the same thing as "captured when it opened".
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(claimId));
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  /**
   * The one close path, and every affordance routes through it.
   *
   * Cancel used to call `onClose` directly, flipping the `open` prop from the
   * outside — and Radix fires `onOpenChange` only for closes *it* observes, so
   * the likeliest close of all skipped the reset the docstring above promises
   * every close performs. Only the caller's `schedulerSession` key masked it.
   */
  function close(): void {
    setRefusal(null);
    schedule.reset();
    onClose();
  }

  function toggle(participant: MeetingParticipant): void {
    setDraft((current) => {
      const next = new Set(current.participants);
      if (!next.delete(participant)) next.add(participant);
      return { ...current, participants: next };
    });
  }

  function submit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (draft.meetingDate.trim() === "") {
      // AC 2: inline, at the field, never `alert()`. This is the one refusal
      // that really is about the date, so it is the one that marks it invalid.
      setRefusal({
        field: "meetingDate",
        feedback: { kind: "invalid", message: MISSING_DATE_MESSAGE },
      });
      return;
    }
    if (draft.claimId === null) {
      // The second client check, and the second thing this modal cannot
      // produce a usable row without. See `MISSING_CLAIM_MESSAGE`.
      setRefusal({
        field: "meetingClaim",
        feedback: { kind: "invalid", message: MISSING_CLAIM_MESSAGE },
      });
      return;
    }
    setRefusal(null);

    const body: NewMeeting = {
      // The claim the modal *opened* against and has been showing, never the
      // live selection — see `Draft.claimId`.
      claimId: draft.claimId,
      meetingType: draft.meetingType,
      meetingDate: draft.meetingDate,
      meetingTime: draft.meetingTime === "" ? null : draft.meetingTime,
      location: draft.location.trim() === "" ? null : draft.location.trim(),
      notes: draft.notes.trim() === "" ? null : draft.notes.trim(),
      // The wire order does not matter — the command re-orders into the
      // vocabulary's own — but a stable order here keeps the request body
      // readable in a network log.
      participants: PARTICIPANT_ORDER.filter((member) => draft.participants.has(member)),
    };

    schedule.mutate(body, {
      onSuccess: () => {
        onScheduled();
        onClose();
      },
      // The server's 422 and any other refusal land in the same alert the
      // client-side check uses, through the shared mapper — so a handler never
      // has to look in two places for the same kind of answer. They do *not*
      // borrow the date's `aria-invalid`: none of them is about the date.
      onError: (error) => setRefusal({ field: null, feedback: feedbackFromError(error) }),
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        // Every close — the ✕, Escape, the backdrop, Cancel — arrives here.
        // Except while the POST is in flight: closing mid-save would drop the
        // `onSuccess` announcement of a row the server has already written, so
        // the dialog holds until the command settles and the controls below
        // are disabled to say so.
        if (busy) return;
        close();
      }}
    >
      <DialogContent data-testid="meeting-scheduler" className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-[13px]">📅 Schedule Meeting</DialogTitle>
          <DialogDescription className="text-[11px]">
            A scheduled meeting is recorded on this claim's diary. No calendar invitation is
            sent.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="grid grid-cols-2 gap-[10px]">
          <div>
            <label htmlFor="meeting-type" className={LABEL_CLASS}>
              Meeting Type
            </label>
            <select
              id="meeting-type"
              data-testid="scheduler-type"
              value={draft.meetingType}
              disabled={busy}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  meetingType: event.target.value as MeetingType,
                }))
              }
              className={FIELD_CLASS}
            >
              {MEETING_TYPE_ORDER.map((type) => (
                <option key={type} value={type}>
                  {MEETING_TYPE_LABEL[type]}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="meeting-claim" className={LABEL_CLASS}>
              Linked Claim
            </label>
            {/* Read-only, in the steel-blue treatment the prototype gives it
                and `STAGE_PILL` gives every non-editable claim reference. */}
            <input
              id="meeting-claim"
              data-testid="scheduler-claim"
              data-claim-id={draft.claimId ?? ""}
              readOnly
              aria-invalid={refusal?.field === "meetingClaim"}
              aria-describedby={refusal?.field === "meetingClaim" ? ERROR_ID : undefined}
              // Three states, not two, mirroring `CopilotPane`'s sub-line: the
              // case file is a separate request, so between opening the modal
              // and its arrival — or if it fails outright — `workerName` is
              // null, and `${claimId} — ` with nothing after it reads as a
              // truncated field rather than as a claim reference.
              //
              // The **captured** id, so what is displayed is what will be sent.
              // The worker's name still comes from the live prop: it is a lookup
              // of the same claim arriving a moment later, not a second claim.
              value={
                draft.claimId === null
                  ? "No claim selected"
                  : workerName === null || draft.claimId !== claimId
                    ? draft.claimId
                    : `${draft.claimId} — ${workerName}`
              }
              className={`${FIELD_CLASS} bg-steel-soft font-semibold text-steel`}
            />
          </div>

          <div>
            <label htmlFor="meeting-date" className={LABEL_CLASS}>
              Date
            </label>
            <input
              id="meeting-date"
              data-testid="scheduler-date"
              type="date"
              value={draft.meetingDate}
              disabled={busy}
              // Scoped to the refusals that are actually about this input —
              // see `Refusal`.
              aria-invalid={refusal?.field === "meetingDate"}
              aria-describedby={refusal?.field === "meetingDate" ? ERROR_ID : undefined}
              onChange={(event) =>
                setDraft((current) => ({ ...current, meetingDate: event.target.value }))
              }
              className={FIELD_CLASS}
            />
          </div>

          <div>
            <label htmlFor="meeting-time" className={LABEL_CLASS}>
              Time
            </label>
            <input
              id="meeting-time"
              data-testid="scheduler-time"
              type="time"
              value={draft.meetingTime}
              disabled={busy}
              onChange={(event) =>
                setDraft((current) => ({ ...current, meetingTime: event.target.value }))
              }
              className={FIELD_CLASS}
            />
          </div>

          <fieldset className="col-span-2">
            <legend className={LABEL_CLASS}>Participants</legend>
            <div className="grid grid-cols-2 gap-[6px]">
              {PARTICIPANT_ORDER.map((participant) => (
                <label
                  key={participant}
                  htmlFor={`participant-${participant}`}
                  className="flex items-center gap-[6px] text-[11px] text-text"
                >
                  <Checkbox
                    id={`participant-${participant}`}
                    data-testid="scheduler-participant"
                    data-participant={participant}
                    checked={draft.participants.has(participant)}
                    disabled={busy}
                    onCheckedChange={() => toggle(participant)}
                  />
                  {PARTICIPANT_LABEL[participant]}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="col-span-2">
            <label htmlFor="meeting-notes" className={LABEL_CLASS}>
              Notes / Agenda
            </label>
            <Textarea
              id="meeting-notes"
              data-testid="scheduler-notes"
              value={draft.notes}
              disabled={busy}
              // **The column's width, declared on the control.** Neither free
              // text field had a cap, so a pasted agenda past 2,000 characters
              // was refused by Pydantic with the generic "The request body or
              // parameters failed validation." — no field named, no limit
              // shown, and nothing on screen marked invalid. 4.2's note
              // textarea already declares its cap; this is that, back-ported to
              // the two controls that needed it.
              maxLength={MEETING_NOTES_LENGTH_CAP}
              aria-invalid={refusal?.field === "meetingNotes"}
              aria-describedby={refusal?.field === "meetingNotes" ? ERROR_ID : undefined}
              placeholder="Meeting agenda, topics to cover, documents needed…"
              onChange={(event) =>
                setDraft((current) => ({ ...current, notes: event.target.value }))
              }
              className={`${FIELD_CLASS} min-h-[70px]`}
            />
            <p
              data-testid="scheduler-notes-length"
              className="mt-[2px] text-right text-[9.5px] text-faint"
            >
              {draft.notes.length} of {MEETING_NOTES_LENGTH_CAP} characters
            </p>
          </div>

          <div className="col-span-2">
            <label htmlFor="meeting-location" className={LABEL_CLASS}>
              Location / Link
            </label>
            <input
              id="meeting-location"
              data-testid="scheduler-location"
              value={draft.location}
              disabled={busy}
              maxLength={MEETING_LOCATION_LENGTH_CAP}
              aria-invalid={refusal?.field === "meetingLocation"}
              aria-describedby={refusal?.field === "meetingLocation" ? ERROR_ID : undefined}
              placeholder="e.g. Teams call, plant office, adjuster office…"
              onChange={(event) =>
                setDraft((current) => ({ ...current, location: event.target.value }))
              }
              className={FIELD_CLASS}
            />
            <p
              data-testid="scheduler-location-length"
              className="mt-[2px] text-right text-[9.5px] text-faint"
            >
              {draft.location.length} of {MEETING_LOCATION_LENGTH_CAP} characters
            </p>
          </div>

          {refusal !== null && (
            <p
              id={ERROR_ID}
              role="alert"
              data-testid="scheduler-error"
              className="col-span-2 text-[11px] font-semibold text-error"
            >
              {refusal.feedback.message}
            </p>
          )}

          <div className="col-span-2 flex justify-end gap-[6px]">
            <button
              type="button"
              data-testid="scheduler-cancel"
              // Through `close`, not `onClose`: the reset lives there, and a
              // Cancel that flipped the prop from outside skipped it.
              onClick={close}
              // Disabled while the save is in flight, with the ✕, Escape and
              // the backdrop, so a cancel cannot swallow the announcement of a
              // meeting that was created.
              disabled={busy}
              className="rounded border border-border bg-surface-2 px-[10px] py-[5px] text-[11px] font-semibold text-muted-text hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="scheduler-save"
              // Disabled while *any* meeting command is in flight, relabelled
              // by this mutation's own `isPending` — conflating the two was a
              // logged defect on the checklist card (code review, 2026-08-17).
              disabled={busy}
              className="rounded border border-brand bg-brand px-[10px] py-[5px] text-[11px] font-bold text-white hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50"
            >
              {schedule.isPending ? "Scheduling…" : "📅 Schedule Meeting"}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
