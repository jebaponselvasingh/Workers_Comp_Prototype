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
      const { data } = await api.GET("/copilot/claims/{claim_business_id}/threads", {
        params: { path: { claim_business_id: claimId! } },
      });
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
      const { data } = await api.POST("/copilot/claims/{claim_business_id}/threads", {
        params: { path: { claim_business_id: claimId } },
      });
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

/** The three frames that end a run. Exactly one arrives — the server's invariant. */
const TERMINAL_EVENTS: ReadonlySet<string> = new Set([
  SERVER_EVENTS.interrupt,
  SERVER_EVENTS.error,
  SERVER_EVENTS.done,
]);

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
 */
const TRUNCATED_STREAM: Problem = {
  type: "/problems/copilot-run-truncated",
  title: "Copilot unavailable",
  status: 503,
  detail:
    "The connection to the copilot ended before it finished answering. Anything above may be incomplete, and nothing was changed on the claim.",
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
  const response = await fetch(`/api/copilot/threads/${encodeURIComponent(threadId)}/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

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
        if (TERMINAL_EVENTS.has(translated.event)) terminated = true;
        yield translated;
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
      if (TERMINAL_EVENTS.has(tail.event)) terminated = true;
      yield tail;
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
function translate(frame: string, messageId: string): RunEvent | null {
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
      return {
        event: "messages",
        data: [{ id: messageId, type: "AIMessageChunk", content }, {}],
      };
    }
    case SERVER_EVENTS.updates:
      return { event: "updates", data: payload };
    case SERVER_EVENTS.interrupt:
      // Story 6.5 raises the first one. The frame is translated now so the
      // round trip does not need a client change when it does.
      return { event: "interrupt", data: payload };
    case SERVER_EVENTS.error:
      return { event: "error", data: payload };
    case SERVER_EVENTS.done:
      return { event: "done", data: payload };
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
