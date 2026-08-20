/**
 * The ⚡ Actions tab — the copilot's chat surface (Story 6.3, UX-DR8).
 *
 * The tab Story 4.1 shipped disabled behind "Copilot arrives with the AI epic —
 * Epic 6". This is Epic 6. What it renders, top to bottom: the per-claim thread
 * switcher and "new conversation", the seeded case-summary greeting, the
 * transcript, the composer, and the disclaimer.
 *
 * ## The streaming is the runtime's, not this file's (AD-9)
 *
 * `useLangGraphMessages` from `@assistant-ui/react-langgraph` owns the run: it
 * consumes the `stream` callback's frames, accumulates a streamed chunk onto the
 * assistant turn it belongs to, tracks the interrupt state, and hands back a
 * settled message array. **Nothing in `features/copilot/` appends to a string**
 * — which is what "no hand-rolled streaming" means in practice, and is the
 * reason a partial answer never renders as two half-messages.
 *
 * The lower-level `useLangGraphMessages` is used rather than
 * `useLangGraphRuntime` plus the vendor's `ThreadPrimitive`. Both are the same
 * package and the same accumulation; the difference is who draws the pane. The
 * primitives bring their own DOM and their own class hooks, and UX-DR8 asks for
 * *this* console's pane — a 320px column in the prototype's information-dense
 * language, sharing `Button`, the border tokens and the type scale with the
 * three tabs beside it. Adopting the primitives would have meant re-skinning a
 * component library to look like six lines of Tailwind. What the vendor is here
 * for is the streaming contract, and that is exactly what is taken.
 *
 * ## The first conversation is minted by this component (AC 1)
 *
 * `GET …/threads` on a claim nobody has opened the panel on answers
 * `{items: [], currentThreadId: null}` — a first-class state, not a 404 — and
 * the SPA `POST`s **once** to mint `seq 1`. The server's own route docstring
 * says so ("The SPA `POST`s once when the list comes back empty"), and until
 * the review of Story 6.3 nothing did: the pane rendered "start a conversation
 * with + New" and waited for a click, so the documented behaviour existed in
 * two docstrings and no code path, and the e2e helper had to press the button
 * itself to get a thread at all.
 *
 * The mint is guarded by a ref keyed on the claim, not by a piece of state, and
 * that is what makes "once" true rather than "once per render": a failed mint
 * leaves an inline notice and does **not** retry, because a `POST` that loops
 * against a claim the server keeps refusing is worse than a button.
 *
 * ## Where each piece of state lives
 *
 * - **Which threads exist** is server state (`useClaimThreads`), because it is.
 * - **Which thread is selected** is local UI state, `DiaryTab`'s sub-tab rule:
 *   it is not a server resource and not shareable the way `?claim=` is. It
 *   defaults to the server's `currentThreadId` and follows it when the claim
 *   changes.
 * - **The transcript** is server state on first load (`useThreadTranscript`,
 *   read back from the checkpoints — FR-CP-2) and the runtime's thereafter.
 *   Seeding one from the other is what makes a reload show the conversation.
 *   **Once per thread**, which is the whole of "and the runtime's thereafter":
 *   re-seeding on every arrival of `history.data` meant the invalidation a
 *   finished run performs raced the answer it had just streamed and sometimes
 *   wiped it, and it meant the previous claim's conversation was still on
 *   screen under the new claim's greeting until the new transcript landed.
 * - **The message being streamed** is the runtime's alone.
 *
 * ## The 409 is an inline notice keyed to its thread
 *
 * `MeetingsSubTab`'s refusal shape (NFR-3, UX-DR11): never `alert()`, never a
 * blocking dialog, and **stored with the thread it was raised about** so that
 * "this conversation is busy" does not follow the handler onto the next one they
 * select. Both single-flight causes carry one `type`, so there is one sentence.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { appendLangChainChunk, useLangGraphMessages } from "@assistant-ui/react-langgraph";
import type { LangChainMessage } from "@assistant-ui/react-langgraph";

import {
  isThreadBusy,
  isThreadReadOnly,
  streamRun,
  useClaimThreads,
  useCopilotWriteInFlight,
  useNewThread,
  useThreadTranscript,
} from "@/api/copilot";
import { queryKeys } from "@/api/queryKeys";
import { useQueryClient } from "@tanstack/react-query";

import { Composer } from "./Composer";
import { COPILOT_DISCLAIMER } from "./disclaimer";
import { ThreadSwitcher } from "./ThreadSwitcher";
import { Transcript, type TranscriptTurn } from "./Transcript";

/** What a busy thread reads like. Both causes, one sentence — see the router. */
const BUSY_MESSAGE =
  "⚠ This conversation is busy. A message is being answered, or the copilot is waiting for you to approve something.";

