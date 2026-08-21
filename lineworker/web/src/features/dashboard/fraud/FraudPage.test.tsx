import { MemoryRouter, useLocation } from "react-router";
import userEvent from "@testing-library/user-event";

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  DRILL_CLAIMS,
  DRILL_CLAIMS_EMPTY,
  FRAUD_PANEL,
  FRAUD_PANEL_EMPTY,
  FRAUD_PANEL_SCOPED,
  FRAUD_RATES,
  FRAUD_RATES_SORTED,
  FRAUD_RED_FLAGS,
  FRAUD_RED_FLAGS_ALL_CLEAR,
  FRAUD_RED_FLAGS_EMPTY,
  stubApi,
} from "@/test/api-mock";

import { FraudPage } from "./FraudPage";

/**
 * Story 7.1 AC 1/2/3/4 — the Fraud section renders the server's figures and
 * nothing of its own.
 *
 * The line these tests police is the one `noDerivation.test.ts` polices
 * structurally, and this surface strains it harder than any before it: the
 * browser is handed a band distribution, the two edges it was banded at, a
 * separate flagged population that *equals the high band on the seeded data*,
 * and a table of rates each carrying its own numerator and denominator. Every
 * one of those is a number a component could produce from the others, and each
 * would be right today and wrong the day an operator retuned a threshold.
 *
 * So the contrast fixture is the substance rather than a nicety.
 * `FRAUD_PANEL_SCOPED` pulls the two populations apart — six flagged against
 * four in the high band, at an edge of 70 — and a component that derived either
 * from the other renders four where six belongs. Reading both fixtures through
 * the same components is the only assertion that can tell them apart.
 *
 * **Everything is read off the accessible renderings**, never off Recharts' SVG:
 * the donut's visible legend and the bar charts' value lists. That is deliberate
 * twice over — it is what a screen-reader user gets, and it keeps these tests off
 * Recharts' internal element structure and off jsdom measuring anything.
 *
 * **Rendered through `FraudPage`, never a section alone.** The sections take
 * their query state as props, so mounting one directly would test a wiring
 * nobody ships — and "a red-flag outage leaves the distribution standing" would
 * not be assertable at all.
 */

function renderPage(routes: Parameters<typeof stubApi>[0]) {
  stubApi(routes);
  return render(
    <MemoryRouter>
      <QueryClientProvider client={createQueryClient()}>
        <FraudPage />
        {/* Every segment and every table row is a click target, and what a click
            *does* is a navigation. Rendering the current location beside the page
            is the smallest way to assert that without mounting the real route
            table — the subject is which URL a segment opens, not what renders at
            it. `PortfolioCharts.test.tsx`'s probe, verbatim. */}
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location">{`${location.pathname}${location.search}`}</span>
  );
}

/**
 * A **404**, not a 502 or a 500, everywhere a failure is stubbed.
 *
 * `createQueryClient` retries 5xx twice with backoff, so a 500 fixture leaves
 * every assertion below racing a retry timer rather than testing the render
 * branch. `App.test.tsx` records the same choice for the same reason. What is
 * under test is what a section draws when its query has failed, and 4xx is final.
 */
const NOT_FOUND = { status: 404, body: { title: "Not Found", status: 404 } };

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Every label/value pair a donut legend or a bar list is showing, in order. */
function rowsOf(testId: string): string[] {
  return screen
    .getAllByTestId(testId)
    .map((node) => node.textContent?.replace(/\s+/g, " ").trim() ?? "");
}

// --- the band distribution (AC 1) ---------------------------------------

test("the band legend renders the server's segments, zero-fill and edges", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-band-distribution-legend")).toBeInTheDocument(),
  );

  // Label/value pairs, in the order they arrived — low to high, which is a
  // distribution's reading order and deliberately not the severity donut's
  // high-first legend order.
  expect(rowsOf("fraud-band-distribution-legend-row")).toEqual([
    "Low:63",
    "Medium (≥ 35):24",
    "High (≥ 55):13",
  ]);
  // The centre total is the scope, not the banded subset.
  expect(screen.getByTestId("fraud-band-distribution-total")).toHaveTextContent("100");
});

