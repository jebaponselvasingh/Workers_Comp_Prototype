import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  DASHBOARD_CHARTS,
  DASHBOARD_CHARTS_EMPTY,
  DASHBOARD_CHARTS_SCOPED,
  SLA_STRIP,
  stubApi,
} from "@/test/api-mock";

import { DashboardPage } from "../DashboardPage";

/**
 * Story 5.3 AC 1/2/3/4 — the seven surfaces render the server's series and
 * nothing of their own.
 *
 * The line these tests police is the one `noDerivation.test.ts` polices
 * structurally: each surface receives a finished series and may format it,
 * colour it and hand it to Recharts to turn into pixels; it may not produce,
 * re-order, truncate or percentage one. So every assertion reads **label/value
 * pairs in the order they arrived** off each surface's own list — the donut's
 * visible legend, the bar chart's `sr-only` list — and a contrast fixture with
 * a different scope, a different order and a moved band renders through the
 * same components. A component that sorted or banded on its own would read the
 * same under both; one that renders what it was sent cannot.
 *
 * Reading the lists rather than the SVG is deliberate twice over: it is the
 * *accessible* rendering of each chart (so asserting it is asserting what a
 * screen-reader user gets), and it keeps every test off Recharts' internal
 * element structure and off jsdom measuring anything. The jsdom size seam in
 * `test/setup.ts` is still needed — a `ResponsiveContainer` that measured zero
 * would render nothing at all, in every test, silently — but it is backstopped
 * by one assertion that an `<svg>` exists rather than being the substance of
 * the rest.
 *
 * Rendered through `DashboardPage` with a fresh `createQueryClient()` per house
 * convention: the section takes its query state as props, so mounting it
 * directly would test a wiring nobody ships — and "a charts failure leaves the
 * KPI cards and the handler table standing" would not be assertable at all.
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

/** Every surface's `data-testid` stem, in the prototype's grid order. */
const SURFACES = [
  "chart-settlement-status",
  "chart-severity",
  "chart-sla",
  "chart-recovery-status",
  "chart-injury-type",
  "chart-employer-paid",
  "chart-state",
];

/** The six that draw a series; the SLA group is four styled cards, not a chart. */
const SERIES_SURFACES = SURFACES.filter((testId) => testId !== "chart-sla");

/**
 * The box each surface reserves, restated from `chartTheme.CHART_HEIGHT`.
 *
 * Written out here rather than imported, `seed_fixture`'s discipline: the point
 * of the assertion is that the box is the *same* height in all four states, and
 * an expectation that read the constant under test would agree with a component
 * that had stopped applying it in one of them.
 */
const BODY_HEIGHT: Record<string, string> = {
  "chart-settlement-status": "172px",
  "chart-severity": "172px",
  "chart-sla": "172px",
  "chart-recovery-status": "232px",
  "chart-injury-type": "232px",
  "chart-employer-paid": "232px",
  "chart-state": "232px",
};

/** A donut's legend, as `[label, count]` pairs in render order. */
function legend(container: HTMLElement, testId: string): [string, string][] {
  return [...container.querySelectorAll(`[data-testid='${testId}-legend-row']`)].map(
    (row) => [
      row.querySelector("span:nth-of-type(2)")?.textContent ?? "",
      row.querySelector("b")?.textContent ?? "",
    ],
  );
}

/** A bar chart's `sr-only` list, as whole strings in render order. */
function bars(container: HTMLElement, testId: string): string[] {
  return [...container.querySelectorAll(`[data-testid='${testId}-values'] li`)].map(
    (item) => item.textContent ?? "",
  );
}

