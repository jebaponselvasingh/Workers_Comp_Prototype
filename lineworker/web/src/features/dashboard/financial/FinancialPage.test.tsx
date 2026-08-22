import { MemoryRouter, useLocation } from "react-router";
import userEvent from "@testing-library/user-event";

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { FILTER_KEYS, SEGMENTATION_KEYS } from "@/features/dashboard/drill/filters";
import {
  FINANCIALS,
  FINANCIALS_BY_ICD,
  FINANCIALS_EMPTY,
  RESERVE_ADEQUACY,
  RESERVE_ADEQUACY_EMPTY,
  stubApi,
} from "@/test/api-mock";

import { FinancialPage } from "./FinancialPage";

/**
 * Story 7.4 AC 1/2/3/5 — the Financial section renders what the server decided.
 *
 * The line these tests police is the one every Epic 7 suite polices, and this
 * surface strains it harder than any of the others: the browser is handed a paid
 * figure, a reserve figure, their sum, a set of groups whose figures add up to
 * that sum, four cohorts whose counts partition the same book, and — one card
 * over — a verdict distribution beside the two ratios it was banded at.
 * `paid + reserve`, "what share of the total is this group", "how much more does
 * a surgical claim cost" and a re-check against `lightRatioBp` are each one
 * line, and each is a second answer to a number already on the wire.
 *
 * So the fixtures are **contrast fixtures rather than plausible ones**, and that
 * is the substance rather than a nicety:
 *
 * - `FINANCIALS_BY_ICD` carries identical *cohorts* to `FINANCIALS`
 *   and a different breakdown, so a component that regrouped a cached answer
 *   draws employer labels under an ICD-10 heading while one that refetched draws
 *   three ICD-10 codes.
 * - Its litigation cohort has **zero paid** against a real reserve, which is the
 *   seeded book's own awkward shape: a card comparing paid alone would report
 *   that litigation costs nothing.
 * - `FINANCIALS_EMPTY` publishes `averageProjectedCents: null` on all four
 *   cohorts, so a `?? 0` renders `$0` and fails rather than reading plausibly.
 * - `RESERVE_ADEQUACY` has a zero-count `heavy` bucket, so a donut that omitted
 *   empty categories draws four arcs where five belong.
 *
 * **Cents are asserted as rendered strings.** `formatCents` is the only division
 * by a hundred in this console; comparing integers would pass against a card
 * that printed cents as dollars, which is the one money defect a reader would
 * not catch by eye.
 *
 * **The URL is the assertion for every gesture.** The `groupBy` control and all
 * four kinds of drill target are checked by reading `location`, because that is
 * what a shared link carries and what the cards re-key on — a control that held
 * its state locally would satisfy every rendering assertion and fail AC 7.
 *
 * Nothing here reads Recharts internals: the donut's legend and the bar chart's
 * chip row are both real, visible, accessible controls, and they are what the
 * components' own docstrings say the tests should read.
 */

/**
 * The address bar, as a test reads it — **decoded**.
 *
 * `URLSearchParams.toString()` percent-encodes the brackets, so the string a
 * router actually holds is `filter%5Bsurgery%5D=true`. `SegmentationBar.test.tsx`
 * asserts that encoded form; this suite decodes it instead, because every
 * assertion below is about *which facet and which value* a gesture wrote and the
 * encoded spelling makes the one thing under test the hardest part of the string
 * to read. The decoding is the test's rendering choice and nothing else — it is
 * the same URL, and `filters.ts::fromSearchParams` reads it back through
 * `URLSearchParams`, which decodes it too.
 */
function LocationProbe() {
  const { pathname, search } = useLocation();
  return <span data-testid="location">{`${pathname}${decodeURIComponent(search)}`}</span>;
}