test("a moved band edge moves the segment and the caption together", async () => {
  // The whole point of putting the edges on the wire: a component holding its
  // own 55 would render the same legend under both fixtures.
  renderPage({ fraudPanel: FRAUD_PANEL_SCOPED });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-band-distribution-legend")).toBeInTheDocument(),
  );

  expect(rowsOf("fraud-band-distribution-legend-row")).toEqual([
    "Low:0",
    "Medium (≥ 35):23",
    "High (≥ 70):4",
  ]);
});

test("the flagged population is the server's and is not the high band", async () => {
  // The assertion this whole story turns on. On `FRAUD_PANEL` the two are both
  // 13; here they are 6 and 4, and a page that derived either from the other
  // would render 4 in the card or 6 in the legend.
  renderPage({ fraudPanel: FRAUD_PANEL_SCOPED });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-kpi-flagged-value")).toHaveTextContent(/^6$/),
  );
  expect(rowsOf("fraud-band-distribution-legend-row")).toContain("High (≥ 70):4");
  // …and the SIU population is a third figure again.
  expect(screen.getByTestId("fraud-kpi-siu-value")).toHaveTextContent(/^2$/);
});

test("a band with no claims keeps its segment rather than disappearing", async () => {
  // The server's zero-fill, which every other distribution on this dashboard
  // would have omitted. A donut that dropped zero-count segments draws two arcs
  // here, and a reader cannot tell that from a build that forgot the third.
  //
  // Asserted on the **scoped** book rather than the empty one: a book with
  // claims in it is where an empty band is a fact about the portfolio. An empty
  // book has no claims to distribute at all and gets the sentence below instead.
  renderPage({ fraudPanel: FRAUD_PANEL_SCOPED });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-band-distribution-legend")).toBeInTheDocument(),
  );

  expect(rowsOf("fraud-band-distribution-legend-row")).toEqual([
    "Low:0",
    "Medium (≥ 35):23",
    "High (≥ 70):4",
  ]);
});

test("an empty book gets the donut's sentence, not three zeroes round a hole", async () => {
  // The empty message was written for exactly this reader and never rendered:
  // the server zero-fills the band vocabulary, so `items.length` is three for a
  // portfolio with nothing in it, and the emptiness test never fired. What an
  // analyst with no claims saw was a donut with no arcs over a legend of three
  // "0" rows — which is what a *failed* chart looks like.
  renderPage({ fraudPanel: FRAUD_PANEL_EMPTY, drillClaims: DRILL_CLAIMS_EMPTY });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-band-distribution-empty")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("fraud-band-distribution-empty")).toHaveTextContent(
    "No claims in this portfolio yet.",
  );
  expect(screen.queryByTestId("fraud-band-distribution-legend")).not.toBeInTheDocument();
});

// --- the SIU pipeline (AC 1) --------------------------------------------

test("the pipeline renders both groupings, omitting what the scope does not reach", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL });

  await waitFor(() =>
    expect(screen.getByTestId("siu-pipeline-stage-values")).toBeInTheDocument(),
  );

  // Three stages, not four: no seeded intake claim clears the referral
  // threshold, so the pipeline does not reach that stage. A component laying
  // out four fixed rows would draw an empty one.
  expect(
    within(screen.getByTestId("siu-pipeline-stage-values"))
      .getAllByRole("listitem")
      .map((node) => node.textContent?.trim()),
  ).toEqual(["Investigation: 2", "Under Treatment: 4", "Settled & Closed: 3"]);

  expect(
    within(screen.getByTestId("siu-pipeline-handler-values"))
      .getAllByRole("listitem")
      .map((node) => node.textContent?.trim()),
  ).toEqual(["Kaya Johnson: 4", "Marcus Chen: 3", "Sarah Williams: 2"]);
});

