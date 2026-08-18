/**
 * The ✉ Email to Stakeholders modal (Story 4.3, AC 1-3 and AC 5, UX-DR10).
 *
 * The prototype's `#emailModal` (lines 580-621) with a command behind it: the
 * six recipient checkboxes with Employee pre-checked, the row of six quick
 * templates, a required subject, a ~180px body, the read-only claim reference in
 * the steel-blue treatment, a Normal/High/Urgent priority, and Cancel beside
 * "✉ Send Email (logged)".
 *
 * **Radix `Dialog`, for `MeetingSchedulerDialog`'s reasons** — focus-trapped,
 * `role="dialog"`, closable by ✕, backdrop and Escape, with every close path
 * funnelled through one `close()` so there is one place to reset local state.
 *
 * **The refusals are inline and none of them is a native dialog** (AC 3,
 * NFR-3, UX-DR11). The prototype calls `alert('Please enter a subject.')`; here
 * an empty subject renders `role="alert"` at the subject field with
 * `aria-invalid` and `aria-describedby`, an empty recipient set does the same at
 * the six checkboxes — *not* at the `<fieldset>`, which maps to `role="group"`
 * and where `aria-invalid` is inert, as it shipped — and the server's own 422
 * lands in the same place through `feedbackFromError`. Only the *completed* send raises a toast — a message that
 * disappears after a few seconds is a worse answer than one that stays at the
 * control the handler has to fix.
 *
 * **The SPA merges nothing.** A template button sets `fill` and this component
 * asks the server for the merged letter (AD-1); the subject, body and recipient
 * set that come back are what the form shows. There is no `replace()` here, no
 * placeholder syntax, and no claim field interpolated into prose anywhere in
 * `features/diary`.
 *
 * **A base plus edits, rather than one seeded draft — and it is the no-effect
 * rule that decides it.** The scheduler seeds a `useState` at mount because
 * everything it needs is a prop; here the initial subject and body arrive from
 * the network *after* mount, and copying them into state when they land is
 * precisely the `setState`-in-effect shape `react-hooks/set-state-in-effect`
 * refuses. So the server's answer is the **base**, the handler's typing is a
 * sparse **edits** record over it, and every field is derived during render.
 * Choosing a template is then one event handler that sets the new fill and drops
 * the edits — which is also exactly what "the recipient set is replaced by the
 * template's, not unioned with what is ticked" means, expressed as data rather
 * than as a sequence of writes.
 *
 * **The claim cannot be retargeted, and it is captured at mount** — the
 * scheduler's ruling, for the same reason: the workspace selection can move
 * under an open modal (auto-select landing, Back, Forward, "Open Claim" on a
 * card in the pane behind), and what the read-only field has been showing must
 * be what the send carries. A merge overrides it with the claim the *server*
 * resolved, which is the only value that can be right for a meeting's
 * confirmation letter.
 *
 * **A composition with no claim is legal here, unlike a meeting.** The column is
 * nullable by the ERD's `CLAIM |o--o{ EMAIL_LOG` and free composition is a real
 * thing to log; what is refused is a *template* without a claim, because the six
 * are claim-aware by definition and the prototype's claim-less render produced
 * letters full of holes. The buttons are disabled with the reason stated, which
 * is the house's seam pattern rather than a new one.
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

import { EMAIL_BODY_MAX, EMAIL_SUBJECT_MAX } from "@/api/fieldLimits";
import type { EmailPriority, EmailRecipient, NewEmail } from "@/api/emails";
import {
  isEmailWrittenButUnreadable,
  useEmailTemplates,
  useEmailWriteInFlight,
  useMeetingEmailDraft,
  useMergedTemplate,
  useSendEmail,
} from "@/api/emails";
import { feedbackFromError } from "@/features/claim-detail/useInlineEdits";
import type { FieldFeedback } from "@/features/claim-detail/InlineEditField";

import type { ComposerPrefill } from "./DiaryNav";
import {
  EMAIL_PRIORITY_LABEL,
  EMAIL_PRIORITY_ORDER,
  RECIPIENT_LABEL,
  RECIPIENT_ORDER,
} from "./labels";

/** The shared control class every inline edit in this console already uses. */
const FIELD_CLASS =
  "w-full rounded-[3px] border border-border bg-surface px-[5px] py-[3px] text-[11px] text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60";