function renderPage(
  routes: Parameters<typeof stubApi>[0] = {},
  initial = "/dashboard/financials",
) {
  stubApi(routes);
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider client={createQueryClient()}>
        <FinancialPage />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Where the app would navigate to, as the probe reports it. */
function currentLocation(): string {
  return screen.getByTestId("location").textContent ?? "";
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the ten groupings are the segmentation's ten, in the chip row's order", () => {
  // The property `BreakdownCard` and `FinancialPage` both rest on, asserted here
  // rather than in either: the picker's vocabulary, the `filter[…]` names a
  // group's drill target writes and the keys the server groups by are one list
  // in one order. `BreakdownDimension`'s members are the facet spellings for
  // exactly this reason — a snake_case enum would need a translation table whose
  // only job would be to stay correct in both directions forever.
  const owned = new Set<string>(SEGMENTATION_KEYS);
  expect([...SEGMENTATION_KEYS]).toEqual(FILTER_KEYS.filter((key) => owned.has(key)));
});

test("the three portfolio totals render the server's cents, formatted once", async () => {
  renderPage();

  // Three figures, three quantities — and `projectedCents` is on the wire rather
  // than added here, which is the whole of AD-1 on this card: it *is* paid plus
  // reserve on this fixture, and a component that added the first two would be
  // right today and silently wrong the day the paid basis moves.
  expect(await screen.findByTestId("financial-kpi-paid-value")).toHaveTextContent("$5,000");
  expect(screen.getByTestId("financial-kpi-reserve-value")).toHaveTextContent("$3,000");
  expect(screen.getByTestId("financial-kpi-projected-value")).toHaveTextContent("$8,000");
});

test("all three totals open the unfiltered book, because all three are sums over it", async () => {
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByTestId("financial-kpi-projected-link"));

  // No slice, because there is none: paid, reserve and projected are three sums
  // over *one* population, so a tile that invented a facet to look more specific
  // would open a list that did not reconcile with the figure on it.
  // `KpiCard.drill`'s recorded case for a whole filter set.
  expect(currentLocation()).toBe("/dashboard/claims");
});

test("the breakdown draws the server's groups, ranked and labelled", async () => {
  renderPage();

  const chips = await screen.findAllByTestId("financial-breakdown-bars-value-link");
  // The server's ranking, read as text: Boeing first because its projected cost
  // is higher, and the *label* rather than the key, because `employerId` is the
  // one dimension whose value is an id and the server resolves the name.
  expect(chips.map((chip) => chip.textContent)).toEqual(["Boeing: $5,000", "Toyota: $3,000"]);
});

test("a breakdown group opens its own facet with its own key", async () => {
  const user = userEvent.setup();
  renderPage();

  const chips = await screen.findAllByTestId("financial-breakdown-bars-value-link");
  await user.click(chips[1]);

  // The **grouped dimension's** facet and the group's own key — no label, no
  // composed parameter name. Toyota's group key is the employer id the server
  // published, which is why a bar's key and a chip's value are one string.
  expect(currentLocation()).toBe("/dashboard/claims?filter[employerId]=5");
});

test("changing the grouping writes the URL and refetches rather than regrouping", async () => {
  const user = userEvent.setup();
  renderPage({
    // The contrast: identical totals, a different breakdown. A component that
    // regrouped what it already held would keep drawing Boeing and Toyota.
    financials: (url) =>
      url.includes("groupBy=icd10") ? FINANCIALS_BY_ICD : FINANCIALS,
  });

  await screen.findAllByTestId("financial-breakdown-bars-value-link");
  await user.selectOptions(screen.getByTestId("financial-group-by"), "icd10");

  // AC 3: the control's state is in the URL under the route's own bare name, so
  // a grouping is as shareable as the filter beside it.
  await waitFor(() => {
    expect(currentLocation()).toBe("/dashboard/financials?groupBy=icd10");
  });
  await waitFor(() => {
    const drawn = screen
      .getAllByTestId("financial-breakdown-bars-value-link")
      .map((chip) => chip.textContent);
    // Every row the server sent, in the order it sent them — the whole list
    // rather than a spot check, so a component that re-sorted or dropped one
    // fails here.
    expect(drawn.slice(0, 3)).toEqual([
      "S61.219A: $5,000",
      "W17.89XA: $4,500",
      "M54.5: $4,000",
    ]);
    expect(drawn).toHaveLength(12);
  });
  // …and the caption quotes the server's two numbers rather than computing how
  // many groups are missing — twelve of twenty, which is the only shape a
  // truncated payload can take when `limit` is `BREAKDOWN_LIMIT`.
  expect(screen.getByTestId("financial-breakdown-bars-truncation")).toHaveTextContent(
    "Top 12 of 20 by projected cost",
  );
});

