/**
 * The free-text input — the one control that starts a run.
 *
 * A form rather than a textarea with a key handler, so Enter submits through the
 * browser's own semantics and a screen reader announces a form with a submit
 * button. ⌘/Ctrl+Enter is *not* bound: a single-line question is what this pane
 * is for, and a second submit gesture is a second thing to document.
 *
 * **Disabled state is a prop, not a decision made here.** Story 6.6 owns
 * degradation and will disable exactly the affected inputs when the model is
 * unreachable; what this story owes it is a component whose disabled-ness has
 * one source, so that adding a reason is a change at the call site rather than a
 * second condition in this file.
 *
 * The composer is **absent** — not disabled — on a superseded thread. That is
 * `ActionsTab`'s decision and it is the honest rendering of read-only history: a
 * greyed-out input invites a handler to work out why, where an absent one and a
 * "this conversation is read-only" line say it outright.
 */
import { useState } from "react";

import { COPILOT_MESSAGE_MAX } from "@/api/fieldLimits";
import { Button } from "@/components/ui/button";

export function Composer({
  onSend,
  disabled,
  busy,
}: {
  onSend: (message: string) => void;
  /** Whether the input is unusable at all (6.6's degradation seam). */
  disabled?: boolean;
  /** Whether a run is in flight — the client half of the single-flight rule. */
  busy?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const blocked = Boolean(disabled) || Boolean(busy);

  return (
    <form
      data-testid="copilot-composer"
      className="flex items-end gap-1.5 border-t border-border p-2"
      onSubmit={(event) => {
        event.preventDefault();
        const message = draft.trim();
        // An empty submit is a no-op rather than a refusal: there is nothing to
        // tell the handler that the empty input does not already say.
        if (!message || blocked) return;
        setDraft("");
        onSend(message);
      }}
    >
      <label className="sr-only" htmlFor="copilot-message">
        Ask the copilot about this claim
      </label>
      <input
        id="copilot-message"
        data-testid="copilot-input"
        className="min-w-0 flex-1 rounded border border-border bg-surface px-2 py-1 text-[11px] text-text disabled:cursor-not-allowed disabled:opacity-50"
        placeholder="Ask about this claim…"
        maxLength={COPILOT_MESSAGE_MAX}
        value={draft}
        disabled={blocked}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button
        type="submit"
        size="sm"
        data-testid="copilot-send"
        disabled={blocked || draft.trim().length === 0}
      >
        {busy ? "…" : "Send"}
      </Button>
    </form>
  );
}