test("an empty book shows the pipeline's empty state rather than a bar of nothing", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL_EMPTY, drillClaims: DRILL_CLAIMS_EMPTY });

  await waitFor(() =>
    expect(screen.getByTestId("siu-pipeline-stage-empty")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("siu-pipeline-handler-empty")).toBeInTheDocument();
});

// --- drill-through (AC 4) -----------------------------------------------

test("a band legend row opens that band's claims", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL });
  await waitFor(() =>
    expect(screen.getByTestId("fraud-band-distribution-legend")).toBeInTheDocument(),
  );

  // Keyed on the segment rather than on its position: the legend is in the
  // rule's declaration order, and a positional click would open a different
  // band's list the moment the vocabulary grew.
  const high = screen
    .getAllByTestId("fraud-band-distribution-legend-link")
    .find((node) => node.getAttribute("data-key") === "high");
  await userEvent.click(high!);

  expect(screen.getByTestId("location")).toHaveTextContent(
    "/dashboard/claims?filter%5BfraudBand%5D=high",
  );
});

test("a pipeline stage carries both facets into the URL", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL });
  await waitFor(() =>
    expect(screen.getByTestId("siu-pipeline-stage-values")).toBeInTheDocument(),
  );

  const treatment = screen
    .getAllByTestId("siu-pipeline-stage-value-link")
    .find((node) => node.getAttribute("data-key") === "treatment");
  await userEvent.click(treatment!);

  // **Both**, and the order is `FILTER_KEYS`'. A segment of this chart is "the
  // referred claims in that stage"; dropping the first facet would open a list
  // several times longer than the bar that was clicked.
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/dashboard/claims?filter%5Bstage%5D=treatment&filter%5BsiuReview%5D=true",
  );
});

test("the two population cards open two different lists", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL });
  await waitFor(() =>
    expect(screen.getByTestId("fraud-kpi-flagged-value")).toBeInTheDocument(),
  );

  expect(screen.getByTestId("fraud-kpi-flagged-link")).toHaveAttribute(
    "href",
    "/dashboard/claims?filter%5BfraudFlagged%5D=true",
  );
  // The narrower referral rule, and deliberately not the card beside it — 9
  // claims against 13 on the seeded book.
  expect(screen.getByTestId("fraud-kpi-siu-link")).toHaveAttribute(
    "href",
    "/dashboard/claims?filter%5BsiuReview%5D=true",
  );
});

// --- the rate tables (AC 3) ---------------------------------------------

test("a rate row shows the rate and both counts it was computed from", async () => {
  renderPage({ fraudRates: FRAUD_RATES });

  // The section frame exists immediately as a skeleton, so the wait has to be
  // for a *row* — waiting for the section would assert against the loading state.
  await waitFor(() =>
    expect(screen.getAllByTestId("fraud-rate-injury-row").length).toBeGreaterThan(0),
  );

  const rows = within(screen.getByTestId("fraud-rate-injury")).getAllByTestId(
    "fraud-rate-injury-row",
  );
  // The thin bucket, first: one flagged claim of one is 100%, and the
  // denominator beside it is the whole reason the table is readable.
  expect(rows[0]).toHaveTextContent("Vibration White Finger (HAVS)");
  expect(rows[0]).toHaveTextContent("100.00%");
  expect(rows[0]).toHaveTextContent("1");
  // …and a real rate, formatted from basis points by `lib/rate.ts` and never by
  // a division in the component.
  expect(rows[1]).toHaveTextContent("Amputation");
  expect(rows[1]).toHaveTextContent("30.77%");
});

test("the truncation caption is the server's two numbers, and only when it cut", async () => {
  renderPage({ fraudRates: FRAUD_RATES });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-rate-injury-truncation")).toHaveTextContent(
      "Showing 8 of 20 injury types.",
    ),
  );
  // The uncapped tables carry no apology for a cut that did not happen.
  expect(screen.queryByTestId("fraud-rate-employer-truncation")).not.toBeInTheDocument();
  expect(screen.queryByTestId("fraud-rate-handler-truncation")).not.toBeInTheDocument();
});

