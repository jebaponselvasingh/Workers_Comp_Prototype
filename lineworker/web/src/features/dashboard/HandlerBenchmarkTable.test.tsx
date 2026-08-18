import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  HANDLER_BENCHMARKS,
  HANDLER_BENCHMARKS_EMPTY,
  HANDLER_BENCHMARKS_RERANKED,
  HANDLER_BENCHMARKS_SINGLE,
  HANDLER_BENCHMARKS_UNRANKED,
  stubApi,
} from "@/test/api-mock";

import { DashboardPage } from "./DashboardPage";

/**
 * Story 5.2 AC 1/2/4 — the table renders the server's rows and nothing of its
 * own.
 *
 * The line these tests police is the one `noDerivation.test.ts` polices
 * structurally: the component receives nine columns' worth of decided figures
 * per row and may format and colour them; it may not produce, re-order or
 * re-band one. So the fixture carries figures no cell could have derived from
 * anything else on screen, the **order is asserted as a list** rather than as
 * six independent lookups, and a re-ranked contrast fixture renders through the
 * same component — a table that sorted on a figure of its own would read the
 * same under both, and one that renders what it was sent cannot.
 *
 * Rendered through `DashboardPage` rather than through the table alone: the
 * table takes its query state as props, so mounting it directly would test a
 * wiring nobody ships. This is also what makes "a benchmark failure does not
 * blank the KPI cards" assertable at all.
 */

function renderPage(routes: Parameters<typeof stubApi>[0]) {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DashboardPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The nine headers, in the prototype's order — the design contract. */
const HEADERS = [
  "#",
  "Handler",
  "Cases",
  "Cycle Speed",
  "Avg Days",
  "RTW %",
  "Complexity",
  "Pending Approvals",
  "Status",
];

function handlerOrder(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll("[data-testid='handler-row']")].map((row) =>
    row.getAttribute("data-handler"),
  );
}

test("the table renders all nine columns in the prototype's order", async () => {
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  // Scoped to this section rather than to the whole page: Story 5.4 put a
  // second `<table>` on the dashboard, so a query over `container` now reads
  // nineteen headers from two tables and the ordering assertion means nothing.
  const section = screen.getByTestId("handler-benchmarks");
  const headers = [...section.querySelectorAll("th")].map((th) => th.textContent);
  expect(headers).toEqual(HEADERS);
  // Every header is a real column header, not a styled cell — the whole reason
  // this is a `<table>` rather than a grid of divs.
  for (const th of section.querySelectorAll("th")) {
    expect(th).toHaveAttribute("scope", "col");
  }
  expect(section.querySelector("caption")).toHaveClass("sr-only");
});

test("the rows arrive in the server's rank order and are not re-sorted", async () => {
  const { container } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  // As a list, because the order *is* the contract: a table that rendered every
  // row in the wrong position would satisfy six independent lookups.
  //
  // This is also the seeded portfolio's real order, and the pair in the middle
  // is the one the first implementation got wrong: Marcus Chen's composite is
  // 72.036 days and Fatima Al-Mansoori's is 72.667, so Marcus is faster —
  // summing the SLA strip's whole-day settle tiles made 72.4 look slower than
  // 72.3 and swapped them.
  expect(handlerOrder(container)).toEqual([
    "Liam O'Sullivan",
    "Kaya Johnson",
    "Marcus Chen",
    "Fatima Al-Mansoori",
    "Sarah Williams",
    "Dante Reyes",
  ]);
});

