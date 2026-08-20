/**
 * `streamRun` — the SPA's one hand-rolled fetch, at the frame level (Story 6.3).
 *
 * The component tests drive this through `ActionsTab` and assert what a handler
 * sees. What they cannot see is the *shape* of what comes out of the reader, and
 * three defects in this file were invisible from a component precisely because
 * of that:
 *
 * 1. **The assistant message id was constant for the whole session.** The
 *    runtime merges a chunk into whichever message already carries its id, so a
 *    second question appended its entire answer onto the first answer's bubble.
 *    A component test that sends one message per thread — which every one of
 *    them did — cannot reach it, and neither can an e2e that reloads the
 *    transcript between turns.
 * 2. **A truncated stream resolved as a success.** No terminal frame arrived,
 *    the buffered remainder was discarded, and a half-sentence was left on
 *    screen as though the copilot had finished.
 * 3. **Abandoning the generator released the reader without cancelling it**, so
 *    the response body stayed open and the server kept streaming into it.
 *
 * All three are properties of the generator, so they are asserted on the
 * generator: `fetch` is stubbed with a `ReadableStream` this file controls, and
 * the assertions are about the events yielded and about whether the stream was
 * cancelled.
 */
import { afterEach, expect, test, vi } from "vitest";

import { sseFrame } from "@/test/api-mock";

import { type RunEvent, streamRun } from "./copilot";

const encoder = new TextEncoder();

/**
 * A `fetch` that answers one SSE body per call, and reports cancellation.
 *
 * `cancelled` is the observable the abandonment test needs: a `ReadableStream`'s
 * `cancel` callback fires when its reader is cancelled and does not fire when
 * the lock is merely released, which is exactly the distinction under test.
 */
function stubStream(
  bodies: readonly string[],
  { leaveOpen = false }: { leaveOpen?: boolean } = {},
): { cancelled: boolean[] } {
  const cancelled: boolean[] = bodies.map(() => false);
  let call = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (): Promise<Response> => {
      const index = call++;
      const body = bodies[index] ?? "";
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(body));
          // `leaveOpen` models a run that is still streaming: a closed stream
          // has nothing left to cancel, so an abandonment test against one
          // would pass whatever the generator's cleanup did.
          if (!leaveOpen) controller.close();
        },
        cancel() {
          cancelled[index] = true;
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    }),
  );
  return { cancelled };
}

/** One SSE body: two content chunks and a `done`, the ordinary success. */
const ONE_GOOD_RUN =
  sseFrame("messages", { content: "The claim is " }) +
  sseFrame("messages", { content: "in treatment." }) +
  sseFrame("done", { threadId: "claim.WC-20017.u1.s1" });

async function collect(events: AsyncGenerator<RunEvent>): Promise<RunEvent[]> {
  const seen: RunEvent[] = [];
  for await (const event of events) seen.push(event);
  return seen;
}

/** The id the runtime would merge this chunk into. */
function chunkId(event: RunEvent): string {
  const [chunk] = event.data as [{ id: string }, unknown];
  return chunk.id;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("two runs in one session use different assistant message ids", async () => {
  // The transcript-corrupting one. `appendLangChainChunk` merges by id, so a
  // constant id means the second answer is appended to the first answer's
  // bubble — the handler sees two questions and one reply that grew.
  stubStream([ONE_GOOD_RUN, ONE_GOOD_RUN]);

  const first = await collect(streamRun("claim.WC-20017.u1.s1", { message: "one" }));
  const second = await collect(streamRun("claim.WC-20017.u1.s1", { message: "two" }));

  const firstIds = first.filter((event) => event.event === "messages").map(chunkId);
  const secondIds = second.filter((event) => event.event === "messages").map(chunkId);

  // Within a run the id is stable — that is what makes streaming accumulate.
  expect(new Set(firstIds).size).toBe(1);
  expect(new Set(secondIds).size).toBe(1);
  // Across runs it is not — that is what keeps two answers two answers.
  expect(firstIds[0]).not.toBe(secondIds[0]);
});

test("a stream that ends without a terminal frame is reported as an error", async () => {
  // The connection dropped mid-answer. The server's invariant is exactly one
  // terminal frame per run, so none means the run did not end — it stopped, and
  // whatever arrived is a fragment.
  stubStream([sseFrame("messages", { content: "The reserve is " })]);

  const events = await collect(streamRun("claim.WC-20017.u1.s1", { message: "one" }));

  const last = events.at(-1)!;
  expect(last.event).toBe("error");
  expect((last.data as { type: string }).type).toBe("/problems/copilot-run-truncated");
  // …and the partial content still arrived, because it did — the notice is
  // about what the handler may rely on, not about hiding what was said.
  expect(events.filter((event) => event.event === "messages")).toHaveLength(1);
});

test("a final frame with no trailing blank line is delivered, not discarded", async () => {
  // SSE separates frames with a blank line, so the buffered remainder after the
  // last separator used to be dropped on the floor. A server that wrote its
  // terminal frame and was cut off before the separator — or a proxy that
  // trimmed it — therefore looked exactly like a truncated stream, and the
  // whole answer was reported as a failure.
  const withoutSeparator = ONE_GOOD_RUN.slice(0, -1);
  stubStream([withoutSeparator]);

  const events = await collect(streamRun("claim.WC-20017.u1.s1", { message: "one" }));

  expect(events.at(-1)!.event).toBe("done");
  expect(events.filter((event) => event.event === "error")).toHaveLength(0);
});

test("abandoning a run cancels the response body", async () => {
  // What the panel does when it is unmounted mid-run: the runtime finalises the
  // generator, and a generator that only released its reader left the body open
  // — the browser kept reading, the server kept writing, and the run kept the
  // thread's advisory lock until it had finished talking to nobody.
  const { cancelled } = stubStream([ONE_GOOD_RUN], { leaveOpen: true });

  const run = streamRun("claim.WC-20017.u1.s1", { message: "one" });
  await run.next();
  await run.return(undefined);

  expect(cancelled[0]).toBe(true);
});

test("a run read to completion releases its reader rather than cancelling it", async () => {
  // The other half, so the assertion above is about *when* rather than about
  // "always cancel": a finished stream has nothing to cancel, and cancelling one
  // surfaces as a spurious network error in the console.
  const { cancelled } = stubStream([ONE_GOOD_RUN]);

  await collect(streamRun("claim.WC-20017.u1.s1", { message: "one" }));

  expect(cancelled[0]).toBe(false);
});
