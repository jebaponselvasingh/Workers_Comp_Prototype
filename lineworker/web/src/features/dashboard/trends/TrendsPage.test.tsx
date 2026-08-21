import { MemoryRouter, useLocation } from "react-router";
import userEvent from "@testing-library/user-event";

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  TRENDS,
  TRENDS_BY_SECTOR,
  TRENDS_BY_SECTOR_RERANKED,
  TRENDS_BY_SEVERITY,
  TRENDS_CONTRAST,
  TRENDS_EMPTY,
  TRENDS_NONE_SETTLED,
  stubApi,
} from "@/test/api-mock";

import { TrendsPage } from "./TrendsPage";

/**
 * Story 7.2 AC 1/2/3 — the Trends section renders the server's window and
 * nothing of its own.
 *
 * The line these tests police is the one `noDerivation.test.ts` polices
 * structurally, and this surface strains it differently from every one before
 * it. The fraud workspace handed the browser a distribution and the edges it was
 * banded at; this one hands it a *window*: five metrics over N cohorts over M
 * buckets, each point carrying its value beside the claim count that value was
 * folded from, beside the ceiling that decided whether the count was too thin to
 * trust. Bucketing, banding, ranking, counting the gaps and summing the cohorts
 * back together are each one line, and each would be right on one fixture.
 *
 * So `TRENDS_CONTRAST` is the substance rather than a nicety. It differs from
 * `TRENDS_BY_SEVERITY` in four ways *at once* — a different grain, a different
 * anchor, a moved band edge, a missing cohort and a narrower book — and reading
 * both through the same components is the only assertion that can tell a page
 * that renders the server's answer from one that reconstructs a plausible
 * answer of its own. A single fixture cannot: bucket keys a component derived
 * from dates, a legend laid out from a fixed list of three bands and a caption
 * quoting a constant 65 all pass on the first fixture and fail on the second.
 *
 * `TRENDS_BY_SECTOR_RERANKED` is the second discriminator, for AC 2's other
 * half. Severity band's colours come from `RISK_FILL` and are keyed, so they
 * cannot drift whatever a component does; sector's come from
 * `CATEGORICAL_FILLS[paletteSlot]` and would drift the moment anything coloured
 * by rank or by array position. The re-ranked fixture keeps the vocabulary and
 * the slots and reverses the sizes, so a rank-coloured legend repaints and a
 * slot-coloured one does not.
 *
 * **Everything is read off the accessible rendering**, never off Recharts' SVG:
 * each card publishes an `sr-only` list of `bucketLabel: value` per series, and
 * that is what a screen-reader user gets. Recharts is never mocked — the tests
 * simply do not assert on anything it draws, which keeps them off its internal
 * element structure and off jsdom measuring anything.
 *
 * **Rendered through `TrendsPage`, never a card alone.** The cards take their
 * query state as props, so mounting one directly would test a wiring nobody
 * ships — and "the previous charts stay on screen while a new grain is in
 * flight" would not be assertable at all.
 */