test("the # column shows the server's rank, not the row's position", async () => {
  const { container } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_RERANKED });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  // Read off `data-testid="handler-rank"` rather than off the row index, which
  // pins the *cell* to the rank field: a `#` column wired to `caseCount`, or to
  // the wrong row, fails here.
  //
  // What it does **not** catch is `{index + 1}`. Every healthy response ranks
  // every row, so `items[i].rank === i + 1` holds in this fixture and in every
  // other one on a scope the server could rank — the two implementations are
  // indistinguishable on all of them. The test that tells them apart is the
  // unrankable-scope one below, where the server publishes `rank: null` and an
  // index would invent the numbers 1, 2, 3.
  const pairs = [...container.querySelectorAll("[data-testid='handler-row']")].map(
    (row) => [
      row.getAttribute("data-handler"),
      row.querySelector("[data-testid='handler-rank']")?.textContent,
    ],
  );
  expect(pairs).toEqual([
    ["Dante Reyes", "1"],
    ["Sarah Williams", "2"],
    ["Fatima Al-Mansoori", "3"],
    ["Marcus Chen", "4"],
    ["Kaya Johnson", "5"],
    ["Liam O'Sullivan", "6"],
  ]);
});

test("a scope the server could not rank numbers no row at all", async () => {
  const { container } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_UNRANKED });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(3));

  // **This is the test that kills `{index + 1}`.** The server publishes
  // `rank: null` alongside `compositeDays: null` when the scope's own portfolio
  // is missing a cycle-time segment — the rows are then in *name* order and
  // there is no ordinal to show — so a `#` column reading the row's position
  // would put a confident 1, 2, 3 beside three rows of em dashes and turn a
  // name ordering into a ranking claim. Every other fixture in this file ranks
  // every row, which is why none of them can see the difference.
  const ranks = [...container.querySelectorAll("[data-testid='handler-rank']")].map(
    (cell) => cell.textContent,
  );
  expect(ranks).toEqual(["—", "—", "—"]);

  // The four figures that are null with it, on the first row — one condition on
  // the wire, so a component that handled the composite and not the bar would
  // show a full-width fill for an unknown quantity.
  const cells = [...container.querySelectorAll("td")].map((td) => td.textContent);
  expect(cells.slice(0, 9)).toEqual([
    "—",
    "Dante Reyes",
    "24",
    "—",
    "—",
    "69%",
    "Medium (57)",
    "11",
    "—",
  ]);
  // No composite anywhere in scope, so no callout and no portfolio clause in
  // the footnote — but the bands are still stated.
  expect(screen.queryByTestId("benchmark-callout")).not.toBeInTheDocument();
  expect(screen.getByTestId("benchmark-bands")).toHaveTextContent(
    "On Track at or below -8%",
  );
});

test("each cell shows the figure the server put in its column", async () => {
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  const rows = await screen.findAllByTestId("handler-row");
  const cells = (index: number) =>
    [...rows[index].querySelectorAll("td")].map((td) => td.textContent);

  // The fixture's figures are pairwise distinct within a row, so a cell wired
  // to the wrong column fails here rather than coincidentally matching.
  //
  // The Cycle Speed cell is **not** empty: a `<td>` whose only content is a
  // decorative span reads as an unnamed cell under a header promising a figure,
  // so the composite and what the fill is a percentage of are in it as
  // screen-reader text. The Status chip likewise carries the deviation it was
  // banded on — the footnote quotes both thresholds as percentages, and a chip
  // banded by a number the page never shows leaves that sentence measuring
  // against nothing.
  expect(cells(0)).toEqual([
    "1",
    "Liam O'Sullivan",
    "7",
    "64.1 days, 82% of the slowest cycle time in this portfolio",
    "64.1d",
    "75%",
    "Medium (64)",
    "5",
    "On Track -11%",
  ]);
  // Dante's composite is a whole number of days on the wire (78.0) and still
  // shows its tenth. `String(78.0)` is `"78"`, which read as a whole-day figure
  // beside Fatima's "72.7d" and hid the precision the rank was decided at.
  expect(cells(5)).toEqual([
    "6",
    "Dante Reyes",
    "24",
    "78.0 days, 100% of the slowest cycle time in this portfolio",
    "78.0d",
    "69%",
    "Medium (57)",
    "11",
    "Attention +8%",
  ]);
  // A handler exactly level with the desk still shows the number the band was
  // decided on rather than a bare chip — "Watch" at 0% and "Watch" at +7% are
  // very different facts about a desk.
  expect(cells(2)[8]).toBe("Watch 0%");
});

