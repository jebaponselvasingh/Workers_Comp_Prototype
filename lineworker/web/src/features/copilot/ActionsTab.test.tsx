/**
 * The ⚡ Actions tab — the states only a browser can be in (Story 6.3).
 *
 * The server tests cover the wire: minting, sequences, single-flight, the
 * transcript round trip. What is left for this file is the set of things the API
 * contract cannot describe, which is what a component test is for:
 *
 * 1. **The three undescribable states** — loading, error, and "no conversation
 *    yet". The third is not a failure and must not read as one (NFR-3): it is
 *    the state every claim is in on a fresh deployment, and it has an affordance
 *    rather than a message.
 * 2. **The 409 as an inline notice**, keyed to the thread it belongs to.
 *    `MeetingsSubTab`'s refusal shape — never `alert()`, never a blocking
 *    dialog, and never following the handler onto a different conversation.
 * 3. **A superseded thread renders read-only**, with the composer *absent*
 *    rather than disabled.
 * 4. **A quick action puts a key on the run body** (Story 6.4). The strip is
 *    mounted here, hidden on a read-only thread, disabled while a run is in
 *    flight, and — the assertion that matters — the `quickAction` key it sends
 *    is what reaches the wire, rather than only the runtime config the `stream`
 *    callback discards. `copilotRunBodies` is what makes that visible at all: a
 *    quick action and a chat message are the same route, the same stream and the
 *    same message, differing only by one key on the request.
 * 5. **Raw HTML in a model's answer renders as text and creates no element**
 *    (AC 7). This is the one assertion in the suite that is about a security
 *    property rather than about a behaviour, and it is asserted on the DOM
 *    rather than on the markdown configuration — a test that read the plugin
 *    list would be testing the code it was reading.
 *
 * `page.on("dialog")`'s equivalent is here too: `window.alert` is stubbed to
 * throw, so a refusal that reached for it fails the test that provoked it rather
 * than being noticed by a reviewer months later (Story 4.1's spec makes the same
 * move in Playwright).
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import {
  COPILOT_AVAILABLE,
  COPILOT_RUN_AI_LIMIT,
  COPILOT_RUN_AI_UNAVAILABLE,
  COPILOT_RUN_INTERRUPT,
  COPILOT_RUN_OK,
  COPILOT_RUN_RTW_DRAFT,
  COPILOT_THREAD_BUSY,
  COPILOT_THREADS,
  COPILOT_THREADS_EMPTY,
  COPILOT_THREADS_TWO,
  COPILOT_TRANSCRIPT,
  COPILOT_TRANSCRIPT_EMPTY,
  COPILOT_TRANSCRIPT_PENDING,
  COPILOT_UNAVAILABLE,
  copilotRunBodies,
  sseFrame,
  stubApi,
  type StubRoutes,
} from "@/test/api-mock";

import { ActionsTab } from "./ActionsTab";
import { COPILOT_DISCLAIMER } from "./disclaimer";

/**
 * Renders the pane and hands back its query client.
 *
 * The client is returned (rather than created inline, as it was until Story
 * 6.6) so a test can drive a **refetch** the way the fifteen-second poll would.
 * That is the only way to write the one degradation case a component test can
 * otherwise not reach: the availability query itself starting to fail. See
 * `an availability query that starts failing does not latch the outage`.
 */
