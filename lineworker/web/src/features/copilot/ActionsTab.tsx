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
 * ## The quick-action key rides the run body, not the runtime config
 *
 * `useLangGraphMessages`' `sendMessage` takes a second argument, and the `stream`
 * callback receives it as `config` — but that config carries an `abortSignal`
 * and nothing this component put in it, so a key passed that way would be
 * discarded on the way to `streamRun`. It travels beside the call instead:
 * `send` enqueues it immediately before `sendMessage` and `stream` takes it off
 * the front on the way past. That is a handoff between two functions the runtime
 * owns the call order of, and it is what makes "the key that reaches the wire is
 * the key that was clicked" true without the callback having to be rebuilt (a
 * dependency array that rebuilt `stream` would rebuild the runtime with it, and
 * with it the transcript).
 *
 * **A FIFO queue rather than one slot, and a synchronous guard on `send`.**
 * Both halves are needed and each fixes a different way the key could be the
 * wrong one — a determinism break, on the story whose whole subject is
 * determinism, and one that would surface as "Reserve review" answered by the
 * fraud node.
 *
 * - `stream` is an **async generator function**. Calling it constructs the
 *   generator and runs none of its body; the body — and therefore the read —
 *   happens at the first `next()`, an arbitrary time later. So the write in
 *   `send` and the read in `stream` are not adjacent in time, whatever they
 *   look like in the source, and a single slot is a slot the *next* run can
 *   overwrite before the first has read it. A queue makes each run take the key
 *   it was sent with, whenever it gets round to asking.
 * - `disabled={busy || running}` cannot prevent the second click on its own:
 *   `setRunning(true)` schedules a render, and two clicks dispatched in one tick
 *   both reach `send` before React commits either. `sending` is a ref, so it is
 *   true on the very next statement.
 *
 * The queue is drained as it is read, so a free-text question typed straight
 * after a quick action cannot inherit the button's key — which would be a chat
 * message silently answered by a deterministic node. An empty queue means "free
 * text", which is the honest default: a run that lost its key is a chat message,
 * never some other button's action.
 *
 * ## The 409 is an inline notice keyed to its thread
 *
 * `MeetingsSubTab`'s refusal shape (NFR-3, UX-DR11): never `alert()`, never a
 * blocking dialog, and **stored with the thread it was raised about** so that
 * "this conversation is busy" does not follow the handler onto the next one they
 * select. Both single-flight causes carry one `type`, so there is one sentence.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  appendLangChainChunk,
  useLangGraphMessages,
} from "@assistant-ui/react-langgraph";
import type { LangChainMessage } from "@assistant-ui/react-langgraph";

import {
  isThreadBusy,
  isThreadReadOnly,
  pendingAction,
  rtwDraftOf,
  streamRun,
  useClaimThreads,
  useCopilotWriteInFlight,
  useNewThread,
  useThreadTranscript,
  type ApprovalDecision,
  type CopilotRunRequest,
  type RtwDraft,
} from "@/api/copilot";
import { queryKeys } from "@/api/queryKeys";
import { useQueryClient } from "@tanstack/react-query";
import { useDiaryNav } from "@/features/diary/DiaryNav";

import { ApprovalCard } from "./ApprovalCard";
import { Composer } from "./Composer";
import { COPILOT_DISCLAIMER } from "./disclaimer";
import { QuickActions } from "./QuickActions";
import { QUICK_ACTIONS, type QuickActionKey } from "./quickActionMeta";
import { RtwLetterDialog, type RtwLetterDraft } from "./RtwLetterDialog";
import { ThreadSwitcher } from "./ThreadSwitcher";
import { Transcript, type TranscriptTurn } from "./Transcript";

/** What a busy thread reads like. Both causes, one sentence — see the router. */
const BUSY_MESSAGE =
  "⚠ This conversation is busy. A message is being answered, or the copilot is waiting for you to approve something.";

/** What a superseded thread reads like when a run is refused against it. */
const READ_ONLY_MESSAGE =
  "⚠ This conversation has been replaced by a newer one. Use the current conversation to ask something new.";

