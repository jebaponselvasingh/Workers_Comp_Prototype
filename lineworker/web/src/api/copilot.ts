/**
 * Server state for the copilot: threads, transcripts, and the run stream
 * (Story 6.3, AD-9).
 *
 * The three read/write hooks are ordinary `openapi-fetch` calls in the shape
 * `useClaimInsights`/`useRefreshInsights` established. The fourth thing here is
 * not, and it needs the exemption argued rather than assumed.
 *
 * ## The hand-rolled-fetch exemption, argued
 *
 * `api/client.ts` says it in as many words: "Nothing in `web/` may hand-roll a
 * fetch to `/api`." The rule exists because the generated client is what makes
 * a renamed backend field a TypeScript error here rather than a runtime
 * `undefined` in front of a user, and because one client is where the problem+json
 * translation and the credential handling live.
 *
 * **SSE is the first thing `openapi-fetch` cannot express.** It returns a
 * parsed body, and a `text/event-stream` has no body until it is over; there is
 * no option, middleware or response type in that library that hands back a
 * `ReadableStream` of frames as they arrive. So `streamRun` below opens the one
 * hand-rolled `fetch` in the SPA, and it is scoped to exactly that: the request
 * *body* is still the generated `RunRequest` type, the failure path still goes
 * through `ApiError` and the same RFC 9457 members every other call reads, and
 * the URL is built from the same `/api` base. What is hand-rolled is the frame
 * reader and nothing else.
 *
 * AD-9's actual rule — "no hand-rolled streaming" — is about the *runtime*: token
 * accumulation, message reconciliation, cancellation and the interrupt UI come
 * from `@assistant-ui/react-langgraph` rather than from a component holding a
 * string it keeps appending to. That is untouched. `streamRun` is the transport
 * adapter the runtime's own `stream` callback exists to be given.
 *
 * ## Why the events are translated here
 *
 * The server speaks this build's own copilot stream convention — `messages`,
 * `updates`, `interrupt`, `error` and exactly one terminal `done` — because that
 * convention is what the spine fixed and what the server tests assert. The
 * assistant-ui runtime speaks LangGraph Platform's vocabulary
 * (`messages/partial`, `messages/complete`, `updates`, `error`). `streamRun`
 * maps one onto the other, which is a dozen lines in one place instead of a
 * server that has to spell its events twice.
 */
