import { PERSONAS, loginAs } from "../fixtures/login";
import { firstClaimInStage } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import {
  awaitModelAvailability,
  startModelStub,
  stopModelStub,
} from "../fixtures/stack";
import { expect, test } from "../fixtures/test";

/**
 * Story 6.6 — Honest Degradation.
 *
 * The one spec in the suite that runs against a **broken stack on purpose**.
 * AD-15 names the technique — "copilot degradation specs stop the stub and
 * assert the `ai_unavailable` path" — and it is the only way to produce a real
 * outage: the stub holds no state and has no control endpoint by design, so
 * the honest way to make the model unreachable is to make it unreachable.
 *
 * What is proved here:
 *
 * 1. **Claim operations do not depend on the agent runtime** (AC 3, the
 *    `@smoke`). Queue, claim detail, Bills and Diary all load and function with
 *    the model container stopped. This is the clause that matters most to a
 *    handler and the one no unit test can reach: it is a statement about the
 *    whole composed stack, and it fails the moment anything on a claim screen
 *    acquires a dependency on the copilot.
 * 2. **Exactly the affected inputs are disabled** (AC 3, UX-DR8). The composer
 *    and the six `requiresLlm` buttons; not the ⚡ data-alignment button, not
 *    the transcript, not the thread switcher, not the 📓 Diary tab. Degradation
 *    is the *disabled* case, never the absent one.
 * 3. **The model-free action still streams to exactly one terminal `done`**
 *    (AC 2). AD-14's promise that deterministic actions keep working, asserted
 *    against a stack where the model genuinely is gone rather than against a
 *    fake that pretends.
 * 4. **A free-chat attempt ends with one terminal `error` carrying
 *    `code: "ai_unavailable"`** (AC 1), and the transcript shows a typed error
 *    state with **no assistant-styled prose**. That absence is the point of the
 *    whole story: the prototype answered a failed API call with `offlineAnswer()`
 *    — pre-written claim text in an assistant bubble — and AD-14 bans it by
 *    name.
 * 5. **An approved write is never lost to an outage** (AC 6). The RTW save is
 *    deterministic end to end by 6.5's design — no model call on the proposal
 *    and none on the confirmation — so with the model gone the letter still
 *    pauses at the gate, still executes on approval, and still lands on the
 *    claim. Asserted on the document list rather than on the terminal event,
 *    because "the run ended cleanly" is satisfied identically by a gate that
 *    wrote and one that did not.
 *
 * **What this spec deliberately does not assert.** Any sentence a model wrote,
 * as everywhere in this epic — but here the rule cuts the other way too, and
 * harder: the assertions about the outage are about **event names, problem
 * `type`s and `code`s**, never about the wording of the notice, because a
 * degradation spec that asserted its own copy would pass on a pane that said
 * the right thing and disabled nothing.
 *
 * ## The stub is stopped for the whole file, and unconditionally put back
 *
 * `playwright.config.ts` runs `workers: 1` with `fullyParallel: false` and
 * `fixtures/test.ts` asserts the first of those, so no sibling spec is running
 * while the model is down. The risk is the tail rather than concurrency: a file
 * that stopped the stub and died would leave every later spec file failing for
 * reasons that have nothing to do with them.
 *
 * Hence the restore is an `afterAll` that runs **unconditionally** — Playwright
 * runs it whether the tests passed, failed or the `beforeAll` itself threw,
 * which is what a `try/finally` around a hand-written setup would have bought
 * and is stronger, since it survives a timeout too. And `startModelStub()`
 * blocks until compose reports the container healthy: `docker compose start`,
 * unlike `up --wait`, returns as soon as the process is launched, so a spec that
 * restarted the stub and finished would hand the next file a container that is
 * up and not yet answering.
 *
 * The api caches its availability probe for `ai_health_probe_cache_seconds`
 * (ten), so a container that is healthy again is still reported unavailable for
 * up to that long. That used to be written down here as a residual excused by
 * "nothing runs after this file today" — which is true of a filename rather
 * than of a decision, and would have handed the next spec anyone added an
 * intermittent failure with nothing in its own code to explain it. So
 * `startModelStub()` now waits for the api to agree, not merely for the
 * container to be healthy, and this file leaves the stack in the state it found
 * it in. See `fixtures/stack.ts`.
 *
 * The api container is **not** restarted by any of this: `compose.e2e.yaml`
 * declares no `restart:` policy and `api`'s dependency on `model-stub` is
 * `service_healthy` for startup ordering only. That is exactly what makes AC 3
 * assertable.
 *
 * The filename must match `stories/6-6-*.spec.ts` and the branch name must
 * contain `6-6`, or CI's grep derivation silently runs the whole suite.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

/**
 * The letter body the AC 6 test files. Committed test data, never model output.
 *
 * That distinction is what makes asserting it legitimate in a suite that reads
 * no prose: this is the handler's own text going through the gate, and the point
 * of the test is that an outage does not lose it.
 */