const LABEL_CLASS =
  "mb-[3px] block font-display text-[9.5px] font-bold tracking-[0.3px] text-muted-text uppercase";

const SECTION_CLASS =
  "mb-[5px] font-display text-[9.5px] font-bold tracking-[0.3px] text-muted-text uppercase";

/**
 * The message an empty subject shows.
 *
 * A constant so the vitest file asserts the string the component renders rather
 * than a paraphrase of it — `MISSING_DATE_MESSAGE`'s rule, and so the sentence
 * is one edit away from being reworded, which is what it is: prose, not logic.
 */
export const MISSING_SUBJECT_MESSAGE = "Write a subject before sending this email.";

/** The refusal for a send addressed to nobody. */
export const NO_RECIPIENT_MESSAGE =
  "Choose at least one recipient — an email addressed to nobody records nothing about who was told.";

/**
 * Why the six template buttons are disabled with no claim selected.
 *
 * Stated rather than merely greyed, which is the whole of the house's seam
 * pattern: NFR-3 forbids a dead click, and a control whose explanation only a
 * pointer can reach is a dead click for everybody else. Duplicated as the
 * button's `title` and announced through `aria-describedby`.
 */
export const NO_CLAIM_TEMPLATE_REASON =
  "Select a case first — a quick template merges that claim's details.";

/** The failure a **template** merge that did not come back is reported with. */
export const MERGE_FAILED_MESSAGE =
  "That template could not be merged just now. Try again, or write the email by hand.";

/**
 * The same failure for a **meeting's** letter, which is a different sentence
 * because it is a different event.
 *
 * "That template could not be merged" is a lie in the convert-to-email path: the
 * handler pressed ✉ on a meeting card and chose no template at all, so the one
 * message told them to retry something they had not done and pointed at a row of
 * buttons that has nothing to do with the failure.
 */
export const DRAFT_FAILED_MESSAGE =
  "That meeting's letter could not be prepared just now. Try again, or write the email by hand.";

/** Shown while the six are on their way — see the button row. */
export const TEMPLATES_LOADING_MESSAGE = "Loading quick templates…";

/**
 * A 200 carrying no templates — a migration applied without its seed, a table
 * somebody emptied.
 *
 * Its own branch rather than an empty row, for `GLOSSARY_EMPTY`'s reason: a
 * heading over nothing reads as a row that failed to draw, and the honest answer
 * names the one thing that still works.
 */
export const TEMPLATES_EMPTY_MESSAGE =
  "No quick templates are available. The email can still be written by hand.";

/** The ids the two alerts carry, and the only things that ever point at them. */
const ERROR_ID = "email-composer-error";
const TEMPLATE_REASON_ID = "email-composer-template-reason";

/**
 * A refusal, and the control it is about.
 *
 * `MeetingSchedulerDialog`'s `Refusal`, for its reason: `aria-invalid` is a
 * statement about *one* input, and most refusals are not — a 404 for a claim
 * that left the book, a network failure, a 422 about the body's length. `null`
 * is the ordinary case: the message renders where it always does and no input
 * claims it.
 */
interface Refusal {
  field: "emailSubject" | "emailRecipients" | "emailBody" | null;
  feedback: FieldFeedback;
}

/**
 * What the form is currently filled *from*.
 *
 * `blank` is an empty letter; `template` and `meeting` each name a server merge.
 * Exactly one of the two queries below is enabled at a time, and the enabled
 * one's `data` is the base every field is derived from.
 */
type Fill =
  | { kind: "blank" }
  | { kind: "template"; templateKey: string }
  | { kind: "meeting"; meetingId: number };

/** The handler's overrides on top of whatever the server filled in. */
interface Edits {
  subject?: string;
  body?: string;
  recipients?: ReadonlySet<EmailRecipient>;
}

