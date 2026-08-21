/**
 * Story 6.2 — the AI Insights tab renders what it was sent, and never decides.
 *
 * Three of these tests are the states the *server cannot describe* — pending,
 * a failed read, and a claim nobody has generated for — which is this file's
 * first job by house convention, and the third of them is the one AC 2 names.
 * "Not generated yet" is a 200 with four explicit cards, not an error and not a
 * spinner, because it is the state every claim is in until a refresh reaches
 * it.
 *
 * The rest assert faithfulness. The payload said X, the screen says X: the
 * figures are the server's display strings, the neighbour list is in the order
 * it arrived, the actions keep their urgencies, and the fraud card's variant is
 * `outcome` rather than a comparison this component made.
 * `noDerivation.test.ts` proves the arithmetic is absent as *source*; this
 * proves nothing was quietly relabelled on the way to the DOM.
 *
 * **Nothing here asserts on a sentence.** The narratives are model output, and
 * AD-15's rule about asserting structure rather than prose holds at every level
 * — the summaries in the fixture are obvious filler for exactly that reason.
 * What is asserted about them is that they *arrive*: a card that dropped the
 * narrative would still pass every figure assertion.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import {
  CLAIM_INSIGHTS,
  CLAIM_INSIGHTS_LOW_RISK,
  CLAIM_INSIGHTS_NOT_GENERATED,
  CLAIM_INSIGHTS_PARTIAL_REFRESH,
  CLAIM_INSIGHTS_REFRESHED,
  COPILOT_AVAILABLE,
  COPILOT_UNAVAILABLE,
  ME_HANDLER,
  type StubRoute,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { INSIGHT_KIND_LABEL } from "../labels";
import { InsightsTab } from "./InsightsTab";

const CLAIM_ID = "WC-20017";
const READY = CLAIM_INSIGHTS.body;

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderTab(
  claimInsights: StubRoute = CLAIM_INSIGHTS,
  refreshInsights?: StubRoute,
  extra: Partial<StubRoutes> = {},
) {
  stubApi({ me: ME_HANDLER, claimInsights, refreshInsights, ...extra });
  const client = createQueryClient();
  render(
    <QueryClientProvider client={client}>
      <InsightsTab claimId={CLAIM_ID} />
    </QueryClientProvider>,
  );
  return client;
}

// --- NFR-3 / UX-DR11: the three states the server cannot describe ---------

test("a request in flight renders a skeleton rather than an empty grid", async () => {
  renderTab("pending");

  expect(await screen.findByTestId("insights-skeleton")).toBeInTheDocument();
  expect(screen.queryByTestId("insights-tab")).not.toBeInTheDocument();
});

test("a failed read renders one alert, not four blank cards", async () => {
  // A 403 rather than a 500: the shared client retries 5xx twice with backoff,
  // so a 500 would race the retry timer instead of testing the branch (Story
  // 1.4's lesson, restated on the pane's and the Bills tab's own error tests).
  renderTab({
    status: 403,
    body: {
      type: "/problems/forbidden",
      title: "Forbidden",
      status: 403,
      detail: "no",
    },
  });

  const alert = await screen.findByTestId("insights-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(screen.queryByTestId("insights-tab")).not.toBeInTheDocument();
});

test("a claim nobody has generated for shows four empty cards and a refresh", async () => {
  // AC 2's explicit not-yet-generated state, and the distinction that makes it
  // worth a branch: this is a **200**, so the tab is not in its error state and
  // the four headings are on screen. A 404 would have sent it down the branch
  // above and told the handler their claim could not be loaded, which is a
  // different and untrue thing.
  renderTab(CLAIM_INSIGHTS_NOT_GENERATED);
  await screen.findByTestId("insights-tab");

  for (const card of [
    "insight-similar",
    "insight-reserve",
    "insight-actions",
    "insight-fraud",
  ]) {
    expect(screen.getByTestId(`${card}-empty`)).toBeInTheDocument();
    // No timestamp row: there is nothing to date, and "Generated —" would read
    // as a load that half-finished.
    expect(screen.queryByTestId(`${card}-generated`)).not.toBeInTheDocument();
  }
  expect(screen.getByTestId("insights-refresh")).toBeEnabled();
});

// --- AC 2: four cards, each with its timestamp and model ------------------

test("every generated card carries its generation timestamp and the model", async () => {
  // AD-10's rule made visible: an AI narrative is a cache row rendered with the
  // time it was generated and the model that wrote it, never a claim column. A
  // card without those two reads as a claim fact, which is the confusion the
  // whole design is arranged to prevent.
  renderTab();
  await screen.findByTestId("insights-tab");

  for (const card of [
    "insight-similar",
    "insight-reserve",
    "insight-actions",
    "insight-fraud",
  ]) {
    const stamp = screen.getByTestId(`${card}-generated`);
    expect(stamp).toHaveTextContent(/^Generated /);
    expect(stamp).toHaveTextContent("model-stub");
    expect(screen.queryByTestId(`${card}-empty`)).not.toBeInTheDocument();
  }
});

test("the tab offers no edit affordance anywhere (FR-H-9)", async () => {
  // The structural half of "never user-editable". `ai_insight` has no `version`
  // column, so there is nothing an inline edit could send back — and this
  // asserts the consequence a handler can see: no input, no textarea, no
  // select, and exactly one button on the whole tab, which regenerates.
  renderTab();
  const tab = await screen.findByTestId("insights-tab");

  expect(within(tab).queryAllByRole("textbox")).toHaveLength(0);
  expect(within(tab).queryAllByRole("combobox")).toHaveLength(0);
  expect(within(tab).getAllByRole("button")).toHaveLength(1);
  expect(within(tab).getByRole("button")).toHaveAttribute(
    "data-testid",
    "insights-refresh",
  );
});

// --- faithfulness: the payload said X, the screen says X -----------------

test("the similar-case card lists the neighbours in the order they arrived", async () => {
  renderTab();
  await screen.findByTestId("insights-tab");

  const expected = READY.similarCaseOutcomes.content.neighbours;
  const rendered = screen.getAllByTestId("insight-neighbour");
  expect(rendered).toHaveLength(expected.length);
  expected.forEach((neighbour, index) => {
    expect(rendered[index]).toHaveTextContent(neighbour.employerShortName);
    expect(rendered[index]).toHaveTextContent(neighbour.injuryType);
  });
});

test("the reserve card renders the server's display strings, never the cents", async () => {
  // The AD-2 boundary at the last hop. Both halves are on the payload — the
  // integer for Story 7.1's aggregation, the string for the reader — and the
  // string was produced by the same `format_dollars` the rationale above it is
  // written with. A card that formatted `cents` itself would be a second
  // rounding of one figure, inside a card whose subject is that two numbers
  // agree.
  renderTab();
  await screen.findByTestId("insights-tab");

  const reserve = READY.reserveAdequacyReview.content;
  expect(screen.getByTestId("insight-reserve-amount")).toHaveTextContent(
    reserve.reserve.display,
  );
  expect(screen.getByTestId("insight-reserve-medical")).toHaveTextContent(
    reserve.remainingMedical.display,
  );
  // …and the raw cents appear nowhere on the card.
  expect(screen.getByTestId("insight-reserve-body")).not.toHaveTextContent(
    String(reserve.reserve.cents),
  );
});

test("the next-actions card keeps the server's order and urgencies", async () => {
  renderTab();
  await screen.findByTestId("insights-tab");

  const expected = READY.nextBestActions.content.actions;
  const rendered = screen.getAllByTestId("insight-action");
  expect(rendered.map((row) => row.getAttribute("data-action"))).toEqual(
    expected.map((action) => action.id),
  );
  expect(
    screen
      .getAllByTestId("insight-action-urgency")
      .map((chip) => chip.getAttribute("data-urgency")),
  ).toEqual(expected.map((action) => action.urgency));
});

// --- AC 3: the fraud card's two variants ---------------------------------

test("an elevated claim's fraud card lists red flags in the error tone", async () => {
  renderTab();
  await screen.findByTestId("insights-tab");

  const outcome = screen.getByTestId("insight-fraud-outcome");
  expect(outcome).toHaveAttribute("data-outcome", "red_flags");
  expect(outcome).toHaveClass("text-error");
  expect(screen.getAllByTestId("insight-fraud-flag").length).toBeGreaterThan(0);
  expect(
    screen.queryByTestId("insight-fraud-confirmation"),
  ).not.toBeInTheDocument();
});

test("a low-risk claim's fraud card confirms rather than showing an empty list", async () => {
  // AC 3, and the assertion is deliberately in three parts. The *variant* is
  // the server's `outcome`, so a component that branched on the score would
  // render the wrong half of the union; the *tone* is ok rather than error,
  // because a confirmation is the one thing on this tab that genuinely is good
  // news; and there is **no red-flag list at all** — not an empty one, which is
  // exactly what the criterion rules out.
  renderTab(CLAIM_INSIGHTS_LOW_RISK);
  await screen.findByTestId("insights-tab");

  const outcome = screen.getByTestId("insight-fraud-outcome");
  expect(outcome).toHaveAttribute("data-outcome", "low_risk");
  expect(outcome).toHaveClass("text-ok");
  expect(screen.getByTestId("insight-fraud-confirmation")).toBeInTheDocument();
  expect(screen.queryAllByTestId("insight-fraud-flag")).toHaveLength(0);
});

// --- the refresh affordance ----------------------------------------------

test("refresh installs the response and leaves the case file alone", async () => {
  // The response body *is* the fresh payload, so it is installed rather than
  // re-fetched (`afterChecklistWrite`'s move). And nothing else is invalidated:
  // a regenerated narrative is not a claim fact, so the case file, the queue
  // and the top-bar tiles have no reason to move.
  const client = renderTab(
    CLAIM_INSIGHTS_NOT_GENERATED,
    CLAIM_INSIGHTS_REFRESHED,
  );
  await screen.findByTestId("insight-similar-empty");

  await userEvent.click(screen.getByTestId("insights-refresh"));

  await waitFor(() => {
    expect(screen.getByTestId("insight-similar-generated")).toBeInTheDocument();
  });
  // The four cards, and **not** `failedKinds`: the key is typed `ClaimInsights`
  // and the extra member is a fact about the run rather than about the cache,
  // so writing the response whole left an entry describing a refresh that had
  // long since finished (follow-up review of Story 6.2, C6).
  const { failedKinds, ...cards } = CLAIM_INSIGHTS_REFRESHED.body;
  expect(failedKinds).toEqual([]);
  expect(client.getQueryData(queryKeys.claims.insights(CLAIM_ID))).toEqual(
    cards,
  );
  expect(
    client.getQueryData(queryKeys.claims.detail(CLAIM_ID)),
  ).toBeUndefined();
  // Every kind was written, so there is nothing to disclose — the partial
  // notice must not appear on a clean run.
  expect(screen.queryByTestId("insights-partial")).not.toBeInTheDocument();
});

test("a partial refresh says which card the model refused", async () => {
  // Three of four written is a 200 with a fresh payload, because three new
  // narratives are a better answer than a refusal — but the refused card goes
  // on showing its previous generation, and without `failedKinds` a handler
  // watching one card not change could not tell that from a button that did
  // nothing (review of Story 6.2, M7).
  //
  // The *kind* is named, which is publishable where the model's own refusal
  // text is not: a kind token is the same closed vocabulary the payload is
  // already keyed by, and carries no content (AD-11).
  renderTab(CLAIM_INSIGHTS, CLAIM_INSIGHTS_PARTIAL_REFRESH);
  await screen.findByTestId("insights-tab");

  await userEvent.click(screen.getByTestId("insights-refresh"));

  const notice = await screen.findByTestId("insights-partial");
  expect(notice).toHaveTextContent(INSIGHT_KIND_LABEL.fraud_risk_indicators);
  // This claim's fraud card *has* a previous generation, so the sentence about
  // one is true here — and the sibling test below is the case where it is not.
  expect(notice).toHaveTextContent("still shows its previous generation");
  // Not an alert: nothing failed that the handler must act on, and the cards
  // are all still on screen.
  expect(notice).toHaveAttribute("role", "status");
  expect(screen.getByTestId("insight-fraud-generated")).toBeInTheDocument();
});

test("a partial refresh on a never-generated claim does not claim a previous generation", async () => {
  // The common case, and the one the sentence was wrong about (follow-up review
  // of Story 6.2, C6). Every claim starts with four `not_generated` cards, so
  // the *first* Refresh anybody presses is exactly the run where "it still
  // shows its previous generation" is false: there is no previous generation,
  // and the refused card is showing an empty state.
  //
  // Asserted on the words rather than on the notice merely existing, because
  // the defect was entirely in the words.
  renderTab(CLAIM_INSIGHTS_NOT_GENERATED, {
    status: 200,
    body: {
      ...CLAIM_INSIGHTS_NOT_GENERATED.body,
      failedKinds: ["fraud_risk_indicators"],
    },
  });
  await screen.findByTestId("insights-tab");

  await userEvent.click(screen.getByTestId("insights-refresh"));

  const notice = await screen.findByTestId("insights-partial");
  expect(notice).toHaveTextContent(INSIGHT_KIND_LABEL.fraud_risk_indicators);
  expect(notice).toHaveTextContent("has not been generated yet");
  expect(notice).not.toHaveTextContent("previous generation");
  expect(screen.getByTestId("insight-fraud-empty")).toBeInTheDocument();
});

test("a refused refresh renders the reason inline and keeps the cards standing", async () => {
  // Story 6.6 owns degradation UX; what this story owes is that a 503 does not
  // blank the tab. The previously generated cards are exactly the cards a
  // handler could see before the model server went down, which is the honest
  // behaviour for a cache — and the refusal is announced in place rather than
  // in a dialog (UX-DR11).
  renderTab(CLAIM_INSIGHTS, {
    status: 503,
    body: { detail: "Model server unavailable" },
  });
  await screen.findByTestId("insights-tab");

  await userEvent.click(screen.getByTestId("insights-refresh"));

  const alert = await screen.findByTestId("insights-refresh-error");
  expect(alert).toHaveAttribute("role", "alert");
  // **A written sentence, not the error's `message`** (review of Story 6.2,
  // M16). `api/client.ts` synthesises `detail` for any response without a
  // problem envelope, so a proxy timeout used to render as "The server answered
  // 504." — machine text in the place a handler reads. Asserted on the words,
  // because "a `role="alert"` exists" was all this test checked and would have
  // passed against exactly that.
  expect(alert).toHaveTextContent("could not be regenerated");
  expect(alert).not.toHaveTextContent("The server answered");
  expect(screen.getByTestId("insight-similar-generated")).toBeInTheDocument();
});

test("a failed read still offers the refresh that would repair it", async () => {
  // The error branch had a sentence and no way out (review of Story 6.2, M3).
  // That matters here more than on most surfaces: the likeliest cause of a
  // failed *read* on this tab is a stored narrative the server can no longer
  // validate against its schema, and regenerating overwrites exactly that row —
  // so the one control that fixes it was the one control the branch omitted.
  renderTab(
    {
      status: 403,
      body: {
        type: "/problems/forbidden",
        title: "Forbidden",
        status: 403,
        detail: "no",
      },
    },
    CLAIM_INSIGHTS_REFRESHED,
  );
  await screen.findByTestId("insights-error");

  const refresh = screen.getByTestId("insights-refresh");
  expect(refresh).toBeEnabled();
  await userEvent.click(refresh);

  // …and it really does repair the tab, which is the point rather than a
  // bonus: the refresh response *is* the fresh payload, so installing it puts
  // the query back into a success state and the four cards render without a
  // second GET. A branch that offered a button which could not fix anything
  // would be worse than the sentence it replaced.
  await waitFor(() => {
    expect(screen.getByTestId("insight-similar-generated")).toBeInTheDocument();
  });
  expect(screen.queryByTestId("insights-error")).not.toBeInTheDocument();
});

// --- Story 6.6: the refresh control during a model outage -----------------

test("Refresh disables itself when the model is unavailable, and says why", async () => {
  // Task 4's audit found exactly two non-copilot server surfaces that reach the
  // chat client, and this is the one with a button on it. The 503 that
  // `POST …/insights/refresh` answers is unchanged and still the backstop — what
  // changes is that it stops being the *discovery* mechanism, which is the same
  // "disable exactly the affected input" rule the copilot panel follows.
  renderTab(CLAIM_INSIGHTS, undefined, {
    copilotAvailability: COPILOT_UNAVAILABLE,
  });

  await screen.findByTestId("insights-tab");
  await waitFor(() =>
    expect(screen.getByTestId("insights-refresh")).toBeDisabled(),
  );
  expect(
    screen.getByTestId("insights-refresh-unavailable"),
  ).toHaveTextContent(/AI is unavailable/i);
});

test("a 503 from Refresh marks the outage instead of leaving the button live", async () => {
  // The window the probe cannot cover. It is cached server-side and polled
  // every fifteen seconds, so the model can go down while this control is still
  // enabled — and a handler who presses it has learned, first hand and sooner
  // than any probe could, that the model server is not answering. Leaving the
  // button enabled after that would invite them to find out again, which is the
  // discovery-by-failure Story 6.6 exists to remove.
  //
  // The availability route answers `true` first and `false` afterwards, so the
  // assertion below can only pass if the failed press caused a **refetch**: a
  // mutation that merely marked the entry stale would leave the first answer
  // rendered until the interval elapsed.
  let polls = 0;
  renderTab(
    CLAIM_INSIGHTS,
    { status: 503, body: { detail: "Model server unavailable" } },
    {
      copilotAvailability: () => {
        polls += 1;
        return polls === 1 ? COPILOT_AVAILABLE : COPILOT_UNAVAILABLE;
      },
    },
  );
  await screen.findByTestId("insights-tab");
  expect(screen.getByTestId("insights-refresh")).toBeEnabled();

  await userEvent.click(screen.getByTestId("insights-refresh"));

  await screen.findByTestId("insights-refresh-error");
  await waitFor(() =>
    expect(screen.getByTestId("insights-refresh")).toBeDisabled(),
  );
  expect(
    screen.getByTestId("insights-refresh-unavailable"),
  ).toBeInTheDocument();
});

test("the cached cards keep rendering while the model is down", async () => {
  // The **read** path is deliberately untouched: `GET …/insights` answers 200
  // from a cache that needs no model, so an outage costs a handler the ability
  // to regenerate and nothing else. A tab that had gone to an error state here
  // would be exactly the hard dependency on the agent runtime AC 3 forbids.
  renderTab(CLAIM_INSIGHTS, undefined, {
    copilotAvailability: COPILOT_UNAVAILABLE,
  });

  expect(
    await screen.findByTestId("insight-similar-generated"),
  ).toBeInTheDocument();
  expect(screen.queryByTestId("insights-error")).not.toBeInTheDocument();
});
