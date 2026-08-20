import { PERSONAS, loginAs } from "../fixtures/login";
import { firstClaimInStage } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 6.3 — Copilot Chat with Persistent Threads.
 *
 * The right-hand pane's other tab, filled. What is being proved here is not that
 * a model wrote something useful — that is unassertable and AD-15 forbids trying
 * — but that the *composed stack* carries a conversation:
 *
 * 1. **A run streams end to end.** The api container assembles a run against the
 *    one compiled `StateGraph`, sends it to `model-stub` over the internal
 *    network with the real `ChatOllama` client, checkpoints the turn through the
 *    real `AsyncPostgresSaver` into tables the vendored migration created, and
 *    emits SSE frames the browser's assistant-ui runtime accumulates. Nothing is
 *    swapped in Python or in TypeScript; only the model behind the API is
 *    deterministic (`deploy/model-stub/`).
 * 2. **The conversation survives a reload.** The prototype's `chatHistory` was a
 *    browser-lifetime array re-seeded at every login. This one is in Postgres,
 *    and only a real navigation can show that.
 * 3. **"New conversation" freezes the prior thread.** The old one stays readable
 *    and its composer is gone; the server refuses a run against it.
 * 4. **A second message while a run is in flight is refused, inline.** The
 *    single-flight 409 is one of the two invariants most likely to regress, and
 *    it is asserted here against the composed stack rather than only in a unit
 *    test with a scripted lock.
 *
 * **What this spec deliberately does not assert.** Any sentence a model wrote.
 * The stub's answers are hash-derived filler, and even against a real model "the
 * answer mentions the reserve" would be a test of the model rather than of this
 * codebase. Every assertion below is structural: which elements exist, that a
 * turn arrived, that it is still there after a reload, and which controls are
 * present on a frozen thread.
 *
 * **Why the stub streams in more than one frame.** `deploy/model-stub/app.py`
 * answered `stream: true` with a single NDJSON line until this story — legal,
 * and it would have made "the stream renders" a one-frame assertion with the
 * one-terminal-event property asserted vacuously. It now emits
 * `STUB_CHAT_CHUNKS` content lines and one terminal, which is Ollama's real
 * shape.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

/** Open the workspace on a claim with the ⚡ Actions tab showing. */
async function openCopilot(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
  // ⚡ Actions is the tab the panel opens on since this story; clicking it is
  // idempotent and makes the spec independent of that default, which is a
  // rendering decision rather than a contract.
  await byTestId(page, "copilot-tab-actions").click();
  await expect(byTestId(page, "copilot-tab-actions")).toHaveAttribute("aria-selected", "true");
}

/**
 * Wait for the claim's current conversation, and answer with its id.
 *
 * **Requested per test rather than left to run order** — 6.1's `embedEverything`
 * lesson, which cost that spec two tests that silently depended on the smoke
 * test having run first. The reset fixture rebuilds the database once per spec
 * *file*, not per test, so a shared prerequisite has to be asked for.
 *
 * **It no longer presses "+ New", and that is AC 1.** The SPA mints the first
 * conversation itself when the list comes back empty; a helper that clicked the
 * button was creating the thread the panel is supposed to create, which is how
 * an unimplemented acceptance criterion survived a whole e2e suite that
 * exercised it in every test. Waiting for the composer is waiting for the mint,
 * because the composer only renders once a thread exists.
 *
 * Idempotent by construction: a claim that already has a conversation has one
 * on the first read, and nothing here writes.
 */
async function currentThread(page: Page, claimId: string): Promise<string> {
  await expect(byTestId(page, "copilot-composer")).toBeVisible();
  const response = await page.request.get(`/api/copilot/claims/${claimId}/threads`);
  expect(response.status(), await response.text()).toBe(200);
  const listed = (await response.json()) as { currentThreadId: string | null };
  expect(
    listed.currentThreadId,
    "the panel opened on a claim with no conversation and the SPA minted none (AC 1)",
  ).not.toBeNull();
  return listed.currentThreadId!;
}