function renderTab(
  routes: Partial<StubRoutes> = {},
  claimId: string | null = "WC-20017",
) {
  stubApi({
    copilotThreads: COPILOT_THREADS,
    copilotTranscript: COPILOT_TRANSCRIPT,
    ...routes,
  });
  const client = createQueryClient();
  render(
    <QueryClientProvider client={client}>
      <ActionsTab claimId={claimId} />
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  // NFR-3 / UX-DR11: refusals are inline, never a blocking dialog. The
  // prototype's copilot had no refusals at all; the console's must not learn
  // the habit its scheduler had (`alert('Please select a date.')`).
  vi.stubGlobal(
    "alert",
    vi.fn(() => {
      throw new Error("a copilot refusal reached window.alert");
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- the three states the server cannot describe -------------------------

test("a pending thread list is a loading state, not a blank pane", async () => {
  renderTab({ copilotThreads: "pending" });

  expect(await screen.findByTestId("copilot-loading")).toBeInTheDocument();
  expect(screen.queryByTestId("copilot-composer")).not.toBeInTheDocument();
});

test("a failed thread list is an alert, not an empty conversation", async () => {
  // A 404 rather than a 500, and the choice is about the *retry policy* rather
  // than about the branch: `createQueryClient` retries a 5xx twice with
  // backoff — correctly, because a gateway hiccup is worth one more try — so a
  // 500 fixture would spend three seconds proving something about TanStack.
  // A 404 is also the refusal this route really produces (a claim outside the
  // caller's book), and the pane renders every error identically.
  renderTab({
    copilotThreads: {
      status: 404,
      body: {
        type: "/problems/claim-not-found",
        title: "Not Found",
        status: 404,
        detail: "No claim WC-20017 in your caseload.",
      },
    },
  });

  const error = await screen.findByTestId("copilot-error");
  expect(error).toHaveAttribute("role", "alert");
  expect(screen.queryByTestId("copilot-transcript")).not.toBeInTheDocument();
});

test("a claim with no conversation is minted by the SPA, exactly once (AC 1)", async () => {
  // **Amended into its opposite** (AD-15). This test asserted that the pane
  // renders "start a conversation with + New" and waits — which is what it did,
  // and which is not what AC 1 or either docstring says: the SPA `POST`s once
  // when the list comes back empty, and nothing did.
  //
  // "Once" is half the assertion and the harder half. A mint driven by an
  // effect over server state re-fires every time that state settles, and the
  // list this one is watching stays empty in this fixture — so a guard that was
  // not keyed on the claim would `POST` for ever.
  const minted: string[] = [];
  renderTab({
    copilotThreads: COPILOT_THREADS_EMPTY,
    copilotNewThread: (url) => {
      minted.push(url);
      return {
        status: 201,
        body: {
          threadId: "claim.WC-20017.u1.s1",
          conversationSeq: 1,
          isCurrent: true,
          createdAt: "2026-08-20T09:00:00Z",
        },
      };
    },
  });

  await waitFor(() => expect(minted).toHaveLength(1));
  // The greeting is there regardless — it is a property of the claim, not of a
  // conversation — and this is not an error state.
  expect(screen.getByTestId("copilot-greeting")).toHaveTextContent(
    "Case summary for WC-20017",
  );
  expect(screen.getByTestId("copilot-new-thread")).toBeEnabled();
  expect(screen.queryByTestId("copilot-error")).not.toBeInTheDocument();

  // …and it stays once. The list never fills in this fixture, so a second POST
  // here would be the beginning of an unbounded loop against the server.
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(minted).toHaveLength(1);
});

test("a refused mint is an inline notice, not a dead click", async () => {
  // `useNewThread` shipped with no `onError`, so a 409 — the one raised when
  // "new conversation" is pressed while the current one is still answering — was
  // a button that did nothing and said nothing.
  renderTab({
    copilotThreads: COPILOT_THREADS_EMPTY,
    copilotNewThread: {
      status: 409,
      body: {
        type: "/problems/thread-busy",
        title: "Conflict",
        status: 409,
        detail:
          "A new conversation cannot be started while the current one is still answering.",
      },
    },
  });

  const notice = await screen.findByTestId("copilot-mint-error");
  expect(notice).toHaveAttribute("role", "status");
  expect(notice).toHaveTextContent(/could not be started/i);
  // The affordance survives the refusal, which is what makes it recoverable.
  expect(screen.getByTestId("copilot-new-thread")).toBeEnabled();
});

test("with no claim selected the pane says so rather than fetching", async () => {
  renderTab({}, null);

  expect(await screen.findByTestId("copilot-no-claim")).toBeInTheDocument();
});

// --- the seeded greeting and the disclaimer ------------------------------

test("the seeded greeting and the disclaimer both render (UX-DR8)", async () => {
  renderTab();

  // The greeting is deterministic server output (AD-2), so this asserts it is
  // *rendered* rather than asserting on prose a model wrote — which is the only
  // kind of assertion AD-15 allows about model output, and the reason the
  // greeting is not model output at all.
  expect(await screen.findByTestId("copilot-greeting")).toHaveTextContent(
    "Case summary for WC-20017",
  );
  expect(screen.getByTestId("copilot-disclaimer")).toHaveTextContent(
    COPILOT_DISCLAIMER,
  );
});

// --- the transcript ------------------------------------------------------

test("the checkpointed transcript is what the pane opens with (FR-CP-2)", async () => {
  // The conversation lives on the server, so a fresh mount — which is what a
  // reload or a re-login is — reads it back rather than starting empty.
  renderTab();

  const transcript = await screen.findByTestId("copilot-transcript");
  expect(transcript).toHaveTextContent("what is the status of this claim?");
  expect(transcript).toHaveTextContent("The claim is in treatment.");
  expect(screen.getAllByTestId("copilot-turn-user")).toHaveLength(1);
  expect(screen.getAllByTestId("copilot-turn-assistant")).toHaveLength(1);
});

test("the transcript is a live region, so a streamed answer is announced", async () => {
  // A handler using a screen reader presses Send and the reply materialises
  // below them with nothing focused. Without a live region nothing announces it
  // — this is the one place in the console where content appears entirely on
  // its own.
  renderTab();

  const transcript = await screen.findByTestId("copilot-transcript");
  expect(transcript).toHaveAttribute("aria-live", "polite");
  // Not `assertive`: an answer is worth announcing at the next pause, never
  // worth interrupting what the reader is already saying.
  expect(transcript).not.toHaveAttribute("aria-live", "assertive");
});

test("switching conversation never shows the previous one under the new one", async () => {
  // The transcript is read back per thread, so there is a window between
  // selecting a conversation and its checkpoints arriving. The runtime holds
  // the *previous* thread's messages for the whole of it, and they were
  // rendered — a handler switching conversations (or clicking through the
  // queue) read one conversation's turns under another's heading.
  renderTab({
    copilotThreads: COPILOT_THREADS_TWO,
    copilotTranscript: (url) =>
      url.includes("u1.s1")
        ? {
            status: 200,
            body: {
              threadId: "claim.WC-20017.u1.s1",
              isCurrent: false,
              messages: [{ role: "user", content: "the first conversation" }],
            },
          }
        : // The thread being switched *to* never answers, which is the window
          // under test held open.
          "pending",
  });

  // Thread 2 is current, so its (pending) transcript is what the pane opens on;
  // select thread 1 and let it render, then go back.
  await userEvent.selectOptions(
    await screen.findByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s1",
  );
  await screen.findByTestId("copilot-transcript");
  expect(screen.getByTestId("copilot-transcript")).toHaveTextContent(
    "the first conversation",
  );

  await userEvent.selectOptions(
    screen.getByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s2",
  );

  expect(
    await screen.findByTestId("copilot-transcript-loading"),
  ).toBeInTheDocument();
  expect(screen.queryByText("the first conversation")).not.toBeInTheDocument();
});

test("an empty conversation says so rather than rendering a blank list", async () => {
  renderTab({ copilotTranscript: COPILOT_TRANSCRIPT_EMPTY });

  expect(
    await screen.findByTestId("copilot-transcript-empty"),
  ).toBeInTheDocument();
  expect(screen.getByTestId("copilot-composer")).toBeInTheDocument();
});

test("raw HTML in an assistant turn renders as text and creates no element", async () => {
  // AC 7, and the assertion that matters most in this file. `react-markdown`
  // builds React elements from an AST, so a `<script>` is a text node — but
  // that is only true while nothing adds `rehype-raw` or reaches for
  // `dangerouslySetInnerHTML`, and this is what would notice.
  renderTab({
    copilotTranscript: {
      status: 200,
      body: {
        threadId: "claim.WC-20017.u1.s1",
        isCurrent: true,
        messages: [
          {
            role: "assistant",
            content:
              '<script>alert(1)</script><img src="x" onerror="alert(2)">',
          },
        ],
      },
    },
  });

  const turn = await screen.findByTestId("copilot-turn-assistant");
  expect(turn).toHaveTextContent("<script>alert(1)</script>");
  expect(turn.querySelector("script")).toBeNull();
  expect(turn.querySelector("img")).toBeNull();
});

test("a link in an assistant turn is rendered as text, never as an anchor", async () => {
  // AD-16: URLs in model output are never auto-fetched by server or client, and
  // an `<a href>` in a transcript is a fetch one click away — aimed at a URL
  // that may have come out of a claim narrative somebody else wrote.
  renderTab({
    copilotTranscript: {
      status: 200,
      body: {
        threadId: "claim.WC-20017.u1.s1",
        isCurrent: true,
        messages: [
          {
            role: "assistant",
            content: "See [the policy](https://example.test/x).",
          },
        ],
      },
    },
  });

  const turn = await screen.findByTestId("copilot-turn-assistant");
  expect(turn).toHaveTextContent("the policy");
  expect(turn.querySelector("a")).toBeNull();
});

// --- the run and its refusals -------------------------------------------

test("sending a message streams an answer into the transcript", async () => {
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_OK },
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "what next?",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));

  // The two `messages` frames are accumulated into one assistant turn by the
  // runtime — which is what "no hand-rolled streaming" buys, and would show up
  // here as two half-messages if this component were appending strings itself.
  await waitFor(() =>
    expect(screen.getByTestId("copilot-transcript")).toHaveTextContent(
      "The claim is in treatment.",
    ),
  );
  expect(screen.queryByTestId("copilot-refusal")).not.toBeInTheDocument();
});

test("a new turn scrolls the transcript to the bottom", async () => {
  // A streaming answer that grows past the fold with nothing scrolling is an
  // answer the handler has to go looking for — and the container that scrolls
  // is `ActionsTab`'s, so nothing in `Transcript` could have done it.
  //
  // jsdom has no layout, so `scrollHeight` is stubbed: what is under test is
  // that the component *assigns* `scrollTop` when the conversation grows, which
  // is the whole of the behaviour.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_OK },
  });

  const scroller = await screen.findByTestId("copilot-scroller");
  Object.defineProperty(scroller, "scrollHeight", {
    value: 640,
    configurable: true,
  });

  await userEvent.type(screen.getByTestId("copilot-input"), "what next?");
  await userEvent.click(screen.getByTestId("copilot-send"));

  await waitFor(() => expect(scroller.scrollTop).toBe(640));
});