const LETTER =
  "Dear worker,\n\nModified duty is available from a date to be confirmed.";

/**
 * The phrase that makes `model-stub` end its completion on `length`.
 *
 * `deploy/model-stub/app.py::STUB_TRUNCATE_PHRASE`, spelled once here — the
 * stub is request-shape-driven and holds no state, so the only way to select
 * its behaviour is to send words it recognises, exactly as Story 6.5's write
 * proposal does. This is committed test data, not model output, which is what
 * makes naming it legitimate in a suite that asserts on no prose.
 */
const TRUNCATE_PHRASE = "answer at length forever";

/** The six keys that need a model. The seventh is the point of the story. */
const LLM_BACKED_KEYS = [
  "laborlaw",
  "similar",
  "rtw",
  "reserve",
  "fraud",
  "nextactions",
];

type Page = Parameters<typeof byTestId>[0];

/** Open the workspace on a claim with the ⚡ Actions tab showing. */
async function openCopilot(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
  await byTestId(page, "copilot-tab-actions").click();
  await expect(byTestId(page, "copilot-tab-actions")).toHaveAttribute(
    "aria-selected",
    "true",
  );
}

/**
 * Block until the api has *noticed* the outage. **Call before asserting on a
 * disabled control.**
 *
 * The api caches its availability probe for `ai_health_probe_cache_seconds`
 * (ten by default) so that N open panels cost one upstream request — a
 * deliberate property, argued in `config.py`, and the reason this helper
 * exists. When this file runs immediately after another copilot spec, that
 * cache is warm and holds `available: true` from a moment when the stub really
 * was up. Everything the *server* does is already correct at that point; what is
 * not yet true is that the panel has been told.
 *
 * Without this the file passed alone and failed in suite order, which is the
 * worst shape a spec can have: the failure would be attributed to whichever
 * story happened to run before it.
 *
 * One line, because the wait is `fixtures/stack.ts`'s and is used in both
 * directions: this file waits for `false` before asserting on a disabled
 * control, and the restore waits for `true` before handing the stack to the
 * next file. Same cache, same poll, opposite expectation.
 */
async function awaitOutageObserved(): Promise<void> {
  await awaitModelAvailability(false);
}

/** Wait for the claim's current conversation, and answer with its id. */
async function currentThread(page: Page, claimId: string): Promise<string> {
  await expect(byTestId(page, "copilot-quick-actions")).toBeVisible();
  const response = await page.request.get(
    `/api/copilot/claims/${claimId}/threads`,
  );
  expect(response.status(), await response.text()).toBe(200);
  const listed = (await response.json()) as { currentThreadId: string | null };
  expect(listed.currentThreadId).not.toBeNull();
  return listed.currentThreadId!;
}

/** The event names of one run, in the order they arrived. */
function eventsOf(body: string): string[] {
  return body
    .split("\n")
    .filter((line) => line.startsWith("event: "))
    .map((line) => line.slice("event: ".length));
}