function renderPage(routes: Parameters<typeof stubApi>[0]): QueryClient {
  stubApi(routes);
  const client = createQueryClient();
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TrendsPage />
        {/* What a drill *does* is a navigation. Rendering the current location
            beside the page is the smallest way to assert that without mounting
            the real route table — `FraudPage.test.tsx`'s probe, verbatim. */}
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return client;
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
 * branch. `FraudPage.test.tsx` and `App.test.tsx` record the same choice for the
 * same reason: what is under test is what a card draws when its query has
 * failed, and 4xx is final.
 */
const NOT_FOUND = { status: 404, body: { title: "Not Found", status: 404 } };

/** The five cards, by the stem each one publishes. */
const CARDS = [
  "trend-volume",
  "trend-days-open",
  "trend-settlement",
  "trend-rtw-rate",
  "trend-paid",
];

/**
 * The body height every card reserves, restated from `chartTheme.CHART_HEIGHT`.
 *
 * Written out rather than imported, `PortfolioCharts.test.tsx`' discipline: the
 * point of the assertion is that the box is the *same* height in all four
 * states, and an expectation that read the constant under test would agree with
 * a component that had stopped applying it in one of them.
 */
const BODY_HEIGHT = "244px";

/**
 * One card's accessible list, as `bucketLabel: value` strings in render order.
 *
 * A `for` loop rather than a chained partition-and-map, and that is a deliberate
 * house style rather than a preference: this story's verification greps the
 * whole of `features/dashboard/trends/` for the three array methods AD-1 bans
 * from a rendering surface and expects no match anywhere in the folder. Test
 * files are outside `noDerivation.test.ts`'s scan — they are the independent
 * oracle and may restate a rule in order to disagree with it — but a grep cannot
 * tell the two apart, and a verification step with one known exception is a
 * verification step that stops being read.
 */
function points(testId: string, cohortKey = ""): string[] {
  const found: string[] = [];
  for (const node of screen.getAllByTestId(`${testId}-point`)) {
    if (node.getAttribute("data-cohort-key") !== cohortKey) continue;
    found.push(node.textContent?.replace(/\s+/g, " ").trim() ?? "");
  }
  return found;
}

/** The cohort keys one card draws a line for, in the order it drew them. */
function lines(testId: string): string[] {
  return screen
    .getAllByTestId(`${testId}-line`)
    .map((node) => node.getAttribute("data-cohort-key") ?? "");
}

/** The colour one card gave one cohort. */
function fillOf(testId: string, cohortKey: string): string | null {
  const line = screen
    .getAllByTestId(`${testId}-line`)
    .find((node) => node.getAttribute("data-cohort-key") === cohortKey);
  return line?.getAttribute("data-fill") ?? null;
}

/** A stub that answers each selector set with the fixture that belongs to it. */
function bySelector(url: string) {
  if (url.includes("cohort=severity_band")) return TRENDS_BY_SEVERITY;
  if (url.includes("cohort=sector")) return TRENDS_BY_SECTOR;
  if (url.includes("grain=quarter")) return TRENDS_CONTRAST;
  return TRENDS;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- the five series, as the server sent them (AC 1) ---------------------

test("every card draws the values the server sent, in the unit the metric names", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));

  // A count, a whole-day mean, a basis-point rate and cents — four units, one
  // per card, each applied to a number that arrived decided. Nothing here is
  // divided, summed or rounded in the browser: `88.89%` comes out of
  // `lib/rate.ts` and `$29,805` out of `lib/money.ts`, which are the only two
  // places allowed to divide by a hundred.
  expect(points("trend-volume")).toEqual([
    "Jan 2026: 12",
    "Feb 2026: 0",
    "Mar 2026: 2 (low confidence)",
    // April is the bucket the window was cut inside — the server says so and
    // the list repeats it, because a count of a month that is three weeks old
    // is not comparable with the three whole months to its left.
    "Apr 2026: 9 (partial period)",
  ]);
  expect(points("trend-days-open")).toEqual([
    "Jan 2026: 148d",
    "Feb 2026: —",
    "Mar 2026: 96d (low confidence)",
    "Apr 2026: 41d (partial period)",
  ]);
  expect(points("trend-rtw-rate")).toEqual([
    "Jan 2026: 75.00%",
    "Feb 2026: —",
    // March saw two claims and settled none, so the rate has *no* denominator
    // here: an em dash, and no low-confidence mark beside it — that verdict is
    // decided on the claims that fed this metric, and none did.
    "Mar 2026: —",
    "Apr 2026: 88.89% (partial period)",
  ]);
  expect(points("trend-paid")).toEqual([
    "Jan 2026: $42,100",
    "Feb 2026: $0",
    "Mar 2026: $6,150 (low confidence)",
    "Apr 2026: $29,805 (partial period)",
  ]);
});

test("the window caption states both populations and the clock", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() =>
    expect(screen.getByTestId("trend-window-caption")).toBeInTheDocument(),
  );
  // A window describing 23 of 100 claims is not wrong, but a reader who thinks
  // it describes 100 is — and `asOf` is on screen because `avgDaysOpen` counts
  // up to a day and never freezes.
  const caption = screen.getByTestId("trend-window-caption");
  expect(caption).toHaveTextContent("2026-01-01 to 2026-04-30");
  expect(caption).toHaveTextContent("4 periods");
  expect(caption).toHaveTextContent("23 of 100 claims");
  expect(caption).toHaveTextContent("as of 2026-04-21");
});

test("the day-open card says which day the ages were counted to", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() =>
    expect(screen.getByTestId("trend-days-open-footnote")).toBeInTheDocument(),
  );
  // Design note 3 on screen: this series slopes upward toward older buckets
  // purely because those claims are older, which is a property of the metric.
  // Saying which day it was measured on is the honest fix; a second computer
  // that froze the age at settlement is the dishonest one (AD-10).
  expect(screen.getByTestId("trend-days-open-footnote")).toHaveTextContent(
    "Ages counted from FNOL to 2026-04-21",
  );
  // …and the card names its own axis, which is the half that is not obvious:
  // `daysOpen` is measured from the FNOL date on both anchors, so under
  // `anchor=doi` this card would otherwise read as "days since injury".
  expect(
    screen.getByRole("heading", { name: "Average days open by FNOL" }),
  ).toBeInTheDocument();
  // …and the settlement card says what its cohort *is*, so nobody reads
  // "claims that settled in March" off a chart of claims filed in March.
  expect(
    screen.getByRole("heading", { name: /Settlement cycle time — by FNOL cohort/ }),
  ).toBeInTheDocument();
});