test("a 409 is an inline notice, never a dialog", async () => {
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: COPILOT_THREAD_BUSY,
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "second question",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));

  const refusal = await screen.findByTestId("copilot-refusal");
  expect(refusal).toHaveTextContent(/busy/i);
  expect(refusal).toHaveAttribute("role", "status");
  // The composer stays usable: a busy thread is a "try again in a moment", not
  // a dead pane.
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
});

test("a refusal does not follow the handler onto another conversation", async () => {
  // `MeetingsSubTab`'s lesson, restated: a refusal belongs to the thread it was
  // raised about. Without the key, "this conversation is busy" would be sitting
  // under a different conversation the handler switched to.
  renderTab({
    copilotThreads: COPILOT_THREADS_TWO,
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: COPILOT_THREAD_BUSY,
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "second question",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));
  await screen.findByTestId("copilot-refusal");

  await userEvent.selectOptions(
    screen.getByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s1",
  );

  expect(screen.queryByTestId("copilot-refusal")).not.toBeInTheDocument();
});

// --- read-only history ---------------------------------------------------

test("a superseded conversation is read-only and has no composer at all", async () => {
  // Absent rather than disabled: a greyed-out input invites a handler to work
  // out why, where an absent one and a sentence say it outright. The server
  // refuses a run against it regardless (409 `/problems/thread-read-only`), so
  // this is the honest rendering of a rule rather than the enforcement of it.
  renderTab({
    copilotThreads: COPILOT_THREADS_TWO,
    copilotTranscript: {
      status: 200,
      body: {
        threadId: "claim.WC-20017.u1.s1",
        isCurrent: false,
        messages: [{ role: "user", content: "the first conversation" }],
      },
    },
  });

  await userEvent.selectOptions(
    await screen.findByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s1",
  );

  expect(await screen.findByTestId("copilot-read-only")).toBeInTheDocument();
  expect(screen.queryByTestId("copilot-composer")).not.toBeInTheDocument();
  // …and its transcript is still readable, which is the point of keeping it.
  expect(screen.getByTestId("copilot-transcript")).toHaveTextContent(
    "the first conversation",
  );
});

test("the switcher is hidden while there is only one conversation", async () => {
  // A dropdown that never has two entries reads as a broken control rather than
  // as a simple one. "+ New" is always there, because that is the affordance.
  renderTab();

  expect(await screen.findByTestId("copilot-new-thread")).toBeEnabled();
  expect(screen.queryByTestId("copilot-thread-picker")).not.toBeInTheDocument();
});

