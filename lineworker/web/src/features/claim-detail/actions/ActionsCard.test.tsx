/**
 * Story 3.5 — the Upcoming Actions card renders what it was sent, and its
 * controls do what they say.
 *
 * Most assertions here are of one kind: the payload said X, the screen says X,
 * in that order. That is the point of the card — the ranking, the urgency, the
 * cap and whether a control is offered are all the server's decisions, so what
 * a component test can prove is that none of them was recomputed, reordered or
 * quietly relabelled on the way to the DOM. `noDerivation.test.ts` proves the
 * arithmetic is absent as *source*; this proves the rendering is faithful.
 *
 * The exceptions are the four states the server cannot describe — loading,
 * error, empty and a refusal (NFR-3) — and the three completion controls,
 * which are about what a click sends and what the response is allowed to
 * change.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import type { ClaimDetail } from "@/api/claims";
import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import {
  ASSESSMENT_CONFLICT,
  CLAIM_ACTIONS,
  CLAIM_ACTIONS_EMPTY,
  CLAIM_DETAIL_APPROVED,
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  type StubRoute,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ActionsCard } from "./ActionsCard";

const CLAIM_ID = "WC-20017";
const ITEMS = CLAIM_ACTIONS.body.items;

/**
 * One row by its server-assigned `id`.
 *
 * A filter over `getAllByTestId` rather than `getByTestId(..., { selector })`:
 * that option narrows *which elements the matcher considers*, not which of the
 * matches is returned, so it finds all six rows and throws "multiple elements".
 * Addressing by `id` is also the point — it is the identity the server sends,
 * and a test that indexed into the list would pass against a card that had
 * reordered it.
 */
/**
 * Every URL the stub was asked for, however the client addressed it.
 *
 * `openapi-fetch` calls `fetch(new Request(...))` rather than
 * `fetch(url, init)`, so a `String(input)` here reads `[object Request]` and
 * every assertion about which endpoint was called silently passes. The stub
 * itself unwraps the same three shapes; this is that unwrapping, in the one
 * other place it is needed.
 */
function requested(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input)
    .filter((input): input is Request => input instanceof Request);
}

