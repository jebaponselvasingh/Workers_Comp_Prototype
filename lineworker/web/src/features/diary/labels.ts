/**
 * Display labels for the diary's snake_case enums — UI-owned, by convention.
 *
 * Its own module rather than more entries in `claim-detail/labels.ts` for the
 * reason the router is its own file: these are the diary aggregate's
 * vocabularies, read by the right pane, and Stories 4.2 and 4.3 add their own
 * beside them.
 *
 * Every map is `Record<Enum, string>` rather than a lookup with a fallback, so
 * a new enum member fails the build here — beside the other labels, where
 * somebody can write one — instead of rendering `undefined` in a select.
 */
import type { MeetingParticipant, MeetingStatus, MeetingType } from "@/api/meetings";

/**
 * The ten scheduler options, in the wire enum's order — which is the modal's.
 *
 * Copied from the prototype's `<option>` list verbatim, em dashes included:
 * these are the words a handler picks between, and "3-Point Contact — Initial"
 * is the industry's phrasing rather than something to tidy.
 * [Source: docs/Workers_Comp_Prototype.html lines 541-551]
 */
export const MEETING_TYPE_LABEL: Record<MeetingType, string> = {
  three_point_contact_initial: "3-Point Contact — Initial",
  rtw_conference: "RTW Conference",
  ncm_care_coordination: "NCM Care Coordination",
  ime_preparation: "IME Preparation",
  settlement_discussion: "Settlement Discussion",
  physician_consultation: "Physician Consultation",
  employer_accommodation_review: "Employer Accommodation Review",
  litigation_prep: "Litigation Prep",
  claim_review_supervisor: "Claim Review — Supervisor",
  other: "Other",
};

/**
 * The order the ten render in. Declared rather than derived from the label
 * map's key order, because object key order is a language detail and the
 * select's order is a design decision (it is the prototype's, which is roughly
 * the order a claim encounters them).
 */
export const MEETING_TYPE_ORDER: readonly MeetingType[] = [
  "three_point_contact_initial",
  "rtw_conference",
  "ncm_care_coordination",
  "ime_preparation",
  "settlement_discussion",
  "physician_consultation",
  "employer_accommodation_review",
  "litigation_prep",
  "claim_review_supervisor",
  "other",
];

/**
 * The checkbox grid's six labels, with the prototype's emoji.
 *
 * Longer than the card's tags on purpose: a checkbox is read once, while
 * choosing, so "Nurse Case Manager" is worth the width; a participant tag is
 * scanned on every card, so it says "NCM". Two labels for one token is what
 * the Enums convention buys — a token that carried either wording would make
 * the tag and the checkbox two different vocabularies.
 * [Source: docs/Workers_Comp_Prototype.html lines 555-562]
 */
export const PARTICIPANT_LABEL: Record<MeetingParticipant, string> = {
  employee: "👤 Employee",
  employer_hr: "🏭 Employer HR",
  ncm: "🩺 Nurse Case Manager",
  treating_physician: "👨‍⚕️ Treating Physician",
  supervisor: "👔 My Supervisor",
  attorney: "⚖️ Attorney",
};

/** The short form, for a card's participant tags. */
export const PARTICIPANT_TAG_LABEL: Record<MeetingParticipant, string> = {
  employee: "Employee",
  employer_hr: "Employer HR",
  ncm: "NCM",
  treating_physician: "Physician",
  supervisor: "Supervisor",
  attorney: "Attorney",
};

/**
 * The checkbox grid's order, left to right — the prototype's.
 *
 * It is also the wire enum's order, which is not a coincidence: the command
 * stores participants in the vocabulary's order so that two identical meetings
 * cannot render their tags shuffled, and that vocabulary is this list.
 */
export const PARTICIPANT_ORDER: readonly MeetingParticipant[] = [
  "employee",
  "employer_hr",
  "ncm",
  "treating_physician",
  "supervisor",
  "attorney",
];

/**
 * The two accent classes the prototype gives NCM and Attorney tags.
 *
 * A record over the whole enum rather than a lookup with a default, so a
 * seventh participant is a build error here rather than an unstyled chip.
 * [Source: docs/Workers_Comp_Prototype.html lines 338-340]
 */
export const PARTICIPANT_TAG_TONE: Record<MeetingParticipant, string> = {
  employee: "bg-steel-soft text-steel",
  employer_hr: "bg-steel-soft text-steel",
  ncm: "bg-ok-soft text-ok",
  treating_physician: "bg-steel-soft text-steel",
  supervisor: "bg-steel-soft text-steel",
  attorney: "bg-error-soft text-error",
};

/**
 * The glyph in front of a card's title, by the **server's** status.
 *
 * `📅` for a meeting still ahead and `✓` for one behind or ticked — the
 * prototype's own pair. Keyed by `status` rather than by `isDone`, because the
 * status is the derived answer (AD-10) and a card that combined `isDone` with
 * a date comparison would be the second computer the registry exists to
 * prevent.
 */
export const MEETING_STATUS_GLYPH: Record<MeetingStatus, string> = {
  upcoming: "📅",
  done: "✓",
};

/** The card's border treatment, by the server's status. */
export const MEETING_STATUS_TONE: Record<MeetingStatus, string> = {
  upcoming: "border-l-[3px] border-l-brand",
  done: "opacity-60",
};