test("changing a sort control changes the request and the rendered order is the server's", async () => {
  // The whole of "the browser sorts nothing", as an observable property: the
  // control sets a parameter, a *different request* goes out, and what renders
  // is what came back. A client-side sort would show the same rows re-ordered
  // and would never touch the network.
  const requested: string[] = [];
  renderPage({
    fraudRates: (url) => {
      requested.push(url);
      return url.includes("sort%5BinjuryType%5D=label_asc") ||
        url.includes("sort[injuryType]=label_asc")
        ? FRAUD_RATES_SORTED
        : FRAUD_RATES;
    },
  });

  await waitFor(() =>
    expect(screen.getAllByTestId("fraud-rate-injury-row").length).toBeGreaterThan(0),
  );
  const before = within(screen.getByTestId("fraud-rate-injury"))
    .getAllByTestId("fraud-rate-injury-row")
    .map((row) => row.getAttribute("data-row-key"));
  expect(before[0]).toBe("Vibration White Finger (HAVS)");

  await userEvent.selectOptions(
    screen.getByTestId("fraud-rate-injury-sort"),
    "label_asc",
  );

  await waitFor(() => {
    const after = within(screen.getByTestId("fraud-rate-injury"))
      .getAllByTestId("fraud-rate-injury-row")
      .map((row) => row.getAttribute("data-row-key"));
    expect(after[0]).toBe("Amputation");
  });
  // A second request went out carrying the new order.
  expect(requested.some((url) => url.includes("label_asc"))).toBe(true);
  // …and the other two tables did not move: three independent controls.
  const employers = within(screen.getByTestId("fraud-rate-employer"))
    .getAllByTestId("fraud-rate-employer-row")
    .map((row) => row.getAttribute("data-row-key"));
  expect(employers[0]).toBe("6");
});

test("sorting one table does not blank the other two", async () => {
  // All three tables ride one query keyed on the whole sort set, so re-keying it
  // used to drop `data` to `undefined` and put every table back into skeletons
  // with `aria-busy` flipped — announcing a load for two tables nobody touched,
  // to the reader least able to see that their rows had not moved.
  renderPage({
    fraudRates: (url) =>
      url.includes("sort%5BinjuryType%5D=label_asc") || url.includes("sort[injuryType]=label_asc")
        ? "pending"
        : FRAUD_RATES,
  });

  await waitFor(() =>
    expect(screen.getAllByTestId("fraud-rate-employer-row").length).toBeGreaterThan(0),
  );

  await userEvent.selectOptions(screen.getByTestId("fraud-rate-injury-sort"), "label_asc");

  await waitFor(() =>
    expect(screen.getByTestId("fraud-rate-injury")).toHaveAttribute("aria-busy", "true"),
  );
  // The untouched tables keep their rows and say nothing about being busy.
  expect(screen.getByTestId("fraud-rate-employer")).toHaveAttribute("aria-busy", "false");
  expect(screen.getByTestId("fraud-rate-handler")).toHaveAttribute("aria-busy", "false");
  expect(screen.getAllByTestId("fraud-rate-employer-row").length).toBeGreaterThan(0);
  expect(screen.queryAllByTestId("fraud-rate-row-skeleton")).toHaveLength(0);
});

test("the control renders the order the server says it applied, not the last click", async () => {
  // `RateBreakdownResponse.sort` exists so a control can render the server's
  // answer rather than its own last click, and it was structurally unreachable:
  // the prop type did not carry the field and nothing read it. This is the
  // property, made observable — the stub answers `sort: "rate_desc"` whatever is
  // asked for, so a `<select>` echoing the response reads `rate_desc` and one
  // rendering its own state reads `label_asc`. Which is the point: the order of
  // these tables is the server's answer, including the part of it the reader
  // sees.
  renderPage({ fraudRates: FRAUD_RATES });

  await waitFor(() =>
    expect(screen.getAllByTestId("fraud-rate-injury-row").length).toBeGreaterThan(0),
  );
  await userEvent.selectOptions(screen.getByTestId("fraud-rate-injury-sort"), "label_asc");

  await waitFor(() =>
    expect(screen.getByTestId("fraud-rate-injury-sort")).toHaveValue("rate_desc"),
  );
  expect(screen.getByTestId("fraud-rate-injury")).toHaveAttribute("aria-busy", "false");
});

