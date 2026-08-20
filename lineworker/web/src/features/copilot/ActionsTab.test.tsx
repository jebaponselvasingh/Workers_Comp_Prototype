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
 * 4. **Raw HTML in a model's answer renders as text and creates no element**
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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  COPILOT_RUN_OK,
  COPILOT_THREAD_BUSY,
  COPILOT_THREADS,
  COPILOT_THREADS_EMPTY,
  COPILOT_THREADS_TWO,
  COPILOT_TRANSCRIPT,
  COPILOT_TRANSCRIPT_EMPTY,
  sseFrame,
  stubApi,
  type StubRoutes,
} from "@/test/api-mock";

import { ActionsTab } from "./ActionsTab";
import { COPILOT_DISCLAIMER } from "./disclaimer";

function renderTab(routes: Partial<StubRoutes> = {}, claimId: string | null = "WC-20017") {
  stubApi({
    copilotThreads: COPILOT_THREADS,
    copilotTranscript: COPILOT_TRANSCRIPT,
    ...routes,
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ActionsTab claimId={claimId} />
    </QueryClientProvider>,
  );
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
  expect(screen.getByTestId("copilot-greeting")).toHaveTextContent("Case summary for WC-20017");
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
        detail: "A new conversation cannot be started while the current one is still answering.",
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
  expect(screen.getByTestId("copilot-disclaimer")).toHaveTextContent(COPILOT_DISCLAIMER);
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
  expect(screen.getByTestId("copilot-transcript")).toHaveTextContent("the first conversation");

  await userEvent.selectOptions(
    screen.getByTestId("copilot-thread-picker"),
    "claim.WC-20017.u1.s2",
  );

  expect(await screen.findByTestId("copilot-transcript-loading")).toBeInTheDocument();
  expect(screen.queryByText("the first conversation")).not.toBeInTheDocument();
});

test("an empty conversation says so rather than rendering a blank list", async () => {
  renderTab({ copilotTranscript: COPILOT_TRANSCRIPT_EMPTY });

  expect(await screen.findByTestId("copilot-transcript-empty")).toBeInTheDocument();
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
            content: '<script>alert(1)</script><img src="x" onerror="alert(2)">',
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
        messages: [{ role: "assistant", content: "See [the policy](https://example.test/x)." }],
      },
    },
  });

  const turn = await screen.findByTestId("copilot-turn-assistant");
  expect(turn).toHaveTextContent("the policy");
  expect(turn.querySelector("a")).toBeNull();
});

// --- the run and its refusals -------------------------------------------

test("sending a message streams an answer into the transcript", async () => {
  renderTab({ copilotTranscript: COPILOT_TRANSCRIPT_EMPTY, copilotRun: { sse: COPILOT_RUN_OK } });

  await userEvent.type(await screen.findByTestId("copilot-input"), "what next?");
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
  renderTab({ copilotTranscript: COPILOT_TRANSCRIPT_EMPTY, copilotRun: { sse: COPILOT_RUN_OK } });

  const scroller = await screen.findByTestId("copilot-scroller");
  Object.defineProperty(scroller, "scrollHeight", { value: 640, configurable: true });

  await userEvent.type(screen.getByTestId("copilot-input"), "what next?");
  await userEvent.click(screen.getByTestId("copilot-send"));

  await waitFor(() => expect(scroller.scrollTop).toBe(640));
});

test("a 409 is an inline notice, never a dialog", async () => {
  renderTab({
    copilotTranscript: COPILOT_TRANSCRIPT_EMPTY,
    copilotRun: COPILOT_THREAD_BUSY,
  });

  await userEvent.type(await screen.findByTestId("copilot-input"), "second question");
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

  await userEvent.type(await screen.findByTestId("copilot-input"), "second question");
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
  expect(screen.getByTestId("copilot-transcript")).toHaveTextContent("the first conversation");
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
          detail: "The copilot could not finish answering. Nothing was changed on the claim.",
        }),
      ],
    },
  });

  await userEvent.type(await screen.findByTestId("copilot-input"), "what is the reserve?");
  await userEvent.click(screen.getByTestId("copilot-send"));

  await waitFor(() => expect(screen.getByTestId("copilot-refusal")).toBeInTheDocument());
  expect(screen.getByTestId("copilot-refusal")).toHaveTextContent(/could not/i);
});