import {
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "./client";
import { ApiError, type Problem, problemType } from "./errors";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type CopilotThread = components["schemas"]["ThreadResponse"];
export type CopilotThreads = components["schemas"]["ThreadListResponse"];
export type CopilotTranscript = components["schemas"]["TranscriptResponse"];
export type CopilotMessage = components["schemas"]["TranscriptMessage"];
export type CopilotRunRequest = components["schemas"]["RunRequest"];
export type CopilotAvailability =
  components["schemas"]["AvailabilityResponse"];
export type CopilotQuickActionFlag =
  components["schemas"]["QuickActionAvailability"];

/**
 * Why an AI run stopped — the server's closed vocabulary, mirrored **once**.
 *
 * `api/routers/copilot.py::StreamErrorCode` is the declaration and this is the
 * mirror, spelled here rather than read off `schema.d.ts` for
 * `quickActionMeta.ts`'s reason in reverse: the code rides on a problem
 * document *inside an SSE frame*, and an SSE payload has no OpenAPI schema to
 * generate from — the runs endpoint's response body is `text/event-stream`.
 * So the union is written out, and the e2e spec is what keeps the two honest:
 * it asserts the literal `ai_unavailable` off a real frame from a real server
 * with the model container stopped.
 *
 * **The array is the declaration and the type is derived from it**, which is the
 * form the argument above actually requires. The first cut of this story
 * declared the union and then wrote the same five literals out again as a
 * `readonly StreamErrorCode[]` so that `streamErrorCode` could test membership —
 * two copies of a vocabulary, in the same file, in a diff whose central claim is
 * that a vocabulary has exactly one copy, with nothing keeping them in step: a
 * sixth code added to the type alone would have compiled and then been reported
 * as `null` for ever. `as const` plus an indexed access type costs one line and
 * makes the array the single source.
 *
 * **`code` answers a coarser question than `type` does**, which is why both
 * exist. `problemType` tells a component *which* failure happened and is what
 * the two 409 predicates below read; this tells it *why the AI stopped*, which
 * is the granularity a disabled composer needs — the panel does not care
 * whether the model was unreachable on a quick action or on free chat, only
 * that it was.
 */
const STREAM_ERROR_CODES = [
  "ai_unavailable",
  "ai_limit",
  "copilot_empty_answer",
  "copilot_approval_unreadable",
  "copilot_run_failed",
] as const;

export type StreamErrorCode = (typeof STREAM_ERROR_CODES)[number];

/**
 * The single-flight refusal — a run is streaming, or an approval is pending.
 *
 * Two causes and one `type`, which is the server's decision and the right one:
 * a caller cannot act differently on them, and telling them apart would be
 * telling them about a run they cannot see.
 */
export const THREAD_BUSY = "/problems/thread-busy";

/** The superseded-thread refusal — this conversation is read-only history. */
export const THREAD_READ_ONLY = "/problems/thread-read-only";

/** Whether a failed run was refused because the thread is busy. */
export function isThreadBusy(error: unknown): boolean {
  return problemType(error) === THREAD_BUSY;
}

/** Whether a failed run was refused because the thread has been superseded. */
export function isThreadReadOnly(error: unknown): boolean {
  return problemType(error) === THREAD_READ_ONLY;
}

/**
 * The `code` on a problem document, if it carries one this build knows.
 *
 * Beside `problemType` in spirit and beside `isThreadBusy`/`isThreadReadOnly`
 * in placement: one narrowing, here, so no component holds a cast. Anything
 * outside the union reads as `null` rather than throwing — a copilot pane that
 * crashed on an unrecognised code would take the whole workspace column with
 * it, which is `pendingAction`'s recorded rule and the same one applies.
 *
 * It takes the raw `error` object the runtime's `onError` hands over rather
 * than an `ApiError`, because that is where this is read: a mid-stream failure
 * is a *frame* carrying the problem document inline, never a rejection, so it
 * never becomes an `ApiError` at all.
 */
export function streamErrorCode(error: unknown): StreamErrorCode | null {
  if (typeof error !== "object" || error === null) return null;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" && (STREAM_ERROR_CODES as readonly string[]).includes(code)
    ? (code as StreamErrorCode)
    : null;
}

/**
 * Whether a run ended because the local model server did not answer.
 *
 * **The reactive half of the degradation signal** (Story 6.6, AD-14). The
 * availability query below polls, and a poll is by definition behind: the run
 * that actually hit the outage knows about it first and knows it for certain,
 * because it is the one that tried. So the panel marks the model unavailable
 * from this the moment an `error` frame carries it, rather than leaving the
 * handler to press a disabled-looking button again while the interval elapses.
 *
 * It reads the `code` and not the `type`, deliberately. A second `type` for the
 * same outage — a future `/problems/ai-unavailable-on-resume`, say — would have
 * to be added to a list here to keep working; a code is what the vocabulary is
 * for.
 */
export function isAiUnavailable(error: unknown): boolean {
  return streamErrorCode(error) === "ai_unavailable";
}

/**
 * How often the panel re-asks whether the model is up. **The SPA's first poll.**
 *
 * `api/dashboard.ts` records "nothing in the app polls" as a deliberate stance
 * and `queryClient.ts` turns `refetchOnWindowFocus` off, so this is a departure
 * and it needs its reason stated rather than assumed.
 *
 * Availability is the one piece of state in this console that **changes without
 * any user action and whose staleness silently costs the handler work**. Every
 * other server fact the SPA holds moves because somebody moved it — a claim is
 * edited, a payment is approved, a note is written — so a refetch on the next
 * mutation or the next mount is exactly right. A model container restarting is
 * nobody's action, and a panel that only learned about it by sending a message
 * would be discovering the outage the way this story exists to stop it being
 * discovered: by failing.
 *
 * Fifteen seconds, and the arithmetic is deliberate. The server caches its
 * probe for `ai_health_probe_cache_seconds` (ten), so a poll faster than that
 * would cost requests to receive an identical cached answer; a poll much slower
 * would leave the composer disabled for a noticeable stretch after a recovery.
 * The manual "Try again" control exists so a handler never has to wait for it
 * at all — see `useRecheckAvailability`, which is what makes that sentence true
 * rather than merely written — and there is deliberately no automatic re-fire
 * of the failed run when the flag flips back (NFR-6): recovery re-enables the
 * input, it does not re-ask the question.
 */
export const AVAILABILITY_POLL_MS = 15_000;

/**
 * Whether the local model is answering, and which quick actions need it.
 *
 * One query for two facts, which is the server's decision and the right one: a
 * client that knew which buttons need a model but not whether one is up — or
 * the reverse — still could not disable the right set, and two queries would
 * let the panel render a half-decided state while one of them was in flight.
 *
 * **Not `enabled`-gated on a claim.** The answer is a property of the
 * deployment, so the panel can hold it before it knows which case file it is
 * showing, and the Insights tab's Refresh control reads the same cache entry
 * without paying for a second request.
 *
 * `staleTime` is deliberately absent: the interval is what governs freshness
 * here, and a stale time longer than it would silently cancel the poll.
 *
 * **This hook never sends `force`.** The poll wants the server's cached answer —
 * that cache is what makes an endpoint every open panel polls cost one upstream
 * request per ten seconds regardless of how many panels there are. Bypassing it
 * is a human's decision and has its own hook below.
 */
export function useCopilotAvailability() {
  return useQuery({
    queryKey: queryKeys.copilot.availability,
    queryFn: async (): Promise<CopilotAvailability> => {
      const { data } = await api.GET("/copilot/availability");
      return data!;
    },
    refetchInterval: AVAILABILITY_POLL_MS,
  });
}

/**
 * "Try again" — ask the model server **now**, not the ten-second-old answer.
 *
 * The panel's manual retry, and it is a mutation rather than the query's own
 * `refetch()` for a reason the review of this story found the hard way. Three
 * docstrings in this build promised that pressing the button "shortens to zero"
 * the wait after a recovery, and AC 5 rests on it — but `refetch()` re-issues
 * `GET /copilot/availability`, which the server answers out of
 * `ModelAvailabilityProbe`'s cache. Inside that window the retry handed back the
 * identical stale `false`, so a handler who had just restarted their model
 * container pressed a control that could not help them and had no way to know
 * why. `?force=true` is the fix; the cache stays, because coalescing the *poll*
 * is what it is for.
 *
 * A `useMutation` because the semantics are a mutation's: it happens because
 * somebody pressed something, exactly once per press, and it exposes
 * `isPending` for the button's own disabled state. The result is written into
 * the poll's cache entry with `setQueryData` rather than left beside it —
 * everything that renders a disabled input reads that one entry, and a second
 * source of truth about availability inside the SPA would be the client-side
 * version of the mistake the server side spends `agents/degradation.py` arguing
 * against.
 *
 * `setQueryData` also stamps `dataUpdatedAt`, which is what clears
 * `ActionsTab`'s reactive outage latch: a probe answered *after* the run that
 * failed supersedes it, with no second flag to reset.
 *
 * **It re-fires nothing** (NFR-6). What it refreshes is a fact about the
 * deployment; asking the question again is the handler's decision, not this
 * hook's.
 */
export function useRecheckAvailability() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<CopilotAvailability> => {
      const { data } = await api.GET("/copilot/availability", {
        params: { query: { force: true } },
      });
      return data!;
    },
    onSuccess: (fresh) => {
      client.setQueryData<CopilotAvailability>(
        queryKeys.copilot.availability,
        fresh,
      );
    },
  });
}