test("the targets on screen are the response's, not a constant in the client", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() =>
    expect(screen.getByTestId("trend-settlement-footnote")).toHaveTextContent("Target 30d"),
  );
  expect(screen.getByTestId("trend-rtw-rate-footnote")).toHaveTextContent("Target 80.00%");
});

// --- gaps, not zero lines (AC 3) ----------------------------------------

test("a bucket with no denominator reads as absent, never as zero", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));

  // February saw no claims. The **count** and the **sum** are `0` because a sum
  // over an empty set is 0 and drawing it is not a lie; the **mean** and the
  // **rate** are absent, because "average days open: 0" and "RTW rate: 0%" are
  // both false and a line through zero reads as an excellent result. That split
  // is by metric kind and not by bucket, and it is the whole of AC 3.
  expect(points("trend-volume")[1]).toBe("Feb 2026: 0");
  expect(points("trend-paid")[1]).toBe("Feb 2026: $0");
  expect(points("trend-days-open")[1]).toBe("Feb 2026: —");
  expect(points("trend-settlement")[1]).toBe("Feb 2026: —");
  expect(points("trend-rtw-rate")[1]).toBe("Feb 2026: —");

  // …and March is the *other* gap: claims arrived and none of them settled, so
  // the two SLA metrics are absent while volume, paid and days-open are numbers.
  // A component that treated "no claims" and "nothing to settle" as one state
  // would render these two buckets alike.
  expect(points("trend-days-open")[2]).toBe("Mar 2026: 96d (low confidence)");
  // …and the low-confidence mark follows the *metric's* population, not the
  // bucket's: two claims arrived in March and neither settled, so the
  // settlement point has nothing behind it at all. A verdict decided on the
  // bucket would put "low confidence" against an em dash, which is a warning
  // about the reliability of a number that was never published.
  expect(points("trend-settlement")[2]).toBe("Mar 2026: —");
  // January is the same rule the other way up: twelve claims filed, three of
  // them settled with a recorded duration, so the busy month's settlement mean
  // *is* thin while its volume is not.
  expect(points("trend-volume")[0]).toBe("Jan 2026: 12");
  expect(points("trend-settlement")[0]).toBe("Jan 2026: 34d (low confidence)");
});

test("each card states how many of the window's periods have no data", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() =>
    expect(screen.getByTestId("trend-volume-footnote")).toBeInTheDocument(),
  );
  // `noDataBuckets` and `bucketCount` are both the server's, stated rather than
  // counted — counting the nulls in the browser is the arithmetic AD-1 removes,
  // and it is one `.filter` away on a list the component already holds.
  expect(screen.getByTestId("trend-volume-footnote")).toHaveTextContent(
    "0 of 4 periods have no data",
  );
  expect(screen.getByTestId("trend-days-open-footnote")).toHaveTextContent(
    "1 of 4 periods have no data",
  );
  expect(screen.getByTestId("trend-settlement-footnote")).toHaveTextContent(
    "2 of 4 periods have no data",
  );
});

test("a thin bucket carries the server's low-confidence verdict, and a full one does not", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));

  const marks = screen
    .getAllByTestId("trend-volume-point")
    .map((node) => node.getAttribute("data-low-confidence"));
  // Exactly March: two claims against a ceiling of three. The verdict is the
  // server's — `lowConfidence` is `0 < claimCount <= lowConfidenceClaimMax` and
  // both operands ride this payload, so the comparison is one line away from
  // being made here instead, against a rule nobody could see change (AD-8).
  expect(marks).toEqual(["false", "false", "true", "false"]);
  // A bucket with *no* claims is not low confidence, it is no confidence — and
  // its value is already absent.
  expect(marks[1]).toBe("false");
});

// --- the cohort split, and the contrast fixture (AC 2) -------------------

