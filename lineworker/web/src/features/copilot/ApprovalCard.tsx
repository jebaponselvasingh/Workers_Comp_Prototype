/**
 * The inline approval card — **the server's pending tool call, not a paraphrase**
 * (Story 6.5, AC 5; AD-16, UX-DR8/DR11).
 *
 * When the copilot proposes a write, the run pauses and the terminal frame
 * carries `HumanInTheLoopMiddleware`'s own `HITLRequest`. This renders it: the
 * tool's name, and every one of its typed arguments as a labelled row. What it
 * deliberately does **not** render is the model's sentence about what it is
 * about to do — AD-16's approval-honesty rule exists because a hijacked model
 * whose paraphrase the human approved would have been approved for something
 * else entirely, and the payload beside the buttons is the only defence against
 * that which does not depend on the model's cooperation.
 *
 * Every argument is shown, not a curated subset. A card that showed the letter
 * and hid `expectedVersion`, or showed the field and hid the claim, would be a
 * card deciding which parts of a write a handler needs to see — which is the
 * paraphrase again, one layer down. `args` is rendered generically for the same
 * reason: a third write tool needs no third card.
 *
 * ## Inline, in the thread — never a dialog (NFR-3, UX-DR11)
 *
 * A `<li>`-shaped turn at the foot of the transcript, in the same visual
 * language as an assistant turn. The prototype's approval affordances are
 * `window.confirm`; a blocking modal over a claims console is the pattern this
 * build has refused everywhere else, and an approval is exactly the moment a
 * handler is most likely to want to scroll back and read what was said.
 *
 * ## Two buttons, and the third decision that is not missing
 *
 * The server configures `approve | edit | reject` and guards all three
 * (`agents/approval.py`). This offers Approve and Reject, because the handler's
 * edit affordance for the write this story centres on is the RTW modal's text
 * area — which revises the payload *before* it is proposed, and is strictly
 * better than revising it after: they edit prose in a wide editor rather than
 * arguments in a card. The wire shape for `edit` is typed and tested; adding a
 * button later is a change to this file and to nothing else.
 *
 * ## Hand-rolled Tailwind, like every other control in this pane
 *
 * No `@assistant-ui/react` primitive is imported anywhere in `src/`, and this is
 * not the file to start: `ActionsTab` argues it at length — the vendor is here
 * for the streaming contract, and the pane is this console's own dense
 * language. Raw `<button>` with token classes, `EmailComposerDialog`'s pattern.
 */
import type { PendingAction } from "@/api/copilot";

/**
 * How one drafted argument reads.
 *
 * `JSON.stringify` for anything that is not a string or a number, which in
 * practice never happens — every write tool's arguments are scalars — and which
 * is what stops a nested value rendering as `[object Object]` on the one screen
 * where a handler is deciding whether to let it through. Strings are shown as
 * they are, because the letter *is* the argument and quoting it would put
 * escaping between a reader and the words they are approving.
 */
function readable(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return JSON.stringify(value) ?? "";
}

/**
 * How long a string has to be before it is laid out as a block.
 *
 * A **layout** threshold and named as one, which is also what keeps it clear of
 * `noDerivation.test.ts`: that guard refuses a numeric comparison in `web/`
 * because bands, cut-offs and score thresholds are JDM parameters the server
 * owns (AD-8), and it is deliberately blunt. This is neither — it decides
 * whether a value gets one line or a scroll box, and nothing about a claim
 * depends on it.
 */
const PROSE_LENGTH = 80;

/**
 * Which arguments are shown as a long block rather than on one line.
 *
 * A letter is three hundred words; a claim id is nine characters. Both are
 * arguments and both are shown, but a single row layout makes one of them
 * unreadable. Decided by the value's own shape — a string with a newline in it,
 * or a long one — rather than by the argument's name, so a future write tool
 * with a different long field needs no entry here.
 */
function isProse(value: unknown): boolean {
  return (
    typeof value === "string" &&
    (value.includes("\n") || value.length > PROSE_LENGTH)
  );
}

export function ApprovalCard({
  action,
  busy,
  onApprove,
  onReject,
}: {
  action: PendingAction;
  /** A decision is already in flight — the single-flight rule, client-side. */
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const args = Object.entries(action.args);

  return (
    <div
      data-testid="copilot-approval"
      data-tool={action.name}
      role="group"
      aria-label="Approval required"
      className="mx-3 mb-3 rounded border border-warn/60 bg-surface-2 px-2 py-2 text-[11px] text-text"
    >
      <p className="font-semibold text-warn">
        ⚠ The copilot wants to change this claim
      </p>
      <p
        data-testid="copilot-approval-tool"
        className="mt-[2px] font-mono text-[10.5px]"
      >
        {action.name}
      </p>

      <dl data-testid="copilot-approval-args" className="mt-2">
        {args.map(([key, value]) =>
          isProse(value) ? (
            <div
              key={key}
              data-testid="copilot-approval-arg"
              data-arg={key}
              className="py-[3px]"
            >
              <dt className="text-[10px] text-muted-text">{key}</dt>
              {/* Pre-wrapped plain text, never markup: this began as model
                  output a handler edited, and it is being shown so it can be
                  checked. `Transcript.tsx` is the standard — no
                  `dangerouslySetInnerHTML` anywhere in this feature. */}
              <dd className="mt-[2px] max-h-40 overflow-y-auto rounded bg-surface px-1.5 py-1 font-mono text-[10.5px] whitespace-pre-wrap">
                {readable(value)}
              </dd>
            </div>
          ) : (
            <div
              key={key}
              data-testid="copilot-approval-arg"
              data-arg={key}
              className="flex items-baseline justify-between gap-3 py-[3px]"
            >
              <dt className="shrink-0 text-[10px] text-muted-text">{key}</dt>
              <dd className="min-w-0 truncate text-right font-mono text-[10.5px]">
                {readable(value)}
              </dd>
            </div>
          ),
        )}
      </dl>

      <div className="mt-2 flex gap-2">
        <button
          type="button"
          data-testid="copilot-approve"
          disabled={busy}
          onClick={onApprove}
          className="rounded border border-ok px-2 py-[3px] text-[11px] font-semibold text-ok disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          data-testid="copilot-reject"
          disabled={busy}
          onClick={onReject}
          className="rounded border border-border px-2 py-[3px] text-[11px] font-semibold text-muted-text disabled:opacity-50"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
