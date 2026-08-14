/**
 * The benefit card: what it renders, and what the comp-rate override sends
 * (Story 3.1, AC 1–4).
 *
 * Two halves, on `InjuryWrites.test.tsx`'s split. What the card *draws* is
 * asserted against the fixture, because the acceptance criterion is that every
 * figure came from the server. What the override *sends* is asserted against
 * the recorded request, because the body — basis points, and which version
 * travelled with them — is the part a render test cannot see and the part a
 * compare-and-swap depends on.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { applyOptimisticCompRate, type Benefit, type ClaimDetail } from "@/api/claims";
import { createQueryClient } from "@/api/queryClient";
import {
  BENEFIT,
  BENEFIT_OVERRIDDEN,
  BENEFIT_OVERRIDDEN_AT_DEFAULT,
  BENEFIT_PTD,
  CLAIM_DETAIL_INTAKE,
  CLAIM_DETAIL_INVESTIGATION,
  CLAIM_DETAIL_SETTLED,
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "./ClaimDetailPane";

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

let recorded: Recorded[] = [];

/** Every `/api/*` request, recorded in front of the stub — 2.4's helper. */
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

function compRateWrites(): Recorded[] {
  return recorded.filter((call) => call.method !== "GET" && call.url.includes("/comp-rate"));
}

async function openOverview(claimDetail: StubRouteFor = CLAIM_DETAIL_TREATMENT) {
  stubApi({ me: ME_HANDLER, claimDetail });
  recordRequests();
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("case-header");
}

/** A case file whose benefit block is `benefit`, everything else unchanged. */
function withBenefit(benefit: Benefit) {
  return {
    ...CLAIM_DETAIL_TREATMENT,
    body: { ...CLAIM_DETAIL_TREATMENT.body, benefit },
  };
}

/** Type into the comp-rate field and commit it the way a handler does. */
async function commitRate(value: string): Promise<void> {
  const input = screen.getByTestId("edit-compRate");
  await userEvent.clear(input);
  await userEvent.type(input, value);
  await userEvent.tab();
}

afterEach(() => {
  recorded = [];
  vi.unstubAllGlobals();
});

// --- AC 1, 2, 3: what the card shows ------------------------------------

test("every figure on the card is the server's, formatted", async () => {
  await openOverview();

  expect(screen.getByTestId("benefit-weekly")).toHaveTextContent("$955/wk");
  expect(screen.getByTestId("benefit-type")).toHaveTextContent(
    "TTD — Temporary Total Disability",
  );
  expect(screen.getByTestId("benefit-bounds")).toHaveTextContent("$257 – $1,711/wk");
  expect(screen.getByTestId("benefit-state")).toHaveTextContent("Washington");
  expect(screen.getByTestId("edit-compRate")).toHaveValue(66.67);
});

test("the payment-schedule note carries the server's waiting period", async () => {
  // Seven days is a rules-tier parameter (`benefit_params.waitingPeriodDays`),
  // so the sentence is assembled from the payload rather than written out —
  // Story 3.3 applies the same number to generate the schedule itself.
  await openOverview();

  expect(screen.getByTestId("benefit-schedule")).toHaveTextContent(
    "Weekly, starting 7-day waiting period after DOI",
  );
});

test("the reserve rationale is rendered exactly as the server wrote it", async () => {
  // A deterministic service output (AD-2), not a template with holes in it —
  // so the assertion is the whole paragraph rather than a phrase from it.
  await openOverview();

  expect(screen.getByTestId("benefit-rationale")).toHaveTextContent(
    BENEFIT.reserveRationale,
  );
});

test("the provenance note cites the schedule's effective date", async () => {
  // The prototype closes with "Illustrative figures for prototype purposes";
  // until statutory data is validated (NFR-4) the honest equivalent is the
  // date the schedule on file took effect, which is a fact the table holds.
  await openOverview();

  expect(screen.getByTestId("benefit-provenance")).toHaveTextContent(
    "Statutory schedule effective 2026-01-01",
  );
  expect(screen.getByTestId("benefit-provenance")).toHaveTextContent(
    "current WA WC board benefit schedule",
  );
});

test("a permanent total disability shows the full-wage rate and its own label", async () => {
  await openOverview(withBenefit(BENEFIT_PTD));

  expect(screen.getByTestId("edit-compRate")).toHaveValue(100);
  expect(screen.getByTestId("benefit-type")).toHaveTextContent(
    "PTD — Permanent Total Disability",
  );
  // The clamp, visible: at 100% of wage this claim is on Washington's maximum.
  expect(screen.getByTestId("benefit-weekly")).toHaveTextContent("$1,711/wk");
});

test("the card renders on the two variants the prototype puts it on", async () => {
  await openOverview(CLAIM_DETAIL_INVESTIGATION);
  expect(screen.getByTestId("benefit-card")).toBeInTheDocument();
});

test.each([
  ["intake", CLAIM_DETAIL_INTAKE],
  ["settled", CLAIM_DETAIL_SETTLED],
])("the card does not render on the %s variant", async (_stage, fixture) => {
  // The *payload* carries a benefit at every stage — Story 3.3 reads it from
  // the Bills tab — but an intake claim's card would show a benefit nobody is
  // paying yet and a settled claim's one that has stopped.
  await openOverview(fixture);

  expect(screen.queryByTestId("benefit-card")).not.toBeInTheDocument();
});