/**
 * One claim's conversations for this caller, plus the seeded greeting.
 *
 * **No `staleTime`**, unlike `useClaimInsights`' five minutes, and the contrast
 * is the point: an insight is a cache with a visible generation timestamp, so
 * re-reading it costs requests to receive identical rows. A thread list changes
 * whenever the handler starts a conversation — including in another tab — and it
 * is a handful of rows. The default zero staleness is right.
 *
 * The greeting rides on this payload rather than on the transcript because it
 * is a property of the *claim*: every thread on one claim opens with the same
 * one, and it renders before the first message exists.
 */
export function useClaimThreads(claimId: string | null) {
  return useQuery({
    queryKey: queryKeys.copilot.threads(claimId ?? ""),
    queryFn: async (): Promise<CopilotThreads> => {
      const { data } = await api.GET(
        "/copilot/claims/{claim_business_id}/threads",
        {
          params: { path: { claim_business_id: claimId! } },
        },
      );
      return data!;
    },
    enabled: claimId !== null,
  });
}

/**
 * Start a new conversation on a claim — the "new conversation" affordance.
 *
 * **The same call the panel makes when a claim has no conversation at all.**
 * One server operation behind two affordances: mint `max(seq) + 1`. The panel's
 * first-open path issues it once when the list comes back empty, which is one
 * extra round trip on the first conversation and none thereafter.
 *
 * It carries `queryKeys.copilot.writes` and **not** `claims.writes`: minting a
 * thread bumps no claim version, and carrying that key would grey out the
 * severity score and the comp-rate input on the case file for no reason a
 * handler could see (`queryKeys.copilot` argues it at length).
 *
 * Only the thread list is invalidated. A conversation is not a claim fact, so
 * no queue card, no top-bar tile and no case file changes because one started.
 */