test("every surface draws the values the server sent, in the order it sent them", async () => {
  const { container } = renderPage({ dashboardCharts: DASHBOARD_CHARTS });

  await screen.findByTestId("chart-settlement-status-legend");

  // The stage donut keys on `stage`, which is Story 5.1's ruling: 62 settled,
  // matching the Settled & Closed card above it, where the prototype's
  // `status === "Settled & Closed"` filter would show 54.
  expect(legend(container, "chart-settlement-status")).toEqual([
    ["Intake:", "6"],
    ["Investigation:", "4"],
    ["Under Treatment:", "28"],
    ["Settled & Closed:", "62"],
  ]);
  expect(screen.getByTestId("chart-settlement-status-total")).toHaveTextContent("100");

  // The High legend row quotes the band the server counted at, not a constant.
  expect(legend(container, "chart-severity")).toEqual([
    ["High (≥ 65):", "32"],
    ["Medium:", "51"],
    ["Low:", "17"],
  ]);

  expect(bars(container, "chart-recovery-status")).toEqual([
    "Under Treatment: 36",
    "Under Therapy: 18",
    "Fully Recovered: 46",
  ]);

  // The server's ranking, rendered as it arrived — count descending, and the
  // tie-break already applied.
  expect(bars(container, "chart-injury-type")).toEqual([
    "Amputation: 13",
    "Fracture: 12",
    "Carpal Tunnel Syndrome: 11",
    "Crushing: 10",
    "Fall from Height: 9",
    "Vibration White Finger (HAVS): 8",
    "Eye Injury: 7",
    "Laceration: 5",
  ]);

  // Cents on the wire, dollars on screen — `lib/money.ts` does the one
  // division, and the order is the server's paid-descending ranking.
  expect(bars(container, "chart-employer-paid").slice(0, 3)).toEqual([
    "Caterpillar: $435,029",
    "Toyota: $303,937",
    "GM: $194,333",
  ]);
  expect(bars(container, "chart-employer-paid")).toHaveLength(10);

  expect(bars(container, "chart-state").slice(0, 3)).toEqual([
    "MN: 11",
    "OH: 10",
    "IL: 9",
  ]);
});

test("the contrast fixture proves the surfaces follow the wire", async () => {
  const { container } = renderPage({ dashboardCharts: DASHBOARD_CHARTS_SCOPED });

  await screen.findByTestId("chart-settlement-status-legend");

  // **A category the scope does not contain is absent, not zero.** Jennifer
  // Park's book has no intake claim, so the donut has three legend rows. A
  // component laying out four fixed rows and looking each one up would draw an
  // empty fourth here and would pass the base fixture happily.
  expect(legend(container, "chart-settlement-status")).toEqual([
    ["Investigation:", "2"],
    ["Under Treatment:", "5"],
    ["Settled & Closed:", "20"],
  ]);
  expect(screen.getByTestId("chart-settlement-status-total")).toHaveTextContent("27");

  // The band moved with the document, and so did the caption. A legend holding
  // its own "≥ 65" would read the same here while the slice beside it changed.
  expect(legend(container, "chart-severity")).toEqual([
    ["High (≥ 70):", "8"],
    ["Medium:", "13"],
    ["Low:", "6"],
  ]);

  // A different scope, a different order, a different set of labels — from the
  // same components, which sort nothing.
  expect(bars(container, "chart-injury-type").slice(0, 2)).toEqual([
    "Fracture: 5",
    "Crushing: 4",
  ]);
  expect(bars(container, "chart-employer-paid")).toEqual([
    "Toyota: $303,937",
    "GM: $194,333",
    "3M: $134,667",
  ]);
});

test("the truncation caption appears only where the server truncated", async () => {
  const { unmount } = renderPage({ dashboardCharts: DASHBOARD_CHARTS });

  // Both numbers come off the wire — `limit` and `totalCategories` — so no
  // client holds the number 8, and the prototype's hardcoded "(top 8)" heading
  // cannot go stale against a series that was not cut.
  expect(await screen.findByTestId("chart-injury-type-truncation")).toHaveTextContent(
    "Showing 8 of 20 injury types.",
  );
  expect(screen.getByTestId("chart-state-truncation")).toHaveTextContent(
    "Showing 10 of 17 states.",
  );
  // Ten of ten employers: nothing was cut, so there is no caption to apologise
  // with.
  expect(screen.queryByTestId("chart-employer-paid-truncation")).not.toBeInTheDocument();

  unmount();
  vi.unstubAllGlobals();

  renderPage({ dashboardCharts: DASHBOARD_CHARTS_SCOPED });
  await screen.findByTestId("chart-injury-type-truncation");

  // Park's seven states are seven of seven. The same component, the same
  // conditional, the other branch.
  expect(screen.queryByTestId("chart-state-truncation")).not.toBeInTheDocument();
  expect(screen.getByTestId("chart-injury-type-truncation")).toHaveTextContent(
    "Showing 8 of 14 injury types.",
  );
});

