/**
 * The return-to-work letter's wide editable modal (Story 6.5, AC 4, UX-DR10).
 *
 * The 📄 Review RTW Policy quick action streams a draft into the transcript;
 * this is where the handler reads it whole, edits it, prints it, copies it, and
 * — if they choose to — files it on the claim through the approval gate.
 *
 * ## The four affordances, and which of them touch the network
 *
 * **✏ Edit** toggles a `Textarea` over the same text. `contentEditable` is the
 * prototype's mechanism and is deliberately not ported: a `contentEditable` div
 * hands back `innerHTML`, which would make "what did the handler type?" a
 * sanitisation question on a string that is about to be stored and re-rendered.
 * A textarea hands back a string. `EmailComposerDialog`'s body field is the same
 * decision for the same reason, and there is no `contentEditable` anywhere in
 * `src/`.
 *
 * **🖨 Print** and **📋 Copy** issue **no request of any kind** — no run, no
 * proposal, nothing to approve (the story says so in its own row). Print hands
 * the page to `window.print()`; Copy writes to the clipboard. Both are
 * greenfield — neither call appears anywhere else in `src/` — so both are
 * guarded: a clipboard write is refused by permission policy, by an insecure
 * origin and by a browser that has none, and a refused copy that said nothing
 * would be a button that looked broken.
 *
 * **Print prints the dialog, not a composed letterhead**, and that is recorded
 * rather than papered over. Making the browser print *only* the letter needs a
 * global `@media print` rule that hides the rest of the workspace, which is a
 * change to the app shell's stylesheet rather than to a copilot modal — and a
 * shell-wide print rule introduced by a copilot story is exactly the kind of
 * change that surfaces on some other screen months later. What the handler gets
 * is the modal, which is the letter in a scrollable panel; Copy is the
 * affordance for getting the text somewhere else.
 *
 * **Save to claim** posts one run carrying the *edited* text and the version the
 * draft was pinned to. It is the only one of the four that writes, and it does
 * not write: it proposes, and the approval card in the transcript is what the
 * handler answers.
 *
 * ## Why the version comes in as a prop
 *
 * `expectedVersion` is read at **draft** time, published by the server on the
 * run that drafted the letter (`agents/qas.RTW_DRAFT`) and carried here by
 * `ActionsTab`. This component must not fetch its own: a version read when the
 * modal opened would be newer than the one the letter was composed against, and
 * a save under it would succeed against a claim that had moved — the exact
 * force-write AD-6's version-pinned approvals exist to prevent. The modal
 * refuses to save without one rather than guessing.
 */
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

/** What the dialog holds while it is open. `null` when it is not. */
export interface RtwLetterDraft {
  claimId: string;
  /** The claim's `version` when the letter was drafted — see the module docstring. */
  version: number;
  /** The letter as the copilot streamed it. The handler's edits start here. */
  body: string;
}

/** What a copy that the browser refused reads like. Never a thrown promise. */
const COPY_FAILED =
  "The letter could not be copied. Select the text and copy it manually.";

/** What a copy that worked reads like — it is otherwise entirely invisible. */
const COPIED = "Copied to the clipboard.";

export function RtwLetterDialog({
  draft,
  busy,
  onClose,
  onSave,
}: {
  draft: RtwLetterDraft | null;
  /** A run is in flight, or an approval pends — the client half of single-flight. */
  busy: boolean;
  onClose: () => void;
  /** Propose the save. The text is the handler's, verbatim. */
  onSave: (body: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  // **Adjusted during render**, the pattern `ActionsTab` and `ClaimDetailPane`
  // both use for "a prop changed and some state has to follow it": a new draft
  // replaces whatever was being edited, and it does so before the browser
  // paints rather than one frame later. Keyed on the body itself, so re-opening
  // the modal on the *same* draft keeps the handler's edits.
  const [seenBody, setSeenBody] = useState<string | null>(null);
  if (draft !== null && draft.body !== seenBody) {
    setSeenBody(draft.body);
    setBody(draft.body);
    setEditing(false);
    setNotice(null);
  }

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(body);
      setNotice(COPIED);
    } catch {
      // A clipboard write is refused by permission policy, by an insecure
      // origin, and by a browser that has none — three failures a handler can
      // do exactly one thing about, which is what the sentence says.
      setNotice(COPY_FAILED);
    }
  }

  return (
    <Dialog open={draft !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        data-testid="rtw-letter"
        // UX-DR10's wide variant. `EmailComposerDialog` is the precedent at
        // `sm:max-w-2xl`; a letter is a full page of prose rather than a form,
        // so it gets the next size up.
        className="max-h-[85vh] overflow-y-auto sm:max-w-3xl"
      >
        <DialogHeader>
          <DialogTitle className="text-[13px]">
            📄 Return-to-work letter
          </DialogTitle>
          <DialogDescription className="text-[11px]">
            A draft for {draft?.claimId ?? "this claim"}. Edit it, then save it
            to file it on the claim — saving asks for your approval before
            anything is written.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="rtw-edit"
            aria-pressed={editing}
            onClick={() => setEditing((current) => !current)}
            className="rounded border border-border px-2 py-[3px] text-[11px] font-semibold text-muted-text"
          >
            ✏ Edit
          </button>
          <button
            type="button"
            data-testid="rtw-print"
            // No network call — see the module docstring, which also records
            // what this does and does not print.
            onClick={() => window.print()}
            className="rounded border border-border px-2 py-[3px] text-[11px] font-semibold text-muted-text"
          >
            🖨 Print
          </button>
          <button
            type="button"
            data-testid="rtw-copy"
            onClick={() => void copy()}
            className="rounded border border-border px-2 py-[3px] text-[11px] font-semibold text-muted-text"
          >
            📋 Copy
          </button>
        </div>

        {editing ? (
          <Textarea
            data-testid="rtw-body-input"
            aria-label="Letter body"
            value={body}
            disabled={busy}
            onChange={(event) => setBody(event.target.value)}
            className="min-h-[320px] font-mono text-[11.5px]"
          />
        ) : (
          // **Pre-wrapped plain text, never markup.** The body began as model
          // output; `Transcript.tsx` renders assistant prose as sanitized
          // markdown and this renders it as literally itself, which is the
          // stricter of the two and the right one for a document that is about
          // to be stored verbatim (AD-16).
          <div
            data-testid="rtw-body"
            className="max-h-[50vh] overflow-y-auto rounded border border-border px-3 py-2 text-[11.5px] whitespace-pre-wrap"
          >
            {body}
          </div>
        )}

        {notice === null ? null : (
          <p
            data-testid="rtw-notice"
            role="status"
            className="text-[10.5px] text-muted-text"
          >
            {notice}
          </p>
        )}

        <DialogFooter>
          <button
            type="button"
            data-testid="rtw-save"
            disabled={busy || draft === null || body.trim() === ""}
            onClick={() => onSave(body)}
            className="rounded border border-brand px-2 py-[4px] text-[11px] font-semibold text-brand disabled:opacity-50"
          >
            Save to claim
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