export function useNewThread(claimId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.copilot.writes,
    mutationFn: async (): Promise<CopilotThread> => {
      const { data } = await api.POST(
        "/copilot/claims/{claim_business_id}/threads",
        {
          params: { path: { claim_business_id: claimId } },
        },
      );
      return data!;
    },
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: queryKeys.copilot.threads(claimId),
        exact: true,
      });
    },
  });
}

/**
 * One conversation's transcript, read back from the checkpoints.
 *
 * This is what makes navigation and re-login survivable (FR-CP-2): the messages
 * come out of the server's checkpoint store, so a reload, a logout or the API
 * process being replaced leaves them exactly where they were.
 *
 * **`staleTime: Infinity` for a superseded thread would be tempting and is not
 * done.** A prior thread genuinely never changes, but the *current* one changes
 * on every run and this hook serves both; a per-thread staleness would be a
 * second place the read-only rule lives, and the one that goes wrong is the one
 * that caches a live conversation for ever. Invalidation after a run is what
 * keeps it fresh, and there is exactly one thing that invalidates it.
 */
export function useThreadTranscript(threadId: string | null) {
  return useQuery({
    queryKey: queryKeys.copilot.thread(threadId ?? ""),
    queryFn: async (): Promise<CopilotTranscript> => {
      const { data } = await api.GET("/copilot/threads/{thread_id}/messages", {
        params: { path: { thread_id: threadId! } },
      });
      return data!;
    },
    enabled: threadId !== null,
  });
}

/** Whether any copilot command is in flight — see `queryKeys.copilot.writes`. */
export function useCopilotWriteInFlight(): boolean {
  return useIsMutating({ mutationKey: queryKeys.copilot.writes }) > 0;
}

/**
 * The pending tool call an approval card renders — the server's, verbatim.
 *
 * **`snake_case`, and that is not a slip.** Every other payload in this file is
 * camelCase because it came through Pydantic's alias generator; this one is
 * `HumanInTheLoopMiddleware`'s own `HITLRequest`, passed through the server
 * untouched precisely so that what a handler approves is the payload that will
 * execute rather than something this build reshaped on the way past (AD-16). A
 * rename here would be the first step of a paraphrase.
 *
 * `args` is `Record<string, unknown>` because the shape is the write tool's own
 * argument schema and differs per tool. The card renders it as labelled
 * key/value rows without knowing which tool it belongs to, which is what keeps
 * a third write tool from needing a third card.
 */
