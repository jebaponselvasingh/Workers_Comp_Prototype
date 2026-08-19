import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_NOT_FOUND,
  CLAIM_DETAIL_SETTLED,
  CLAIM_DETAIL_TREATMENT,
  stubApi,
} from "@/test/api-mock";

import { ReadOnlyClaimPage } from "./ReadOnlyClaimPage";

/**
 * Story 5.5 AC 2 — read-only means **absent**, not disabled.
 *
 * The assertion that carries this story is negative, and negative assertions
 * are only worth anything when they are properties of the *tree* rather than of
 * a prop. That is why the view is composed from the presentational half of the
 * case file rather than flagged: a `readOnly` boolean threaded through
 * `InlineEditField` would make "no edit controls" a fact about a variable, and
 * the test below would be reading that variable back.
 *
 * So the assertions are: no textbox, no combobox, and no button whose name
 * matches the edit/approve/add vocabulary. And one more that is not about the
 * tree at all — no request to `/claims/{id}/financials`, whose own docstring
 * says "This GET can write". A read-only surface must not trigger one.
 *
 * Rendered through a real `Routes` table so `useParams` resolves the way it
 * does in the app: the claim id is a *path* segment here, not a query
 * parameter, and a test that passed it as a prop would be testing a component
 * the route table never mounts.
 */
function renderClaim(
  routes: Parameters<typeof stubApi>[0],
  claimId = "WC-20017",
): void {
  stubApi(routes);
  render(
    <MemoryRouter initialEntries={[`/dashboard/claims/${claimId}`]}>
      <QueryClientProvider client={createQueryClient()}>
        <Routes>
          <Route path="/dashboard/claims/:claimId" element={<ReadOnlyClaimPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/**
 * The same page, entered the way a click enters it — carrying its origin.
 *
 * `MemoryRouter`'s `initialEntries` takes a location object, which is how
 * router state is supplied without a real navigation. Separate from
 * `renderClaim` rather than an optional argument on it, so the two cases a
 * reader has to tell apart — arrived by clicking, arrived by pasting — do not
 * differ by one defaulted parameter.
 */
function renderClaimFrom(state: { from: string }, claimId = "WC-20017"): void {
  stubApi({ claimDetail: CLAIM_DETAIL_TREATMENT });
  render(
    <MemoryRouter initialEntries={[{ pathname: `/dashboard/claims/${claimId}`, state }]}>
      <QueryClientProvider client={createQueryClient()}>
        <Routes>
          <Route path="/dashboard/claims/:claimId" element={<ReadOnlyClaimPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Every URL the app asked for, in order.
 *
 * Read off the global `fetch` `stubApi` installed rather than off a handle it
 * returns, because it returns none — and the *absence* of a request is what
 * this file's central assertion is about, so it has to be read from the one
 * place every request actually goes.
 */
function requested(): string[] {
  return vi.mocked(globalThis.fetch).mock.calls.map(([input]) => {
    if (typeof input === "string") return input;
    if (input instanceof URL) return input.href;
    return (input as Request).url;
  });
}

test("the case header, the gauge and the stepper render", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  expect(await screen.findByTestId("case-header-claim-id")).toHaveTextContent(
    "WC-20017",
  );
  // The gauge lives inside the header, which is how `RiskGauge` is reused here
  // — by mounting the component that already owns it rather than a second copy.
  expect(screen.getByTestId("risk-gauge")).toBeInTheDocument();
  expect(screen.getAllByTestId("stepper-step").length).toBeGreaterThan(0);
});

test("the stage's own facts and its money render from the case-file payload", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  const overview = await screen.findByTestId("readonly-overview");
  expect(overview).toHaveTextContent("Kaya Johnson");

  // The figures come off the stage variant the detail payload already carries —
  // 1_240_000 cents is $12,400 — so no second request is needed to draw them.
  expect(screen.getByTestId("readonly-paid-medical")).toHaveTextContent("$12,400");
  expect(screen.getByTestId("readonly-paid-indemnity")).toHaveTextContent("$8,600");
  expect(screen.getByTestId("readonly-reserve")).toHaveTextContent("$45,000");
});

test("a settled claim shows the four figures its own variant carries", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_SETTLED }, "WC-20068");

  // Each variant shows what its payload holds and nothing more: the settled one
  // carries a total and an expense column the treatment one does not, and
  // inventing a zero for an absent figure would be the client asserting a fact
  // the server did not send.
  expect(await screen.findByTestId("readonly-total-paid")).toHaveTextContent("$31,000");
  expect(screen.getByTestId("cost-bar")).toBeInTheDocument();
});

test("the reserve verdict is stated, not offered as a control", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  const verdict = await screen.findByTestId("readonly-reserve-verdict");
  // The server's token through the shared accent map, so one claim reads one
  // judgement wherever it is drawn.
  expect(verdict).toHaveAttribute(
    "data-verdict",
    CLAIM_DETAIL_TREATMENT.body.reserveCheck.verdict,
  );
  expect(verdict.tagName).toBe("P");
});