test("the frames of a run that ends in an error surface as a notice", async () => {
  // The mid-stream failure: the status is already 200 by the time the model
  // stops answering, so the failure can only be an `error` frame carrying the
  // problem document inline. What a handler must not see is a half-answer
  // presented as a complete one.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: {
      sse: [
        sseFrame("messages", { content: "The reserve is " }),
        sseFrame("error", {
          type: "/problems/copilot-run-failed",
          title: "Copilot unavailable",
          status: 503,
          detail:
            "The copilot could not finish answering. Nothing was changed on the claim.",
          // The fifth member since Story 6.6. Present here so the fixture is
          // what the server actually sends, and `copilot_run_failed` rather
          // than `ai_unavailable` because this frame is the catch-all — the
          // assertion below is that it does *not* disable anything.
          code: "copilot_run_failed",
        }),
      ],
    },
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "what is the reserve?",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));

  await waitFor(() =>
    expect(screen.getByTestId("copilot-refusal")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("copilot-refusal")).toHaveTextContent(/could not/i);
});

// --- Story 6.4: the quick actions ----------------------------------------

test("the seven quick actions render above the transcript", async () => {
  // UX-DR8's placement, asserted as *order in the DOM* rather than as CSS: the
  // greeting says what the claim is, the buttons say what can be asked about it,
  // and the answers appear underneath. A strip that rendered below the scroller
  // would satisfy every other assertion in this file.
  renderTab();

  const strip = await screen.findByTestId("copilot-quick-actions");
  expect(screen.getAllByTestId("copilot-quick-action")).toHaveLength(7);

  const greeting = screen.getByTestId("copilot-greeting");
  const scroller = screen.getByTestId("copilot-scroller");
  expect(
    greeting.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    strip.compareDocumentPosition(scroller) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("clicking a quick action posts its key with the button's label", async () => {
  // **The assertion this story turns on.** A quick action is a `quickAction`
  // key on the run body and nothing else — same route, same stream, same
  // message — so "the button ran a reserve quick action" and "the button sent a
  // chat message saying Reserve review" are indistinguishable from the DOM. The
  // recorded body is the only place they differ, and the key had to reach
  // `streamRun`'s body rather than the runtime config, which the `stream`
  // callback discards.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_OK },
  });

  const reserve = (await screen.findAllByTestId("copilot-quick-action")).find(
    (button) => button.dataset.quickAction === "reserve",
  )!;
  await userEvent.click(reserve);

  await waitFor(() => expect(copilotRunBodies).toHaveLength(1));
  expect(copilotRunBodies[0]).toEqual({
    message: "Reserve review",
    quickAction: "reserve",
  });

  // …and the answer streams into the transcript exactly as a typed one does.
  await waitFor(() =>
    expect(screen.getByTestId("copilot-transcript")).toHaveTextContent(
      "The claim is in treatment.",
    ),
  );
});

test("a typed question after a quick action carries no key", async () => {
  // The key is a property of one run, and it is cleared as it is read. Without
  // that, the next thing a handler typed would be silently answered by a
  // deterministic node — a chat message routed to a quick action, with nothing
  // on screen to say so.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_OK },
  });

  const fraud = (await screen.findAllByTestId("copilot-quick-action")).find(
    (button) => button.dataset.quickAction === "fraud",
  )!;
  await userEvent.click(fraud);
  await waitFor(() => expect(copilotRunBodies).toHaveLength(1));

  await userEvent.type(screen.getByTestId("copilot-input"), "and what next?");
  await userEvent.click(screen.getByTestId("copilot-send"));

  await waitFor(() => expect(copilotRunBodies).toHaveLength(2));
  expect(copilotRunBodies[1]).toEqual({ message: "and what next?" });
});

test("the quick actions are absent on a read-only conversation", async () => {
  // Absent rather than disabled, the composer's rule and its reason: a
  // greyed-out control invites a handler to work out why, where an absent one
  // under "this conversation is read-only" says it outright.
  renderTab({
    copilotThreads: COPILOT_THREADS_TWO,
    copilotTranscript: {
      status: 200,
      body: {
        threadId: "claim.WC-20017.u1.s1",
        isCurrent: false,
        messages: [{ role: "user", content: "the first conversation" }],
      },
    },
  });

  await userEvent.selectOptions(
    await screen.findByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s1",
  );

  expect(await screen.findByTestId("copilot-read-only")).toBeInTheDocument();
  expect(screen.queryByTestId("copilot-quick-actions")).not.toBeInTheDocument();
});

test("a quick action clicked while a run is in flight posts nothing", async () => {
  // The client half of single-flight. The server would answer 409 and the pane
  // would render it as a notice — correct, and a refusal the handler could not
  // have avoided. So the buttons disable, and the assertion is that **no second
  // body reached the wire**, which is stronger than the disabled attribute: a
  // handler could not have clicked it, and neither could anything else.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: "pending",
  });

  const buttons = await screen.findAllByTestId("copilot-quick-action");
  await userEvent.click(
    buttons.find((b) => b.dataset.quickAction === "similar")!,
  );

  await waitFor(() => expect(copilotRunBodies).toHaveLength(1));
  await waitFor(() =>
    expect(screen.getAllByTestId("copilot-quick-action")[0]).toBeDisabled(),
  );

  await userEvent.click(screen.getAllByTestId("copilot-quick-action")[1]!);
  expect(copilotRunBodies).toHaveLength(1);
});

