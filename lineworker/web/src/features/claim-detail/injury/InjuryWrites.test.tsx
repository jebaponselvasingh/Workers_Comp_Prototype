/**
 * What the Injury Diagram tab's three mutations actually send, and what they
 * do with the answer (Story 2.4, AC 2 and AC 3).
 *
 * Split from `InjuryTab.test.tsx`, which is about what the tab *draws*. The
 * assertions here are on the request — the method, the URL, the body, and
 * which version travelled in it — because those are the parts a render test
 * cannot see and the parts a compare-and-swap depends on.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import {
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  TOPBAR_STATS,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "../ClaimDetailPane";

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

/**
 * Every `/api/*` request, recorded in front of the stub.
 *
 * **Not read off the `vi.fn()`'s `mock.calls`**, which is the obvious move
 * and does not work: `openapi-fetch` builds a `Request` and passes it as the
 * *only* argument, so `init` is undefined and both the method and the body
 * live on an object whose body is a stream that can be read once. Cloning it
 * here is what makes "what did the browser actually send" answerable — and
 * the body is the whole point of these tests, since a compare-and-swap is
 * only as good as the version that travelled in it.
 */
let recorded: Recorded[] = [];

function recordRequests(): void {
  const inner = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : null;
    const raw = request
      ? await request.clone().text()
      : typeof init?.body === "string"
        ? init.body
        : "";
    recorded.push({
      url: request ? request.url : String(input),
      method: request ? request.method : (init?.method ?? "GET"),
      body: raw ? JSON.parse(raw) : undefined,
    });
    return inner(input, init);
  }) as typeof fetch;
}

function writesTo(fragment: string): Recorded[] {
  return recorded.filter((call) => call.method !== "GET" && call.url.includes(fragment));
}

async function openTab(claimDetail: StubRouteFor = CLAIM_DETAIL_TREATMENT) {
  stubApi({ me: ME_HANDLER, claimDetail });
  recordRequests();
  const client = createQueryClient();
  // The top bar and the queue are not rendered by this pane, so their cache
  // entries have to be put there for an invalidation to have anything to
  // mark. Seeding them is what the shell would have done on load; without
  // it, `invalidateQueries` on an absent key is a no-op and the assertion
  // below would pass against a mutation that invalidated nothing.
  client.setQueryData(queryKeys.stats.topbar, TOPBAR_STATS.body);
  client.setQueryData(queryKeys.claims.queue("all"), { groups: {} });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("case-header");
  await userEvent.click(screen.getByTestId("tab-injury"));
  return client;
}

/** Type into the severity field and commit it the way a handler does. */
async function commitSeverity(value: string): Promise<void> {
  const input = screen.getByTestId("edit-severityScore");
  await userEvent.clear(input);
  await userEvent.type(input, value);
  await userEvent.tab();
}

afterEach(() => {
  recorded = [];
  vi.unstubAllGlobals();
});

test("adding an injury posts the region, the type, the score and the claim's version", async () => {
  await openTab();
  await userEvent.click(screen.getByTestId("injury-add-open"));

  await userEvent.selectOptions(
    await screen.findByTestId("injury-new-body-key"),
    "shoulder_left",
  );
  await userEvent.type(screen.getByTestId("injury-new-type"), "  Sprain  ");
  await userEvent.click(screen.getByTestId("injury-add-submit"));

  await waitFor(() => expect(writesTo("/injuries")).toHaveLength(1));
  const [request] = writesTo("/injuries");
  expect(request.method).toBe("POST");
  expect(request.body).toEqual({
    // The **claim's** version: an injury must not be recorded against a case
    // file that has moved on.
    expectedVersion: CLAIM_DETAIL_TREATMENT.body.version,
    bodyKey: "shoulder_left",
    injuryType: "Sprain",
    severityScore: CLAIM_DETAIL_TREATMENT.body.injury.defaultSeverityScore,
  });
  // No `bodyPart`: the label is the server's answer for the key, and a
  // client that could supply its own could file "Left Hand" against `head`.
  expect(request.body).not.toHaveProperty("bodyPart");
});

test("removing an injury sends the injury row's version, not the claim's", async () => {
  // The distinction is the whole design of the delete: guarding it with the
  // claim's version would refuse a handler's ✕ because somebody corrected an
  // unrelated ICD-10 code a moment earlier.
  await openTab();
  await userEvent.click(screen.getByTestId("injury-add-open"));
  await userEvent.click(await screen.findByTestId("injury-remove"));

  await waitFor(() => expect(writesTo("/injuries/")).toHaveLength(1));
  const [request] = writesTo("/injuries/");
  const secondary = CLAIM_DETAIL_TREATMENT.body.injury.markers[1];
  expect(request.method).toBe("DELETE");
  expect(request.url).toContain(`/injuries/${secondary.id}`);
  expect(request.url).toContain(`expectedVersion=${secondary.version}`);
  // The two versions differ in the fixture on purpose: if they were equal,
  // this test would pass against a component that sent the wrong one.
  expect(secondary.version).not.toBe(CLAIM_DETAIL_TREATMENT.body.version);
});

test("the severity score goes to its own route, with the claim's version", async () => {
  await openTab();
  await commitSeverity("31");

  await waitFor(() => expect(writesTo("/severity")).toHaveLength(1));
  const [request] = writesTo("/severity");
  expect(request.method).toBe("PATCH");
  expect(request.body).toEqual({
    expectedVersion: CLAIM_DETAIL_TREATMENT.body.version,
    severityScore: 31,
  });
});