/** Everything else. A written sentence, `InsightsTab`'s `REFRESH_FAILED` rule. */
const FAILED_MESSAGE =
  "⚠ The copilot could not answer. Nothing was changed on the claim.";

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

/**
 * What a save with no version pin reads like.
 *
 * Unreachable through the modal's own opener, which only opens on a run that
 * published a pin — and stated anyway, because the alternative when it *is*
 * reached is a Save button that does nothing. See `saveLetter` on why there is
 * no fallback version.
 */
const SAVE_UNPINNED_MESSAGE =
  "⚠ This letter cannot be saved: the claim version it was drafted against is not known. Run 📄 Review RTW Policy again.";

/**
 * The handler's turn when they save the letter.
 *
 * A real message, because a save *is* a turn — the transcript should read as a
 * conversation in which somebody asked for the letter to be filed, and the
 * approval card that follows is the answer. It is fixed rather than composed,
 * so nothing a model produced reaches the wire as though a person had typed it.
 */
const SAVE_LETTER_TURN = "Save the return-to-work letter to this claim.";

/** What an empty conversation says. Never a blank pane (NFR-3). */
const EMPTY_TRANSCRIPT =
  "No messages yet. Ask a question about this claim below.";

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
  const [refusal, setRefusal] = useState<{
    threadId: string;
    message: string;
  } | null>(null);
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
    selectedThreadId &&
    items.some((thread) => thread.threadId === selectedThreadId)
      ? selectedThreadId
      : currentThreadId;
  const activeThread =
    items.find((thread) => thread.threadId === activeThreadId) ?? null;
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

  // **What each queued run is**, oldest first — widened by Story 6.5 from a
  // quick-action key to the whole run body.
  //
  // The queue and its reasoning are Story 6.4's and are argued in the module
  // docstring: `stream` is an async generator whose body runs an arbitrary time
  // after `send` constructed it, so a single slot is a slot the next run can
  // overwrite before the first has read it.
  //
  // What changed is what is queued. 6.4 queued a key and `stream` built the
  // body around it; 6.5 has three kinds of run — a message, a quick action, and
  // a resume carrying a decision — and building the body at the *call site*
  // that knows which kind it is beats a `stream` that has to reconstruct it
  // from three optional refs. It also gives the resume path what it needs for
  // free: a resume queues a body with a `command` and **no `quickAction`**, so
  // it cannot steal the next run's key, which is the determinism break 6.4's
  // review fixed and the one a resume would otherwise have reintroduced.
  //
  // Boxed rather than bare, so a run that failed before `stream` ever read it
  // can take *its own* entry back out by identity. Two runs of the same button
  // are two different objects; two runs of the same bare body might not be.
  const pendingRuns = useRef<{ body: CopilotRunRequest | null }[]>([]);

  // Whether `send` has already started a run this tick. Synchronous, because
  // `setRunning(true)` only takes effect after React commits and two clicks in
  // one tick would both get past `disabled` and both enqueue.
  const sending = useRef(false);

  // The version the last RTW draft was composed against, from the server's own
  // `updates` frame. A ref for the reason `activeThreadRef` is one — it is
  // never a rendering input, and it is written by a callback the runtime owns.
  const rtwPin = useRef<RtwDraft | null>(null);

  // See `letter` below: the run's assistant text, accumulated from its frames.
  const streamedText = useRef("");

  // The letter modal's contents, or `null` when it is closed. Local UI state,
  // AD-9: it is not a server resource, and the letter it holds is already in
  // the transcript the runtime owns.
  const [letter, setLetter] = useState<RtwLetterDraft | null>(null);

  // **The assistant text of the run that is streaming**, accumulated off the
  // frames as they pass through `stream`.
  //
  // Read by `send` after its run has finished, which is why it cannot come from
  // `turns`: that array belongs to the render that *started* the run, and a
  // ref filled from it would have to be written either during render (which
  // `react-hooks/refs` refuses) or in an effect (whose flush a resolved promise
  // does not wait for). The frames are the same bytes the transcript is built
  // from, so the letter the modal opens with is character for character the one
  // the handler watched arrive.

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
    async function* (
      messages: LangChainMessage[],
      config: { abortSignal: AbortSignal },
    ) {
      const outgoing = messages[messages.length - 1];
      const text =
        outgoing && typeof outgoing.content === "string"
          ? outgoing.content
          : "";
      const threadId = activeThreadId;
      if (!threadId) return;
      // Taken off the front **and removed**: the body belongs to the one run
      // `send` enqueued it for, and a body left behind would route the next
      // typed question down a quick action's node — or, worse, resend a
      // decision. See the module docstring on why a queue rather than the
      // runtime config or a single slot.
      //
      // An empty queue means "free text", which is the honest default: a run
      // that lost its entry is a chat message, never some other button's action
      // and never somebody's approval.
      const queued = pendingRuns.current.shift()?.body ?? null;
      // Reset per run: what is accumulated below belongs to this one.
      streamedText.current = "";
      try {
        for await (const frame of streamRun(
          threadId,
          queued ?? { message: text },
          config.abortSignal,
        )) {
          // A `messages` frame is `[chunk, metadata]` after translation, and
          // `chunk.content` is a delta. Accumulating it here rather than
          // reading the runtime's messages later is what makes the value
          // available on the statement after the run's `await`.
          if (frame.event === "messages" && Array.isArray(frame.data)) {
            const [chunk] = frame.data as [{ content?: unknown }];
            if (typeof chunk?.content === "string") {
              streamedText.current += chunk.content;
            }
          }
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
        setRefusal({
          threadId,
          message: detail ? `⚠ ${detail}` : FAILED_MESSAGE,
        });
      },
      // **The RTW draft's version pin** (Story 6.5). The server publishes it on
      // an `updates` frame during the run that drafts the letter, because the
      // save has to compare-and-swap on the version the draft was composed
      // against — a version this pane fetched when the modal opened would be a
      // newer one, and saving under it would be the force-write the approval
      // gate exists to prevent.
      //
      // A ref rather than state, `activeThreadRef`'s reason: this callback is
      // created once, and a dependency array that rebuilt it would rebuild the
      // runtime and with it the transcript.
      onUpdates: (update: unknown) => {
        const draft = rtwDraftOf(update);
        if (draft) rtwPin.current = draft;
      },
    },
  });
  const { messages, sendMessage, setMessages, interrupt, setInterrupt } =
    runtime;

  // The approval the run paused on, or `null`. **The runtime's**, not this
  // component's: `useLangGraphMessages` owns interrupt state, and owning a
  // second copy here is how a composer stays enabled while a write pends.
  const pending = pendingAction(interrupt);

  // The client half of the server's interrupt-pending 409. Named rather than
  // inlined three times, so "what disables the composer?" is one expression.
  const awaitingApproval = pending !== null;

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
  //
  // **And the pending approval is re-seeded with them** (review of Story 6.5).
  // The interrupt payload arrived on a run's terminal frame and lived in the
  // runtime's state alone, so it died with this component: switching to 📓
  // Diary and back, reloading, or opening the claim tomorrow discarded the card
  // while the server kept the thread paused — and a paused thread 409s every
  // message, so the conversation had no reachable way forward and nothing on
  // screen said why. The pause is durable in the checkpoints and the transcript
  // route now publishes it, so it is re-seeded exactly like the messages: once
  // per thread, from the same payload, in the same block.
  //
  // Written **unconditionally** rather than only when something pends, which is
  // the direction that matters on a *second* thread: selecting a conversation
  // with no pause must clear a card left over from the one before it, or the
  // handler is looking at another conversation's approval.
  const [seededThreadId, setSeededThreadId] = useState<string | null>(null);
  const loaded = history.data;
  if (
    loaded &&
    loaded.threadId === activeThreadId &&
    seededThreadId !== loaded.threadId
  ) {
    setSeededThreadId(loaded.threadId);
    setMessages(
      loaded.messages.map((message, index) => ({
        id: `${loaded.threadId}-${index}`,
        type: message.role === "user" ? "human" : "ai",
        content: message.content,
      })) as LangChainMessage[],
    );
    setInterrupt(
      loaded.pendingApproval ? { value: loaded.pendingApproval } : undefined,
    );
  }

  // Whether the messages the runtime holds are this thread's. `false` between
  // selecting a conversation and its transcript arriving, which is exactly the
  // window in which the wrong conversation used to be on screen.
  const showingThread =
    activeThreadId !== null && seededThreadId === activeThreadId;

  const turns: TranscriptTurn[] = !showingThread
    ? []
    : messages
        .map((message, index) => ({
          id: message.id ?? `turn-${index}`,
          role:
            message.type === "human"
              ? ("user" as const)
              : ("assistant" as const),
          content: plainText(message.content),
        }))
        // An assistant turn with no text is one that only called tools — the
        // server drops those from the transcript and the runtime can hold one
        // mid-stream. Written as an emptiness check rather than a length
        // comparison, for `ThreadSwitcher`'s recorded reason.
        .filter((turn) => turn.content !== "");

  // **The centre pane's deep link into the letter** (Story 6.5). The action
  // checklist's `rtw_letter` row lives in `ClaimDetailPane`, three components
  // away, so it raises a counter on the shared workspace-navigation context and
  // this fires the quick action — the same journey Story 3.5's `meetings` row
  // takes, and for the same reason there is no common ancestor to thread a
  // callback through.
  //
  // **An effect with a ref, not the render-time adjustment the other deep links
  // use**, and the difference is what the arrival triggers. `CopilotPane` and
  // `seenClaimId` adjust *state* when a prop changes, which React documents and
  // which re-renders before paint; this one starts a **network run**, and a
  // `setState` inside an effect is the cascading-render pattern the lint rule
  // refuses. A ref carries "which session have I already acted on", written in
  // the effect where refs may be written, so nothing re-renders at all.
  //
  // **The ref lives in the provider, not here, and that is Story 6.5's review.**
  // 📓 Diary is the tab the panel opens on, so the usual journey is: the
  // checklist raises the counter, `CopilotPane` switches to ⚡ Actions, and this
  // component *mounts* — at which point a `useRef(rtwRequestSession)` here
  // initialises to the value it was supposed to react to, the comparison below
  // finds no change, and the "Draft RTW Letter →" row is a dead click. From the
  // tab it already happened to be on it worked, which is why it looked fine.
  // `takeRtwRequest` is the same ref, held by `DiaryNavProvider`, which outlives
  // every mount of either tab; it answers `true` at most once per click, so a
  // later manual return to ⚡ Actions does not replay the run either.
  const { rtwRequestSession, takeRtwRequest } = useDiaryNav();
  const rtwLabel = QUICK_ACTIONS.rtw.label;
  const threadsSettled = !threads.isPending;
  useEffect(() => {
    // **Wait for a conversation before claiming the request**, which is the
    // second half of the dead click. Selecting ⚡ Actions is what mounts this
    // component, so on the deep link's own journey the thread list has not
    // arrived yet and `activeThreadId` is still `null` — and `run` returns
    // silently when it is. Leaving the request unclaimed is what lets this
    // effect fire again when the list lands; claiming it here would move the
    // dead click one layer in rather than fixing it.
    if (!threadsSettled) return;
    if (activeThreadId === null && !threads.isError) return;
    // Claimed from here on, so a deep link that arrived at a genuinely bad
    // moment — no claim, a read-only conversation, a thread list that failed —
    // is dropped rather than replayed the next time this component renders.
    if (!takeRtwRequest()) return;
    if (claimId === null || readOnly || activeThreadId === null) return;
    void send(rtwLabel, "rtw");
    // `send` is redefined on every render and is deliberately not a dependency:
    // this effect fires on a counter change and on nothing else, which is what
    // "one click, one run" means here. `rtwRequestSession` is listed even though
    // only `takeRtwRequest` is read, because it is what makes a *second* click
    // re-run the effect — the callback's identity follows it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    rtwRequestSession,
    takeRtwRequest,
    threadsSettled,
    threads.isError,
    activeThreadId,
    claimId,
    readOnly,
    rtwLabel,
  ]);

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
  const lastTurnLength =
    turns.length === 0 ? 0 : turns[turns.length - 1]!.content.length;
  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [turns.length, lastTurnLength]);

  /**
   * Start one run. **The single place a run is posted**, whichever kind it is.
   *
   * `body` is what reaches the wire and `turn` is what the runtime is told to
   * append to the transcript — two arguments because a resume has a body and no
   * turn: a decision is not something the handler said, and appending one would
   * put "approve" in the conversation as though it had been typed.
   *
   * **It returns whether the run was accepted**, and two callers need that
   * answer rather than the `refusal` state it also sets. `setRefusal` schedules
   * a render; the statement after `await run(…)` executes before React commits
   * it, so a caller reading the state would read the *previous* render's value.
   * A returned boolean is true on the next statement, which is the same reason
   * `sending` is a ref (review of Story 6.5). `false` means the server refused
   * the run outright — a 409, a 404, a dropped connection — not that the copilot
   * declined what was asked.
   */
  async function run(
    body: CopilotRunRequest,
    turn: string | null,
  ): Promise<boolean> {
    if (!activeThreadId) return false;
    // **The synchronous half of single-flight.** `disabled={busy || running}`
    // only takes effect once React has committed the render `setRunning(true)`
    // schedules, so two clicks dispatched in the same tick both arrive here
    // with the strip still enabled — and the server's 409 would refuse the
    // second run after both bodies had already been handed over. A ref is true
    // on the next statement, which is the only guarantee that holds here.
    if (sending.current) return false;
    sending.current = true;
    setRefusal(null);
    setRunning(true);
    // Enqueued immediately before the runtime is asked to run, and taken by
    // `stream` on the way past — the module docstring argues the handoff and why
    // it is a queue.
    const queued = { body };
    pendingRuns.current.push(queued);
    try {
      await sendMessage(
        turn === null ? [] : [{ type: "human", content: turn }],
        {},
      );
      return true;
    } catch (error) {
      // A run refused before `stream` ran leaves its entry behind. Removed by
      // identity, or the next run would take a failed run's body.
      pendingRuns.current = pendingRuns.current.filter(
        (entry) => entry !== queued,
      );
      setRefusal({
        threadId: activeThreadId,
        message: isThreadBusy(error)
          ? BUSY_MESSAGE
          : isThreadReadOnly(error)
            ? READ_ONLY_MESSAGE
            : FAILED_MESSAGE,
      });
      return false;
    } finally {
      sending.current = false;
      setRunning(false);
    }
  }

  /** A typed question, or a quick action — the two kinds of message run. */
  /**
   * A typed question, or a quick action — the two kinds of message run.
   *
   * **The RTW modal opens here, from the run that drafted the letter**, and not
   * from a render-time comparison of the transcript's last turn. That was the
   * first shape and it was wrong in a way only the round trip shows: a *save*
   * is also a run and its confirmation is also a new last turn, so a rule of
   * the form "a new last turn plus a pin means open the modal" re-opened the
   * modal over the approval card, holding the confirmation sentence where the
   * letter should have been — and the handler could not reach the case file
   * underneath it. Opening it from the one run that can produce a draft makes
   * "once per draft" true by construction rather than by a guard that has to be
   * right about every other run.
   */
  async function send(
    text: string,
    quickAction: QuickActionKey | null = null,
  ): Promise<void> {
    const drafting = quickAction === "rtw";
    // The pin belongs to the draft this run produces, if it produces one.
    // Cleared first so a run that publishes none cannot open the modal on a
    // version from two answers ago.
    if (drafting) rtwPin.current = null;
    await run(
      quickAction === null ? { message: text } : { message: text, quickAction },
      text,
    );
    if (!drafting) return;

    const pin = rtwPin.current;
    const drafted = streamedText.current;
    // No pin means the draft did not publish the version its save has to be
    // compare-and-swapped on — a run that failed, or a server that answered
    // something else. A modal whose Save could only invent a version is a modal
    // with a force-write in it, so there is none.
    if (pin && drafted !== "") {
      setLetter({ claimId: pin.claimId, version: pin.version, body: drafted });
    }
  }

  /**
   * Answer the pending approval. **The resume, and the one run with no turn.**
   *
   * It goes through the same `run` as everything else — the same single-flight
   * ref, the same queue, the same refusal handling — and its queued body
   * carries a `command` and no `quickAction`, which is what stops a resume
   * stealing the next run's key. The interrupt is cleared optimistically so the
   * card cannot be answered twice while the run is in flight; the server's own
   * `updates` frames clear it again on the way past, which is the runtime's
   * job and not this component's.
   *
   * **And it is put back if the run was refused** (review of Story 6.5). The
   * optimistic clear was unconditional, so a 409, a 503 or a dropped connection
   * left the card gone and the thread still paused: the write could no longer be
   * answered from this pane at all, and the only recovery was a reload. A
   * refused resume changed nothing on the server, so the pending approval is
   * exactly as pending as it was — and holding the value across the `await` is
   * what lets it be restored rather than re-fetched.
   */
  async function decide(decision: ApprovalDecision): Promise<void> {
    const held = interrupt;
    setInterrupt(undefined);
    const accepted = await run(
      { command: { resume: { decisions: [decision] } } },
      null,
    );
    if (!accepted) {
      setInterrupt(held);
      return;
    }
    // **The case file has moved, so the cache has to be told** (AD-9). An
    // approved write is the one thing the copilot does that changes a claim,
    // and it does not go through a TanStack mutation — the run is a stream, so
    // nothing invalidates on its behalf. Without this the letter was filed, the
    // server had it, and the Documents tab kept rendering the list it fetched
    // before the approval; a handler would have concluded the save had failed.
    //
    // Invalidated on **every** decision rather than only on an approval,
    // because this component cannot tell them apart usefully: an `edit` writes
    // too, and a refused or stale approval costs one refetch of a payload the
    // handler is already looking at. Guessing wrong in the other direction
    // leaves a stale case file on screen.
    if (claimId !== null) {
      void client.invalidateQueries({
        queryKey: queryKeys.claims.detail(claimId),
      });
      void client.invalidateQueries({ queryKey: queryKeys.claims.queues });
    }
  }

  /**
   * Propose the letter's save — the modal's only networked affordance.
   *
   * The body is the handler's edited text, verbatim, and the version is the one
   * the *draft* was pinned to. There is deliberately no fallback when the pin is
   * missing: a save with no version could only be built by inventing one, and an
   * invented pin is a force-write. The modal stays open and says so.
   *
   * **The modal closes only once the run has been accepted** (review of Story
   * 6.5). It closed first, so a refused save — a 409 on a busy thread, most of
   * all — destroyed the letter the handler had just spent minutes editing, and
   * there was no way back to it: re-running 📄 Review RTW Policy drafts a *new*
   * letter, and the edited one existed nowhere but in the closed dialog's state.
   * Keeping it open costs a modal over an inline notice; closing it early costs
   * somebody's work. The dialog is `busy` for the length of the run either way,
   * so Save cannot be pressed twice.
   */
  async function saveLetter(body: string): Promise<void> {
    const pin = rtwPin.current;
    if (!pin) {
      setRefusal({
        threadId: activeThreadId ?? "",
        message: SAVE_UNPINNED_MESSAGE,
      });
      return;
    }
    const accepted = await run(
      {
        message: SAVE_LETTER_TURN,
        rtwLetter: { bodyText: body, expectedVersion: pin.version },
      },
      SAVE_LETTER_TURN,
    );
    if (accepted) setLetter(null);
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
      <p
        data-testid="copilot-error"
        role="alert"
        className="p-3 text-[11px] text-error"
      >
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
        // **`awaitingApproval` joins this expression too** (review of Story
        // 6.5). It was on the quick actions and the composer and not here, and
        // the strip is the one control that can make a paused thread
        // unanswerable. Selecting another conversation re-points `decide` at
        // *that* thread, so the next Approve would resume the wrong one; and
        // "+ New" supersedes the paused thread, which then 409s a resume as
        // read-only for ever — the write stays pending in the checkpoints with
        // no route left that can answer it. Resume is the only way forward
        // while something pends, and the card is the only control that offers
        // one.
        busy={busy || running || awaitingApproval}
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

      {/* UX-DR8's seven buttons, between the greeting and the transcript — the
          prototype's own placement. Absent on a read-only thread for
          `Composer`'s reason: a greyed-out control invites a handler to work out
          why, where an absent one under "this conversation is read-only" says
          it outright. Disabled while a run is in flight, which is the client's
          half of the single-flight rule the server answers 409 for. */}
      {readOnly || items.length === 0 ? null : (
        <QuickActions
          // **`awaitingApproval` joins the disable expression** (Story 6.5).
          // The thread is single-flight and an interrupt-pending thread 409s a
          // message, so a quick action pressed while a write awaits a decision
          // would be refused by the server — and the handler would be told the
          // conversation was busy for a button they had not been stopped from
          // pressing. Resume is the only way forward, and the card is the only
          // control that offers one.
          busy={busy || running || awaitingApproval}
          onPick={(key, label) => void send(label, key)}
        />
      )}

      <div
        ref={scroller}
        data-testid="copilot-scroller"
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {items.length === 0 ? (
          // The panel mints on its own (AC 1), so this is the moment between
          // an empty list and the thread that answers it — or, when the mint
          // was refused, the state the notice below explains. "+ New" stays
          // there either way, which is what makes a refusal recoverable.
          <p
            data-testid="copilot-no-thread"
            className="p-3 text-[11px] text-faint"
          >
            {mintFailed
              ? "No conversation yet. Use + New to try again."
              : "Starting a conversation about this claim…"}
          </p>
        ) : !showingThread ? (
          <p
            data-testid="copilot-transcript-loading"
            className="p-3 text-[11px] text-faint"
          >
            {LOADING_TRANSCRIPT}
          </p>
        ) : (
          <Transcript turns={turns} emptyLabel={EMPTY_TRANSCRIPT} />
        )}
      </div>

      {pending !== null && showingThread ? (
        <ApprovalCard
          action={pending}
          busy={busy || running}
          onApprove={() => void decide({ type: "approve" })}
          onReject={() => void decide({ type: "reject" })}
        />
      ) : null}

      {mintFailed ? (
        <p
          data-testid="copilot-mint-error"
          role="status"
          className="flex-shrink-0 border-t border-border px-3 py-1.5 text-[10.5px] text-warn"
        >
          {MINT_FAILED_MESSAGE}
        </p>
      ) : null}

      {refusal?.threadId === activeThreadId && refusal !== null ? (
        <p
          data-testid="copilot-refusal"
          role="status"
          className="flex-shrink-0 border-t border-border px-3 py-1.5 text-[10.5px] text-warn"
        >
          {refusal.message}
        </p>
      ) : null}

      {readOnly ? (
        <p
          data-testid="copilot-read-only"
          className="flex-shrink-0 border-t border-border px-3 py-2 text-[10.5px] text-faint"
        >
          This conversation is read-only history. Start a new one to ask
          something else.
        </p>
      ) : items.length === 0 ? null : (
        // **`busy || running`, the same expression `QuickActions` gets**, and
        // the strip's version is the correct one: `busy` is a copilot command
        // in flight — today, a "new conversation" mint — and a message sent
        // while the thread it addresses is being replaced is a run the server
        // refuses. Blocking the buttons and leaving the input live meant a
        // handler could type past a condition they had just been stopped from
        // clicking past, and be told the conversation was busy for it.
        <Composer
          onSend={(text) => void send(text)}
          busy={busy || running || awaitingApproval}
        />
      )}

      <RtwLetterDialog
        draft={letter}
        busy={busy || running || awaitingApproval}
        onClose={() => setLetter(null)}
        onSave={(body) => void saveLetter(body)}
      />

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