/** Send one message through the composer and wait for a turn to land. */
async function ask(page: Page, message: string): Promise<void> {
  await byTestId(page, "copilot-input").fill(message);
  await byTestId(page, "copilot-send").click();
  // The *user's* turn appearing is the signal the run started; the assistant's
  // is what the caller usually goes on to assert.
  await expect(byTestId(page, "copilot-turn-user").filter({ hasText: message })).toBeVisible();
}

test.describe("@story:6-3 @epic:6 copilot chat with persistent threads", () => {
  test("@smoke a message streams an answer that survives a reload", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);

    // UX-DR8's two fixed lines. The greeting is deterministic server output
    // (AD-2) — asserted as *present* rather than by its prose, which is the
    // only kind of assertion AD-15 allows about anything a model could have
    // written, and the reason this one is not.
    await expect(byTestId(page, "copilot-greeting")).toContainText(claimId);
    await expect(byTestId(page, "copilot-disclaimer")).toBeVisible();

    await currentThread(page, claimId);
    await ask(page, "what is the status of this claim?");

    // The stream rendered: an assistant turn exists with some content in it.
    // Structure, never prose — the stub's answer is hash-derived filler and it
    // says so.
    const answer = byTestId(page, "copilot-turn-assistant").first();
    await expect(answer).toBeVisible();
    await expect(answer).not.toBeEmpty();

    // **A second question in the same conversation is a second answer.** The
    // runtime merges a streamed chunk into whichever message carries its id, so
    // an id fixed for the session — which is what shipped — appended the second
    // answer onto the first answer's bubble: two questions, one ever-growing
    // reply. Every test in this suite sent one message per thread, which is
    // precisely why nothing saw it.
    await ask(page, "and what happens next?");
    await expect(byTestId(page, "copilot-turn-assistant")).toHaveCount(2);

    // …and it is in Postgres rather than in a JavaScript array. Only a real
    // navigation can show that, which is the whole reason this assertion is in
    // a browser at all (FR-CP-2).
    await page.reload();
    await byTestId(page, "copilot-tab-actions").click();
    await expect(
      byTestId(page, "copilot-turn-user").filter({ hasText: "what is the status of this claim?" }),
    ).toBeVisible();
    await expect(byTestId(page, "copilot-turn-assistant").first()).toBeVisible();
  });

  test("a run emits exactly one terminal event", async ({ page }) => {
    // The invariant most likely to regress, asserted on the wire rather than
    // through the UI: a component that rendered correctly would look the same
    // whether the server sent one `done` or three.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const response = await page.request.post(`/api/copilot/threads/${threadId}/runs`, {
      data: { message: "summarise this claim" },
    });
    expect(response.status(), await response.text()).toBe(200);
    expect(response.headers()["content-type"]).toContain("text/event-stream");

    const events = (await response.text())
      .split("\n")
      .filter((line) => line.startsWith("event: "))
      .map((line) => line.slice("event: ".length));

    expect(events, "no assistant content was streamed").toContain("messages");
    expect(events.filter((event) => ["done", "interrupt", "error"].includes(event))).toEqual([
      "done",
    ]);
    expect(events.at(-1)).toBe("done");
    // More than one content frame, which is what `STUB_CHAT_CHUNKS` bought: with
    // a single-frame stub "the stream renders" and "the whole answer arrived at
    // once" are indistinguishable.
    expect(events.filter((event) => event === "messages").length).toBeGreaterThan(1);
  });

  test("a second message while a run is in flight is refused inline", async ({ page }) => {
    // AC 3, against the composed stack.
    //
    // **Run from inside the browser rather than through `page.request`**, and
    // that is the whole reason this test is written the way it is. Playwright's
    // request API buffers a whole response before it resolves, so a first run
    // against a stub that answers in milliseconds is *finished* — and its lock
    // released — before a second request can be issued. The race is unwinnable
    // from outside, and a test that lost it would report a green single-flight
    // guard that had never been exercised.
    //
    // `fetch` resolves as soon as the **headers** arrive, and this response is
    // an SSE stream whose body is still being written — so the first run is
    // demonstrably still in flight when the second goes out. That is the state
    // the guard exists for, created deliberately rather than hoped for.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const outcome = await page.evaluate(async (thread: string) => {
      const url = `/api/copilot/threads/${thread}/runs`;
      const post = (message: string) =>
        fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ message }),
        });

      // Headers only — the body is left unread, so the run keeps streaming and
      // keeps its lock.
      const first = await post("the first question");
      const second = await post("the second question");
      const problem = second.status === 409 ? await second.json() : null;
      // Drain the first so the run finishes and the lock is released before the
      // next test touches this thread.
      await first.text();
      return { first: first.status, second: second.status, problem };
    }, threadId);

    expect(outcome.first).toBe(200);
    expect(outcome.second).toBe(409);
    // One `type` for both causes — a run in flight and an interrupt pending —
    // because a caller cannot act differently on them (the router argues it).
    expect((outcome.problem as { type: string }).type).toBe("/problems/thread-busy");
    // No extension member: unlike a version conflict there is no fresh entity to
    // hand back.
    expect(Object.keys(outcome.problem as object).sort()).toEqual([
      "detail",
      "status",
      "title",
      "type",
    ]);
  });

  test("new conversation freezes the prior thread and keeps it readable", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const firstThread = await currentThread(page, claimId);
    await ask(page, "the first conversation");
    await expect(byTestId(page, "copilot-turn-assistant").first()).toBeVisible();

    await byTestId(page, "copilot-new-thread").click();

    // The switcher appears once there are two, and the new conversation is
    // empty — a minting rule that reused an id would show the old conversation
    // under a button labelled "new".
    await expect(byTestId(page, "copilot-thread-picker")).toBeVisible();
    await expect(byTestId(page, "copilot-transcript-empty")).toBeVisible();

    // The server refuses a run against the frozen one…
    const refused = await page.request.post(`/api/copilot/threads/${firstThread}/runs`, {
      data: { message: "still talking?" },
    });
    expect(refused.status()).toBe(409);
    expect(((await refused.json()) as { type: string }).type).toBe(
      "/problems/thread-read-only",
    );

    // …and the browser renders it as read-only history rather than as an error:
    // the transcript is there, the composer is not.
    await byTestId(page, "copilot-thread-picker").selectOption(firstThread);
    await expect(byTestId(page, "copilot-read-only")).toBeVisible();
    await expect(byTestId(page, "copilot-composer")).toHaveCount(0);
    await expect(
      byTestId(page, "copilot-turn-user").filter({ hasText: "the first conversation" }),
    ).toBeVisible();
  });

  test("another handler's conversation is a 404, never a 403", async ({ page }) => {
    // AC 5 through the composed stack. Thread ids are readable by design, so
    // "guessing one gets you nothing" is a property worth asserting in the place
    // where a real session cookie is doing the work.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const mine = await currentThread(page, claimId);

    // The same claim, a different user id — a well-formed id that is not this
    // caller's, which is exactly the id an attacker would construct.
    const forged = mine.replace(/\.u\d+\./, ".u99999.");
    const refused = await page.request.get(`/api/copilot/threads/${forged}/messages`);

    expect(refused.status()).toBe(404);
    expect(((await refused.json()) as { type: string }).type).toBe(
      "/problems/thread-not-found",
    );
  });

  test("the copilot panel has no disabled control and names no epic", async ({ page }) => {
    // The seam, gone. Story 4.1 shipped ⚡ Actions disabled behind "Copilot
    // arrives with the AI epic — Epic 6" in three places at once; `4-1-…spec.ts`
    // asserted the disabled state and has been amended into its opposite. This
    // is the same claim from the other side, over the whole pane, so a leftover
    // carrier anywhere in it fails here.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);

    await expect(byTestId(page, "copilot")).not.toContainText("Epic 6");
    await expect(byTestId(page, "copilot-tab-actions")).toBeEnabled();
    await expect(byTestId(page, "copilot-tab-diary")).toBeEnabled();
    await expect(byTestId(page, "copilot-actions-seam")).toHaveCount(0);
  });
});