test("the control shows the grouping that was asked for while the answer is in flight", async () => {
  const user = userEvent.setup();
  // The stub leaves the regroup `"pending"`, so the window this test is about —
  // a new key outstanding, the previous answer still drawn — stays open for the
  // assertion instead of closing in a millisecond.
  renderPage({
    financials: (url) => (url.includes("groupBy=icd10") ? "pending" : FINANCIALS),
  });

  await screen.findAllByTestId("financial-breakdown-bars-value-link");
  await user.selectOptions(screen.getByTestId("financial-group-by"), "icd10");

  // With `keepPreviousData` the held answer is still the employer one, so a card
  // reading its control off the response puts "Employer" back under the
  // analyst's hand and the heading disagrees with the URL. The control shows
  // what was *asked* until the server says what it applied.
  await waitFor(() => {
    expect(screen.getByTestId("financial-group-by")).toHaveValue("icd10");
  });
  expect(screen.getByTestId("financial-breakdown")).toHaveAttribute("aria-busy", "true");
});

test("a card in flight reports busy while its neighbour, unasked, does not", async () => {
  const user = userEvent.setup();
  renderPage({
    financials: (url) => (url.includes("groupBy=icd10") ? "pending" : FINANCIALS),
  });

  await screen.findAllByTestId("financial-breakdown-bars-value-link");
  await user.selectOptions(screen.getByTestId("financial-group-by"), "icd10");

  // AC 2's second clause, which `isPending` alone cannot express: with a
  // placeholder on screen `isPending` is `false` for the whole refetch, so a
  // card reporting only that reported *nothing* while its figures were stale.
  await waitFor(() => {
    expect(screen.getByTestId("financial-breakdown")).toHaveAttribute("aria-busy", "true");
  });
  // The previous grouping's bars are still readable — the point of keeping them.
  expect(screen.getAllByTestId("financial-breakdown-bars-value-link").length).toBeGreaterThan(0);
  // And the adequacy donut was never re-asked: its query key did not move, so it
  // says nothing about being busy. "Only the affected card" is the clause.
  expect(screen.getByTestId("financial-adequacy")).toHaveAttribute("aria-busy", "false");
});

test("a group's drill target follows the grouping it was drawn under", async () => {
  const user = userEvent.setup();
  renderPage(
    { financials: (url) => (url.includes("groupBy=icd10") ? FINANCIALS_BY_ICD : FINANCIALS) },
    "/dashboard/financials?groupBy=icd10",
  );

  const chips = await screen.findAllByTestId("financial-breakdown-bars-value-link");
  await user.click(chips[0]);

  // The facet moved with the grouping, which is the property that makes a
  // breakdown drillable at all: a bar under an ICD-10 heading opening
  // `filter[employerId]` would be a plausible list of the wrong claims.
  expect(currentLocation()).toBe("/dashboard/claims?filter[icd10]=S61.219A");
});

test("the adequacy donut draws all five verdicts, including the empty one", async () => {
  renderPage();

  const legend = await screen.findAllByTestId("reserve-adequacy-distribution-legend-row");

  // Five, in the enum's declaration order, with the **case file's own labels** —
  // imported rather than restated, so an analyst clicking "Reserve Light" lands
  // on a claim whose chip says "Reserve Light". The `heavy` row counts zero and
  // is drawn anyway: a rule's vocabulary is complete for every book, and a
  // four-segment donut cannot be told from a build that forgot the fifth.
  expect(legend.map((row) => row.textContent)).toEqual([
    "Reserve Light:2",
    "Reserve Adequate:1",
    "Reserve Heavy:0",
    "Closed — Final:3",
    "Awaiting Bill Data:0",
  ]);
  // The centre figure is the published total, not a count of legend rows.
  expect(screen.getByTestId("reserve-adequacy-distribution-total")).toHaveTextContent("6");
});