/** The terminal events of one run. Exactly one arrives — AD-6's invariant. */
function terminalsOf(body: string): string[] {
  return eventsOf(body).filter((event) =>
    ["done", "interrupt", "error"].includes(event),
  );
}

/** The payload of the last `data:` line — the terminal frame's document. */
function lastPayload(body: string): Record<string, unknown> {
  const lines = body
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice("data: ".length));
  const last = lines.at(-1);
  expect(last, "the run emitted no data frames at all").toBeDefined();
  return JSON.parse(last!) as Record<string, unknown>;
}

/**
 * The token ceiling, **declared before the outage block and therefore run
 * before it** — because this half needs a model that answers.
 *
 * `ai_limit` covers two shapes and until this file only one of them had
 * end-to-end coverage. The `num_predict` half was exercised by a fake chat
 * model whose `generation_info` this repository writes, which asserts this
 * repository's idea of the wire format rather than `langchain_ollama`'s: if the
 * vendor stopped publishing `done_reason`, truncated answers would silently
 * have gone back to terminating `done` and every test would still have passed.
 * `deploy/model-stub/app.py` now emits `done_reason: "length"` on request, the
 * same way it emits a proposed write on request, so the real client, the real
 * wrapper and the real classifier are all in the path here.
 *
 * Its own `describe` rather than a test inside the one below, because the stub
 * has to be **up**: the block below stops it for its whole duration. Declared
 * first so this does not depend on that block's `afterAll` having restored
 * anything — an ordering that would make this file's own correctness a property
 * of hook execution order.
 */
test.describe("@story:6-6 @epic:6 the answer-length ceiling", () => {
  test("a completion that stops on length ends one ai_limit and keeps its tokens", async ({
    page,
  }) => {
    // AC 4's `num_predict` half. Three claims in one run: the terminal event is
    // singular and is an `error`; its problem document carries the `ai_limit`
    // code on the truncation `type`; and whatever decoded before the stop is
    // still on the wire above it — the run is a completed answer that was cut
    // short, not a lost one.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const response = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { message: `Please ${TRUNCATE_PHRASE} about this claim.` } },
    );
    expect(response.status(), await response.text()).toBe(200);

    const body = await response.text();
    expect(terminalsOf(body)).toEqual(["error"]);
    expect(eventsOf(body)).toContain("messages");
    const problem = lastPayload(body);
    expect(problem.code).toBe("ai_limit");
    expect(problem.type).toBe("/problems/copilot-output-truncated");
    expect(problem.status).toBe(503);

    // …and the thread is accepting, which is AC 4's other clause: a 409 here
    // would mean the advisory lock leaked or an approval was left pending.
    const again = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { message: "Data alignment note", quickAction: "data_alignment" } },
    );
    expect(again.status(), await again.text()).toBe(200);
    expect(terminalsOf(await again.text())).toEqual(["done"]);
  });
});