function row(id: string): HTMLElement {
  const found = screen
    .getAllByTestId("action-row")
    .find((element) => element.dataset.action === id);
  if (!found) throw new Error(`no action row ${id} is rendered`);
  return found;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderCard(
  routes: {
    claimActions?: StubRouteFor;
    approveAssessment?: StubRouteFor;
    documentReview?: StubRouteFor;
    oshaLog?: StubRouteFor;
  } = {},
  onNavigate: (target: string) => void = () => {},
) {
  stubApi({ me: ME_HANDLER, claimActions: CLAIM_ACTIONS, ...routes });
  const client = createQueryClient();
  // The case file is not rendered here, so its cache entry has to be put there
  // for an invalidation to have anything to mark — `BillsTab.test.tsx`'s rule:
  // on an absent key `invalidateQueries` is a no-op, and an assertion about it
  // would pass against a mutation that invalidated nothing.
  client.setQueryData(queryKeys.claims.detail(CLAIM_ID), CLAIM_DETAIL_TREATMENT.body);
  render(
    <QueryClientProvider client={client}>
      <ActionsCard
        claim={CLAIM_DETAIL_TREATMENT.body as unknown as ClaimDetail}
        onNavigate={(target) => onNavigate(target)}
      />
    </QueryClientProvider>,
  );
  return client;
}

// --- NFR-3: the states the server cannot describe -------------------------

test("a request in flight renders a loading line rather than an empty card", async () => {
  renderCard({ claimActions: "pending" as StubRoute });

  expect(await screen.findByTestId("actions-loading")).toBeInTheDocument();
  expect(screen.queryByTestId("actions-empty")).not.toBeInTheDocument();
});

test("a failed request says so instead of reading as a claim on track", async () => {
  // The distinction this protects is the one that matters on this card: an
  // error rendered as the empty state tells a handler there is nothing to do.
  // A 4xx rather than a 500: `createQueryClient` retries a 5xx with backoff,
  // so a 500 here would still be in flight when the assertion ran and the test
  // would be about the *loading* branch.
  renderCard({ claimActions: { status: 403, body: {} } });

  expect(await screen.findByTestId("actions-error")).toBeInTheDocument();
  expect(screen.queryByTestId("actions-empty")).not.toBeInTheDocument();
});

test("a claim with no outstanding actions gets the prototype's own sentence", async () => {
  renderCard({ claimActions: CLAIM_ACTIONS_EMPTY });

  expect(await screen.findByTestId("actions-empty")).toHaveTextContent(
    "No outstanding actions — claim is on track.",
  );
  expect(screen.queryAllByTestId("action-row")).toHaveLength(0);
});

// --- AC 1: the list is the server's --------------------------------------

test("every row the server sent is rendered, in the order it sent them", async () => {
  renderCard();

  const rows = await screen.findAllByTestId("action-row");

  expect(rows).toHaveLength(ITEMS.length);
  expect(rows.map((row) => row.dataset.action)).toEqual(ITEMS.map((item) => item.id));
});

test("each row shows the server's label and the server's urgency chip", async () => {
  renderCard();
  await screen.findAllByTestId("action-row");

  for (const item of ITEMS) {
    const element = row(item.id);
    expect(within(element).getByTestId("action-label")).toHaveTextContent(item.label);
    expect(within(element).getByTestId("action-urgency")).toHaveAttribute(
      "data-urgency",
      item.urgency,
    );
  }
});

test("the urgency chips carry their labels rather than their tokens", async () => {
  // The enum convention's UI half: the wire says `medium`, the chip says
  // "Medium". A chip rendering the token would be the payload leaking through.
  renderCard();
  await screen.findAllByTestId("action-row");

  const chips = screen.getAllByTestId("action-urgency").map((chip) => chip.textContent);
  // Six, which is the server's cap. Story 6.2 added an `overdue_rtw:rtw_letter`
  // seam when the fraud row stopped being one, and briefly made the fixture
  // seven rows — a payload no cap of six can produce (follow-up review, C3).
  expect(chips).toEqual(["High", "High", "High", "High", "Medium", "Low"]);
});

// --- AC 3: deep links, and the seam --------------------------------------

test("an enabled go-to control fires the pane's navigation with its target", async () => {
  const navigated: string[] = [];
  renderCard({}, (target) => navigated.push(target));
  await screen.findAllByTestId("action-row");

  // `surgical_pre_auth`, whose target is `documents`. It carries a command as
  // well, which is the point: a row with both controls must still fire the
  // navigation from the "go to" one.
  const preAuthRow = row("surgical_pre_auth:documents");
  await userEvent.click(within(preAuthRow).getByTestId("action-goto"));

  expect(navigated).toEqual(["documents"]);
});

test("a seam control is disabled and cannot navigate anywhere", async () => {
  const navigated: string[] = [];
  renderCard({}, (target) => navigated.push(target));
  await screen.findAllByTestId("action-row");

  // `rtw_letter`, not `fraud`: Story 6.2 filled the AI Insights tab, so the row
  // this test pointed at now navigates (asserted directly below). Third
  // re-pointing of the same assertion — `diary` → `fraud` → `rtw_letter` — and
  // each one is the same evidence that the seam mechanism works, moved to the
  // seam that is still open. Story 6.5 builds the RTW letter and will move it
  // once more; when there is no seam left, this test's subject is gone and the
  // assertion goes with it rather than being pointed at nothing.
  const seamRow = row("overdue_rtw:rtw_letter");
  const control = within(seamRow).getByTestId("action-goto");

  expect(control).toBeDisabled();
  await userEvent.click(control);
  expect(navigated).toEqual([]);
});

test("the fraud target is live since Story 6.2 and navigates", async () => {
  // The seam the previous version of this file asserted disabled. It is
  // *enabled* on the server now, and the card had to gain `"fraud"` in
  // `NAVIGABLE_FROM_OVERVIEW` — without that line the row renders no control at
  // all, which is the trap Stories 4.1 and 4.2 both hit.
  //
  // The target is `fraud` and the tab is `insights`; turning one into the other
  // is `ClaimDetailPane.navigate`'s job, and this card must not know about it.
  // Asserting the raw target here is what keeps that seam honest: a card that
  // "helpfully" translated would be a second place the mapping lives.
  const navigated: string[] = [];
  renderCard({}, (target) => navigated.push(target));
  await screen.findAllByTestId("action-row");

  const fraudRow = row("siu_escalation:fraud");
  const control = within(fraudRow).getByTestId("action-goto");

  expect(control).toBeEnabled();
  await userEvent.click(control);
  expect(navigated).toEqual(["fraud"]);
});

test("the diary target is live since Story 4.2 and navigates", async () => {
  // The seam this file's previous version asserted disabled. It is *enabled*
  // on the server now, and the card had to gain `"diary"` in
  // `NAVIGABLE_FROM_OVERVIEW` — without that line the row renders no control
  // at all, which is the trap Story 4.1 hit with `meetings`.
  const navigated: string[] = [];
  renderCard({}, (target) => navigated.push(target));
  await screen.findAllByTestId("action-row");

  const diaryRow = row("diary_check_in:diary");
  const control = within(diaryRow).getByTestId("action-goto");

  expect(control).toBeEnabled();
  await userEvent.click(control);
  expect(navigated).toEqual(["diary"]);
  // And no completion control: the note is the completion, so there is no
  // fifth `ActionCommand` on this row.
  expect(within(diaryRow).queryByTestId("action-command")).not.toBeInTheDocument();
});

test("a seam control names the epic that will enable it, without a pointer", async () => {
  // The tooltip is the AC's wording; the `title` and the described-by span are
  // what make the reason reachable to a keyboard and a screen reader, which is
  // what NFR-3's "no dead clicks" actually asks for.
  renderCard();
  await screen.findAllByTestId("action-row");

  const seamRow = row("overdue_rtw:rtw_letter");

  expect(within(seamRow).getByTestId("action-goto")).toHaveAttribute(
    "title",
    "Available with the RTW letter — Epic 6",
  );
  expect(seamRow).toHaveTextContent("Available with the RTW letter — Epic 6");
});

test("the seam reason shown is the server's, not a map held in the browser", async () => {
  // The property Stories 4.2 and 6.2 both flipped: change the sentence on the
  // server and the card changes. A client-side epic map would fail this — and
  // would also have had to be edited twice by now, in a release each time.
  const retuned = {
    status: 200,
    body: {
      ...CLAIM_ACTIONS.body,
      items: CLAIM_ACTIONS.body.items.map((item) =>
        item.id === "overdue_rtw:rtw_letter"
          ? { ...item, disabledReason: "Available in the next release" }
          : item,
      ),
    },
  };
  renderCard({ claimActions: retuned });
  await screen.findAllByTestId("action-row");

  expect(row("overdue_rtw:rtw_letter")).toHaveTextContent("Available in the next release");
});

test("a disabled row offers no completion control", async () => {
  renderCard();
  await screen.findAllByTestId("action-row");

  const seamRow = row("overdue_rtw:rtw_letter");

  expect(within(seamRow).queryByTestId("action-command")).not.toBeInTheDocument();
});

// --- AC 4 and AC 5: the three completions --------------------------------

test("the approve control sends the claim's version and installs the response", async () => {
  const client = renderCard();
  await screen.findAllByTestId("action-row");

  const approveRow = row("assessment_approval:approve");
  await userEvent.click(within(approveRow).getByTestId("action-command"));

  await waitFor(() => {
    // The response *is* the fresh case file, so it lands in the cache — which
    // is what moves the header's status chip without a refetch (AC 4).
    expect(
      client.getQueryData<ClaimDetail>(queryKeys.claims.detail(CLAIM_ID))?.header.status,
    ).toBe("ch_approved");
  });

  expect(requested().some((call) => call.url.includes("/assessment/approval"))).toBe(true);
});

test("the approval re-asks the server for the checklist", async () => {
  // AC 5's "the action list re-renders", and the reason there is no
  // action-state table: the row goes because the server re-evaluated the
  // trigger, never because this component removed it from a list.
  //
  // Asserted as a **second request** rather than as `isInvalidated`, which is
  // racy in exactly the direction that would make this test lie: the key is
  // active, so TanStack refetches immediately and clears the flag — a check
  // that ran afterwards would read `false` on a mutation that did everything
  // right.
  renderCard();
  await screen.findAllByTestId("action-row");
  const before = requested().filter((call) => call.url.includes("/actions")).length;

  await userEvent.click(
    within(row("assessment_approval:approve")).getByTestId("action-command"),
  );

  await waitFor(() => {
    expect(requested().filter((call) => call.url.includes("/actions")).length).toBe(before + 1);
  });
});

test("the document control sends the document's version and the server's step", async () => {
  // Both come off the row: the version is the *document's*, not the claim's,
  // and the step is the completion the server said is next. A client that sent
  // the claim's version would 409 against a payload the handler never saw.
  renderCard();
  await screen.findAllByTestId("action-row");

  const preAuthRow = row("surgical_pre_auth:documents");
  expect(within(preAuthRow).getByTestId("action-command")).toHaveTextContent("Mark Reviewed");

  await userEvent.click(within(preAuthRow).getByTestId("action-command"));

  const sent = await waitFor(() => {
    const call = requested().find((request) => request.url.includes("/documents/41/review"));
    expect(call).toBeDefined();
    return call!;
  });

  expect(await sent.clone().json()).toEqual({
    expectedVersion: 2,
    step: "mark_document_reviewed",
  });
});

test("the document control follows the server when the step advances", async () => {
  // The offered control is `services/claims/assessment.py`'s answer. A card
  // that decided "reviewed means offer Confirm" would be a second copy of a
  // rule that also has to say a *confirmed* document offers nothing.
  const advanced = {
    status: 200,
    body: {
      ...CLAIM_ACTIONS.body,
      items: CLAIM_ACTIONS.body.items.map((item) =>
        item.id === "surgical_pre_auth:documents"
          ? { ...item, command: "confirm_document", documentVersion: 3 }
          : item,
      ),
    },
  };
  renderCard({ claimActions: advanced });
  await screen.findAllByTestId("action-row");

  const preAuthRow = row("surgical_pre_auth:documents");
  expect(within(preAuthRow).getByTestId("action-command")).toHaveTextContent("Confirm");
});

test("the OSHA control records the entry and announces it politely", async () => {
  renderCard();
  await screen.findAllByTestId("action-row");

  const oshaRow = row("osha_log:overview");
  expect(within(oshaRow).getByTestId("action-command")).toHaveTextContent("Mark Logged");

  await userEvent.click(within(oshaRow).getByTestId("action-command"));

  await waitFor(() => {
    expect(screen.getByTestId("actions-status")).toHaveTextContent("OSHA 300 entry recorded.");
  });
  // Never a dialog and never a blocking alert (UX-DR11): a polite live region
  // where the handler is already looking.
  expect(screen.getByTestId("actions-status")).toHaveAttribute("role", "status");
});

test("a refusal renders inline at the card rather than as a dialog", async () => {
  renderCard({ approveAssessment: ASSESSMENT_CONFLICT });
  await screen.findAllByTestId("action-row");

  await userEvent.click(
    within(
      row("assessment_approval:approve"),
    ).getByTestId("action-command"),
  );

  expect(await screen.findByTestId("actions-failure")).toHaveTextContent(
    "changed by someone else",
  );
});

test("a 409 installs the fresh case file the problem document carries", async () => {
  // AD-9: roll nothing back, render what won. The conflict body carries an
  // approved claim, so the header two components up shows the status that
  // actually holds rather than the one the handler was looking at.
  const client = renderCard({ approveAssessment: ASSESSMENT_CONFLICT });
  await screen.findAllByTestId("action-row");

  await userEvent.click(
    within(
      row("assessment_approval:approve"),
    ).getByTestId("action-command"),
  );

  await waitFor(() => {
    expect(
      client.getQueryData<ClaimDetail>(queryKeys.claims.detail(CLAIM_ID))?.header.status,
    ).toBe(CLAIM_DETAIL_APPROVED.body.header.status);
  });
});