test("one rates outage names each table it took down", async () => {
  // One failed request, three sections — so a screen reader reads three alerts
  // back to back. Each has to say which table it is about; three copies of one
  // sentence is a reader unable to tell an echo from a second failure.
  // `PortfolioCharts` sets the precedent: six charts to one request, six alerts,
  // six subjects.
  renderPage({ fraudRates: NOT_FOUND });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-rate-injury-error")).toBeInTheDocument(),
  );
  const alerts = screen.getAllByRole("alert").map((node) => node.textContent?.trim());
  expect(new Set(alerts).size).toBe(alerts.length);
  expect(screen.getByTestId("fraud-rate-employer-error")).toHaveTextContent(
    "flagged-claim rates by employer",
  );
});

test("a panel outage names each chart it took down", async () => {
  // The same rule one query over: the two SIU bar charts were byte-identical.
  renderPage({ fraudPanel: NOT_FOUND });

  await waitFor(() =>
    expect(screen.getByTestId("siu-pipeline-stage-error")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("siu-pipeline-stage-error")).toHaveTextContent("by stage");
  expect(screen.getByTestId("siu-pipeline-handler-error")).toHaveTextContent("by handler");
});

test("a rate row opens the claims behind it, on the identity and not the label", async () => {
  renderPage({ fraudRates: FRAUD_RATES });
  await waitFor(() =>
    expect(screen.getAllByTestId("fraud-rate-handler-row").length).toBeGreaterThan(0),
  );

  const handlerRow = within(screen.getByTestId("fraud-rate-handler")).getAllByTestId(
    "fraud-rate-link",
  )[0];
  expect(handlerRow).toHaveAttribute(
    "href",
    "/dashboard/claims?filter%5BhandlerId%5D=6",
  );

  const employerRow = within(screen.getByTestId("fraud-rate-employer")).getAllByTestId(
    "fraud-rate-link",
  )[0];
  expect(employerRow).toHaveAttribute(
    "href",
    "/dashboard/claims?filter%5BemployerId%5D=6",
  );
});

test("the tables quote the review threshold every flagged column was counted at", async () => {
  renderPage({ fraudRates: FRAUD_RATES });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-rate-threshold")).toHaveTextContent(
      /fraud-flagged claim scoring 55 or above/,
    ),
  );
});

// --- the flagged list is the drill list (AC 1) ---------------------------

test("the flagged panel embeds the existing drill list and links to all of it", async () => {
  renderPage({ fraudPanel: FRAUD_PANEL, drillClaims: DRILL_CLAIMS });

  await waitFor(() =>
    expect(screen.getAllByTestId("queue-card").length).toBeGreaterThan(0),
  );
  // The same rows the handler's queue and the drill-through list draw — one
  // component, never a second flagged-claims list.
  expect(screen.getAllByTestId("queue-card")[0]).toHaveAttribute(
    "data-claim-id",
    "WC-21139",
  );
  expect(screen.getByTestId("fraud-flagged-view-all")).toHaveAttribute(
    "href",
    "/dashboard/claims?filter%5BfraudFlagged%5D=true",
  );
});

// --- the AI card (AC 2, AD-10) ------------------------------------------