export interface PendingAction {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

/** The whole interrupt payload: what is pending, and what may be decided. */
export interface PendingApprovalRequest {
  action_requests: PendingAction[];
  review_configs: { action_name: string; allowed_decisions: string[] }[];
}

/**
 * A decision, in the shape the resume body carries it.
 *
 * Three, matching `agents/approval.ALLOWED_DECISIONS`. `edit` is declared even
 * though the v1 card offers two buttons: the server guards and tests it, and a
 * type that omitted it would make adding the affordance a change to this file
 * as well as to a component.
 */
export type ApprovalDecision =
  | { type: "approve" }
  | { type: "reject" }
  | {
      type: "edit";
      edited_action: { name: string; args: Record<string, unknown> };
    };

/**
 * The one pending action inside the runtime's interrupt state, or `null`.
 *
 * The runtime hands back `{value?: unknown}` because a LangGraph interrupt can
 * carry anything. This narrows it once, here, so no component holds a cast —
 * and it returns `null` rather than throwing for a shape it does not recognise,
 * because a copilot pane that crashed on an unexpected payload would take the
 * whole workspace column with it.
 *
 * Exactly one action, because AD-6 caps the graph at one pending write. A
 * payload carrying two would be a server-side defect, and rendering only the
 * first would hide half of it — so it is refused instead.
 */
export function pendingAction(
  interrupt: { value?: unknown } | undefined,
): PendingAction | null {
  const value = interrupt?.value;
  if (typeof value !== "object" || value === null) return null;
  const requests = (value as { action_requests?: unknown }).action_requests;
  if (!Array.isArray(requests) || requests.length !== 1) return null;
  const [action] = requests as PendingAction[];
  if (!action || typeof action.name !== "string") return null;
  return action;
}

/**
 * The RTW draft's version pin, out of an `updates` frame, or `null`.
 *
 * The number the letter's save is compare-and-swapped on, read at draft time by
 * the server's `rtw_reader` and published on the run that drafted the letter
 * (`agents/qas.RTW_DRAFT`). The modal cannot fetch its own: a version read when
 * the modal *opened* would be newer than the one the letter was composed
 * against, and saving under it would be the force-write the approval gate
 * exists to prevent.
 */
export interface RtwDraft {
  claimId: string;
  version: number;
}

/** `rtwDraft` off one `updates` payload, narrowed — see `RtwDraft`. */
export function rtwDraftOf(update: unknown): RtwDraft | null {
  if (typeof update !== "object" || update === null) return null;
  const draft = (update as { rtwDraft?: unknown }).rtwDraft;
  if (typeof draft !== "object" || draft === null) return null;
  const { claimId, version } = draft as {
    claimId?: unknown;
    version?: unknown;
  };
  if (typeof claimId !== "string" || typeof version !== "number") return null;
  return { claimId, version };
}

/**
 * One frame of a run, in the vocabulary the assistant-ui runtime consumes.
 *
 * `data` is deliberately `unknown`-ish rather than a discriminated union: the
 * runtime narrows it itself per event, and a second narrowing here would be a
 * second place the wire format is described.
 */
export interface RunEvent {
  event: string;
  data: unknown;
}

/**
 * The server's own event names — this build's copilot stream convention.
 *
 * Named rather than inlined because they are a contract with
 * `api/routers/copilot.py`, and a string spelled in two places is a string that
 * stops matching the day one of them changes.
 */
const SERVER_EVENTS = {
  messages: "messages",
  updates: "updates",
  interrupt: "interrupt",
  error: "error",
  done: "done",
} as const;

/**
 * The three frames that end a run. Exactly one arrives — the server's invariant.
 *
 * **Read against the *server's* event name, never the translated one** (Story
 * 6.5). `interrupt` is delivered to the runtime as an `updates` frame, because
 * that is the only shape it reads — so a terminality check performed after
 * translation would decide that a paused run had never terminated and would
 * synthesise `TRUNCATED_STREAM` on top of a perfectly good approval. `translate`
 * therefore reports terminality itself, from the name it read off the wire.
 */
const TERMINAL_EVENTS: ReadonlySet<string> = new Set([
  SERVER_EVENTS.interrupt,
  SERVER_EVENTS.error,
  SERVER_EVENTS.done,
]);

/**
 * One translated frame, plus whether the *server* called it a terminal one.
 *
 * The extra field exists only because the interrupt's translation changes its
 * event name; see `TERMINAL_EVENTS`.
 */
interface TranslatedFrame {
  frame: RunEvent;
  terminal: boolean;
}

/**
 * The problem document a stream that simply stopped is reported as.
 *
 * Synthesised here rather than received, because a connection that dropped
 * mid-answer sends nothing at all — and the alternative is what this used to do:
 * resolve as though the run had succeeded, leaving a half-sentence on screen
 * with no notice that it was half. A partial answer presented as a complete one
 * is the failure mode the spec's I/O matrix names in its own row.
 *
 * Shaped as the same four RFC 9457 members every other failure in the SPA
 * carries, so `onError` needs no second branch to read it. `status: 503` because
 * that is what the server would have said had it been able to say anything.
 *
 * **And a `code`, since Story 6.6**, because a frame without one would be the
 * single hole in a vocabulary whose whole value is being closed —
 * `streamErrorCode` would answer `null` for it and every future reader would
 * need a branch for that. `copilot_run_failed` is the honest member: a
 * connection that ended early is the catch-all's own case, "the copilot could
 * not finish answering", and this build genuinely does not know why.
 *
 * It is deliberately **not** `ai_unavailable`. A dropped stream is a transport
 * failure — a closed laptop lid, a proxy, a redeploy — and marking the model
 * unavailable from it would disable the composer over something the model
 * server had no part in. A real outage sends a real frame that says so.
 */
const TRUNCATED_STREAM: Problem & { code: StreamErrorCode } = {
  type: "/problems/copilot-run-truncated",
  title: "Copilot unavailable",
  status: 503,
  detail:
    "The connection to the copilot ended before it finished answering. Anything above may be incomplete, and nothing was changed on the claim.",
  code: "copilot_run_failed",
};

/**
 * One run's assistant message id. **Minted per run, never per session.**
 *
 * `appendLangChainChunk` merges a chunk into the message that already carries
 * its id, which is exactly what makes streaming work — and exactly what made a
 * fixed `"assistant"` a transcript-corrupting bug: the second question in a
 * session appended its whole answer onto the *first* answer's bubble, so a
 * handler saw two questions and one ever-growing reply. A counter rather than
 * `crypto.randomUUID()` because it needs to be unique within a page, not
 * unguessable, and because the ids show up in test failures where a readable
 * one is worth having.
 */
let runSeq = 0;

/**
 * Stream one run. The SPA's only hand-rolled fetch — see the module docstring.
 *
 * Yields frames as they arrive, translated into the runtime's vocabulary. The
 * generator ends when the stream does; the terminal event is yielded first, so a
 * caller that wants to know how a run ended reads the last frame rather than
 * inspecting the absence of more.
 *
 * **A refusal is an `ApiError` thrown before the first frame**, never a frame.
 * A 409 or a 404 arrives as an ordinary problem+json response with a non-200
 * status, because it is decided before the stream begins — which is exactly why
 * the single-flight answer can be a status code at all. Once a run has started
 * the status is already 200 and a failure can only be an `error` frame carrying
 * the same four RFC 9457 members inline.
 */
export async function* streamRun(
  threadId: string,
  body: CopilotRunRequest,
  signal?: AbortSignal,
): AsyncGenerator<RunEvent> {
  const response = await fetch(
    `/api/copilot/threads/${encodeURIComponent(threadId)}/runs`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
  );

  if (!response.ok || !response.body) {
    throw await problemFrom(response);
  }

  const messageId = `assistant-${++runSeq}`;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  let terminated = false;
  let drained = false;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line. Everything before the last
      // separator is complete; whatever follows is a partial frame and stays in
      // the buffer — the read boundary is a network artefact and lands
      // mid-frame routinely.
      const frames = buffered.split("\n\n");
      buffered = frames.pop() ?? "";
      for (const frame of frames) {
        const translated = translate(frame, messageId);
        if (!translated) continue;
        if (translated.terminal) terminated = true;
        yield translated.frame;
      }
    }
    drained = true;

