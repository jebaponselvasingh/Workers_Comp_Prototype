import { PERSONAS, loginAs } from "../fixtures/login";
import { firstClaimInStage } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 6.4 — Deterministic Quick Actions (QAS).
 *
 * The seven buttons, against the composed stack. What is being proved here is
 * not that a model wrote anything useful — that is unassertable and AD-15
 * forbids trying — but that a key pressed in a browser reaches its own node in
 * the api container and comes back as a stream:
 *
 * 1. **A key routes, and the `updates` frames say which node it routed to.**
 *    That is the whole of AD-14 on the wire: the route is a fact the server
 *    publishes, so a spec can assert determinism without asserting prose.
 * 2. **The model-free action answers with the model container's help not
 *    needed.** `data_alignment` declares `requires_llm: false`, and its note is
 *    assembled from a registered tool's output in committed code. It streams and
 *    terminates `done` like every other action — which is what Story 6.6 will
 *    gate its degradation on.
 * 3. **An unknown key is refused 422 before a thread is touched.** The structured
 *    rejection, asserted at the contract rather than in a unit test with a
 *    hand-built request model.
 * 4. **The buttons are disabled while a run is in flight**, which is the client
 *    half of the thread's single-flight rule.
 *
 * **What this spec deliberately does not assert.** Any sentence a model wrote.
 * `deploy/model-stub/` answers with hash-derived filler and says so; even
 * against a real model "the reserve answer mentions the reserve" would be a test
 * of the model rather than of this codebase. Every assertion below is
 * structural: which elements exist, which node names arrived, which event
 * sequence came back, and which status a bad key got.
 *
 * The filename must match `stories/6-4-*.spec.ts` and the branch name must
 * contain `6-4`, or CI's grep derivation silently runs the whole suite.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