test("the cycle-speed cell has an accessible name carrying the composite", async () => {
  const { container } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  const cell = container.querySelector("[data-testid='handler-cycle-speed']");
  // Not `""`. The bar is `aria-hidden` and the cell's name is the sentence
  // beside it.
  expect(cell?.textContent).toBe(
    "64.1 days, 82% of the slowest cycle time in this portfolio",
  );
  // **One mechanism, not two.** The same sentence used to be a `title` as well.
  // A `title` may be announced *in addition to* an element's contents, so the
  // name could be read twice, and the tooltip it promises never appears on
  // keyboard focus or on touch — an affordance advertised to exactly the
  // readers who cannot reach it. The `sr-only` text is the accessible one and
  // is the one that stayed.
  expect(cell?.querySelector("[title]")).toBeNull();
});

test("the cycle-speed bar is filled to the server's percentage", async () => {
  const { container } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  const fills = [...container.querySelectorAll("[data-testid='cycle-speed-fill']")].map(
    (fill) => (fill as HTMLElement).style.width,
  );
  expect(fills).toEqual(["82%", "88%", "92%", "93%", "95%", "100%"]);
});

test("the leader and laggard callout names the two handlers the server chose", async () => {
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  const callout = await screen.findByTestId("benchmark-callout");
  expect(callout).toHaveTextContent("Liam O'Sullivan");
  expect(callout).toHaveTextContent("leads the desk");
  expect(callout).toHaveTextContent("Dante Reyes");
  expect(callout).toHaveTextContent("workload check-in");
});

test("the band footnote quotes the response, not a constant", async () => {
  const { unmount } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  await waitFor(() =>
    expect(screen.getByTestId("benchmark-bands")).toHaveTextContent(
      "On Track at or below -8%",
    ),
  );
  expect(screen.getByTestId("benchmark-bands")).toHaveTextContent(
    "Attention at or above 8%",
  );
  expect(screen.getByTestId("benchmark-bands")).toHaveTextContent("71.9d");
  expect(screen.getByTestId("benchmark-bands")).toHaveTextContent(
    "Medium from 40 and High from 65",
  );

  unmount();
  vi.unstubAllGlobals();

  // The same page under a superseded rule document. A footnote holding its own
  // constants would read "-8%" here too — and would go on doing so on the day
  // somebody retuned the bands, with the chips beside it already moved.
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_RERANKED });

  await waitFor(() =>
    expect(screen.getByTestId("benchmark-bands")).toHaveTextContent(
      "On Track at or below -3%",
    ),
  );
  expect(screen.getByTestId("benchmark-bands")).toHaveTextContent(
    "Medium from 45 and High from 60",
  );
  // …and the portfolio composite is this scope's, not the other fixture's.
  expect(screen.getByTestId("benchmark-bands")).toHaveTextContent("55.0d");
});

test("the contrast fixture re-ranks the same component", async () => {
  const { container } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_RERANKED });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  // A different handler↔rank pairing, from the same component. Nothing in this
  // file sorts, so the only way both assertions can hold is if the rows are
  // rendered in the order they arrived.
  expect(handlerOrder(container)).toEqual([
    "Dante Reyes",
    "Sarah Williams",
    "Fatima Al-Mansoori",
    "Marcus Chen",
    "Kaya Johnson",
    "Liam O'Sullivan",
  ]);
  expect(await screen.findByTestId("benchmark-callout")).toHaveTextContent(
    "Dante Reyes",
  );
});