test("two quick actions clicked in one tick cannot swap keys", async () => {
  // **The determinism break this story is most exposed to**, and it would read
  // as a model bug: "Reserve review" answered by the fraud node.
  //
  // Two mechanisms had to be wrong together for the key to travel correctly, and
  // both were. `stream` is an async generator function — calling it builds the
  // generator and runs none of its body, so the key was read an arbitrary time
  // after `send` wrote it, not on the next line. And `disabled={busy || running}`
  // does not take effect until React commits the render `setRunning(true)`
  // schedules, so two clicks dispatched inside one tick both reached `send` past
  // an enabled button. A single slot plus a late read is a run carrying the
  // *other* run's key.
  //
  // Dispatched natively inside one `act` rather than through two `userEvent`
  // clicks, because `userEvent` awaits between them and React commits in
  // between — which is the case the existing disabled-strip test covers, and
  // not this one.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_OK },
  });

  const buttons = await screen.findAllByTestId("copilot-quick-action");
  const reserve = buttons.find(
    (button) => button.dataset.quickAction === "reserve",
  )!;
  const fraud = buttons.find(
    (button) => button.dataset.quickAction === "fraud",
  )!;

  await act(async () => {
    reserve.click();
    fraud.click();
  });

  // One run, and it is the one that was clicked first — never a body carrying
  // the first run's message under the second run's key.
  await waitFor(() => expect(copilotRunBodies).toHaveLength(1));
  expect(copilotRunBodies[0]).toEqual({
    message: "Reserve review",
    quickAction: "reserve",
  });

  // …and the queue drained with it, so the next action takes its own key rather
  // than the one the dropped click left behind.
  await waitFor(() =>
    expect(screen.getAllByTestId("copilot-quick-action")[0]).toBeEnabled(),
  );
  await userEvent.click(
    screen
      .getAllByTestId("copilot-quick-action")
      .find((button) => button.dataset.quickAction === "fraud")!,
  );
  await waitFor(() => expect(copilotRunBodies).toHaveLength(2));
  expect(copilotRunBodies[1]).toEqual({
    message: "Fraud risk check",
    quickAction: "fraud",
  });
});

// --- Story 6.5: the approval gate, and the RTW letter --------------------

test("an interrupt renders the server's pending tool call, not a paraphrase", async () => {
  // AC 5 / AD-16. The card shows the tool name and **every** typed argument the
  // middleware is holding, because a card that curated them would be a
  // paraphrase one layer down — and a handler approving a paraphrase has
  // approved something they were not shown.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_INTERRUPT },
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "reserve",
    )!,
  );

  const card = await screen.findByTestId("copilot-approval");
  expect(card.dataset.tool).toBe("update_claim_field");
  expect(screen.getByTestId("copilot-approval-tool")).toHaveTextContent(
    "update_claim_field",
  );

  const shown = screen
    .getAllByTestId("copilot-approval-arg")
    .map((row) => row.dataset.arg)
    .sort();
  expect(shown).toEqual([
    "claim_business_id",
    "expected_version",
    "field",
    "value",
  ]);
  expect(card).toHaveTextContent("Laceration of left hand");
  expect(card).toHaveTextContent("4");
});

test("a pending approval disables the composer and the quick actions", async () => {
  // The client half of the server's interrupt-pending 409. A composer left live
  // while a write awaits a decision is a message the server refuses, and the
  // handler is told the conversation is busy for a control nothing stopped them
  // using. Resume is the only way forward, and the card is the only thing that
  // offers one.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_INTERRUPT },
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "reserve",
    )!,
  );
  await screen.findByTestId("copilot-approval");

  await waitFor(() =>
    expect(screen.getAllByTestId("copilot-quick-action")[0]).toBeDisabled(),
  );
  expect(screen.getByTestId("copilot-input")).toBeDisabled();
});

test("approving resumes with a decision and carries no quick-action key", async () => {
  // **The half a resume could break** (Story 6.4's review, one story on): the
  // run queue carries a body per run, so a resume enqueues one with a `command`
  // and no `quickAction`. A resume that pushed a quick-action entry would steal
  // the *next* run's key, which is the determinism break this pane has already
  // had once.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRunSequence: [COPILOT_RUN_INTERRUPT, COPILOT_RUN_OK],
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "reserve",
    )!,
  );
  await screen.findByTestId("copilot-approval");

  await userEvent.click(screen.getByTestId("copilot-approve"));

  await waitFor(() => expect(copilotRunBodies).toHaveLength(2));
  expect(copilotRunBodies[1]).toEqual({
    command: { resume: { decisions: [{ type: "approve" }] } },
  });
  // …and the card is gone, so the decision cannot be given twice.
  await waitFor(() =>
    expect(screen.queryByTestId("copilot-approval")).toBeNull(),
  );
});

test("rejecting resumes with a reject decision and writes nothing else", async () => {
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRunSequence: [COPILOT_RUN_INTERRUPT, COPILOT_RUN_OK],
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "reserve",
    )!,
  );
  await screen.findByTestId("copilot-approval");
  await userEvent.click(screen.getByTestId("copilot-reject"));

  await waitFor(() => expect(copilotRunBodies).toHaveLength(2));
  expect(copilotRunBodies[1]).toEqual({
    command: { resume: { decisions: [{ type: "reject" }] } },
  });
});

test("the RTW action opens the modal, and print and copy post nothing", async () => {
  // AC 4 and the story's own I/O row: Print and Copy are gate-free because they
  // are not writes. The assertion is a request count, because "no proposal was
  // made" and "a proposal was made and refused" are indistinguishable from the
  // modal.
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  const print = vi.fn();
  vi.stubGlobal("print", print);

  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_RTW_DRAFT },
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "rtw",
    )!,
  );

  const body = await screen.findByTestId("rtw-body");
  expect(body).toHaveTextContent("Modified duty is available.");

  await userEvent.click(screen.getByTestId("rtw-print"));
  await userEvent.click(screen.getByTestId("rtw-copy"));

  expect(print).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  // One run — the draft's — and nothing else.
  expect(copilotRunBodies).toHaveLength(1);
});