    // **What is left in the buffer is a frame, not a leftover.** A server that
    // wrote its last frame without the trailing blank line — or a proxy that cut
    // the connection between the frame and the separator — leaves a complete,
    // parseable frame here, and discarding it silently dropped terminal events.
    // `translate` returns `null` for anything that is not one, so a genuine
    // fragment costs nothing.
    buffered += decoder.decode();
    const tail = translate(buffered, messageId);
    if (tail) {
      if (tail.terminal) terminated = true;
      yield tail.frame;
    }

    // **A stream that stopped is a failure, not a success.** The server's
    // invariant is exactly one terminal frame per run; if none arrived, the
    // connection ended early and whatever is on screen is a fragment. Saying so
    // is the difference between "the copilot answered" and "the copilot was cut
    // off", which is a difference a handler acting on a reserve figure needs.
    if (!terminated) {
      yield { event: SERVER_EVENTS.error, data: TRUNCATED_STREAM };
    }
  } finally {
    if (drained) {
      // Releasing rather than cancelling: the stream is over, and cancelling a
      // finished reader would surface as a spurious network error in the
      // console.
      reader.releaseLock();
    } else {
      // **Cancelled, because the consumer left early.** A generator abandoned
      // mid-run — the panel unmounted, the handler switched tab — used only to
      // release the lock, which leaves the response body open: the browser keeps
      // reading, the server keeps streaming, and the run keeps its advisory lock
      // until it finishes talking to nobody. Cancelling propagates back through
      // the response body and closes it.
      //
      // Awaited rather than fired and forgotten, so the body is demonstrably
      // closed by the time this generator is finished with; `.catch` because a
      // reader whose fetch was already aborted rejects here, and a cleanup path
      // that threw would replace whatever ended the run.
      await reader.cancel().catch(() => undefined);
    }
  }
}