test("the retuned cut-points move complexity chips as well as status chips", async () => {
  const { container, unmount } = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS });

  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));
  const chipsFor = (root: HTMLElement, testid: string) =>
    [...root.querySelectorAll(`[data-testid='${testid}']`)].map((cell) => [
      cell.closest("[data-handler]")?.getAttribute("data-handler"),
      cell.textContent,
    ]);

  // AC 2's two halves. Under the seeded document Liam, Kaya and Marcus are
  // Medium; under the retuned one — `complexityHighMin: 60` — all three are
  // High, from the same scores. A contrast fixture that moved only the status
  // chip would leave the complexity half of AC 2 with no coverage here at all.
  expect(chipsFor(container, "handler-complexity")).toEqual([
    ["Liam O'Sullivan", "Medium (64)"],
    ["Kaya Johnson", "Medium (62)"],
    ["Marcus Chen", "Medium (60)"],
    ["Fatima Al-Mansoori", "Low (38)"],
    ["Sarah Williams", "High (65)"],
    ["Dante Reyes", "Medium (57)"],
  ]);
  // Sarah and Kaya are the two handlers the narrowed bands move: -4% and +4%
  // are both Watch against -8 / +8 here, and become On Track and Attention
  // against -3 / +3 below.
  expect(chipsFor(container, "handler-status")).toEqual([
    ["Liam O'Sullivan", "On Track -11%"],
    ["Kaya Johnson", "Watch -4%"],
    ["Marcus Chen", "Watch 0%"],
    ["Fatima Al-Mansoori", "Watch +1%"],
    ["Sarah Williams", "Watch +3%"],
    ["Dante Reyes", "Attention +8%"],
  ]);

  unmount();
  vi.unstubAllGlobals();

  const retuned = renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_RERANKED });
  await waitFor(() => expect(screen.getAllByTestId("handler-row")).toHaveLength(6));

  expect(chipsFor(retuned.container, "handler-complexity")).toEqual([
    ["Dante Reyes", "Medium (57)"],
    ["Sarah Williams", "High (65)"],
    ["Fatima Al-Mansoori", "Low (38)"],
    ["Marcus Chen", "High (60)"],
    ["Kaya Johnson", "High (62)"],
    ["Liam O'Sullivan", "High (64)"],
  ]);
  // …and each status chip is the band its own deviation falls in under the
  // narrowed -3 / +3 bands, so the chips and the footnote agree. The deviations
  // are this fixture's own — its composites are measured against a 55.0d
  // portfolio, not the 71.9d one above — which is what makes the pair a
  // contrast rather than one set of numbers under two sets of labels.
  expect(chipsFor(retuned.container, "handler-status")).toEqual([
    ["Dante Reyes", "On Track -12%"],
    ["Sarah Williams", "On Track -4%"],
    ["Fatima Al-Mansoori", "Watch 0%"],
    ["Marcus Chen", "Watch +1%"],
    ["Kaya Johnson", "Attention +4%"],
    ["Liam O'Sullivan", "Attention +12%"],
  ]);
});

test("an unknown RTW rate draws an em dash rather than a zero", async () => {
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_RERANKED });

  const rows = await screen.findAllByTestId("handler-row");
  const last = [...rows[rows.length - 1].querySelectorAll("td")].map((td) => td.textContent);

  // A handler who has settled nothing has no return-to-work rate. "0%" would
  // read as "nobody came back", which is a different and much quieter lie.
  expect(last[5]).toBe("—");
});

test("a request in flight draws skeleton rows and no figures", () => {
  renderPage({ handlerBenchmarks: "pending" });

  expect(screen.getByTestId("handler-benchmarks")).toHaveAttribute("aria-busy", "true");
  expect(screen.getAllByTestId("handler-row-skeleton").length).toBeGreaterThan(0);

  // Not one row, not one rank, not one zero — the headers hold the shape open
  // and nothing claims a figure.
  expect(screen.queryAllByTestId("handler-row")).toHaveLength(0);
  expect(screen.queryByTestId("benchmark-callout")).not.toBeInTheDocument();
  expect(screen.queryByTestId("benchmark-bands")).not.toBeInTheDocument();
});