test("a cohort split renders one line per cohort value, in the server's order", async () => {
  renderPage({ trends: (url) => bySelector(url) });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-cohort"), "severity_band");

  await waitFor(() => expect(lines("trend-volume")).toHaveLength(3));

  // Ascending *wire key* — `high`, `low`, `med` — which is the order
  // `paletteSlot` was assigned in and is deliberately neither the severity
  // donut's high-first legend order nor any ranking of the values. `med` is the
  // biggest cohort here and is drawn last.
  expect(lines("trend-volume")).toEqual(["high", "low", "med"]);
  expect(lines("trend-paid")).toEqual(["high", "low", "med"]);

  expect(points("trend-volume", "high")).toEqual([
    // Three claims against a ceiling of three: `lowConfidence` is inclusive at
    // the ceiling, which is the server's `<=` and not this file's guess.
    "Jan 2026: 3 (low confidence)",
    "Feb 2026: 0",
    "Mar 2026: 1 (low confidence)",
    "Apr 2026: 2 (low confidence) (partial period)",
  ]);
  expect(points("trend-volume", "med")).toEqual([
    "Jan 2026: 5",
    "Feb 2026: 0",
    "Mar 2026: 1 (low confidence)",
    "Apr 2026: 4 (partial period)",
  ]);
});

test("the legend names the cohorts in this console's words, once for the section", async () => {
  renderPage({ trends: (url) => bySelector(url) });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  // An unsplit reading has no cohorts, so it has no legend to draw.
  expect(screen.queryByTestId("trend-legend")).not.toBeInTheDocument();

  await userEvent.selectOptions(screen.getByTestId("trend-cohort"), "severity_band");

  await waitFor(() => expect(screen.getByTestId("trend-legend")).toBeInTheDocument());
  // `cohortLabel` is `null` on the wire for all three dimensions this story
  // ships — the enums' labels are the SPA's — and the words are the drill-through
  // chip's, imported rather than restated so a reader who clicks "Medium" cannot
  // land on a chip reading `med`.
  expect(
    within(screen.getByTestId("trend-legend"))
      .getAllByRole("button")
      .map((node) => node.textContent?.trim()),
  ).toEqual(["High", "Low", "Medium"]);
});

test("a different scope, grain, band edge and cohort set render as themselves", async () => {
  // The discriminator. Four differences at once against `TRENDS_BY_SEVERITY`,
  // through the same components — a page that bucketed, banded or laid out
  // anything of its own gets one of them wrong and passes on the other fixture.
  renderPage({ trends: (url) => bySelector(url) });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-grain"), "quarter");

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(6));

  // Quarter labels, which no component could produce from a date it was handed:
  // the label is the server's rendering of its own bucket.
  expect(points("trend-volume", "high")).toEqual([
    "Q1 2026: 6",
    "Q2 2026: 2 (low confidence)",
    // Unfinished *and* empty, which are two different statements about one
    // period: the quarter runs to September and the window was cut in August.
    "Q3 2026: 0 (partial period)",
  ]);
  // **Two cohorts, not three.** This scope has no `med` claims at all, so the
  // vocabulary is two values and `low` holds slot 1 — the slot `med` held in the
  // other fixture. A legend laid out from a fixed list of bands draws an empty
  // third line here.
  expect(lines("trend-volume")).toEqual(["high", "low"]);
  expect(
    within(screen.getByTestId("trend-legend"))
      .getAllByRole("button")
      .map((node) => node.textContent?.trim()),
  ).toEqual(["High", "Low"]);
  // A narrower book and a different clock, both off the response.
  expect(screen.getByTestId("trend-window-caption")).toHaveTextContent("24 of 27 claims");
  expect(screen.getByTestId("trend-window-caption")).toHaveTextContent("as of 2026-08-21");
  // …and this deployment's targets, which are not the other fixture's either.
  expect(screen.getByTestId("trend-settlement-footnote")).toHaveTextContent("Target 45d");
  expect(screen.getByTestId("trend-rtw-rate-footnote")).toHaveTextContent("Target 75.00%");
});

// --- colour is identity (AC 2) ------------------------------------------

test("a severity cohort holds the KPI cards' own colour on every chart", async () => {
  renderPage({ trends: (url) => bySelector(url) });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-cohort"), "severity_band");
  await waitFor(() => expect(lines("trend-volume")).toHaveLength(3));

  // The same three tokens the High Risk card, the severity donut and a claim's
  // risk gauge draw — four renderings of one registered `risk` derivation. Not a
  // positional hue: the High cohort is the *smallest* here, so a rank palette
  // would paint the worst claims in the portfolio steel blue.
  expect(fillOf("trend-volume", "high")).toBe("var(--color-error)");
  expect(fillOf("trend-volume", "med")).toBe("var(--color-warn)");
  expect(fillOf("trend-volume", "low")).toBe("var(--color-ok)");
  // …and every chart in the section agrees, which is what makes the one shared
  // legend honest.
  for (const testId of CARDS) {
    expect(fillOf(testId, "high")).toBe("var(--color-error)");
  }
  expect(within(screen.getByTestId("trend-legend")).getAllByTestId("trend-legend-link")[0])
    .toHaveAttribute("data-fill", "var(--color-error)");
});