test("the red-flag card renders the ranking with its provenance", async () => {
  renderPage({ fraudRedFlags: FRAUD_RED_FLAGS });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-red-flag-card-body")).toBeInTheDocument(),
  );

  const body = screen.getByTestId("fraud-red-flag-card-body");
  // The card's identity in the DOM is the server's own `InsightKind` token, and
  // its status is what decides whether a provenance line exists at all.
  expect(body).toHaveAttribute("data-kind", "fraud_risk_indicators");
  expect(body).toHaveAttribute("data-status", "ready");

  // The clauses, in the server's order, with the distinct-claim counts.
  expect(
    screen.getAllByTestId("fraud-red-flag-clause").map((node) => node.textContent?.trim()),
  ).toEqual([
    "·Injury reported more than a week after the incident date — 3 claims",
    "·Treatment sought from a provider outside the employer network — 2 claims",
    // Singular, and it is the row that matters: exact-text grouping over
    // model-authored prose makes a count of one the *most common* case, which
    // the caption two lines down says outright — so "1 claims" was the first
    // line an analyst read on the card arguing that one is ordinary.
    "·No witness named on the incident report — 1 claim",
  ]);

  // The chip labels the content, not the portfolio: this payload carries no
  // `outcome`, so a verdict here would be the browser deciding one (AD-2).
  expect(screen.getByText("Red-flag clauses")).toBeInTheDocument();

  // AD-10's whole rule: a narrative is rendered with the time it was generated
  // and the model that wrote it, never as claim data.
  expect(screen.getByTestId("fraud-red-flag-card-generated")).toHaveTextContent(/Generated/);
  expect(screen.getByTestId("fraud-red-flag-card-generated")).toHaveTextContent(
    /qwen3:14b, qwen3:8b/,
  );

  // The coverage, the range and the degraded count — the three figures that say
  // what this card is about.
  const coverage = screen.getByTestId("fraud-red-flag-coverage");
  expect(coverage).toHaveTextContent("4 of 100 claims");
  expect(coverage).toHaveTextContent(/generated .* to /);
  expect(coverage).toHaveTextContent("1 cached narratives could not be read");
});

test("a cold cache renders a first-class empty card, not a spinner and not a gap", async () => {
  renderPage({ fraudRedFlags: FRAUD_RED_FLAGS_EMPTY });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-red-flag-card-body")).toHaveAttribute(
      "data-status",
      "not_generated",
    ),
  );
  // **One** paragraph, and it is about this portfolio. The shell's own default
  // sentence — "Use Refresh above to generate this claim's insights" — names a
  // control this page does not have, about a claim this card is not about; it
  // used to render anyway, with a second paragraph underneath correcting it.
  const empty = screen.getByTestId("fraud-red-flag-card-empty");
  expect(empty).toHaveTextContent("No fraud narratives are cached for this portfolio yet");
  expect(empty).not.toHaveTextContent(/Refresh/);
  expect(empty).not.toHaveTextContent(/this claim/);
  expect(screen.queryByTestId("fraud-red-flag-empty-note")).not.toBeInTheDocument();
  // No chip either: the payload carries no `outcome`, and an error-toned
  // "Review indicated" over a cold cache is the browser announcing a verdict it
  // was never told (AD-2).
  expect(screen.queryByText("Red-flag clauses")).not.toBeInTheDocument();
  expect(screen.queryByText("Review indicated")).not.toBeInTheDocument();
  // No provenance line, because there is no provenance: a "Generated —" row
  // would read as a failed load rather than as an empty cache.
  expect(screen.queryByTestId("fraud-red-flag-card-generated")).not.toBeInTheDocument();
  expect(screen.queryByTestId("fraud-red-flag-generated-empty")).not.toBeInTheDocument();
  expect(screen.queryByTestId("fraud-red-flags-skeleton")).not.toBeInTheDocument();
});