/**
 * What a composition holds before anybody types — Employee ticked, and nothing
 * else (UX-DR10: the one recipient almost every letter has).
 */
const BLANK_RECIPIENTS: readonly EmailRecipient[] = ["employee"];

export function EmailComposerDialog({
  open,
  claimId,
  workerName,
  prefill,
  onClose,
  onSent,
}: {
  open: boolean;
  /** The selected claim, or `null` when the workspace has none. */
  claimId: string | null;
  workerName: string | null;
  /** What this open was asked for — see `DiaryNav.ComposerPrefill`. */
  prefill: ComposerPrefill;
  onClose: () => void;
  /** Called after a successful send, with the roles that were addressed. */
  onSent: (recipients: readonly EmailRecipient[]) => void;
}) {
  // **Gated on this modal being open**, which is where the request belongs:
  // `DiaryTab` mounts the composer outside the sub-tab switch (deliberately —
  // ✉ on a meeting card has to work from any sub-tab), so an ungated query
  // fetched the six templates on every workspace load for every handler who
  // never composed anything. See `useEmailTemplates`.
  const templates = useEmailTemplates(open);
  const send = useSendEmail();
  const busy = useEmailWriteInFlight();

  // The lazy initialisers run once per mount, and `EmailsSubTab` keys this
  // component on the composer session — so every *open* is a mount, and
  // "captured at mount" is the same thing as "captured when it opened".
  const [openedClaimId] = useState<string | null>(() => claimId);
  /**
   * The worker's name as it stood when the modal opened — the fallback half of
   * the read-only field, and only the fallback half.
   *
   * The claim is captured; the *name* cannot simply be, because the case file is
   * a separate request and `workerName` is frequently still `null` at mount. So
   * the field prefers the live prop **while the workspace is still showing the
   * claim this composer opened on**, and falls back to this the moment the
   * selection moves — which is what keeps "WC-20017 — " from acquiring somebody
   * else's worker after a navigation behind the modal.
   *
   * The remaining gap is stated rather than hidden: a name that arrived *after*
   * this mount is not in here, so a navigation at that point leaves the field
   * showing the claim id alone. That is a degradation to something true, which
   * is the direction this trade-off has to fail in; keeping it live would fail
   * the other way, and remembering it would need the effect this feature does
   * not have.
   */
  const [openedWorkerName] = useState<string | null>(() => workerName);
  const [fill, setFill] = useState<Fill>(() =>
    prefill.kind === "meeting" ? { kind: "meeting", meetingId: prefill.meetingId } : { kind: "blank" },
  );
  const [edits, setEdits] = useState<Edits>({});
  const [priority, setPriority] = useState<EmailPriority>("normal");
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  // Both hooks are called on every render, as the rules of hooks require, and
  // each gates itself on a null argument — so at most one request is in flight
  // and the other query is inert.
  const templateMerge = useMergedTemplate(
    fill.kind === "template" ? fill.templateKey : null,
    openedClaimId,
  );
  const meetingDraft = useMeetingEmailDraft(fill.kind === "meeting" ? fill.meetingId : null);
  const merge = fill.kind === "template" ? templateMerge : fill.kind === "meeting" ? meetingDraft : null;

  /**
   * The server's answer, or an empty letter — **never something merged here**.
   *
   * `merge.data` survives a template switch through `placeholderData`, so the
   * form keeps the letter it was showing while the next one is fetched rather
   * than blanking for the length of a round trip.
   */
  const base = merge?.data ?? {
    claimId: openedClaimId,
    subject: "",
    body: "",
    recipients: BLANK_RECIPIENTS,
  };

  // Derived during render, which is what keeps this component effect-free —
  // see the module docstring. `??` rather than `||`, so clearing the subject to
  // an empty string is an edit rather than a fall-back to the merged text.
  const subject = edits.subject ?? base.subject;
  const body = edits.body ?? base.body;
  const recipients = edits.recipients ?? new Set(base.recipients);
  /**
   * The claim this email will be logged against.
   *
   * The **merge's**, when there is one: a meeting's confirmation letter is about
   * that meeting's claim, which need not be the one the workspace has selected,
   * and the merged text names it. Otherwise the claim the modal opened on.
   */
  const composedClaimId = base.claimId;
  /**
   * The name the read-only field pairs with the claim, or `null`.
   *
   * The live prop while the workspace is still showing the claim this composer
   * opened on — so a case file that lands *after* the modal opened fills the
   * field in — and the captured one once the selection has moved, so the label
   * never pairs `openedClaimId` with somebody else's worker.
   */
  const shownWorkerName = claimId === openedClaimId ? workerName : openedWorkerName;

  /**
   * A merge is in flight — which disables the templates **and the Send**.
   *
   * Send too, and that is not tidiness: `fill` moves on the click while `base`
   * is still the previous letter (`placeholderData` is what holds it), so a
   * submit inside the round trip logs one template's key against another's
   * subject, body and claim. From a meeting card it is sharper still — `base`
   * is the blank fallback until the draft lands, so the letter would be logged
   * against the *workspace's* claim rather than the meeting's. Both write a row
   * into a table with no edit and no delete.
   */
  const merging = merge?.isFetching ?? false;
  const mergeFailed = merge?.isError ?? false;
  /** Which failure it was — a template the handler picked, or a meeting's letter. */
  const mergeFailedMessage = fill.kind === "meeting" ? DRAFT_FAILED_MESSAGE : MERGE_FAILED_MESSAGE;

  /**
   * The one close path, and every affordance routes through it.
   *
   * Cancel calls it directly rather than flipping the `open` prop from outside:
   * Radix fires `onOpenChange` only for closes *it* observes, so the likeliest
   * close of the four would otherwise skip the reset — `MeetingSchedulerDialog`'s
   * logged defect, not repeated here.
   */
  function close(): void {
    setRefusal(null);
    send.reset();
    onClose();
  }

  /**
   * Choose a quick template — **replace, never merge into** (AC 2).
   *
   * Setting the fill and dropping the edits in one handler is what makes the
   * recipient set become *exactly* the template's: any box the handler had
   * ticked is an edit, and the edits go. The subject and body follow for the
   * same reason, which is the prototype's behaviour and the one the story asks
   * for — a template is a fresh letter, not a patch over the last one.
   */
  function pickTemplate(templateKey: string): void {
    setFill({ kind: "template", templateKey });
    setEdits({});
    setRefusal(null);
  }

  function toggle(recipient: EmailRecipient): void {
    setEdits((current) => {
      const next = new Set(current.recipients ?? base.recipients);
      if (!next.delete(recipient)) next.add(recipient);
      return { ...current, recipients: next };
    });
    if (refusal?.field === "emailRecipients") setRefusal(null);
  }

  function submit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    // The third of the three that must agree about being busy — the fields and
    // the Send button already do, and a form submits on Enter as well as on the
    // button. A second Enter mid-send would log the same letter twice into a
    // table with no version to refuse it and no delete to undo it.
    //
    // `merging` for the same reason one control down: Enter reaches this without
    // touching the Send button, and a letter sent mid-merge is a row whose
    // template key, body and claim came from two different letters.
    if (busy || merging) return;

    const trimmedSubject = subject.trim();
    if (trimmedSubject === "") {
      // AC 3: inline, at the field, never `alert()`. This refusal really is
      // about the subject, so it is one of the two that marks a control invalid.
      setRefusal({
        field: "emailSubject",
        feedback: { kind: "invalid", message: MISSING_SUBJECT_MESSAGE },
      });
      return;
    }

    // In the vocabulary's own order — the command re-orders anyway, and a stable
    // order here keeps the request body readable in a network log.
    const chosen = RECIPIENT_ORDER.filter((member) => recipients.has(member));
    if (chosen.length === 0) {
      setRefusal({
        field: "emailRecipients",
        feedback: { kind: "invalid", message: NO_RECIPIENT_MESSAGE },
      });
      return;
    }
    setRefusal(null);

    const trimmedBody = body.trim();
    const payload: NewEmail = {
      // The claim the modal has been *showing*, never the live selection.
      claimId: composedClaimId,
      // Provenance, not a request to merge: the text has already been merged
      // and possibly edited by hand. `null` for a free composition and for a
      // meeting's letter, which starts from no template at all.
      templateKey: fill.kind === "template" ? fill.templateKey : null,
      subject: trimmedSubject,
      // Empty free text is sent as null rather than "", so "not written" has one
      // representation on the wire and the card's snippet cannot be fooled by
      // whitespace.
      body: trimmedBody === "" ? null : trimmedBody,
      priority,
      recipients: chosen,
    };

    /** The letter is on the record — toast it, close, and show the log. */
    function logged(): void {
      onSent(chosen);
      onClose();
    }

    send.mutate(payload, {
      onSuccess: logged,
      onError: (error) => {
        // **`/problems/email-not-readable` is a completed send, not a refusal.**
        // The row is committed and audited by the time the server answers it;
        // only the scoped re-read failed, which is why its own `detail` says
        // "Do not send it again; reload the list." Treated as an error, this
        // modal stayed open over an intact draft with Send enabled — inviting
        // exactly the second copy the sentence forbids, into an append-only
        // table with no edit and no delete. `useSendEmail.onError` has already
        // invalidated the list for this case, so the ✉ Emails sub-tab the
        // success path switches to shows the letter that landed.
        if (isEmailWrittenButUnreadable(error)) {
          logged();
          return;
        }
        // Every other server refusal lands in the alert the client-side checks
        // use, through the shared mapper — so a handler never has to look in two
        // places for the same kind of answer. They do *not* borrow the subject's
        // `aria-invalid`: none of them is necessarily about the subject.
        setRefusal({ field: null, feedback: feedbackFromError(error) });
      },
    });
  }

  const templatesDisabled = openedClaimId === null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        // Every close — the ✕, Escape, the backdrop, Cancel — arrives here.
        // Except while the POST is in flight: closing mid-send would drop the
        // confirmation of a row the server has already written, into a log with
        // no delete path.
        if (busy) return;
        close();
      }}
    >
      <DialogContent
        data-testid="email-composer"
        // The wide variant UX-DR10 asks for: this modal carries a checkbox grid,
        // a six-button row and a 180px body, where the scheduler carries eight
        // short controls.
        className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
      >
        <DialogHeader>
          <DialogTitle className="text-[13px]">✉ Email to Stakeholders</DialogTitle>
          <DialogDescription className="text-[11px]">
            Sending logs this message on the claim's diary. Nothing is transmitted to a
            recipient.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="flex flex-col gap-[10px]">
          {/* --- recipients first, as the prototype has it ------------------ */}
          <fieldset
            // **`aria-describedby` here, `aria-invalid` on the boxes.** The
            // invalid state shipped on this element, where it is inert: a
            // `<fieldset>` maps to `role="group"`, which does not support
            // `aria-invalid`, so the refusal reached a screen reader only
            // through the `role="alert"` below and the control the handler has
            // to fix was never marked. A group *is* allowed a description, and
            // that half is worth keeping — the sentence is about the set.
            aria-describedby={refusal?.field === "emailRecipients" ? ERROR_ID : undefined}
          >
            <legend className={SECTION_CLASS}>Select recipients</legend>
            <div data-testid="composer-recipients" className="grid grid-cols-2 gap-[6px]">
              {RECIPIENT_ORDER.map((recipient) => (
                <label
                  key={recipient}
                  htmlFor={`recipient-${recipient}`}
                  className="flex items-center gap-[6px] text-[11px] text-text"
                >
                  <Checkbox
                    id={`recipient-${recipient}`}
                    data-testid="composer-recipient"
                    data-recipient={recipient}
                    checked={recipients.has(recipient)}
                    disabled={busy}
                    // On all six rather than on one: "addressed to nobody" is a
                    // statement about the set, and there is no single box that
                    // is the wrong one. Radix renders `role="checkbox"`, which
                    // does support this — the subject's twin, on a control that
                    // actually exposes it.
                    aria-invalid={refusal?.field === "emailRecipients"}
                    aria-describedby={
                      refusal?.field === "emailRecipients" ? ERROR_ID : undefined
                    }
                    onCheckedChange={() => toggle(recipient)}
                  />
                  {RECIPIENT_LABEL[recipient]}
                </label>
              ))}
            </div>
          </fieldset>

          {/* --- the six quick templates ------------------------------------ */}
          <div>
            <p className={SECTION_CLASS}>Quick templates</p>
            {/* **Four branches, not two** (NFR-3, and the epic's rule for every
                list surface). The row shipped handling only `isError`, so the
                heading rendered over an empty strip both while the six were in
                flight and for a 200 that carried none — two different facts,
                drawn as the same nothing, with the second indistinguishable
                from a row that failed to paint. */}
            {templates.isError ? (
              <p
                role="alert"
                data-testid="composer-templates-error"
                className="text-[11px] text-error"
              >
                ⚠ The quick templates could not be loaded. The email can still be written by
                hand.
              </p>
            ) : templates.isPending ? (
              <p data-testid="composer-templates-loading" className="text-[11px] text-faint">
                {TEMPLATES_LOADING_MESSAGE}
              </p>
            ) : templates.data.items.length === 0 ? (
              <p data-testid="composer-templates-empty" className="text-[11px] text-faint">
                {TEMPLATES_EMPTY_MESSAGE}
              </p>
            ) : (
              <div data-testid="composer-templates" className="flex flex-wrap gap-[5px]">
                {templates.data.items.map((template) => (
                  <button
                    key={template.templateKey}
                    type="button"
                    data-testid="composer-template"
                    data-template-key={template.templateKey}
                    data-selected={fill.kind === "template" && fill.templateKey === template.templateKey}
                    // Claim-aware by definition, so a template with no claim is
                    // refused rather than degraded — see `NO_CLAIM_TEMPLATE_REASON`.
                    disabled={templatesDisabled || busy || merging}
                    title={templatesDisabled ? NO_CLAIM_TEMPLATE_REASON : undefined}
                    aria-describedby={templatesDisabled ? TEMPLATE_REASON_ID : undefined}
                    onClick={() => pickTemplate(template.templateKey)}
                    className="rounded border border-steel bg-steel-soft px-[8px] py-[4px] text-[10.5px] font-semibold text-steel hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {template.label}
                  </button>
                ))}
              </div>
            )}
            {templatesDisabled && (
              /* Announced as well as shown, so the reason survives without a
                 pointer — Story 3.5's note, and NFR-3's no-dead-click rule. */
              <p
                id={TEMPLATE_REASON_ID}
                data-testid="composer-template-reason"
                className="mt-[3px] text-[10.5px] text-faint"
              >
                {NO_CLAIM_TEMPLATE_REASON}
              </p>
            )}
            {merging && (
              <p data-testid="composer-merging" className="mt-[3px] text-[10.5px] text-faint">
                Merging the letter…
              </p>
            )}
            {mergeFailed && !merging && (
              <p
                role="alert"
                data-testid="composer-merge-error"
                className="mt-[3px] text-[10.5px] font-semibold text-error"
              >
                {mergeFailedMessage}
              </p>
            )}
          </div>

          {/* --- the letter -------------------------------------------------- */}
          <div>
            <label htmlFor="email-subject" className={LABEL_CLASS}>
              Subject
            </label>
            <input
              id="email-subject"
              data-testid="composer-subject"
              value={subject}
              disabled={busy}
              // **The column's width, declared on the control.** Without it an
              // over-long subject is refused by Pydantic with "The request body
              // or parameters failed validation." — no field named, no limit
              // shown, and nothing on screen marked invalid.
              maxLength={EMAIL_SUBJECT_MAX}
              aria-invalid={refusal?.field === "emailSubject"}
              aria-describedby={refusal?.field === "emailSubject" ? ERROR_ID : undefined}
              placeholder="Subject line…"
              onChange={(event) => {
                setEdits((current) => ({ ...current, subject: event.target.value }));
                // A message about an empty box must not survive the box
                // stopping being empty.
                if (refusal?.field === "emailSubject") setRefusal(null);
              }}
              className={FIELD_CLASS}
            />
          </div>

          <div>
            <label htmlFor="email-body" className={LABEL_CLASS}>
              Body
            </label>
            <Textarea
              id="email-body"
              data-testid="composer-body"
              value={body}
              disabled={busy}
              maxLength={EMAIL_BODY_MAX}
              aria-invalid={refusal?.field === "emailBody"}
              aria-describedby={refusal?.field === "emailBody" ? ERROR_ID : undefined}
              placeholder="Email body…"
              onChange={(event) =>
                setEdits((current) => ({ ...current, body: event.target.value }))
              }
              className={`${FIELD_CLASS} min-h-[180px]`}
            />
            <p
              data-testid="composer-body-length"
              className="mt-[2px] text-right text-[9.5px] text-faint"
            >
              {body.length} of {EMAIL_BODY_MAX} characters
            </p>
          </div>

          <div className="grid grid-cols-2 gap-[10px]">
            <div>
              <label htmlFor="email-claim" className={LABEL_CLASS}>
                Claim reference
              </label>
              {/* Read-only, in the steel-blue treatment the prototype gives it
                  and `STAGE_PILL` gives every non-editable claim reference. */}
              <input
                id="email-claim"
                data-testid="composer-claim"
                data-claim-id={composedClaimId ?? ""}
                readOnly
                // Three states, not two, mirroring the scheduler's field: the
                // case file is a separate request, so between opening the modal
                // and its arrival — or if the merge named a different claim —
                // the name describes something else, and `${id} — ` with
                // nothing after it reads as a truncated field.
                //
                // **Against `openedClaimId`, never the live prop.** The
                // comparison used to be `composedClaimId !== claimId`, which
                // made a navigation *behind* the open modal — Back, Forward,
                // "Open Claim" on a card in the pane — silently drop the worker
                // from a field whose whole promise is that what it shows is what
                // the send carries. `shownWorkerName` is the other half of the
                // same fix: the live name is used only while the workspace is
                // still on the claim this composer opened on.
                value={
                  composedClaimId === null
                    ? "No claim selected"
                    : shownWorkerName === null || composedClaimId !== openedClaimId
                      ? composedClaimId
                      : `${composedClaimId} — ${shownWorkerName}`
                }
                className={`${FIELD_CLASS} bg-steel-soft font-semibold text-steel`}
              />
            </div>

            <div>
              <label htmlFor="email-priority" className={LABEL_CLASS}>
                Priority
              </label>
              <select
                id="email-priority"
                data-testid="composer-priority"
                value={priority}
                disabled={busy}
                onChange={(event) => setPriority(event.target.value as EmailPriority)}
                className={FIELD_CLASS}
              >
                {EMAIL_PRIORITY_ORDER.map((value) => (
                  <option key={value} value={value}>
                    {EMAIL_PRIORITY_LABEL[value]}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {refusal !== null && (
            <p
              id={ERROR_ID}
              role="alert"
              data-testid="composer-error"
              className="text-[11px] font-semibold text-error"
            >
              {refusal.feedback.message}
            </p>
          )}

          <div className="flex justify-end gap-[6px]">
            <button
              type="button"
              data-testid="composer-cancel"
              // Through `close`, not `onClose`: the reset lives there.
              onClick={close}
              // Disabled while the send is in flight, with the ✕, Escape and the
              // backdrop, so a cancel cannot swallow the confirmation of a row
              // that was written.
              disabled={busy}
              className="rounded border border-border bg-surface-2 px-[10px] py-[5px] text-[11px] font-semibold text-muted-text hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="composer-send"
              // **`merging` as well as `busy`** — see `merging` for what a send
              // inside a merge's round trip actually logs. The label stays keyed
              // to the send, because "Logging…" over a template fetch would name
              // the wrong request.
              disabled={busy || merging}
              className="rounded border border-brand bg-brand px-[10px] py-[5px] text-[11px] font-bold text-white hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50"
            >
              {send.isPending ? "Logging…" : "✉ Send Email (logged)"}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