test("a categorical cohort keeps its colour across two charts and across a refetch", async () => {
  // The falsifiable half of AC 2. Severity's colours are keyed and cannot drift;
  // sector's are `CATEGORICAL_FILLS[paletteSlot]` and would drift the moment
  // anything coloured by rank or by position within a chart.
  let sectorReads = 0;
  const client = renderPage({
    trends: (url) => {
      if (!url.includes("cohort=sector")) return TRENDS;
      sectorReads += 1;
      return sectorReads === 1 ? TRENDS_BY_SECTOR : TRENDS_BY_SECTOR_RERANKED;
    },
  });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-cohort"), "sector");
  await waitFor(() => expect(lines("trend-volume")).toHaveLength(3));

  const before = {
    aerospace: fillOf("trend-volume", "Aerospace"),
    automotive: fillOf("trend-volume", "Automotive"),
    heavy: fillOf("trend-volume", "Heavy Equipment"),
  };
  // The palette's first three positions, indexed by the server's ordinal.
  expect(before).toEqual({
    aerospace: "#1D6A96",
    automotive: "#E8560A",
    heavy: "#1D7A45",
  });
  // Two charts, one colour per cohort.
  expect(fillOf("trend-paid", "Heavy Equipment")).toBe(before.heavy);
  expect(fillOf("trend-rtw-rate", "Automotive")).toBe(before.automotive);

  // Refetch the same question. The answer reverses the sizes — `Heavy
  // Equipment` is now the largest and `Aerospace` the smallest — and keeps the
  // vocabulary and therefore the slots, because a slot is assigned by sorting
  // the cohort values on their *wire key* and a key does not move when a number
  // does.
  await client.refetchQueries();
  await waitFor(() =>
    expect(points("trend-volume", "Aerospace")[2]).toBe("Apr 2026: 4 (partial period)"),
  );

  expect(fillOf("trend-volume", "Aerospace")).toBe(before.aerospace);
  expect(fillOf("trend-volume", "Automotive")).toBe(before.automotive);
  expect(fillOf("trend-volume", "Heavy Equipment")).toBe(before.heavy);
  expect(fillOf("trend-paid", "Heavy Equipment")).toBe(before.heavy);
});

// --- emptiness is a total, never a row count ----------------------------

test("an empty book gets every card's sentence, not five flat lines at zero", async () => {
  // The bug Story 7.1 shipped once, in the place it would have been hardest to
  // see. Every series is the full window whatever the book holds, so
  // `points.length` is four here and a length test can never fire — an empty
  // portfolio would have drawn five lines running along the axis, which reads as
  // a real and excellent result. `seriesTotal` is the honest test.
  renderPage({ trends: TRENDS_EMPTY });

  await waitFor(() =>
    expect(screen.getByTestId("trend-volume-empty")).toBeInTheDocument(),
  );
  for (const testId of CARDS) {
    // No list of zeroes hiding behind the sentence.
    expect(screen.queryAllByTestId(`${testId}-point`)).toHaveLength(0);
    // Full height in the empty state too — see the no-reflow test below.
    expect(screen.getByTestId(`${testId}-body`)).toHaveStyle({ height: BODY_HEIGHT });
  }
  // …and each sentence describes the population *that* card could not draw. An
  // empty book empties all five, but the two SLA cards say why their own
  // population is missing rather than repeating a claim about the window — which
  // is the sentence they need on the far more common case below.
  for (const testId of ["trend-volume", "trend-days-open", "trend-paid"]) {
    expect(screen.getByTestId(`${testId}-empty`)).toHaveTextContent(
      "No claims fall in this window.",
    );
  }
  for (const testId of ["trend-settlement", "trend-rtw-rate"]) {
    expect(screen.getByTestId(`${testId}-empty`)).toHaveTextContent(
      "No claims in this window have settled.",
    );
  }
  // The window still exists: it is a question the server answered, not an
  // absence, so the period control is still populated.
  expect(screen.getByTestId("trend-window-caption")).toHaveTextContent("0 of 0 claims");
});