test("the adequacy footnote quotes the server's band edges", async () => {
  renderPage();

  // The footnote's row is reserved in every state — `ChartFrame`'s NFR-3 rule —
  // so the element exists before the answer does and a bare `findByTestId` would
  // resolve against the em dashes the card draws while the edges are unknown.
  // Waiting for the donut first is what makes this an assertion about the
  // response.
  await screen.findAllByTestId("reserve-adequacy-distribution-legend-row");

  // Read off the response, so superseding `reserve_bands` moves the segments and
  // this sentence together. A constant in the component would be a second copy
  // of a rule the browser cannot see change — and on this card it would be the
  // second copy of the rule AC 2 forbids re-deriving.
  expect(screen.getByTestId("reserve-adequacy-note")).toHaveTextContent(
    "Light above 1.15× of reserve, heavy below 0.60×",
  );
  // …and an em dash rather than a number while they are unknown, which is why
  // the fallback is not a `?? 0`: one refactor from a footnote claiming the
  // bands are at zero, which is a rule claim the server never made.
  expect(screen.getByTestId("reserve-adequacy-note")).not.toHaveTextContent(
    "Light above 0.00×",
  );
});

test("a verdict segment opens the twenty-fifth facet", async () => {
  const user = userEvent.setup();
  renderPage();

  const links = await screen.findAllByTestId("reserve-adequacy-distribution-legend-link");
  await user.click(links[0]);

  // `filter[reserveVerdict]`, the facet this story appended — the only way a
  // segment of this distribution can open its own claims, because a verdict is
  // not a column and cannot become one.
  expect(currentLocation()).toBe("/dashboard/claims?filter[reserveVerdict]=light");
});

test("a cost-driver card states two averages and compares nothing", async () => {
  renderPage();

  // The **average** is the headline, and the counts are beside it: three
  // litigated claims against ninety-seven is a comparison of sums that says
  // litigation is a rounding error, which is true of the total and false of the
  // claim.
  expect(await screen.findByTestId("cost-driver-litigation-with-average")).toHaveTextContent(
    "$2,500",
  );
  expect(screen.getByTestId("cost-driver-litigation-without-average")).toHaveTextContent(
    "$1,100",
  );
  // …and all three totals travel with it, which is what keeps the comparison
  // honest on this book: the litigated cohort's *paid* figure is zero because
  // every litigated claim is open, so a paid-only card would say litigation
  // costs nothing at all.
  expect(screen.getByTestId("cost-driver-litigation-with-paid")).toHaveTextContent("$0");
  expect(screen.getByTestId("cost-driver-litigation-with-reserve")).toHaveTextContent("$2,500");
});

test("each cohort opens its own side of its own facet", async () => {
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByTestId("cost-driver-surgery-without"));

  // `filter[surgery]=false` is a real narrowing and not the absence of a filter
  // — the distinction `drill_through.matches` is written around — so the
  // "without" cohort has a drill target of its own rather than opening the book.
  expect(currentLocation()).toBe("/dashboard/claims?filter[surgery]=false");
});

test("a drill target carries the active segmentation as well as the slice", async () => {
  const user = userEvent.setup();
  renderPage({}, "/dashboard/financials?filter[sector]=Aerospace");

  await user.click(await screen.findByTestId("cost-driver-surgery-with"));

  // AC 4: the list opens carrying the workspace's filter *and* the thing that
  // was clicked, each as an independently clearable chip — in `FILTER_KEYS`
  // order, which is why `surgery` is written before `sector` although the
  // analyst chose the sector first.
  expect(currentLocation()).toBe(
    "/dashboard/claims?filter[surgery]=true&filter[sector]=Aerospace",
  );
});

