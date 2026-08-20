/**
 * The per-claim conversation switcher and "new conversation" (UX-DR8).
 *
 * A `<select>` and a button, because that is what the affordance is: a handler
 * has a handful of conversations about a claim, all of them are in the list, and
 * there is nothing to page. The prototype had no equivalent — its chat was one
 * browser-lifetime array — so this is a new control rather than a port.
 *
 * **The list is server-ordered and server-labelled.** `conversationSeq` and
 * `isCurrent` both come off the payload; nothing here sorts, and nothing here
 * decides which thread is current. `queryKeys.copilot` and the router both argue
 * that at length — a client that computed "current" would be a second place the
 * read-only rule lives, with the 409 arriving as a surprise when the two
 * disagreed.
 *
 * **Prior threads are selectable, and that is the point of keeping them.**
 * Selecting one shows its transcript with no composer beneath it (`ActionsTab`);
 * the server refuses a run against it regardless, so the UI and the API say the
 * same thing rather than the UI being the enforcement.
 */
import { Button } from "@/components/ui/button";

import type { CopilotThread } from "@/api/copilot";

export function ThreadSwitcher({
  threads,
  selectedThreadId,
  onSelect,
  onNewConversation,
  busy,
}: {
  threads: readonly CopilotThread[];
  selectedThreadId: string | null;
  onSelect: (threadId: string) => void;
  onNewConversation: () => void;
  /** Whether a copilot command is in flight — see `useCopilotWriteInFlight`. */
  busy?: boolean;
}) {
  // One conversation is not a choice. The control is hidden rather than
  // rendered with a single option, which is the state every claim is in until
  // somebody presses New — and a dropdown that never has two entries reads as a
  // broken control rather than as a simple one.
  //
  // Written as "is there a second one?" rather than as a length comparison, and
  // that is `noDerivation.test.ts`'s rule rather than a stylistic preference:
  // its threshold guard is blunt on purpose, and arguing with a guard by
  // editing the guard is how it stops being one. Destructuring says the same
  // thing and says it more directly.
  const [, secondThread] = threads;
  const showPicker = secondThread !== undefined;

  return (
    <div
      data-testid="copilot-threads"
      className="flex flex-shrink-0 items-center gap-1.5 border-b border-border px-2 py-1.5"
    >
      {showPicker ? (
        <>
          <label className="sr-only" htmlFor="copilot-thread-picker">
            Conversation
          </label>
          <select
            id="copilot-thread-picker"
            data-testid="copilot-thread-picker"
            className="min-w-0 flex-1 rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] text-text"
            value={selectedThreadId ?? ""}
            onChange={(event) => onSelect(event.target.value)}
          >
            {threads.map((thread) => (
              <option key={thread.threadId} value={thread.threadId}>
                {thread.isCurrent
                  ? `Conversation ${thread.conversationSeq} (current)`
                  : `Conversation ${thread.conversationSeq}`}
              </option>
            ))}
          </select>
        </>
      ) : (
        <span className="flex-1 text-[10px] text-faint">
          {threads.length === 0 ? "No conversation yet" : "Conversation 1"}
        </span>
      )}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        data-testid="copilot-new-thread"
        disabled={busy}
        onClick={onNewConversation}
      >
        + New
      </Button>
    </div>
  );
}