test("saving the letter sends the handler's edit and the drafted version", async () => {
  // AC 10's client half. The body that reaches the wire is what the handler
  // typed, and the version is the one the *draft* was pinned to — not one the
  // modal fetched, which would be newer and would let a save succeed against a
  // claim that had moved.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRunSequence: [COPILOT_RUN_RTW_DRAFT, COPILOT_RUN_INTERRUPT],
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "rtw",
    )!,
  );
  await screen.findByTestId("rtw-body");

  await userEvent.click(screen.getByTestId("rtw-edit"));
  const input = screen.getByTestId("rtw-body-input");
  await userEvent.clear(input);
  await userEvent.type(input, "Handler wrote this.");
  await userEvent.click(screen.getByTestId("rtw-save"));

  await waitFor(() => expect(copilotRunBodies).toHaveLength(2));
  expect(copilotRunBodies[1]).toEqual({
    message: "Save the return-to-work letter to this claim.",
    rtwLetter: { bodyText: "Handler wrote this.", expectedVersion: 4 },
  });
  // …and the save's own run pauses at the gate, which is the whole point of it.
  await screen.findByTestId("copilot-approval");
});

test("a thread paused on an approval re-seeds its card on mount", async () => {
  // **The card outlives this component, because the pause does** (review of
  // Story 6.5). The interrupt payload used to reach the browser on a run's
  // terminal frame and live in the runtime's state alone, so switching to 📓
  // Diary and back — or reloading, or opening the claim the next morning —
  // discarded it while the server kept the thread paused. A paused thread 409s
  // every message, so the conversation had no reachable way forward and nothing
  // on screen said why.
  //
  // This mount has seen no run at all: the card can only come from the
  // transcript the server just answered with.
  renderTab({ copilotTranscript: COPILOT_TRANSCRIPT_PENDING });

  const card = await screen.findByTestId("copilot-approval");
  expect(card.dataset.tool).toBe("update_claim_field");
  expect(card).toHaveTextContent("Laceration of left hand");
  // …and it is a real pause, so the controls a 409 would refuse are shut.
  expect(screen.getByTestId("copilot-input")).toBeDisabled();
  expect(copilotRunBodies).toHaveLength(0);
});

test("a thread with no pause re-seeds no card", async () => {
  // The negative control the test above needs to mean anything: a card that
  // appeared on every mount would satisfy it and would show one conversation's
  // approval over another's transcript.
  renderTab({ copilotTranscript: COPILOT_TRANSCRIPT });

  await screen.findByTestId("copilot-transcript");
  expect(screen.queryByTestId("copilot-approval")).toBeNull();
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
});

test("a refused resume puts the approval card back", async () => {
  // The card is cleared optimistically so it cannot be answered twice while the
  // run is in flight — and it was cleared *unconditionally*, so a 409, a 503 or
  // a dropped connection left the handler with no card and a thread the server
  // still considered paused. The write became unanswerable from this pane, and
  // the only recovery was a reload.
  //
  // A refused resume changed nothing on the server, so the approval is exactly
  // as pending as it was.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRunSequence: [COPILOT_RUN_INTERRUPT, COPILOT_THREAD_BUSY],
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "reserve",
    )!,
  );
  await screen.findByTestId("copilot-approval");

  await userEvent.click(screen.getByTestId("copilot-approve"));

  await screen.findByTestId("copilot-refusal");
  const restored = await screen.findByTestId("copilot-approval");
  expect(restored.dataset.tool).toBe("update_claim_field");
  expect(restored).toHaveTextContent("Laceration of left hand");
});

test("a pending approval also shuts the conversation switcher", async () => {
  // The strip is the one control that can make a paused thread *unanswerable*.
  // Selecting another conversation re-points the pane's resume at that thread,
  // so the next Approve answers the wrong one; "+ New" supersedes the paused
  // thread, which then refuses a resume as read-only for ever and leaves the
  // proposed write pending in the checkpoints with no route that can answer it.
  renderTab({
    copilotThreads: COPILOT_THREADS_TWO,
    copilotTranscript: {
      ...COPILOT_TRANSCRIPT_PENDING,
      body: {
        ...COPILOT_TRANSCRIPT_PENDING.body,
        threadId: "claim.WC-20017.u1.s2",
      },
    },
  });

  await screen.findByTestId("copilot-approval");

  expect(screen.getByTestId("copilot-new-thread")).toBeDisabled();
  expect(screen.getByTestId("copilot-thread-picker")).toBeDisabled();
});

test("a refused save keeps the letter modal open with the handler's text", async () => {
  // The modal closed before the run was accepted, so a refusal — the 409 on a
  // busy thread, most of all — destroyed the letter the handler had just spent
  // minutes editing. There was no way back to it: re-running 📄 Review RTW
  // Policy drafts a *new* letter, and the edited one existed nowhere but in the
  // closed dialog's state.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRunSequence: [COPILOT_RUN_RTW_DRAFT, COPILOT_THREAD_BUSY],
  });

  await userEvent.click(
    (await screen.findAllByTestId("copilot-quick-action")).find(
      (button) => button.dataset.quickAction === "rtw",
    )!,
  );
  await screen.findByTestId("rtw-body");

  await userEvent.click(screen.getByTestId("rtw-edit"));
  const input = screen.getByTestId("rtw-body-input");
  await userEvent.clear(input);
  await userEvent.type(input, "Handler wrote this.");
  await userEvent.click(screen.getByTestId("rtw-save"));

  await screen.findByTestId("copilot-refusal");
  expect(await screen.findByTestId("rtw-body-input")).toHaveValue(
    "Handler wrote this.",
  );
});

// --- Story 6.6: honest degradation ---------------------------------------

/** The six keys the server declares `requiresLlm` — read off the fixture, not
 *  re-listed, so this file holds no second copy of server truth either. */
const LLM_BACKED_KEYS = COPILOT_UNAVAILABLE.body.quickActions
  .filter((flag) => flag.requiresLlm)
  .map((flag) => flag.key);