/** What a superseded thread reads like when a run is refused against it. */
const READ_ONLY_MESSAGE =
  "⚠ This conversation has been replaced by a newer one. Use the current conversation to ask something new.";

/** Everything else. A written sentence, `InsightsTab`'s `REFRESH_FAILED` rule. */
const FAILED_MESSAGE = "⚠ The copilot could not answer. Nothing was changed on the claim.";

/**
 * What a failed mint reads like — the state a dead click used to leave behind.
 *
 * `useNewThread` had no `onError`, so a refused "new conversation" — a 409 while
 * the current one is answering, a 404 for a claim that left the caller's book,
 * a network failure — produced nothing at all: no thread, no notice, and a
 * button that appeared not to work.
 */
const MINT_FAILED_MESSAGE =
  "⚠ The conversation could not be started. Nothing was changed on the claim — try again in a moment.";

/** What an empty conversation says. Never a blank pane (NFR-3). */
const EMPTY_TRANSCRIPT = "No messages yet. Ask a question about this claim below.";

/** What the pane says while a thread's checkpointed transcript is being read. */
const LOADING_TRANSCRIPT = "Loading this conversation…";

/**
 * A LangChain message's content as the text a transcript renders.
 *
 * Two shapes reach this, and both are the runtime's rather than the server's: a
 * plain string for a message seeded from the checkpointed transcript, and an
 * array of content blocks for one the chunk merger built — `appendLangChainChunk`
 * normalises to blocks as soon as it appends anything. Only `text` blocks are
 * read; a shape this build never produces is dropped rather than stringified,
 * because `[object Object]` in a claims console is worse than a missing
 * sentence.
 */
function plainText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (part): part is { type: "text"; text: string } =>
        typeof part === "object" &&
        part !== null &&
        (part as { type?: unknown }).type === "text",
    )
    .map((part) => part.text)
    .join("");
}