test("a committed severity score marks the queue and the top-bar tiles stale", async () => {
  // AC 3's "stat tiles" clause. The High Risk tile counts claims in the high
  // band across the caller's whole book, so a score crossing the boundary
  // changes a number two panes away — and nothing in the SPA could compute
  // that, so the key has to be invalidated. No earlier mutation touched it.
  //
  // Asserted on the cache rather than on a refetch, deliberately: this pane
  // does not render the top bar, so there is no observer for the key and
  // TanStack correctly issues no request. What the mutation is responsible
  // for is marking it stale; fetching it is the shell's business.
  const client = await openTab();
  await commitSeverity("31");

  await waitFor(() => expect(writesTo("/severity")).toHaveLength(1));
  await waitFor(() =>
    expect(client.getQueryState(queryKeys.stats.topbar)?.isInvalidated).toBe(true),
  );
  // The queue *prefix*, not one filter's key: every cached filter and every
  // "Show more" page under it shows the same claim's risk dot.
  expect(client.getQueryState(queryKeys.claims.queue("all"))?.isInvalidated).toBe(true);
});

test("a committed severity score reaches an open document sheet and spares the insights cache", async () => {
  // The two halves of `markCaseFileStale`'s contract, which is "everything the
  // old `claims.detail` prefix invalidation reached, except insights".
  //
  // The document sheet is the half that was silently lost when that helper
  // replaced the prefix with three exact keys (follow-up review of Story 6.2,
  // B1): a FROI renders the claim's injury type and ICD-10, both of them
  // Story 2.3 editable fields, so a viewer left open across an edit showed the
  // pre-edit values with nothing able to reach it. Its key carries a document
  // id, so it is the one nested entry a helper cannot name exactly.
  //
  // Insights is the half that must *not* be reached — a narrative is a cache
  // with its own timestamp (AD-10) and an edit dates it rather than
  // invalidating it. Asserting both here is what keeps a future "just use a
  // prefix again" from passing.
  const client = await openTab();
  client.setQueryData(queryKeys.claims.documentSheet("WC-20017", 41), {});
  client.setQueryData(queryKeys.claims.insights("WC-20017"), {});

  await commitSeverity("31");
  await waitFor(() => expect(writesTo("/severity")).toHaveLength(1));

  await waitFor(() =>
    expect(
      client.getQueryState(queryKeys.claims.documentSheet("WC-20017", 41))
        ?.isInvalidated,
    ).toBe(true),
  );
  expect(
    client.getQueryState(queryKeys.claims.insights("WC-20017"))?.isInvalidated,
  ).toBe(false);
});

test("one command in flight disables every editable control on the tab", async () => {
  // **The rule `useInlineEdits` documents, across four hooks rather than
  // within one** (code review, 2026-08-12). `expectedVersion` comes from the
  // cached case file and no mutation advances it optimistically, so a second
  // commit launched before the first settles carries a version the first has
  // already consumed: the server answers 409 and the handler is told
  // somebody else changed the claim — about their own edit. Story 2.3 made
  // this true for the field patch; Story 2.4 added three more hooks to the
  // same screen, each with its own `isPending`.
  await openTab((url) =>
    url.includes("/severity") ? "pending" : CLAIM_DETAIL_TREATMENT,
  );
  await commitSeverity("31");

  // The severity field disables itself — that much was already true.
  await waitFor(() => expect(screen.getByTestId("edit-severityScore")).toBeDisabled());
  // These are the three that were not: a different hook each.
  expect(screen.getByTestId("edit-bodyKey")).toBeDisabled();

  await userEvent.click(screen.getByTestId("injury-add-open"));
  expect(await screen.findByTestId("injury-add-submit")).toBeDisabled();
  expect(screen.getByTestId("injury-new-type")).toBeDisabled();
  expect(screen.getByTestId("injury-remove")).toBeDisabled();

  // Exactly one request went out, which is the property all of that buys.
  expect(writesTo("/claims/")).toHaveLength(1);
});

test("a refused severity score renders the server's reason inline and keeps the claim", async () => {
  await openTab((url) =>
    url.includes("/severity")
      ? {
          status: 422,
          body: {
            type: "/problems/invalid-patch",
            title: "Unprocessable Content",
            status: 422,
            detail: "severity score must be a whole number",
          },
        }
      : CLAIM_DETAIL_TREATMENT,
  );
  await commitSeverity("31");

  // The server's own words — they name the field and the rule and never echo
  // the value (AD-11), so they are safe to show verbatim.
  expect(await screen.findByTestId("edit-severityScore-invalid")).toHaveTextContent(
    "whole number",
  );
  // The card still shows the claim's score: nothing was written, and no
  // optimistic value was left behind to suggest otherwise.
  expect(screen.getByTestId("injury-severity-bar")).toHaveStyle({ width: "78%" });
});

test("a conflict shows the value that won, rendered from the 409's fresh entity", async () => {
  const fresh = {
    ...CLAIM_DETAIL_TREATMENT.body,
    version: 9,
    header: { ...CLAIM_DETAIL_TREATMENT.body.header, severityScore: 22, risk: "low" },
  };
  await openTab((url) =>
    url.includes("/severity")
      ? {
          status: 409,
          body: {
            type: "/problems/stale-write",
            title: "Conflict",
            status: 409,
            detail: "This claim was changed by someone else while you were editing.",
            claim: fresh,
          },
        }
      : CLAIM_DETAIL_TREATMENT,
  );
  await commitSeverity("31");

  expect(await screen.findByTestId("edit-severityScore-conflict")).toHaveTextContent(
    "Updated by someone else",
  );
  // The 409 carries the whole case file, so the band moves with the number —
  // the client renders the state that exists rather than merging into it.
  await waitFor(() =>
    expect(screen.getByTestId("injury-severity-bar")).toHaveStyle({ width: "22%" }),
  );
  expect(screen.getByTestId("injury-severity-bar").className).toContain("bg-ok");
});