test("a failed request states it inline and renders no table at all", async () => {
  renderPage({
    // A non-retryable failure (a proxy 404 — the case `client.ts` calls out),
    // so this asserts the rendered branch rather than racing a 5xx's backoff.
    handlerBenchmarks: {
      status: 404,
      body: { title: "Not Found", status: 404, detail: "no route" },
    },
  });

  const alert = await screen.findByTestId("handler-benchmarks-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(alert).toHaveTextContent("Handler performance could not be loaded.");

  expect(screen.queryAllByTestId("handler-row")).toHaveLength(0);
  expect(screen.queryAllByTestId("handler-row-skeleton")).toHaveLength(0);
  expect(screen.getByTestId("handler-benchmarks")).toHaveAttribute("aria-busy", "false");
});

test("a benchmark failure leaves the KPI cards standing", async () => {
  renderPage({
    handlerBenchmarks: {
      status: 404,
      body: { title: "Not Found", status: 404, detail: "no route" },
    },
  });

  // The reason the two sections are two queries. One endpoint's outage must not
  // blank the other's content, and nesting the table inside the summary's error
  // branch would have made it do exactly that.
  await screen.findByTestId("handler-benchmarks-error");
  expect(screen.getByTestId("kpi-total-claims-value")).toBeVisible();
  expect(screen.queryByTestId("portfolio-summary-error")).not.toBeInTheDocument();
});

test("a scope with no handlers renders the empty state, and keeps the band footnote", async () => {
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_EMPTY });

  const empty = await screen.findByTestId("handler-benchmarks-empty");
  expect(empty).toHaveTextContent("No handler carries a claim in this portfolio yet.");

  // A table with headers and no body reads as a broken component; the story
  // asks for a defined empty state instead (NFR-3).
  expect(screen.queryAllByTestId("handler-row")).toHaveLength(0);
  expect(screen.queryByTestId("benchmark-callout")).not.toBeInTheDocument();

  // The response populates the thresholds for an empty book on purpose —
  // "nothing in this book" says nothing about which rules were in force — so the
  // footnote stays. It is the only statement of that on a screen with no rows to
  // explain.
  //
  // Asserted as the **whole sentence**, because the defect it replaces was a
  // sentence and not a value: with no portfolio composite the footnote used to
  // read "…against this portfolio's — On Track at or below -8%", and a
  // `toHaveTextContent("—")` check passed on it happily. An em dash is the right
  // rendering of an unknown *cell*; dropped into the middle of a clause it is
  // just a sentence that stops. The clause naming the portfolio is omitted
  // instead, and the bands — which are known either way — are stated on their
  // own.
  expect(screen.getByTestId("benchmark-bands").textContent).toBe(
    "Status bands each handler's composite cycle time by its deviation from " +
      "this portfolio. On Track at or below -8%, Attention at or above 8%. " +
      "Complexity is Medium from 40 and High from 65.",
  );
  expect(screen.getByTestId("benchmark-bands")).not.toHaveTextContent("—");
});

test("a scope with one ranked handler says what it can know, not a headcount", async () => {
  renderPage({ handlerBenchmarks: HANDLER_BENCHMARKS_SINGLE });

  const callout = await screen.findByTestId("benchmark-callout");

  // `leader === laggard` establishes that exactly one row could be *ranked* —
  // the server picks both names from the rankable subset — which is not the
  // same fact as "one handler has claims in this portfolio". The sentence used
  // to claim the second, which a reader has no way to check and which is false
  // for any scope whose portfolio is missing a cycle-time segment.
  expect(callout).toHaveTextContent(
    "Fatima Al-Mansoori is the only handler this portfolio ranks",
  );
  expect(callout).not.toHaveTextContent("leads the desk");
  expect(callout).not.toHaveTextContent("workload check-in");
  expect(screen.getAllByTestId("handler-row")).toHaveLength(1);
});