/**
 * One raw SSE frame → one runtime event, or `null` for a comment or junk.
 *
 * Keepalives are `: keepalive` comment lines and are dropped here, which is what
 * a conforming client does with a comment: they exist so an idle proxy does not
 * hang up on a cold model, and no code above this line should have to know
 * about them.
 */
function translate(frame: string, messageId: string): TranslatedFrame | null {
  let name = "";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice("event: ".length).trim();
    else if (line.startsWith("data: ")) data.push(line.slice("data: ".length));
  }
  if (!name || data.length === 0) return null;

  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return null;
  }

  const terminal = TERMINAL_EVENTS.has(name);
  const as = (event: RunEvent): TranslatedFrame => ({ frame: event, terminal });

  switch (name) {
    case SERVER_EVENTS.messages: {
      // **A chunk, not a partial message**, and the distinction is the one
      // thing about this translation that is easy to get wrong. LangGraph
      // Platform's `messages/partial` carries the assistant turn *so far* —
      // cumulative — and the runtime installs it wholesale; its `messages`
      // event carries a `AIMessageChunk` and the runtime **appends** it
      // (`appendLangChainChunk`). This server streams deltas, so the second is
      // the honest mapping. Sent as `messages/partial` the transcript showed
      // only the last delta, which looked like a truncated answer rather than
      // like a mis-mapped event.
      //
      // **The id is fixed for the length of *this run*, and minted by
      // `streamRun`.** The runtime needs a stable id to append onto, and a
      // thread is single-flight so exactly one assistant message is in flight
      // at a time — but "one at a time" is not "one ever". A constant
      // `"assistant"` made the second question in a session append its answer
      // onto the first answer's bubble, because that is precisely what merging
      // by id means. `runSeq` is what makes the comment above true rather than
      // aspirational.
      const content = (payload as { content?: string }).content ?? "";
      return as({
        event: "messages",
        data: [{ id: messageId, type: "AIMessageChunk", content }, {}],
      });
    }
    case SERVER_EVENTS.updates:
      return as({ event: "updates", data: payload });
    case SERVER_EVENTS.interrupt:
      // **Translated into an `updates` frame carrying `__interrupt__`, because
      // that is the only shape the vendored runtime reads** (Story 6.5, AD-9:
      // the runtime's protocol wins). `@assistant-ui/react-langgraph` 0.14.24
      // sets its interrupt state in exactly one place — the `Updates` branch of
      // `useLangGraphMessages`, from `chunk.data.__interrupt__?.[0]` — and has
      // no `interrupt` case at all. A frame emitted under that name was
      // therefore parsed, dispatched and dropped: `interrupt` and `setInterrupt`
      // stayed `undefined` and the approval card had nothing to render.
      //
      // The alternative was `onCustomEvent`, which would have meant this
      // component owning the interrupt state the runtime already owns — and
      // owning it in a second place is how the composer stays enabled while a
      // write pends. So the server's frame is reshaped here, in the one file
      // that already exists to reshape frames, and `interrupt`/`setInterrupt`
      // come for free.
      //
      // `value` is the middleware's own `HITLRequest`, passed through untouched:
      // the approval card renders the pending tool call the server is holding,
      // never a paraphrase of it (AD-16), and a translation that summarised it
      // here would be exactly that paraphrase.
      return as({
        event: "updates",
        data: {
          __interrupt__: [{ value: (payload as { value?: unknown }).value }],
        },
      });
    case SERVER_EVENTS.error:
      return as({ event: "error", data: payload });
    case SERVER_EVENTS.done:
      return as({ event: "done", data: payload });
    default:
      return null;
  }
}

/**
 * A non-200 run response as the `ApiError` every other call in the SPA throws.
 *
 * `client.ts`'s middleware does this for the generated client; the same
 * synthesis is repeated here because the hand-rolled fetch does not pass through
 * it. Every field is filled in for that module's recorded reason: a proxy 404 or
 * a gateway 502 carries no problem envelope, and an undefined `title` renders as
 * an error with no reason attached.
 */
async function problemFrom(response: Response): Promise<ApiError> {
  let body: Partial<Problem>;
  try {
    body = (await response.json()) as Partial<Problem>;
  } catch {
    body = {};
  }
  const problem: Problem = {
    type: body.type ?? "about:blank",
    title: body.title ?? response.statusText ?? "Request failed",
    status: body.status ?? response.status,
    detail: body.detail ?? `The server answered ${response.status}.`,
  };
  return new ApiError(problem, body);
}