test("a window whose claims never settled empties the two SLA cards and no others", async () => {
  // **The case the bucket-count `seriesTotal` got wrong**, and the reason it was
  // invisible: a hundred claims none of which have settled is an ordinary state
  // for a young book, and a settlement card counting the *bucket* reports 23
  // claims behind a series of nothing but nulls. `ChartFrame` then draws its
  // data state — a plot with axes, a dashed target line and not one point —
  // which reads as "settlement is at zero" rather than as "nothing here has
  // settled yet". The three cards beside it still have everything to say.
  renderPage({ trends: TRENDS_NONE_SETTLED });

  await waitFor(() =>
    expect(screen.getByTestId("trend-settlement-empty")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("trend-settlement-empty")).toHaveTextContent(
    "No claims in this window have settled.",
  );
  expect(screen.getByTestId("trend-rtw-rate-empty")).toHaveTextContent(
    "No claims in this window have settled.",
  );
  // Not one point on either — the empty state replaces the plot rather than
  // sitting beside a line drawn through no data.
  expect(screen.queryAllByTestId("trend-settlement-point")).toHaveLength(0);
  expect(screen.queryAllByTestId("trend-rtw-rate-point")).toHaveLength(0);

  // …while the three metrics the window *can* describe are drawn in full.
  expect(screen.queryByTestId("trend-volume-empty")).not.toBeInTheDocument();
  expect(points("trend-volume")).toEqual(["Jan 2026: 12", "Feb 2026: 11"]);
  expect(points("trend-days-open")).toEqual(["Jan 2026: 148d", "Feb 2026: 120d"]);
  expect(points("trend-paid")).toEqual(["Jan 2026: $42,100", "Feb 2026: $30,000"]);
});

// --- drill-through (AC 6) -----------------------------------------------

test("the period control and the button open that period's claims on the anchor's own column", async () => {
  renderPage({ trends: TRENDS });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  // The window's last bucket is the default: the period a reader asking "how are
  // we doing" means.
  expect(screen.getByTestId("trend-period")).toHaveValue("2026-04");

  await userEvent.selectOptions(screen.getByTestId("trend-period"), "2026-01");
  await userEvent.click(screen.getByTestId("trend-view-claims"));

  // **The bucket's own published bounds**, inclusive, copied from the point the
  // server folded — never a boundary re-derived from `2026-01` in the browser.
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/dashboard/claims?filter%5BfnolFrom%5D=2026-01-01&filter%5BfnolTo%5D=2026-01-31",
  );
});

test("a legend button carries the cohort facet as well as the period", async () => {
  renderPage({ trends: (url) => bySelector(url) });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-cohort"), "severity_band");
  await waitFor(() => expect(screen.getByTestId("trend-legend")).toBeInTheDocument());

  const high = screen
    .getAllByTestId("trend-legend-link")
    .find((node) => node.getAttribute("data-key") === "high");
  await userEvent.click(high!);

  // **Both**, and the order is `FILTER_KEYS`' — `severityBand` is an older facet
  // than the date bounds and is drawn first, which is the order the server
  // publishes `appliedFilters` in. Dropping the cohort would open a list three
  // times longer than the line that was clicked.
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/dashboard/claims?filter%5BseverityBand%5D=high&filter%5BfnolFrom%5D=2026-04-01&filter%5BfnolTo%5D=2026-04-30",
  );
});

test("the injury-date anchor drills on the injury-date columns", async () => {
  // The failure this pair of facets exists to prevent: a point on the DOI series
  // opened with `filter[fnolFrom]` returns a plausible list of the wrong claims,
  // and the two dates are weeks apart on a third of the book.
  renderPage({ trends: (url) => bySelector(url) });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-grain"), "quarter");
  await waitFor(() => expect(screen.getByTestId("trend-period")).toHaveValue("2026-Q3"));

  await userEvent.selectOptions(screen.getByTestId("trend-period"), "2026-Q1");
  await userEvent.click(screen.getByTestId("trend-view-claims"));

  expect(screen.getByTestId("location")).toHaveTextContent(
    "/dashboard/claims?filter%5BdoiFrom%5D=2026-01-01&filter%5BdoiTo%5D=2026-03-31",
  );
});

// --- the controls set a server parameter, and render its answer ----------

test("changing a selector issues a request and renders what came back", async () => {
  // The whole of "the browser buckets nothing", as an observable property: the
  // control sets a parameter, a *different request* goes out, and what renders
  // is what came back. A client-side re-grain would redraw the same claims and
  // never touch the network.
  const asked: string[] = [];
  renderPage({
    trends: (url) => {
      asked.push(url);
      return bySelector(url);
    },
  });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  expect(points("trend-volume")[0]).toBe("Jan 2026: 12");

  await userEvent.selectOptions(screen.getByTestId("trend-grain"), "quarter");
  await waitFor(() => expect(points("trend-volume", "high")[0]).toBe("Q1 2026: 6"));

  expect(asked.some((url) => url.includes("grain=quarter"))).toBe(true);
  expect(asked.some((url) => url.includes("anchor=fnol"))).toBe(true);
});