/** Open the workspace on a claim with the ⚡ Actions tab showing. */
async function openCopilot(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
  // Clicking is idempotent and makes the spec independent of which tab the
  // panel opens on, which is a rendering decision rather than a contract.
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
 * Idempotent by construction: the SPA mints the first conversation itself when
 * the list comes back empty, a claim that already has one has it on the first
 * read, and nothing here writes. Waiting for the button strip is waiting for the
 * mint, because the strip only renders once a thread exists.
 */
async function currentThread(page: Page, claimId: string): Promise<string> {
  await expect(byTestId(page, "copilot-quick-actions")).toBeVisible();
  const response = await page.request.get(`/api/copilot/claims/${claimId}/threads`);
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

/** The `updates` frames' node names, in order. */
function nodesOf(body: string): string[] {
  const lines = body.split("\n");
  return lines
    .map((line, index) =>
      line.startsWith("data: ") && lines[index - 1] === "event: updates"
        ? (JSON.parse(line.slice("data: ".length)) as { node: string }).node
        : null,
    )
    .filter((node): node is string => node !== null);
}

test.describe("@story:6-4 @epic:6 deterministic quick actions", () => {
  test("@smoke a quick action streams an answer from its own node", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    await currentThread(page, claimId);

    // UX-DR8's strip: seven buttons, each with its glyph, above the transcript.
    const buttons = byTestId(page, "copilot-quick-action");
    await expect(buttons).toHaveCount(7);
    await expect(buttons.nth(0)).toContainText("Labor law & state rules");

    const reserve = page.locator('[data-quick-action="reserve"]');
    await reserve.click();

    // The handler's turn is the button's label — asserted because it is the
    // server's own echo of what was asked, and it is not model output.
    await expect(
      byTestId(page, "copilot-turn-user").filter({ hasText: "Reserve review" }),
    ).toBeVisible();

    // The answer streamed. Structure, never prose: the stub's narration is
    // hash-derived filler and it says so.
    const answer = byTestId(page, "copilot-turn-assistant").first();
    await expect(answer).toBeVisible();
    await expect(answer).not.toBeEmpty();
  });

  test("each key routes to its own node and ends with one terminal event", async ({ page }) => {
    // AD-14 on the wire, key by key. The route is something the server
    // publishes — `updates` frames name the nodes that finished — so
    // determinism is assertable end to end without a word of prose being read.
    //
    // Run through `page.request` rather than the UI: what is under test is the
    // routing and the event sequence, and driving seven buttons through the
    // panel would be seven streams' worth of rendering to assert something the
    // stream itself says.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const expected: Record<string, string> = {
      laborlaw: "qas_laborlaw",
      similar: "qas_similar",
      rtw: "qas_rtw",
      reserve: "qas_reserve",
      fraud: "qas_fraud",
      nextactions: "qas_nextactions",
      data_alignment: "qas_data_alignment",
    };

    for (const [key, node] of Object.entries(expected)) {
      const response = await page.request.post(`/api/copilot/threads/${threadId}/runs`, {
        data: { message: `quick action ${key}`, quickAction: key },
      });
      expect(response.status(), `${key}: ${await response.text()}`).toBe(200);
      expect(response.headers()["content-type"]).toContain("text/event-stream");

      const body = await response.text();
      expect(nodesOf(body), `${key} routed elsewhere`).toEqual(["entry_router", node]);
      expect(eventsOf(body), `${key} streamed no assistant content`).toContain("messages");
      expect(
        eventsOf(body).filter((event) => ["done", "interrupt", "error"].includes(event)),
        `${key} did not end with exactly one terminal event`,
      ).toEqual(["done"]);
    }
  });

  test("the model-free action answers with no model call at all", async ({ page }) => {
    // `data_alignment` declares `requires_llm: false`, and this is the false
    // path Story 6.6 gates degradation on. It is asserted here the only way a
    // black-box spec can: the run reaches its own node, streams content and
    // terminates `done` exactly like the six that do call a model — so 6.6 has
    // something real to keep working when the model container is stopped.
    //
    // The note is *computed*, so unlike every other answer in this epic it is
    // safe to assert one string of it — the sentence is committed code, not
    // model output, which is precisely what makes it not the banned
    // `offlineAnswer` pattern.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const response = await page.request.post(`/api/copilot/threads/${threadId}/runs`, {
      data: { message: "Data alignment note", quickAction: "data_alignment" },
    });
    expect(response.status(), await response.text()).toBe(200);

    const body = await response.text();
    expect(nodesOf(body)).toEqual(["entry_router", "qas_data_alignment"]);
    expect(eventsOf(body).at(-1)).toBe("done");
    expect(body).toContain("No model was involved");
    // No delimiter markup reaches a reader: the fenced half of the tool's
    // envelope is model-context only, and this is the one node that formats
    // tool output for a human.
    expect(body).not.toContain("LINEWORKER-ITEM");
  });

  test("an unknown quick-action key is refused 422", async ({ page }) => {
    // The structured rejection AC 1 asks for, at the contract. `route_entry`
    // falls through to free text for a key it does not know — the right answer
    // for a router and the wrong answer to a client, because a button that
    // quietly became a chat message would be a quick action that had stopped
    // being deterministic with nothing saying so.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    const threadId = await currentThread(page, claimId);

    const refused = await page.request.post(`/api/copilot/threads/${threadId}/runs`, {
      data: { message: "Labor law & state rules", quickAction: "laborlow" },
    });
    expect(refused.status()).toBe(422);
    expect(((await refused.json()) as { type: string }).type).toBe("/problems/validation-error");

    // …and the thread is untouched: a run that never started can be followed
    // immediately by one that does, with no 409 in between.
    const accepted = await page.request.post(`/api/copilot/threads/${threadId}/runs`, {
      data: { message: "Labor law & state rules", quickAction: "laborlaw" },
    });
    expect(accepted.status(), await accepted.text()).toBe(200);
    await accepted.text();
  });

  test("the buttons are disabled while a run is in flight", async ({ page }) => {
    // The client half of single-flight, against the composed stack. Driven
    // through the panel rather than through `page.request`, because what is
    // under test is what the browser does with a run it started: Playwright's
    // request API buffers a whole response before it resolves, so a run issued
    // that way is finished before anything could be clicked.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    await openCopilot(page, claimId);
    await currentThread(page, claimId);

    await page.locator('[data-quick-action="fraud"]').click();
    // The strip greys out for the length of the run. Asserted on the disabled
    // state rather than on the absence of a second request, which a black-box
    // spec cannot see; the server's 409 is the guarantee and this is the
    // courtesy that keeps a handler from meeting it.
    await expect(page.locator('[data-quick-action="reserve"]')).toBeDisabled();

    // …and it comes back when the run finishes, so the strip is not a one-shot.
    await expect(byTestId(page, "copilot-turn-assistant").first()).toBeVisible();
    await expect(page.locator('[data-quick-action="reserve"]')).toBeEnabled();
  });
});
