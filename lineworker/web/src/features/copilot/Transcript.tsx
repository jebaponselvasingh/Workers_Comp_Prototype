/**
 * The conversation, rendered — **sanitized markdown only, never raw HTML** (AC 7).
 *
 * `react-markdown` with its default configuration, and the default is the whole
 * security property: it parses Markdown to an AST and builds React elements from
 * it, so a `<script>` in the model's output is a **text node** rather than an
 * element. There is no HTML sink anywhere in this file for it to reach.
 *
 * Two plugins are therefore deliberately absent and must stay absent:
 *
 * - **`rehype-raw`**, which is the one thing that would turn the paragraph above
 *   into a lie. It exists precisely to parse embedded HTML back into elements,
 *   and adding it here would make a model's `<img onerror=…>` a live element in
 *   a pane rendering claim data.
 * - **`dangerouslySetInnerHTML`**, anywhere, for the same reason and more
 *   bluntly. `web/src/features/copilot/` contains neither string.
 *
 * **Links are rendered as text, not as anchors.** AD-16 says URLs in model
 * output are never auto-fetched by server or client — and an `<a href>` in a
 * transcript is a fetch one click away, aimed at a URL that may have come out of
 * a claim narrative somebody else wrote. The custom `a` renderer below drops the
 * href and keeps the text, so the reader can see what the model said and cannot
 * be one mis-click from acting on it.
 *
 * ## Why the messages come from the runtime rather than from state here
 *
 * AD-9: no hand-rolled streaming. The list this component renders is
 * `useLangGraphMessages`' — the assistant-ui LangGraph runtime accumulates the
 * streamed chunks into messages, reconciles a chunk with the turn it belongs to,
 * and hands back a settled array. Nothing in `features/copilot/` appends to a
 * string.
 */
import Markdown from "react-markdown";
import type { ReactNode } from "react";

/** One turn as this pane renders it — the runtime's message, narrowed. */
export interface TranscriptTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
}

/**
 * A model's link, rendered as the text it wrapped.
 *
 * Not a component in its own file: it is four lines whose only reader is the
 * `components` map below, and separating it would make "does this transcript
 * create anchors?" a two-file question.
 */
function PlainText({ children }: { children?: ReactNode }) {
  return <>{children}</>;
}

/**
 * The element overrides. Small on purpose — everything not named here gets
 * `react-markdown`'s default, which is already an element built from an AST.
 */
const COMPONENTS = {
  a: PlainText,
  // Images are dropped entirely rather than rendered without a `src`: an image
  // is a network fetch of a URL that may have come out of claim text, which is
  // the exact thing AD-16 forbids the client from doing on its own.
  img: () => null,
} as const;

export function Transcript({
  turns,
  emptyLabel,
}: {
  turns: readonly TranscriptTurn[];
  /** What an empty conversation says. Never a blank pane (NFR-3). */
  emptyLabel: string;
}) {
  if (turns.length === 0) {
    return (
      <p data-testid="copilot-transcript-empty" className="p-3 text-[11px] text-faint">
        {emptyLabel}
      </p>
    );
  }

  return (
    <ol
      data-testid="copilot-transcript"
      className="flex flex-col gap-2 p-3"
      // A list, because that is what a transcript is — and it gives a screen
      // reader the count and the position, which a stack of divs does not.
      aria-label="Conversation"
      // **A live region, because an answer arrives without anything being
      // focused.** A handler using a screen reader presses Send and the reply
      // materialises somewhere below them with nothing announcing it — which is
      // the one place in this console where content appears entirely on its own
      // (review of Story 6.3).
      //
      // `polite` rather than `assertive`: an answer is worth announcing at the
      // next pause, never worth interrupting whatever the reader is already
      // saying. `aria-atomic="false"` so a streamed turn is announced as the
      // parts that changed rather than re-read whole on every chunk, which is
      // the difference between a sentence and a stutter.
      aria-live="polite"
      aria-atomic="false"
      aria-relevant="additions text"
    >
      {turns.map((turn) => (
        <li
          key={turn.id}
          data-testid={`copilot-turn-${turn.role}`}
          data-role={turn.role}
          className={
            turn.role === "user"
              ? "self-end rounded bg-surface-2 px-2 py-1.5 text-[11px] text-text"
              : "self-start rounded border border-border px-2 py-1.5 text-[11px] text-text"
          }
        >
          {/* The role is announced rather than only coloured: a transcript in
              which "who said this?" is carried by a background colour is a
              transcript a screen reader renders as one undifferentiated block. */}
          <span className="sr-only">{turn.role === "user" ? "You said:" : "Copilot said:"}</span>
          <div className="copilot-markdown space-y-1 [&_code]:font-mono [&_li]:ml-3 [&_ul]:list-disc">
            <Markdown components={COMPONENTS}>{turn.content}</Markdown>
          </div>
        </li>
      ))}
    </ol>
  );
}