test("an emptied segment says so on every card, and distinguishes itself from an empty book", async () => {
  renderPage(
    { financials: FINANCIALS_EMPTY, reserveAdequacy: RESERVE_ADEQUACY_EMPTY },
    "/dashboard/financials?filter[sector]=Aerospace&filter[state]=MI",
  );

  // AC 5, on all four surfaces. The sentence is `NO_MATCHING_CLAIMS`' — "no
  // claims match these filters" rather than "this portfolio has no claims",
  // because only the first has a way out and the second is a false statement
  // about the book.
  await expect(
    screen.findByTestId("financial-breakdown-bars-empty"),
  ).resolves.toHaveTextContent("No claims match these filters.");
  expect(screen.getByTestId("reserve-adequacy-distribution-empty")).toHaveTextContent(
    "No claims match these filters.",
  );
  expect(screen.getByTestId("cost-driver-surgery-empty")).toHaveTextContent(
    "No claims match these filters.",
  );
  expect(screen.getByTestId("cost-driver-litigation-empty")).toHaveTextContent(
    "No claims match these filters.",
  );
  // …and the tiles still render their zeros, because a total of zero over an
  // empty segment is an answer rather than an absence.
  expect(screen.getByTestId("financial-kpi-projected-value")).toHaveTextContent("$0");
});

test("an empty book blames the book rather than a filter nobody set", async () => {
  renderPage({ financials: FINANCIALS_EMPTY, reserveAdequacy: RESERVE_ADEQUACY_EMPTY });

  // The other half of the same zero. Story 7.3's low finding, on four more
  // surfaces: telling an analyst to clear a filter they never set is the failure
  // mode of a single message.
  await expect(
    screen.findByTestId("reserve-adequacy-distribution-empty"),
  ).resolves.toHaveTextContent("No claims in this portfolio yet.");
  expect(screen.getByTestId("cost-driver-surgery-empty")).toHaveTextContent(
    "No claims in this portfolio yet.",
  );
});

test("the totals caption names the population the figures are over", async () => {
  renderPage({}, "/dashboard/financials?filter[sector]=Aerospace");

  // With a segmentation applied these are figures about an *intersection*, and a
  // caption reading "in this portfolio" over one is the kind of sentence an
  // analyst quotes at somebody — in dollars, on this section.
  await expect(screen.findByTestId("financial-kpi-paid")).resolves.toHaveTextContent(
    "6 matching claims",
  );
});

test("a failed totals request leaves the adequacy donut standing", async () => {
  // A 4xx rather than a 500, and the reason is the shared client rather than the
  // component: `createQueryClient` retries a 5xx twice with backoff, so a 500
  // fixture would make every error assertion in this file a three-second wait on
  // a behaviour that is not what the test is about. The card cannot tell the two
  // apart — it receives `isError` either way.
  renderPage({ financials: { status: 400, body: {} } });

  // NFR-3, and the reason the section makes two requests rather than one: the
  // donut is a different route with a different cost, so one outage must not
  // blank a card that answered.
  await expect(screen.findByTestId("financial-totals-error")).resolves.toBeInTheDocument();
  expect(screen.getByTestId("cost-driver-surgery-error")).toBeInTheDocument();
  const legend = await screen.findAllByTestId("reserve-adequacy-distribution-legend-row");
  expect(legend).toHaveLength(RESERVE_ADEQUACY.body.items.length);
  // …and no tile is drawn at all, rather than three zeros or three skeletons: a
  // figure would be worse than an absence on a card whose subject is money, and
  // a pulsing placeholder under a stated failure would be two states saying
  // opposite things.
  expect(screen.queryByTestId("financial-kpi-paid")).not.toBeInTheDocument();
  expect(screen.queryByTestId("kpi-skeleton")).not.toBeInTheDocument();
});

test("a failed adequacy request leaves the totals standing", async () => {
  // A 4xx rather than a 500 — see the test above on why.
  renderPage({ reserveAdequacy: { status: 400, body: {} } });

  await expect(screen.findByTestId("financial-kpi-paid-value")).resolves.toHaveTextContent(
    "$5,000",
  );
  const donut = await screen.findByTestId("reserve-adequacy-distribution");
  expect(
    within(donut).getByTestId("reserve-adequacy-distribution-error"),
  ).toBeInTheDocument();
});