test("the dashboard SLA tiles read exactly what the top-bar strip was sent", async () => {
  renderPage({ dashboardCharts: DASHBOARD_CHARTS });

  // AC 3 at the component level. The fixture's `sla` block *is* `SLA_STRIP`'s
  // body by reference, and both surfaces render it through one shared spec
  // module — so this asserts the two renderings agree rather than asserting two
  // hand-copied literals happen to.
  await waitFor(() =>
    expect(screen.getByTestId("chart-sla-pick-value")).toHaveTextContent("2.7d"),
  );
  expect(screen.getByTestId("chart-sla-approve-value")).toHaveTextContent("8.1d");
  expect(screen.getByTestId("chart-sla-settle-value")).toHaveTextContent("62d");
  expect(screen.getByTestId("chart-sla-rtw-rate-value")).toHaveTextContent("95%");

  // Precision is the server's, per metric: settle is published to whole days
  // and pick to a tenth, and a client formatting to a precision of its own
  // would eventually print a figure the verdict beside it was not decided on.
  const strip = SLA_STRIP.body;
  expect(strip.settle.decimals).toBe(0);
  expect(strip.pick.decimals).toBe(1);

  // The comparison the server made, written out — the operator flips on a miss,
  // which is how the prototype states it.
  expect(screen.getByTestId("chart-sla-pick-target")).toHaveTextContent("⚠ >1d");
  expect(screen.getByTestId("chart-sla-rtw-rate-target")).toHaveTextContent("✓ >80%");

  // Distinct testids from the top bar's `sla-pick`, because both groups are in
  // one DOM when the dashboard renders inside the shell — which is the whole
  // reason the shared spec carries a `slug` rather than a finished testid.
  expect(screen.queryByTestId("sla-pick-value")).not.toBeInTheDocument();
});

test("a missed return-to-work target is drawn in the error tone, not the warn tone", async () => {
  renderPage({ dashboardCharts: DASHBOARD_CHARTS_SCOPED });

  // The one tile whose miss is not merely a process delay: a missed RTW rate
  // means injured people are still off work. `SLA_STRIP`'s passing tile never
  // renders this branch, which is why the contrast fixture carries a 75%.
  await waitFor(() =>
    expect(screen.getByTestId("chart-sla-rtw-rate-value")).toHaveTextContent("75%"),
  );
  expect(screen.getByTestId("chart-sla-rtw-rate")).toHaveClass("bg-error");
  expect(screen.getByTestId("chart-sla-pick")).toHaveClass("bg-warn");
});

test("every chart surface actually renders an svg", async () => {
  const { container } = renderPage({ dashboardCharts: DASHBOARD_CHARTS });

  await screen.findByTestId("chart-settlement-status-legend");

  // The backstop for the jsdom size seam. Recharts' `ResponsiveContainer`
  // measures its container and renders `null` at a non-positive size, and jsdom
  // has no layout — so without the stub in `test/setup.ts` every chart on this
  // page would draw nothing while every assertion above still passed off the
  // legends and the `sr-only` lists. This is the test that would notice.
  for (const testId of SERIES_SURFACES) {
    expect(
      screen.getByTestId(testId).querySelector("svg"),
      `${testId} rendered no chart`,
    ).not.toBeNull();
  }
  // …and the SLA group deliberately has none: it is four styled cards, per the
  // prototype, because four figures on four different scales are not a plot.
  expect(container.querySelector("[data-testid='chart-sla'] svg")).toBeNull();
});

test("a request in flight draws skeletons at full height and claims no figure", () => {
  renderPage({ dashboardCharts: "pending" });

  for (const testId of SURFACES) {
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId(`${testId}-skeleton`)).toBeInTheDocument();
    // The box is the same height it will be once the data lands, so nothing
    // below it moves when it does (NFR-3).
    expect(screen.getByTestId(`${testId}-body`)).toHaveStyle({
      height: BODY_HEIGHT[testId],
    });
  }

  // Not one legend row, not one bar, not one zero — the reserved boxes hold the
  // layout open and nothing claims a value.
  expect(screen.queryByTestId("chart-settlement-status-legend")).not.toBeInTheDocument();
  expect(screen.queryByTestId("chart-injury-type-values")).not.toBeInTheDocument();
  expect(screen.queryByTestId("chart-sla-pick-value")).not.toBeInTheDocument();
});