test.describe("@story:6-6 @epic:6 honest degradation", () => {
  test.beforeAll(() => {
    stopModelStub();
  });

  test.afterAll(async () => {
    // **Unconditional**, and this is the whole reason the hook exists. Every
    // spec file after this one runs against the same stack, so a stub left
    // stopped would fail them with errors pointing at their own code.
    //
    // Awaited, and what is awaited is not only the container: `startModelStub`
    // polls `GET /copilot/availability` until the api's own cached probe agrees
    // the model is back, so the next file inherits a stack in the state this
    // one found it in rather than one that is healthy and still being reported
    // as down. See `fixtures/stack.ts`.
    await startModelStub();
  });

  test("@smoke every claim screen works with the model container stopped", async ({
    page,
  }) => {
    // AC 3, and it is the `@smoke` because it is the sentence a handler would
    // use: the AI being down does not stop me doing my job. Four surfaces, each
    // asserted as *functioning* rather than merely rendering — a screen that
    // painted a skeleton for ever would satisfy a visibility check.
    await loginAs(page, PERSONAS.handler);

    // The queue lists claims and selecting one drives the centre pane.
    const cards = page.locator('[data-testid="queue-card"]');
    await expect(cards.first()).toBeVisible();
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await page
      .locator(`[data-testid="queue-card"][data-claim-id="${claimId}"]`)
      .click();
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(claimId);

    // The case file renders its stage-adaptive overview.
    await expect(byTestId(page, "detail-pane")).toBeVisible();

    // Bills computes and renders — deterministic financial services, no model.
    await byTestId(page, "tab-bills").click();
    await expect(byTestId(page, "bills-tab")).toBeVisible();
    await expect(byTestId(page, "bills-card-row").first()).toBeVisible();

    // …and the Diary, which is the copilot pane's *other* tab: the pane itself
    // is alive during an outage, which is UX-DR8's "never grey the pane".
    await byTestId(page, "copilot-tab-diary").click();
    await expect(byTestId(page, "copilot-tab-diary")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(byTestId(page, "diary-active-claim")).toContainText(claimId);
  });

  test("exactly the affected inputs are disabled, and the pane stays alive", async ({
    page,
  }) => {
    // UX-DR8 as a set difference. The failure this catches is the coarse one —
    // a pane that disables everything when the model is down — which reads as
    // conservative and is precisely the behaviour AD-14 forbids: the ⚡ note
    // needs no model and must keep answering.
    await loginAs(page, PERSONAS.handler);
    // The panel disables from the server's answer, so the server has to have
    // answered — see `awaitOutageObserved` on the probe cache this waits out.
    await awaitOutageObserved();
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    await currentThread(page, claimId);

    // The notice, and its manual retry. Asserted as *present with a control*,
    // never by its wording.
    await expect(byTestId(page, "copilot-degraded")).toBeVisible();
    await expect(byTestId(page, "copilot-degraded-retry")).toBeEnabled();

    // Disabled: the free-text composer and the six LLM-backed buttons.
    await expect(byTestId(page, "copilot-input")).toBeDisabled();
    await expect(byTestId(page, "copilot-send")).toBeDisabled();
    for (const key of LLM_BACKED_KEYS) {
      await expect(
        page.locator(`[data-quick-action="${key}"]`),
        `${key} needs the model and should be disabled`,
      ).toBeDisabled();
    }

    // Live: the model-free action, the transcript, the greeting, "+ New" and
    // the Diary tab. Disabled, never absent — the composer is still on screen.
    await expect(
      page.locator('[data-quick-action="data_alignment"]'),
    ).toBeEnabled();
    await expect(byTestId(page, "copilot-composer")).toBeVisible();
    await expect(byTestId(page, "copilot-greeting")).toBeVisible();
    await expect(byTestId(page, "copilot-new-thread")).toBeEnabled();
    await expect(byTestId(page, "copilot-tab-diary")).toBeEnabled();
  });

  test("the availability endpoint reports the outage as a 200", async ({
    page,
  }) => {
    // The server contract behind the disabled set, read directly. It carries
    // both facts on purpose: a client that knew which buttons need a model but
    // not whether one is up could not disable the right set.
    //
    // **200 with `available: false`**, not a 503 — a health signal that errors
    // when the thing it reports on is unhealthy has told the caller nothing it
    // can render.
    await loginAs(page, PERSONAS.handler);
    await awaitOutageObserved();

    const response = await page.request.get("/api/copilot/availability");
    expect(response.status(), await response.text()).toBe(200);

    const body = (await response.json()) as {
      available: boolean;
      quickActions: { key: string; requiresLlm: boolean }[];
    };
    expect(body.available).toBe(false);
    expect(
      body.quickActions
        .filter((flag) => flag.requiresLlm)
        .map((flag) => flag.key)
        .sort(),
    ).toEqual([...LLM_BACKED_KEYS].sort());
    expect(
      body.quickActions.filter((flag) => !flag.requiresLlm).map((f) => f.key),
    ).toEqual(["data_alignment"]);
  });

  test("the model-free action streams to exactly one terminal done", async ({
    page,
  }) => {
    // AC 2, against a stack where the model really is gone. Story 6.4 asserted
    // the same run against a *working* stub, which proves the routing and
    // proves nothing about the dependency; this is the assertion that the
    // declaration `requires_llm: false` is true rather than merely written.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const response = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { message: "Data alignment note", quickAction: "data_alignment" } },
    );
    expect(response.status(), await response.text()).toBe(200);

    const body = await response.text();
    expect(eventsOf(body)).toContain("messages");
    expect(terminalsOf(body)).toEqual(["done"]);
  });

  test("free chat ends with one typed ai_unavailable error", async ({
    page,
  }) => {
    // AC 1 on the wire. Three properties in one run, and each was a way an
    // outage could look handled and not be: **exactly one** terminal event, so
    // the wrapper's raise did not become a second frame; the problem document
    // inline, because by then the status is already 200 and there is nowhere
    // else to put one; and the `code` that distinguishes a stopped model
    // container from a bug in the graph, which before this story it did not.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const response = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { message: "what is the reserve on this claim?" } },
    );
    expect(response.status(), await response.text()).toBe(200);

    const body = await response.text();
    expect(terminalsOf(body)).toEqual(["error"]);
    const problem = lastPayload(body);
    expect(problem.code).toBe("ai_unavailable");
    expect(problem.type).toBe("/problems/ai-unavailable");
    expect(problem.status).toBe(503);

    // AD-6: after a terminal error the thread is accepting again. The next run
    // is refused 409 if the advisory lock leaked or an approval was left
    // pending — and it fails the same honest way if neither did.
    const again = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { message: "and again?" } },
    );
    expect(again.status(), await again.text()).toBe(200);
    expect(terminalsOf(await again.text())).toEqual(["error"]);
  });

  test("the transcript shows a typed error and no assistant prose", async ({
    page,
  }) => {
    // The story's named enemy, asserted as an absence. `offlineAnswer()` served
    // pre-written claim-specific text in an assistant bubble whenever the API
    // call failed, and it looked exactly like a working copilot. So the
    // assertion is not that some notice appeared — it is that **no assistant
    // turn exists at all** on a conversation whose only run hit an outage.
    //
    // The ⚡ action is used to make the run, because the composer is disabled —
    // and that state is **established here rather than assumed**. An earlier
    // draft of this test asserted nothing about it and explained the choice with
    // a comment, which is a comment that could go stale without a failure. The
    // wait for the api to notice is what makes the disabled composer true at
    // this point in the test rather than eventually.
    await loginAs(page, PERSONAS.handler);
    await awaitOutageObserved();
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");

    // **A fresh conversation, minted before the pane is opened.** The DB reset
    // is per spec *file*, so by the time this test runs the claim's current
    // thread already holds whatever the tests above put in it — which is why
    // the count below has to be a number this test owns rather than a sample
    // taken from a shared thread. `POST …/threads` makes the new one current,
    // and the transcript then contains exactly what happens here.
    const minted = await page.request.post(
      `/api/copilot/claims/${claimId}/threads`,
    );
    expect(minted.status(), await minted.text()).toBe(201);

    await openCopilot(page, claimId);
    await currentThread(page, claimId);
    await expect(byTestId(page, "copilot-input")).toBeDisabled();

    // `data_alignment` is enabled; the six that need the model are not — so a
    // run through the UI has to come from a key that is allowed to fail. Press
    // ⚡, which succeeds, then assert that a *narrating* action never produced
    // an assistant bubble in this pane.
    //
    // **One, not "however many there are now"**, and the number is asserted
    // rather than sampled. Reading a count into a variable while the ⚡ run may
    // still be streaming and comparing it after a reload is a race that passes
    // whenever the sample happens to be taken late: on a conversation this test
    // minted, the note is the only assistant turn there can be, and
    // `toHaveCount` retries until that is true instead of racing it.
    await page.locator('[data-quick-action="data_alignment"]').click();
    await expect(byTestId(page, "copilot-turn-assistant")).toHaveCount(1);

    // The model-free note is committed code and is the one assistant turn on
    // screen. What must not exist is a *second* one, invented for the outage:
    // the run below is the reserve key, which needs the model.
    const threadId = await currentThread(page, claimId);
    const failed = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { message: "Reserve review", quickAction: "reserve" } },
    );
    expect(failed.status(), await failed.text()).toBe(200);
    expect(lastPayload(await failed.text()).code).toBe("ai_unavailable");

    // Reload so the pane re-reads the checkpointed transcript: what the server
    // persisted for a failed run is the honest question, and it must not be a
    // pre-authored answer.
    await openCopilot(page, claimId);
    await expect(byTestId(page, "copilot-quick-actions")).toBeVisible();
    await expect(byTestId(page, "copilot-turn-assistant")).toHaveCount(1);
  });

  test("an approved write executes and is filed with the model down", async ({
    page,
  }) => {
    // AC 6. **An approved write is never lost to a model outage.** The RTW
    // save's whole path is deterministic by 6.5's design — the proposal is
    // synthesised from the handler's own text with no model call, and the
    // confirmation after it is composed in committed code — so an outage must
    // cost the handler nothing here at all.
    //
    // It is asserted **on the claim's document list** rather than on the run's
    // terminal event, because "the run ended cleanly" is satisfied identically
    // by a gate that wrote and one that did not. The version is read first and
    // is what the approval compare-and-swaps on.
    await loginAs(page, PERSONAS.handler);
    await awaitOutageObserved();
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const detail = await page.request.get(`/api/claims/${claimId}`);
    expect(detail.status(), await detail.text()).toBe(200);
    const caseFile = (await detail.json()) as {
      version: number;
      documents: { documents: unknown[] };
    };
    const before = caseFile.documents.documents.length;

    const proposed = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      {
        data: {
          message: "Save the return-to-work letter to this claim.",
          rtwLetter: { bodyText: LETTER, expectedVersion: caseFile.version },
        },
      },
    );
    expect(proposed.status(), await proposed.text()).toBe(200);
    expect(terminalsOf(await proposed.text())).toEqual(["interrupt"]);

    const approved = await page.request.post(
      `/api/copilot/threads/${threadId}/runs`,
      { data: { command: { resume: { decisions: [{ type: "approve" }] } } } },
    );
    expect(approved.status(), await approved.text()).toBe(200);
    expect(terminalsOf(await approved.text())).toEqual(["done"]);

    const after = await page.request.get(`/api/claims/${claimId}`);
    const filed = (await after.json()) as {
      documents: { documents: { name: string }[] };
    };
    expect(filed.documents.documents).toHaveLength(before + 1);
  });

  test("Refresh Insights is disabled rather than left to fail", async ({
    page,
  }) => {
    // The one non-copilot control that reaches the model. Its 503 is unchanged
    // and still the backstop; what changes is that the 503 stops being the
    // discovery mechanism. The tab itself keeps rendering its cached rows,
    // which is AC 3 on the surface most likely to have taken a dependency.
    await loginAs(page, PERSONAS.handler);
    await awaitOutageObserved();
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await page.goto(`/workspace?claim=${claimId}`);
    await expect(byTestId(page, "case-header")).toBeVisible();

    await byTestId(page, "tab-insights").click();
    await expect(byTestId(page, "insights-refresh")).toBeDisabled();
    await expect(
      byTestId(page, "insights-refresh-unavailable"),
    ).toBeVisible();
  });
});
