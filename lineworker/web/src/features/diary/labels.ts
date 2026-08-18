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
import type { EmailPriority, EmailRecipient } from "@/api/emails";
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

// --- Story 4.3: the email composer's own vocabularies ---------------------

/**
 * The composer's six recipient checkboxes, with the prototype's emoji.
 *
 * **The same six tokens as `PARTICIPANT_LABEL` above, and deliberately not the
 * same strings.** The prototype's meeting modal says "👔 My Supervisor" and its
 * email modal says "👔 Supervisor" — the difference is real: a meeting is
 * something the handler attends, so the possessive reads naturally, and an email
 * is addressed to a role. Labels are UI-owned, so each modal keeps its own
 * wording over one shared token; a token that carried either phrasing would make
 * the two modals two vocabularies.
 * [Source: docs/Workers_Comp_Prototype.html lines 588-593]
 */
export const RECIPIENT_LABEL: Record<EmailRecipient, string> = {
  employee: "👤 Employee",
  employer_hr: "🏭 Employer HR",
  ncm: "🩺 Nurse Case Manager",
  treating_physician: "👨‍⚕️ Treating Physician",
  supervisor: "👔 Supervisor",
  attorney: "⚖️ Attorney",
};

/**
 * The checkbox grid's order — the prototype's, which is also the wire enum's.
 *
 * **`PARTICIPANT_ORDER` itself, rather than a second list with the same six
 * tokens in it.** It shipped as a byte-identical copy with a comment explaining
 * that the two agree, which is the shape of duplication that decays: both
 * `normalise_recipients` and `normalise_participants` store their sets in *the*
 * vocabulary's order so that two identical letters cannot render their chips
 * shuffled, and that argument holds only while there is one order. Nothing
 * enforced the equality, so a re-ordered checkbox grid on either side would have
 * silently made "the order is the enum's" true in one modal and false in the
 * other. The *labels* legitimately differ — see `RECIPIENT_LABEL` — and that is
 * exactly the axis on which these two vocabularies are allowed to disagree.
 */
export const RECIPIENT_ORDER: readonly EmailRecipient[] = PARTICIPANT_ORDER;

/**
 * Normal · High · Urgent — the priority select's three options.
 * [Source: docs/Workers_Comp_Prototype.html line 612]
 */
export const EMAIL_PRIORITY_LABEL: Record<EmailPriority, string> = {
  normal: "Normal",
  high: "High",
  urgent: "Urgent",
};

/**
 * The order the three render in, which is the prototype's and the enum's.
 *
 * Declared rather than derived from the label map's key order, `MEETING_TYPE_ORDER`'s
 * rule: object key order is a language detail and a select's order is a design
 * decision.
 */
export const EMAIL_PRIORITY_ORDER: readonly EmailPriority[] = ["normal", "high", "urgent"];

/**
 * The accent a logged email's card carries for its priority.
 *
 * `normal` is deliberately unaccented — most email is normal, and a chip on
 * every card would say nothing. High and Urgent take the warn and error pairs,
 * which is the story's own instruction and the console's standing semantics.
 *
 * A record over the whole enum rather than a lookup with a default, so a fourth
 * priority is a build error here rather than an unstyled chip.
 */
export const EMAIL_PRIORITY_TONE: Record<EmailPriority, string> = {
  normal: "bg-surface-2 text-muted-text",
  high: "bg-warn-soft text-warn",
  urgent: "bg-error-soft text-error",
};