test("the selectors render the grain the server says it applied, not the last click", async () => {
  // `PortfolioTrendsResponse` echoes `grain`, `anchor` and `cohort` so a control
  // renders the server's answer rather than its own last click — a request that
  // 422s or is coerced must not leave a selector claiming a grain the chart
  // beside it is not drawn at. The stub answers the month reading whatever is
  // asked for, so a `<select>` echoing the response reads `month` and one
  // rendering its own state reads `quarter`.
  renderPage({ trends: TRENDS });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-grain"), "quarter");

  await waitFor(() =>
    expect(screen.getByTestId("trend-analytics")).toHaveAttribute("aria-busy", "false"),
  );
  expect(screen.getByTestId("trend-grain")).toHaveValue("month");
});

test("a new window in flight keeps the previous charts on screen", async () => {
  // AC 7. All five charts ride one query keyed on the whole selector set, so
  // re-keying it without `keepPreviousData` drops `data` to `undefined` and
  // blanks five charts at once — five skeletons and five `aria-busy` flips for a
  // reader who asked one question.
  renderPage({
    trends: (url) => (url.includes("grain=quarter") ? "pending" : TRENDS),
  });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  await userEvent.selectOptions(screen.getByTestId("trend-grain"), "quarter");

  await waitFor(() =>
    expect(screen.getByTestId("trend-analytics")).toHaveAttribute("aria-busy", "true"),
  );
  // The previous answer is still readable underneath, and the control shows the
  // question that is in flight.
  expect(points("trend-volume")[0]).toBe("Jan 2026: 12");
  expect(screen.getByTestId("trend-grain")).toHaveValue("quarter");
  expect(screen.queryAllByTestId("trend-volume-skeleton")).toHaveLength(0);
  // …and the heading still names the anchor those lines were bucketed by. The
  // control and the title read from two different places on purpose: the
  // `<select>` shows the question in flight, the title describes what is drawn.
  expect(
    screen.getByRole("heading", { name: "Claim volume by FNOL" }),
  ).toBeInTheDocument();
});

