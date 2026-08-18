import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  DASHBOARD_SUMMARY,
  DASHBOARD_SUMMARY_RETUNED,
  stubApi,
} from "@/test/api-mock";

import { DashboardPage } from "./DashboardPage";

/**
 * Story 5.1 AC 1/2/4 — the cards render the server's figures and nothing of
 * their own.
 *
 * The line these tests police is the one `noDerivation.test.ts` polices
 * structurally: the page receives twelve counts and two thresholds, and it may
 * format and colour them; it may not produce one. So the fixtures carry
 * numbers the component could not have derived from anything on screen, the
 * captions are asserted against a *retuned* response as well as the seeded
 * one, and the failure case asserts the absence of zeros rather than the
 * presence of a message.
 *
 * Values are matched on the card's own `-value` test id with an anchored
 * regex: "100" contains "10", and a card-level substring check would pass on
 * the wrong figure.
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

/** The ten cards, in the prototype's order, against the seeded fixture. */
const EXPECTED_CARDS: readonly [string, string][] = [
  ["kpi-total-claims", "27"],
  ["kpi-under-treatment", "5"],
  ["kpi-settled-closed", "20"],
  ["kpi-high-risk", "10"],
  ["kpi-total-paid", "$632,937"],
  ["kpi-total-reserve", "$89,733"],
  ["kpi-fraud-flags", "3"],
  ["kpi-osha-recordable", "18"],
  ["kpi-litigation", "0"],
  ["kpi-surgery-required", "11"],
];

test("the ten cards render the server's figures in the prototype's order", async () => {
  const { container } = renderPage({ dashboardSummary: DASHBOARD_SUMMARY });

  await waitFor(() =>
    expect(screen.getByTestId("kpi-total-claims-value")).toBeVisible(),
  );

  for (const [testId, value] of EXPECTED_CARDS) {
    expect(screen.getByTestId(`${testId}-value`)).toHaveTextContent(
      new RegExp(`^${value.replace(/[$,]/g, "\\$&")}$`),
    );
  }

  // The order is the design contract, so it is asserted as an order rather
  // than as ten independent lookups — a page that rendered every card in the
  // wrong row would satisfy the loop above.
  const rendered = [...container.querySelectorAll("[data-testid^='kpi-']")]
    .map((node) => node.getAttribute("data-testid"))
    .filter((testId) => testId !== null && !testId.endsWith("-value"));
  expect(rendered).toEqual(EXPECTED_CARDS.map(([testId]) => testId));
});

test("money is formatted from cents and a zero count is still a zero", async () => {
  renderPage({ dashboardSummary: DASHBOARD_SUMMARY });

  // 63293700 cents is $632,937 — whole dollars, the prototype's `$m`.
  await waitFor(() =>
    expect(screen.getByTestId("kpi-total-paid-value")).toHaveTextContent(
      "$632,937",
    ),
  );
  // Jennifer Park's book genuinely contains no litigated claim. A *known* zero
  // is a zero; it is the unknown one that must render an em dash.
  expect(screen.getByTestId("kpi-litigation-value")).toHaveTextContent(/^0$/);
});

test("the header and dataset chip report the scoped counts, not literals", async () => {
  renderPage({ dashboardSummary: DASHBOARD_SUMMARY });

  expect(
    screen.getByRole("heading", {
      name: /Manufacturing WC — Portfolio Overview/,
    }),
  ).toBeVisible();

  // Awaited on the chip rather than on the heading: the title is static text
  // and is on screen before the request settles, so a `findByRole` on it would
  // return immediately and the chip's counts would still be absent.
  const chip = await screen.findByTestId("dataset-chip");
  expect(chip).toHaveTextContent("WC_Manufacturing_Claims_2026.xlsx");
  // All three from the response. The prototype hardcodes "10 employers · 15 US
  // plants", which is wrong for every scoped persona and for the full
  // portfolio alike.
  expect(chip).toHaveTextContent("27 claims");
  expect(chip).toHaveTextContent("3 employers");
  expect(chip).toHaveTextContent("9 plants");
});

test("the two rule captions quote the server's thresholds, not a constant", async () => {
  const { unmount } = renderPage({ dashboardSummary: DASHBOARD_SUMMARY });

  await waitFor(() =>
    expect(screen.getByTestId("kpi-high-risk")).toHaveTextContent(
      "Severity ≥ 65/100",
    ),
  );
  expect(screen.getByTestId("kpi-fraud-flags")).toHaveTextContent(
    "Score ≥ 55 — review needed",
  );

  unmount();
  vi.unstubAllGlobals();

  // The same page under a superseded rule document. A caption holding its own
  // constant would read "55" here too — and would go on doing so on the day
  // somebody retuned the document, with the count beside it already moved.
  renderPage({ dashboardSummary: DASHBOARD_SUMMARY_RETUNED });

  await waitFor(() =>
    expect(screen.getByTestId("kpi-fraud-flags")).toHaveTextContent(
      "Score ≥ 80 — review needed",
    ),
  );
  expect(screen.getByTestId("kpi-fraud-flags-value")).toHaveTextContent(/^1$/);
});

test("a request in flight draws skeletons and announces itself, never zeros", () => {
  renderPage({ dashboardSummary: "pending" });

  expect(screen.getByTestId("portfolio-dashboard")).toHaveAttribute(
    "aria-busy",
    "true",
  );
  expect(screen.getAllByTestId("kpi-skeleton")).toHaveLength(
    EXPECTED_CARDS.length,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Loading portfolio figures.",
  );

  // Not one figure on screen — the state this story exists to keep honest is
  // ten zeroed cards that read as a quiet portfolio.
  expect(
    screen.queryByTestId("kpi-total-claims-value"),
  ).not.toBeInTheDocument();
  // The chip goes with them: it carries counts, not decoration.
  expect(screen.queryByTestId("dataset-chip")).not.toBeInTheDocument();
});

test("a failed request states it inline and renders no figures at all", async () => {
  renderPage({
    // A non-retryable failure (a proxy 404 — the case `client.ts` calls out),
    // for `TopBar.test.tsx`'s reason: this asserts the rendered branch rather
    // than racing the retry backoff a 5xx would trigger.
    dashboardSummary: {
      status: 404,
      body: { title: "Not Found", status: 404, detail: "no route" },
    },
  });

  const alert = await screen.findByTestId("portfolio-summary-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(alert).toHaveTextContent("Portfolio figures could not be loaded.");

  // No cards and no skeletons: an error is not a loading state, and neither is
  // it a portfolio of zeros.
  expect(screen.queryByTestId("kpi-total-claims")).not.toBeInTheDocument();
  expect(screen.queryByTestId("kpi-skeleton")).not.toBeInTheDocument();
  expect(screen.getByTestId("portfolio-dashboard")).toHaveAttribute(
    "aria-busy",
    "false",
  );
});