/** One quick-action button, by the key it sends. */
function actionButton(key: string): HTMLElement {
  return screen
    .getAllByTestId("copilot-quick-action")
    .find((button) => button.dataset.quickAction === key)!;
}

test("an unavailable model disables exactly the composer and the six LLM actions", async () => {
  // AD-14/UX-DR8's central claim, and the one a coarser implementation would
  // fail in the direction that matters: the pane stays alive. `data_alignment`
  // declares `requires_llm: false` and answers without a model, so a strip that
  // greyed out all seven would have taken away the one action that still works.
  renderTab({ copilotAvailability: COPILOT_UNAVAILABLE });

  await screen.findByTestId("copilot-quick-actions");
  await waitFor(() => expect(screen.getByTestId("copilot-input")).toBeDisabled());

  // **Not `expect(copilot-send).toBeDisabled()`.** `Composer` disables send
  // whenever the draft is empty, and the draft is always empty at this point —
  // that assertion passed on a fully working pane and proved nothing, which is
  // what the review of this story found. The claim that carries weight is that
  // the form refuses to start a run: the input cannot be typed into, and a
  // submit reaching `Composer` while `disabled` is set posts nothing.
  fireEvent.submit(screen.getByTestId("copilot-composer"));
  expect(copilotRunBodies).toHaveLength(0);

  for (const key of LLM_BACKED_KEYS) {
    expect(actionButton(key)).toBeDisabled();
  }
  expect(actionButton("data_alignment")).toBeEnabled();

  // …and the rest of the pane is untouched. Degradation is the *disabled* case,
  // never the absent one: the transcript, the greeting and "+ New" all stay.
  expect(screen.getByTestId("copilot-composer")).toBeInTheDocument();
  expect(screen.getByTestId("copilot-transcript")).toBeInTheDocument();
  expect(screen.getByTestId("copilot-greeting")).toBeInTheDocument();
  expect(screen.getByTestId("copilot-new-thread")).toBeEnabled();
});

test("an unavailable model shows an inline warn notice with a manual retry", async () => {
  // UX-DR11/NFR-3: inline, never a dialog and never a toast — the `beforeEach`
  // above already fails any test that reaches for `window.alert`. `role=status`
  // rather than `alert` because an outage is a state the pane is in rather than
  // an interruption, which is also how the refusal notice beside it is written.
  renderTab({ copilotAvailability: COPILOT_UNAVAILABLE });

  const notice = await screen.findByTestId("copilot-degraded");
  expect(notice).toHaveAttribute("role", "status");
  expect(notice).toHaveTextContent(/AI is unavailable/i);
  expect(screen.getByTestId("copilot-degraded-retry")).toBeEnabled();
});

test("an available model disables nothing at all", async () => {
  // The positive control. Every assertion above would also pass against a pane
  // that disabled its inputs permanently.
  renderTab();

  await screen.findByTestId("copilot-quick-actions");
  expect(screen.queryByTestId("copilot-degraded")).not.toBeInTheDocument();
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
  for (const key of LLM_BACKED_KEYS) {
    expect(actionButton(key)).toBeEnabled();
  }

  // …and the send button, which needs a draft before its state says anything at
  // all. This is the control the degraded test above cannot make: with the same
  // typed text and a working model, send is live.
  await userEvent.type(screen.getByTestId("copilot-input"), "what is the reserve?");
  expect(screen.getByTestId("copilot-send")).toBeEnabled();
});

test("an ai_limit run reports the ceiling and disables nothing", async () => {
  // The second `ai_limit` shape on the client (Story 6.6). A completion that
  // hit `copilot_max_output_tokens` is a *bound this deployment set* being
  // reached, not a model server that is down — so the pane says what happened
  // and leaves every input live, which is the opposite of what it does for
  // `ai_unavailable`. A panel that greyed its composer here would be disabling
  // inputs over a model answering perfectly well.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_AI_LIMIT },
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "tell me everything",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));

  expect(await screen.findByTestId("copilot-refusal")).toBeInTheDocument();
  expect(screen.queryByTestId("copilot-degraded")).not.toBeInTheDocument();
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
  expect(actionButton("reserve")).toBeEnabled();
  // …and what streamed before the stop is still on screen. AC 4's "whatever
  // streamed before the stop remains in the transcript", from the pane's side.
  expect(screen.getByTestId("copilot-turn-assistant")).toHaveTextContent(
    "Deterministic partial answer",
  );
});

test("a pending availability query disables nothing — unknown reads as available", async () => {
  // A slow probe must never block a working model. The failure this prevents is
  // the worst kind of degradation bug: a console that disables its copilot
  // because it has not yet been told the copilot is fine.
  renderTab({ copilotAvailability: "pending" });

  await screen.findByTestId("copilot-quick-actions");
  expect(screen.queryByTestId("copilot-degraded")).not.toBeInTheDocument();
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
  expect(actionButton("reserve")).toBeEnabled();
});

test("an ai_unavailable frame marks the model down without waiting for the poll", async () => {
  // The reactive half (Story 6.6). The availability query says the model is up —
  // it answered before the outage — and the run is what discovers otherwise. A
  // pane that trusted the poll alone would leave the composer live and let the
  // handler retype the same question into it until the interval elapsed.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_AI_UNAVAILABLE },
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "what is the reserve?",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));

  expect(await screen.findByTestId("copilot-degraded")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("copilot-input")).toBeDisabled());
  expect(actionButton("reserve")).toBeDisabled();
  expect(actionButton("data_alignment")).toBeEnabled();
});