export function ActionsTab({ claimId }: { claimId: string | null }) {
  const client = useQueryClient();
  const threads = useClaimThreads(claimId);
  const newThread = useNewThread(claimId ?? "");
  const busy = useCopilotWriteInFlight();

  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ threadId: string; message: string } | null>(null);
  const [running, setRunning] = useState(false);
  // A failed mint has no thread to be keyed to — that is what failed — so it is
  // its own notice rather than a `refusal` with an invented thread id.
  const [mintFailed, setMintFailed] = useState(false);

  const items = useMemo(() => threads.data?.items ?? [], [threads.data]);
  const currentThreadId = threads.data?.currentThreadId ?? null;
  // The selection follows the server's current thread until the handler picks
  // another, and is reset whenever the claim changes — a selection that
  // survived a claim switch would address a conversation about a different
  // case file, which the server would answer 404 and the panel would render as
  // an error the handler could not explain.
  const activeThreadId =
    selectedThreadId && items.some((thread) => thread.threadId === selectedThreadId)
      ? selectedThreadId
      : currentThreadId;
  const activeThread = items.find((thread) => thread.threadId === activeThreadId) ?? null;
  const readOnly = activeThread !== null && !activeThread.isCurrent;

  // **Adjusted during render rather than in an effect**, which is React's own
  // documented pattern for "a prop changed and some state has to follow it" and
  // is what the `react-hooks` rules ask for here. It also happens to be the
  // correct behaviour: an effect would render one frame of the *previous*
  // claim's conversation under the new claim's header, and a handler clicking
  // through the queue would see a flicker of somebody else's case file.
  const [seenClaimId, setSeenClaimId] = useState(claimId);
  if (claimId !== seenClaimId) {
    setSeenClaimId(claimId);
    setSelectedThreadId(null);
    setRefusal(null);
    setMintFailed(false);
  }

  // **The first conversation, minted once.** See the module docstring: this is
  // AC 1's "the SPA POSTs once when the list comes back empty", and the ref is
  // what keeps a refusal from becoming a loop. Keyed on the claim rather than
  // on a boolean so that clicking through the queue mints for each new claim
  // and never twice for one.
  const { mutate: mintThread } = newThread;
  const mintedForClaim = useRef<string | null>(null);
  useEffect(() => {
    if (claimId === null || !threads.data) return;
    // "Is there one already?" rather than a length comparison — `ThreadSwitcher`
    // records why, and it is `noDerivation.test.ts`'s rule rather than a
    // stylistic preference: that guard is blunt on purpose, and arguing with it
    // by editing it is how it stops being one.
    const [existing] = threads.data.items;
    if (existing !== undefined) return;
    if (mintedForClaim.current === claimId) return;
    mintedForClaim.current = claimId;
    mintThread(undefined, {
      onSuccess: (thread) => setSelectedThreadId(thread.threadId),
      onError: () => setMintFailed(true),
    });
  }, [claimId, threads.data, mintThread]);

  // The thread the `onError` handler below should attribute a failure to. A ref
  // rather than a dependency, because that handler is created once and a
  // dependency array that rebuilt it would rebuild the runtime with it.
  //
  // Written in an effect rather than during render, which the `react-hooks/refs`
  // rule requires and which is right here anyway: the value is read only from a
  // callback the runtime invokes, never while rendering, so an update that
  // lands after paint is soon enough.
  const activeThreadRef = useRef<string | null>(activeThreadId);
  useEffect(() => {
    activeThreadRef.current = activeThreadId;
  }, [activeThreadId]);

  const history = useThreadTranscript(activeThreadId);

  /**
   * The runtime's transport. One run, translated frames, and nothing else.
   *
   * The `messages` argument is what the runtime wants sent; only the last human
   * message is ours to send, because the server holds the rest in the thread's
   * checkpoints. That is the whole difference between this and a stateless chat
   * API, and it is why the request body carries a message rather than a history.
   */
  const stream = useCallback(
    async function* (messages: LangChainMessage[], config: { abortSignal: AbortSignal }) {
      const outgoing = messages[messages.length - 1];
      const text =
        outgoing && typeof outgoing.content === "string" ? outgoing.content : "";
      const threadId = activeThreadId;
      if (!threadId) return;
      try {
        for await (const frame of streamRun(threadId, { message: text }, config.abortSignal)) {
          yield frame as never;
        }
      } finally {
        // Whatever the run did, the checkpointed transcript has moved — so the
        // next mount reads the conversation back rather than the one it started
        // with. Exact, because nothing else lives under this key.
        void client.invalidateQueries({
          queryKey: queryKeys.copilot.thread(threadId),
          exact: true,
        });
      }
    },
    [activeThreadId, client],
  );

  const runtime = useLangGraphMessages<LangChainMessage>({
    stream,
    // **The vendor's own chunk merger, passed explicitly.** The accumulator's
    // default `appendMessage` is `(_, curr) => curr` — replace, not append —
    // which is right for a server that streams *cumulative* partials and wrong
    // for one that streams deltas. Without this the transcript showed only the
    // last chunk of every answer, which reads as a truncated model rather than
    // as a missing option. `appendLangChainChunk` is the merge
    // `useLangGraphRuntime` installs for itself; this is the same function,
    // named at the seam it belongs to.
    appendMessage: appendLangChainChunk,
    eventHandlers: {
      // **A mid-stream failure is a frame, not a rejection**, so it does not
      // reach the `catch` around `sendMessage`: by the time the model stops
      // answering the response is already 200 and the only place a problem
      // document can go is inline in an `error` event. The runtime hands it
      // here rather than throwing, which is correct — the partial answer that
      // did arrive is still in the transcript — so this is where it becomes the
      // same inline notice a 409 does.
      //
      // Keyed to the thread through the ref below rather than to
      // `activeThreadId` directly: this callback is created once and would
      // otherwise close over the thread selected at mount.
      onError: (error: unknown) => {
        const threadId = activeThreadRef.current;
        if (!threadId) return;
        const detail =
          typeof error === "object" && error !== null && "detail" in error
            ? String((error as { detail: unknown }).detail)
            : "";
        setRefusal({ threadId, message: detail ? `⚠ ${detail}` : FAILED_MESSAGE });
      },
    },
  });
  const { messages, sendMessage, setMessages } = runtime;

  // **Seed the runtime from the checkpointed transcript once per thread.**
  //
  // This is what makes navigation and re-login survivable (FR-CP-2) — the
  // runtime starts empty on every mount, and the conversation lives on the
  // server. "Once per thread" is the correction the review of Story 6.3 made
  // to it, and it fixes two things at once. A run's `finally` invalidates this
  // query, so re-seeding on every arrival of `history.data` raced the answer
  // that had just streamed and could replace it with a transcript fetched
  // before the run finished. And `seededThreadId` is what the render below uses
  // to decide whether the messages it holds belong to the thread on screen —
  // without it, the previous claim's conversation stayed visible under the new
  // claim's greeting for as long as the new transcript took to arrive.
  // Adjusted **during render** rather than in an effect, the `seenClaimId`
  // block above's pattern and React's own for "a prop changed and some state
  // has to follow it". It re-renders before the browser paints, so no frame of
  // the previous conversation is ever committed under the new one's greeting —
  // which an effect, running after paint, could not promise.
  const [seededThreadId, setSeededThreadId] = useState<string | null>(null);
  const loaded = history.data;
  if (loaded && loaded.threadId === activeThreadId && seededThreadId !== loaded.threadId) {
    setSeededThreadId(loaded.threadId);
    setMessages(
      loaded.messages.map((message, index) => ({
        id: `${loaded.threadId}-${index}`,
        type: message.role === "user" ? "human" : "ai",
        content: message.content,
      })) as LangChainMessage[],
    );
  }

  // Whether the messages the runtime holds are this thread's. `false` between
  // selecting a conversation and its transcript arriving, which is exactly the
  // window in which the wrong conversation used to be on screen.
  const showingThread = activeThreadId !== null && seededThreadId === activeThreadId;

  const turns: TranscriptTurn[] = !showingThread
    ? []
    : messages
        .map((message, index) => ({
          id: message.id ?? `turn-${index}`,
          role: message.type === "human" ? ("user" as const) : ("assistant" as const),
          content: plainText(message.content),
        }))
        // An assistant turn with no text is one that only called tools — the
        // server drops those from the transcript and the runtime can hold one
        // mid-stream. Written as an emptiness check rather than a length
        // comparison, for `ThreadSwitcher`'s recorded reason.
        .filter((turn) => turn.content !== "");

  // **Keep the newest turn in view** (UX-DR8, NFR-3). A streaming answer that
  // grows past the fold with nothing scrolling is an answer the handler has to
  // go looking for, and the container that scrolls is this component's rather
  // than `Transcript`'s — so the effect belongs here, beside the ref.
  //
  // Keyed on the number of turns *and* the length of the last one, because a
  // streamed answer arrives as one turn getting longer: a dependency on the
  // count alone would scroll once and then sit still for the rest of the
  // answer.
  const scroller = useRef<HTMLDivElement>(null);
  const lastTurnLength = turns.length === 0 ? 0 : turns[turns.length - 1]!.content.length;
  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [turns.length, lastTurnLength]);

  async function send(text: string): Promise<void> {
    if (!activeThreadId) return;
    setRefusal(null);
    setRunning(true);
    try {
      await sendMessage([{ type: "human", content: text }], {});
    } catch (error) {
      setRefusal({
        threadId: activeThreadId,
        message: isThreadBusy(error)
          ? BUSY_MESSAGE
          : isThreadReadOnly(error)
            ? READ_ONLY_MESSAGE
            : FAILED_MESSAGE,
      });
    } finally {
      setRunning(false);
    }
  }

  if (claimId === null) {
    return (
      <p data-testid="copilot-no-claim" className="p-3 text-[11px] text-faint">
        Select a case to talk to the copilot about it.
      </p>
    );
  }

  if (threads.isPending) {
    return (
      <p data-testid="copilot-loading" className="p-3 text-[11px] text-faint">
        Loading conversations…
      </p>
    );
  }

  if (threads.isError) {
    return (
      <p data-testid="copilot-error" role="alert" className="p-3 text-[11px] text-er">
        ⚠ The copilot could not be loaded for this claim.
      </p>
    );
  }

  return (
    <div data-testid="copilot-actions" className="flex min-h-0 flex-1 flex-col">
      <ThreadSwitcher
        threads={items}
        selectedThreadId={activeThreadId}
        onSelect={(threadId) => {
          setSelectedThreadId(threadId);
          setRefusal(null);
        }}
        onNewConversation={() => {
          setRefusal(null);
          setMintFailed(false);
          mintThread(undefined, {
            onSuccess: (thread) => setSelectedThreadId(thread.threadId),
            // Without this a refused mint — the 409 while the current
            // conversation is still answering, most of all — was a click that
            // did nothing and said nothing.
            onError: () => setMintFailed(true),
          });
        }}
        busy={busy || running}
      />

      {/* The seeded case-summary greeting (UX-DR8, AD-2). Server-composed from
          deterministic figures — the model does not write it, and it is
          identical on every read, which is what lets the e2e spec assert that
          it rendered without asserting one word of prose. */}
      <p
        data-testid="copilot-greeting"
        className="flex-shrink-0 border-b border-border px-3 py-2 text-[11px] whitespace-pre-line text-muted-text"
      >
        {threads.data.greeting}
      </p>

      <div ref={scroller} data-testid="copilot-scroller" className="min-h-0 flex-1 overflow-y-auto">
        {items.length === 0 ? (
          // The panel mints on its own (AC 1), so this is the moment between
          // an empty list and the thread that answers it — or, when the mint
          // was refused, the state the notice below explains. "+ New" stays
          // there either way, which is what makes a refusal recoverable.
          <p data-testid="copilot-no-thread" className="p-3 text-[11px] text-faint">
            {mintFailed
              ? "No conversation yet. Use + New to try again."
              : "Starting a conversation about this claim…"}
          </p>
        ) : !showingThread ? (
          <p data-testid="copilot-transcript-loading" className="p-3 text-[11px] text-faint">
            {LOADING_TRANSCRIPT}
          </p>
        ) : (
          <Transcript turns={turns} emptyLabel={EMPTY_TRANSCRIPT} />
        )}
      </div>

      {mintFailed ? (
        <p
          data-testid="copilot-mint-error"
          role="status"
          className="flex-shrink-0 border-t border-border px-3 py-1.5 text-[10.5px] text-wn"
        >
          {MINT_FAILED_MESSAGE}
        </p>
      ) : null}

      {refusal?.threadId === activeThreadId && refusal !== null ? (
        <p
          data-testid="copilot-refusal"
          role="status"
          className="flex-shrink-0 border-t border-border px-3 py-1.5 text-[10.5px] text-wn"
        >
          {refusal.message}
        </p>
      ) : null}

      {readOnly ? (
        <p
          data-testid="copilot-read-only"
          className="flex-shrink-0 border-t border-border px-3 py-2 text-[10.5px] text-faint"
        >
          This conversation is read-only history. Start a new one to ask something else.
        </p>
      ) : items.length === 0 ? null : (
        <Composer onSend={(text) => void send(text)} busy={running} />
      )}

      {/* UX-DR8's disclaimer, in `InsightsTab`'s type size and tone. */}
      <p
        data-testid="copilot-disclaimer"
        className="flex-shrink-0 px-3 pb-2 text-[10.5px] text-faint"
      >
        {COPILOT_DISCLAIMER}
      </p>
    </div>
  );
}