test("the input's step is the rate's granularity, so no value it holds is invalid", async () => {
  // Found by code review: `step` is not a convenient spinner increment. The
  // browser marks a value that is not a multiple of it a `stepMismatch`, and
  // `stepUp()` snaps to the next multiple above `min` — so the prototype's
  // `step="0.5"` makes the *statutory default itself* invalid and turns one
  // click of the up arrow into 67.00, discarding the handler's decimals.
  //
  // Asserted on the rendered field rather than on the constant, and over
  // several two-decimal rates rather than the one the fixture happens to
  // carry, because the property is "every value this field can commit is a
  // valid step" — which is what makes 0.01 the right answer and 0.5 a bug
  // whatever the default happens to be.
  await openOverview();
  const input = screen.getByTestId("edit-compRate") as HTMLInputElement;

  for (const rate of ["66.67", "0.01", "70.25", "149.99", "150"]) {
    input.value = rate;
    expect(input.validity.stepMismatch, rate).toBe(false);
  }
});

// --- AC 4: the override, and the ↺ --------------------------------------

test("the reset control appears only when the claim is overridden", async () => {
  await openOverview();
  expect(screen.queryByTestId("benefit-reset")).not.toBeInTheDocument();
});

test("a claim overridden to exactly the default still offers the reset", async () => {
  // The payload a client comparing `compRateBp` with `defaultCompRateBp`
  // cannot tell from an un-overridden claim — which is why the server
  // publishes `isOverridden` and why the ↺ is bound to it. Without this, the
  // one way back to the statutory rate disappears for the handler who typed
  // 66.67 by hand.
  await openOverview(withBenefit(BENEFIT_OVERRIDDEN_AT_DEFAULT));

  expect(screen.getByTestId("benefit-reset")).toBeInTheDocument();
});

test("committing a rate sends basis points and the claim's version", async () => {
  await openOverview();

  await commitRate("70.25");

  await waitFor(() => expect(compRateWrites()).toHaveLength(1));
  const [request] = compRateWrites();
  expect(request.method).toBe("PATCH");
  expect(request.body).toEqual({
    expectedVersion: CLAIM_DETAIL_TREATMENT.body.version,
    // Basis points, not 70.25: the unit is the column's and the rule
    // document's, and it is exact.
    compRateBp: 7025,
  });
});

test("the reset sends a null rate rather than the default's value", async () => {
  // Sending 6667 back would record an *override* that happens to equal the
  // statutory rate — a claim the ↺ could never leave, and a rate that would
  // stop tracking the rule document the day an operator retunes it.
  await openOverview(withBenefit(BENEFIT_OVERRIDDEN));

  await userEvent.click(screen.getByTestId("benefit-reset"));

  await waitFor(() => expect(compRateWrites()).toHaveLength(1));
  expect(compRateWrites()[0].body).toEqual({
    expectedVersion: CLAIM_DETAIL_TREATMENT.body.version,
    compRateBp: null,
  });
});

test.each([
  ["150.01", "above the maximum"],
  ["70.255", "a third decimal"],
  ["abc", "not a number"],
])("an entry that is %s is refused inline without a request", async (typed) => {
  // Refused in the browser *as well as* server-side: the bounds arrive on the
  // payload (`compRateMinBp`/`compRateMaxBp`), so the field can say why
  // without a round trip — and the command still refuses it, which is the
  // enforcement.
  await openOverview();

  await commitRate(typed);

  expect(await screen.findByTestId("edit-compRate-invalid")).toBeInTheDocument();
  expect(compRateWrites()).toHaveLength(0);
});

test("a refused entry keeps what was typed so it can be corrected", async () => {
  await openOverview();

  await commitRate("400");

  expect(screen.getByTestId("edit-compRate")).toHaveValue(400);
  expect(screen.getByTestId("edit-compRate-invalid")).toHaveTextContent(
    "Comp rate must be between 0.00% and 150.00% of AWW.",
  );
});

test("the refusal is inline, never a dialog", async () => {
  // NFR-3 and UX-DR11, the same assertion Story 2.3 makes about the inline
  // editors: a blocking dialog is not how this console refuses anything.
  await openOverview();

  await commitRate("999");

  expect(await screen.findByTestId("edit-compRate-invalid")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

// --- AD-9: what the optimistic update is allowed to move ----------------

test("the optimistic update writes the rate and nothing computed from it", () => {
  // Asserted on the function rather than through a render, because what
  // matters is what it *leaves alone*: the weekly figure, the indemnity type,
  // the statutory bounds and the rationale are the server's answers, and a
  // card that guessed any of them would show a state that exists nowhere.
  // The fixture is a plain literal; the cast is the same seam every other
  // test in this directory uses to hand one to a typed function.
  const detail = CLAIM_DETAIL_TREATMENT.body as unknown as ClaimDetail;
  const next = applyOptimisticCompRate(detail, 7025);

  expect(next.benefit.compRateBp).toBe(7025);
  expect(next.benefit.isOverridden).toBe(true);
  expect(next.benefit.weeklyCents).toBe(detail.benefit.weeklyCents);
  expect(next.benefit.indemnityType).toBe(detail.benefit.indemnityType);
  expect(next.benefit.reserveRationale).toBe(detail.benefit.reserveRationale);
  expect(next.version).toBe(detail.version);
});

test("the optimistic reset shows the default rate and drops the override flag", () => {
  const overridden = {
    ...CLAIM_DETAIL_TREATMENT.body,
    benefit: BENEFIT_OVERRIDDEN,
  } as unknown as ClaimDetail;
  const next = applyOptimisticCompRate(overridden, null);

  expect(next.benefit.compRateBp).toBe(BENEFIT.defaultCompRateBp);
  expect(next.benefit.isOverridden).toBe(false);
  // Still the overridden weekly figure and rationale: those wait for the
  // server, which is the whole distinction this test exists to draw.
  expect(next.benefit.weeklyCents).toBe(BENEFIT_OVERRIDDEN.weeklyCents);
  expect(next.benefit.reserveRationale).toBe(BENEFIT_OVERRIDDEN.reserveRationale);
});