test("an outage leaves a typed error state and no assistant-styled prose", async () => {
  // AD-14's named enemy, asserted as an absence. The prototype answered a failed
  // API call with `offlineAnswer()` — pre-written claim-specific text in an
  // assistant bubble — and the whole point of this story is that failure looks
  // like failure. So: the handler's own turn is there, the typed notice is
  // there, and there is **no assistant turn at all**.
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_AI_UNAVAILABLE },
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "what is the reserve?",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));

  await screen.findByTestId("copilot-degraded");
  expect(screen.getByTestId("copilot-turn-user")).toHaveTextContent(
    "what is the reserve?",
  );
  expect(screen.queryByTestId("copilot-turn-assistant")).not.toBeInTheDocument();
  // The failure is stated in the pane's own inline notice, which is a state
  // rather than a message the copilot wrote.
  expect(screen.getByTestId("copilot-refusal")).toBeInTheDocument();
});

test("Try again re-enables the inputs once the model is back", async () => {
  // Recovery, and the clause that goes with it: **no automatic re-fire**. The
  // handler's failed question is not re-sent when the flag flips — "Try again"
  // refetches the probe and re-enables an input, and asking again is the
  // handler's decision.
  const asked: string[] = [];
  renderTab({
    copilotAvailability: (url) => {
      asked.push(url);
      return asked.length === 1 ? COPILOT_UNAVAILABLE : COPILOT_AVAILABLE;
    },
  });

  await waitFor(() => expect(screen.getByTestId("copilot-input")).toBeDisabled());

  await userEvent.click(screen.getByTestId("copilot-degraded-retry"));

  await waitFor(() =>
    expect(screen.queryByTestId("copilot-degraded")).not.toBeInTheDocument(),
  );
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
  expect(actionButton("reserve")).toBeEnabled();

  // **And the retry asked the *server*, not the api's ten-second cache.** This
  // is the assertion the first cut of the story could not have made: it called
  // the query's own `refetch()`, which re-issues the poll's request, which the
  // probe cache answers with the same stale `false` — so the control claimed to
  // shorten the wait to zero and did nothing at all inside that window. The
  // poll must *not* carry the flag, or the cache would never coalesce anything.
  expect(asked[0]).not.toContain("force=true");
  expect(asked.at(-1)).toContain("force=true");

  // Nothing was posted on the handler's behalf: the only bodies on the wire are
  // the ones they caused, and here there are none.
  expect(copilotRunBodies).toHaveLength(0);
});

test("an availability query that starts failing does not latch the outage", async () => {
  // The recovery path that could not recover (6.6 review). `modelDown` compares
  // the reactive `outageAt` against the query's `dataUpdatedAt`, which only
  // advances on a *successful* fetch — and `outageAt` is never reset. So a 401
  // after a session refresh, a proxy hiccup or a network blip on the poll left
  // the composer disabled for the life of the pane, under a "Try again" that
  // could not clear it, with the model perfectly capable of answering.
  //
  // Comparing against the later of `dataUpdatedAt` and `errorUpdatedAt` fixes
  // it: a probe that answered *at all* moves the pane on, and an errored
  // availability query then reads as unknown, which reads as available — the
  // safe direction, and the same rule a pending query already followed.
  //
  // A 404 rather than a 500 so the shared client does not retry it (this file's
  // own convention, three tests up).
  let succeed = true;
  const client = renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: { sse: COPILOT_RUN_AI_UNAVAILABLE },
    copilotAvailability: () =>
      succeed
        ? COPILOT_AVAILABLE
        : {
            status: 404,
            body: {
              type: "about:blank",
              title: "Not Found",
              status: 404,
              detail: "no",
            },
          },
  });

  await userEvent.type(
    await screen.findByTestId("copilot-input"),
    "what is the reserve?",
  );
  await userEvent.click(screen.getByTestId("copilot-send"));
  await screen.findByTestId("copilot-degraded");

  // The poll fires again and this time the request fails outright.
  succeed = false;
  await act(async () => {
    await client.invalidateQueries({
      queryKey: queryKeys.copilot.availability,
    });
  });

  await waitFor(() =>
    expect(screen.queryByTestId("copilot-degraded")).not.toBeInTheDocument(),
  );
  expect(screen.getByTestId("copilot-input")).toBeEnabled();
  expect(actionButton("reserve")).toBeEnabled();
});

test("a quick action the server no longer publishes is disabled during an outage", async () => {
  // The orphaned button (6.6 review). The disabled set is an intersection of
  // "the model is down" with "this key needs a model", read off the server's
  // own flags — so a key this build still renders and the server has stopped
  // publishing falls through the intersection and stays *enabled* through an
  // outage, for a handler to press and discover the failure with.
  //
  // Discovery-by-failure is the thing this story exists to remove, so an
  // unknown key is treated as needing the model. Note what is *not* changed:
  // the whole list being absent still reads as available, because that is a
  // query that has not answered rather than a server that has.
  renderTab({
    copilotAvailability: {
      status: 200,
      body: {
        available: false,
        quickActions: COPILOT_UNAVAILABLE.body.quickActions.filter(
          (flag) => flag.key !== "data_alignment",
        ),
      },
    },
  });

  await screen.findByTestId("copilot-quick-actions");
  await waitFor(() =>
    expect(actionButton("data_alignment")).toBeDisabled(),
  );
});

test("a read-only conversation shows no outage notice", async () => {
  // The notice explains why the composer and the strip are unusable, and on a
  // superseded thread neither is *there* — the read-only line already says why.
  // Two overlapping explanations for one absent control is noise.
  renderTab({
    copilotThreads: COPILOT_THREADS_TWO,
    copilotAvailability: COPILOT_UNAVAILABLE,
  });

  await userEvent.selectOptions(
    await screen.findByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s1",
  );

  expect(await screen.findByTestId("copilot-read-only")).toBeInTheDocument();
  expect(screen.queryByTestId("copilot-degraded")).not.toBeInTheDocument();
});