test("a failed request states it inline on every surface and draws no chart", async () => {
  renderPage({
    // A non-retryable failure (a proxy 404 — the case `client.ts` calls out),
    // so this asserts the rendered branch rather than racing a 5xx's backoff.
    dashboardCharts: {
      status: 404,
      body: { title: "Not Found", status: 404, detail: "no route" },
    },
  });

  const alert = await screen.findByTestId("chart-settlement-status-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(alert).toHaveTextContent("Settlement status could not be loaded.");

  for (const testId of SURFACES) {
    expect(screen.getByTestId(`${testId}-error`)).toHaveAttribute("role", "alert");
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByTestId(`${testId}-skeleton`)).not.toBeInTheDocument();
  }

  // The alert *replaces* the chart rather than sitting above an empty one: an
  // empty chart under a warning reads as "this scope has nothing in it", which
  // is a different and much quieter lie than a stated failure. And no dialog —
  // errors on this dashboard are inline (NFR-3).
  expect(screen.queryByTestId("chart-settlement-status-legend")).not.toBeInTheDocument();
  expect(screen.queryByTestId("chart-injury-type-values")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a charts failure leaves the KPI cards and the handler table standing", async () => {
  renderPage({
    dashboardCharts: {
      status: 404,
      body: { title: "Not Found", status: 404, detail: "no route" },
    },
  });

  // The reason the three sections are three queries. One endpoint's outage must
  // not blank another's content, and nesting the charts inside the summary's
  // error branch would have made it do exactly that.
  await screen.findByTestId("chart-severity-error");
  expect(screen.getByTestId("kpi-total-claims-value")).toBeVisible();
  expect(screen.queryByTestId("portfolio-summary-error")).not.toBeInTheDocument();
  expect(await screen.findAllByTestId("handler-row")).not.toHaveLength(0);
  expect(screen.queryByTestId("handler-benchmarks-error")).not.toBeInTheDocument();
});

test("an empty scope renders every surface's empty state at full height", async () => {
  renderPage({ dashboardCharts: DASHBOARD_CHARTS_EMPTY });

  await screen.findByTestId("chart-settlement-status-empty");

  for (const testId of SERIES_SURFACES) {
    expect(screen.getByTestId(`${testId}-empty`)).toBeInTheDocument();
    // Full height in the empty state too: a surface that collapsed to its
    // sentence would reflow the whole grid the moment a scope turned out to be
    // empty, which is the layout collapse NFR-3 names.
    expect(screen.getByTestId(`${testId}-body`)).toHaveStyle({
      height: BODY_HEIGHT[testId],
    });
    // No zero rows. `items: []` is the contract, not seven categories at zero,
    // so there is nothing here to draw and nothing pretending there is.
    expect(screen.queryByTestId(`${testId}-legend-row`)).not.toBeInTheDocument();
  }

  // The SLA group is the one surface that still renders four of something: a
  // segment with nothing behind it is a `no_data` tile, not an absent one, and
  // an em dash is what Story 1.5 decided that looks like. Never a zero.
  expect(screen.getByTestId("chart-sla-pick-value")).toHaveTextContent("—");
  expect(screen.getByTestId("chart-sla-rtw-rate-value")).toHaveTextContent("—");
  expect(screen.getByTestId("chart-sla-pick")).toHaveClass("bg-surface-2");
  expect(screen.queryByTestId("chart-sla-empty")).not.toBeInTheDocument();

  // A cut that did not happen gets no caption.
  expect(screen.queryByTestId("chart-injury-type-truncation")).not.toBeInTheDocument();
});

test("a truncated surface is the same height before and after its data lands", async () => {
  // The reflow NFR-3 is actually about. The truncation caption is written only
  // in the data state, so rendering it with no reserved space made the injury
  // and state cards one line taller the moment `/dashboard/charts` resolved —
  // and it resolves *after* the KPI cards, so the second chart row grew under
  // the reader's cursor. Asserting the body height alone missed this entirely:
  // the caption sits outside the body, which is why the assertion here is on
  // the section's own class list, where the reserved row lives.
  const { unmount } = renderPage({ dashboardCharts: "pending" });

  const skeletonRow = screen
    .getByTestId("chart-injury-type")
    .lastElementChild?.className;
  expect(screen.queryByTestId("chart-injury-type-truncation")).toBeNull();

  unmount();
  vi.unstubAllGlobals();
  renderPage({ dashboardCharts: DASHBOARD_CHARTS });
  await screen.findByTestId("chart-injury-type-truncation");

  // Same element, same reserved height, caption or no caption.
  expect(screen.getByTestId("chart-injury-type").lastElementChild?.className).toBe(
    skeletonRow,
  );
});