test("an analysed book with no red flags says so rather than 'not generated'", async () => {
  // The second empty, and it is a *result*: every claim in the book has a cached
  // narrative and every one of them came back low risk. `claimsWithInsight > 0`
  // with an empty ranking used to read "Not generated yet", which is false — and
  // on a fraud surface it is the more reassuring sentence being replaced by a
  // wrong one.
  renderPage({ fraudRedFlags: FRAUD_RED_FLAGS_ALL_CLEAR });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-red-flag-card-body")).toHaveAttribute(
      "data-status",
      "not_generated",
    ),
  );
  const empty = screen.getByTestId("fraud-red-flag-card-empty");
  expect(empty).toHaveTextContent(
    "100 of 100 claims in this portfolio have a cached fraud narrative",
  );
  expect(empty).toHaveTextContent("none of them raised a red flag");
  expect(empty).not.toHaveTextContent(/Not generated yet/);
});

test("a clause is not a click target", async () => {
  // A clause is not a claim population, and inventing one would be the browser
  // deciding which claims a phrase names.
  renderPage({ fraudRedFlags: FRAUD_RED_FLAGS });

  await waitFor(() =>
    expect(screen.getAllByTestId("fraud-red-flag-clause").length).toBeGreaterThan(0),
  );
  for (const clause of screen.getAllByTestId("fraud-red-flag-clause")) {
    expect(within(clause).queryByRole("link")).toBeNull();
    expect(within(clause).queryByRole("button")).toBeNull();
  }
});

// --- loading, error and the independence of the four queries -------------

test("a pending panel is busy and draws skeletons rather than zeros", async () => {
  renderPage({
    fraudPanel: "pending",
    fraudRates: "pending",
    fraudRedFlags: "pending",
    drillClaims: "pending",
  });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-analytics")).toHaveAttribute("aria-busy", "true"),
  );
  expect(screen.getByTestId("fraud-band-distribution-skeleton")).toBeInTheDocument();
  expect(screen.getAllByTestId("fraud-rate-row-skeleton").length).toBeGreaterThan(0);
  expect(screen.getByTestId("fraud-red-flags-skeleton")).toBeInTheDocument();
  expect(screen.getByTestId("fraud-flagged-skeleton")).toBeInTheDocument();
  // No zeros standing in for unknown figures: the population cards are absent
  // while the panel is in flight rather than reading "0".
  expect(screen.queryByTestId("fraud-kpi-flagged-value")).not.toBeInTheDocument();
});

test("each section fails on its own and leaves the others standing", async () => {
  renderPage({
    fraudPanel: FRAUD_PANEL,
    fraudRates: NOT_FOUND,
    fraudRedFlags: NOT_FOUND,
    drillClaims: DRILL_CLAIMS,
  });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-red-flags-error")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("fraud-rate-injury-error")).toBeInTheDocument();

  // …and the panel is untouched: four server answers, four failure modes.
  expect(rowsOf("fraud-band-distribution-legend-row")).toContain("High (≥ 55):13");
  expect(screen.getByTestId("fraud-kpi-flagged-value")).toHaveTextContent(/^13$/);
  expect(screen.getAllByTestId("queue-card").length).toBeGreaterThan(0);
  // The page's own busy flag tracks the panel, which has landed.
  expect(screen.getByTestId("fraud-analytics")).toHaveAttribute("aria-busy", "false");
});

test("a failed panel shows an alert per surface and no figures at all", async () => {
  renderPage({
    fraudPanel: NOT_FOUND,
  });

  await waitFor(() =>
    expect(screen.getByTestId("fraud-band-distribution-error")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("siu-pipeline-stage-error")).toBeInTheDocument();
  // No zeroed cards in place of the figures: two zeroed populations are a
  // portfolio with no suspected fraud in it, which is a quieter lie than an
  // error message.
  expect(screen.queryByTestId("fraud-kpi-flagged-value")).not.toBeInTheDocument();
  expect(screen.queryByTestId("fraud-band-distribution-skeleton")).not.toBeInTheDocument();
  // …and no skeleton in the population row either. A pulsing placeholder under
  // an alert is two states on one screen saying opposite things, and it would
  // keep saying "loading" for as long as the reader stayed.
  expect(screen.queryByTestId("kpi-skeleton")).not.toBeInTheDocument();
});