test("the documents on file are listed", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  const list = await screen.findByTestId("document-list");
  expect(within(list).getAllByTestId("document-row").length).toBeGreaterThan(0);
});

// --- AC 2: the negative assertions --------------------------------------

/**
 * The vocabulary an edit affordance is named with.
 *
 * A name list rather than a count of buttons, because the page legitimately
 * has controls that are *not* edits: a back link, and the document rows that
 * open the read-only viewer. What must not appear is anything that writes.
 */
const EDIT_VERBS = /edit|save|approve|add|remove|delete|confirm|update|log|override/i;

test("the read-only view renders no edit affordance of any kind", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  await screen.findByTestId("case-header-claim-id");

  // Not "the edit controls are disabled" — **absent**. A disabled control is
  // still an affordance, and a test asserting `disabled` would be reading a
  // prop rather than a property of the tree.
  expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
  for (const button of screen.queryAllByRole("button")) {
    expect(button.textContent ?? "").not.toMatch(EDIT_VERBS);
  }
});

test("the read-only view issues no request that can write", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  await screen.findByTestId("case-header-claim-id");

  const urls = requested();
  expect(urls.some((url) => url.includes("/claims/WC-20017"))).toBe(true);
  // `GET /claims/{id}/financials` refreshes `payment_schedule_week` before
  // reading it — its own docstring says "This GET can write" — so a read-only
  // surface must not reach it, and `FinancialSummaryCard` is absent for that
  // reason rather than for a layout one.
  expect(urls.some((url) => url.includes("/financials"))).toBe(false);
  expect(urls.some((url) => url.includes("/actions"))).toBe(false);
});

test("a claim outside the caller's book renders the in-app not-found state", async () => {
  renderClaim({ claimDetail: CLAIM_DETAIL_NOT_FOUND }, "WC-29999");

  const notFound = await screen.findByTestId("readonly-not-found");
  expect(notFound).toHaveTextContent("WC-29999");
  // A 404 is an answer rather than a failure, so it is not a `role="alert"` and
  // it does not say something went wrong. And no redirect: the URL is left as
  // typed, because rewriting it under somebody working out why their link was
  // wrong is worse than showing them the answer.
  expect(notFound).not.toHaveAttribute("role", "alert");
  expect(screen.queryByTestId("readonly-error")).not.toBeInTheDocument();
});

test("a genuine failure is distinguished from a not-found", async () => {
  // 400 rather than 5xx: `createQueryClient` retries a server-side failure
  // twice, so a 503 would leave this test waiting on two more round trips for
  // no gain — and the branch under test is "not a 404", which any non-404 is.
  renderClaim({ claimDetail: { status: 400, body: {} } });

  const alert = await screen.findByTestId("readonly-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(screen.queryByTestId("readonly-not-found")).not.toBeInTheDocument();
});

test("a request in flight draws a skeleton", () => {
  renderClaim({ claimDetail: "pending" });

  expect(screen.getByTestId("readonly-skeleton")).toBeInTheDocument();
});

test("a back control returns to the list a cold-loaded claim cannot know", async () => {
  // No router state, because nobody navigated — this is the pasted URL of AC 4.
  // The unfiltered list is the honest fallback: a bare claim URL records no
  // narrowing, and inventing one would be a worse answer than the whole book.
  renderClaim({ claimDetail: CLAIM_DETAIL_TREATMENT });

  expect(await screen.findByTestId("readonly-back")).toHaveAttribute(
    "href",
    "/dashboard/claims",
  );
});

test("a back control returns to the filtered list the claim was opened from", async () => {
  // The defect this replaces: the link was hardcoded to the bare list path, so
  // drilling High Risk, opening the seventh claim and pressing Back landed on
  // the whole portfolio — contradicting the page's own premise that the filter
  // set lives in the URL and a drill URL reconstructs the view.
  renderClaimFrom({ from: "/dashboard/claims?filter%5BseverityBand%5D=high" });

  const back = await screen.findByTestId("readonly-back");
  expect(back).toHaveAttribute("href", "/dashboard/claims?filter%5BseverityBand%5D=high");
  expect(back).toHaveTextContent("Back to claims");
});

test("a claim opened from the dashboard offers its way back there, not into a list", async () => {
  // The worklist path: the row lives on the dashboard, not in a filtered list,
  // so sending it to the unfiltered drill list would answer a question nobody
  // asked — and the label has to say where it actually goes.
  renderClaimFrom({ from: "/dashboard" });

  const back = await screen.findByTestId("readonly-back");
  expect(back).toHaveAttribute("href", "/dashboard");
  expect(back).toHaveTextContent("Back to dashboard");
});