test("a card's title names the anchor of the lines drawn, not the one in flight", async () => {
  // The half the grain change above cannot show, because a grain does not
  // appear in a title. `keepPreviousData` keeps the FNOL lines on screen while
  // the injury-date window is outstanding, so a title read off the *requested*
  // parameters captions an FNOL chart "by injury date" for the length of the
  // request — and every figure under it means something else. The selector is
  // the opposite call and stays on the question asked.
  renderPage({
    trends: (url) => (url.includes("anchor=doi") ? "pending" : TRENDS),
  });

  await waitFor(() => expect(screen.getAllByTestId("trend-volume-point")).toHaveLength(4));
  expect(
    screen.getByRole("heading", { name: "Claim volume by FNOL" }),
  ).toBeInTheDocument();

  await userEvent.selectOptions(screen.getByTestId("trend-anchor"), "doi");
  await waitFor(() =>
    expect(screen.getByTestId("trend-analytics")).toHaveAttribute("aria-busy", "true"),
  );

  expect(screen.getByTestId("trend-anchor")).toHaveValue("doi");
  expect(points("trend-volume")[0]).toBe("Jan 2026: 12");
  expect(
    screen.getByRole("heading", { name: "Claim volume by FNOL" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Claim volume by injury date" }),
  ).not.toBeInTheDocument();
  // All three anchored titles, since they move together and one of them names
  // the card whose measure is *not* the anchor's — see `MetricSpec.title`.
  expect(
    screen.getByRole("heading", { name: "Average days open by FNOL" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: /Settlement cycle time — by FNOL cohort/ }),
  ).toBeInTheDocument();
});

test("the partial period is named on the card as well as on its point", async () => {
  // The rightmost point is the one the section exists to read and it is a part
  // of a period drawn at a whole period's width. A sighted reader gets the
  // footnote clause and the dashed dot; the accessible list gets the words. The
  // clause names the *bucket the server marked*, never "the last one", which is
  // the shortcut that would caveat a finished quarter on a window asked for
  // ahead of today.
  renderPage({ trends: TRENDS });

  await waitFor(() =>
    expect(screen.getByTestId("trend-volume-footnote")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("trend-volume-footnote")).toHaveTextContent(
    "Apr 2026 is a part period",
  );
  const marks = screen
    .getAllByTestId("trend-volume-point")
    .map((node) => node.getAttribute("data-partial"));
  expect(marks).toEqual(["false", "false", "false", "true"]);
});

// --- loading, error, and the reserved box -------------------------------

test("a request in flight draws skeletons at full height and claims no figure", () => {
  renderPage({ trends: "pending" });

  expect(screen.getByTestId("trend-analytics")).toHaveAttribute("aria-busy", "true");
  for (const testId of CARDS) {
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId(`${testId}-skeleton`)).toBeInTheDocument();
    // The box is the same height it will be once the data lands, so nothing
    // below it moves when it does (NFR-3).
    expect(screen.getByTestId(`${testId}-body`)).toHaveStyle({ height: BODY_HEIGHT });
  }
  // Not one point, not one zero — the reserved boxes hold the layout open and
  // nothing claims a value.
  expect(screen.queryAllByTestId("trend-volume-point")).toHaveLength(0);
  expect(screen.queryByTestId("trend-window-caption")).not.toBeInTheDocument();
  // …and no period to drill on, rather than a control that opens the wrong one.
  expect(screen.getByTestId("trend-view-claims")).toBeDisabled();
});

test("a failed request states it inline on every card and draws no line", async () => {
  renderPage({ trends: NOT_FOUND });

  await waitFor(() =>
    expect(screen.getByTestId("trend-volume-error")).toBeInTheDocument(),
  );
  for (const testId of CARDS) {
    const alert = screen.getByTestId(`${testId}-error`);
    expect(alert).toHaveAttribute("role", "alert");
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByTestId(`${testId}-skeleton`)).not.toBeInTheDocument();
    // The alert *replaces* the chart rather than sitting above an empty one: an
    // empty chart under a warning reads as "this scope has nothing in it", which
    // is a quieter lie than a stated failure.
    expect(screen.queryAllByTestId(`${testId}-point`)).toHaveLength(0);
    // The reserved box, in the fourth state too.
    expect(screen.getByTestId(`${testId}-body`)).toHaveStyle({ height: BODY_HEIGHT });
  }
  // Errors on this dashboard are inline (NFR-3).
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("one outage names each card it took down", async () => {
  // One failed request, five cards — so a screen reader reads five alerts back
  // to back. Each has to say which chart it is about; five copies of one
  // sentence is a reader unable to tell an echo from four more failures.
  // `PortfolioCharts` sets the precedent, six charts to one request.
  renderPage({ trends: NOT_FOUND });

  await waitFor(() =>
    expect(screen.getByTestId("trend-volume-error")).toBeInTheDocument(),
  );
  const alerts = screen.getAllByRole("alert").map((node) => node.textContent?.trim());
  expect(alerts).toHaveLength(CARDS.length);
  expect(new Set(alerts).size).toBe(alerts.length);
  expect(screen.getByTestId("trend-rtw-rate-error")).toHaveTextContent(
    "Return-to-work rate could not be loaded",
  );
  expect(screen.getByTestId("trend-paid-error")).toHaveTextContent(
    "Total paid could not be loaded",
  );

  // …and the section's own controls survive the outage: a reader who arrived on
  // a window that failed can still ask for a different one, which is the only
  // way out of the failure that does not involve the address bar.
  expect(screen.getByTestId("trend-controls")).toBeInTheDocument();
  expect(screen.getByTestId("trend-grain")).toBeEnabled();
  expect(screen.getByTestId("trend-analytics")).toHaveAttribute("aria-busy", "false");
});

test("a card is the same height with a caption and without one", async () => {
  // The reflow NFR-3 is actually about. The footnote is written only in the data
  // state, so rendering it with no reserved space makes every card one line
  // taller the moment the response lands — under the reader's cursor.
  // `PortfolioCharts.test.tsx`'s assertion, on the element where the reserved
  // row lives.
  const { unmount } = render(<span />);
  unmount();

  renderPage({ trends: "pending" });
  const skeletonRow = screen.getByTestId("trend-volume").lastElementChild?.className;
  expect(screen.queryByTestId("trend-volume-footnote")).toBeNull();

  vi.unstubAllGlobals();
  renderPage({ trends: TRENDS });
  await screen.findAllByTestId("trend-volume-footnote");

  // Same element, same reserved height, caption or no caption.
  expect(screen.getAllByTestId("trend-volume").at(-1)?.lastElementChild?.className).toBe(
    skeletonRow,
  );
});
