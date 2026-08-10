import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { SLA_NO_DATA, SLA_STRIP, stubApi } from "@/test/api-mock";

import { SlaStrip } from "./SlaStrip";

/**
 * Story 1.5 AC 1/2 — the strip renders the server's verdicts and nothing
 * of its own.
 *
 * The line these tests police: the component receives `value`, `target`
 * and `status` and may style them; it may not decide them. Every fixture
 * therefore carries a status the component could not have derived by
 * accident, and the `no_data` cases assert an em dash where the prototype
 * would have printed 7.4d.
 */

function renderStrip(routes: Parameters<typeof stubApi>[0]) {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <SlaStrip />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the four tiles render the server's numbers at the prototype's precision", async () => {
  renderStrip({ sla: SLA_STRIP });

  // Exact matches throughout: "2.7" contains "2", and a substring
  // assertion would accept the wrong tile's figure.
  await waitFor(() => expect(screen.getByTestId("sla-pick-value")).toHaveTextContent(/^2\.7d$/));
  expect(screen.getByTestId("sla-approve-value")).toHaveTextContent(/^8\.1d$/);
  // Whole days and whole percent — no ".0" tails on the tiles the
  // prototype renders as integers.
  expect(screen.getByTestId("sla-settle-value")).toHaveTextContent(/^62d$/);
  expect(screen.getByTestId("sla-rtw-rate-value")).toHaveTextContent(/^95%$/);

  expect(screen.getByTestId("sla-strip")).toHaveTextContent("SLA");
  for (const label of ["Pick", "Approve", "Settle", "RTW Rate"]) {
    expect(screen.getByText(label)).toBeVisible();
  }
});

test("a whole-day average keeps its decimal place on the Pick tile", async () => {
  renderStrip({
    sla: {
      status: 200,
      body: {
        ...SLA_STRIP.body,
        pick: { value: 3, target: 1, direction: "below", decimals: 1, status: "warn" },
      },
    },
  });

  // The prototype's `toFixed(1)`: `3d` on a one-decimal tile reads as a
  // different measurement from `2.7d` beside it.
  await waitFor(() => expect(screen.getByTestId("sla-pick-value")).toHaveTextContent(/^3\.0d$/));
});

test("each tile states the target and the comparison the server made (AC 2)", async () => {
  renderStrip({ sla: SLA_STRIP });

  // A missed "below" target is annotated with the operator flipped —
  // `⚠ >5d` against a `<5d` target, exactly as the prototype writes it.
  await waitFor(() => expect(screen.getByTestId("sla-pick-target")).toHaveTextContent("⚠ >1d"));
  expect(screen.getByTestId("sla-approve-target")).toHaveTextContent("⚠ >5d");
  expect(screen.getByTestId("sla-settle-target")).toHaveTextContent("⚠ >30d");
  expect(screen.getByTestId("sla-rtw-rate-target")).toHaveTextContent("✓ >80%");
});

test("the target annotation follows the configured target, not a literal", async () => {
  renderStrip({
    sla: {
      status: 200,
      body: {
        ...SLA_STRIP.body,
        pick: { value: 2.7, target: 10, direction: "below", decimals: 1, status: "pass" },
      },
    },
  });

  await waitFor(() => expect(screen.getByTestId("sla-pick-target")).toHaveTextContent("✓ <10d"));
});

test("pass and warn are styled from the server's status, and a missed RTW rate is an error", async () => {
  renderStrip({
    sla: {
      status: 200,
      body: {
        pick: { value: 0.8, target: 1, direction: "below", decimals: 1, status: "pass" },
        approve: { value: 8.1, target: 5, direction: "below", decimals: 1, status: "warn" },
        settle: { value: 62, target: 30, direction: "below", decimals: 0, status: "warn" },
        rtwRate: { value: 61, target: 80, direction: "above", decimals: 0, status: "warn" },
      },
    },
  });

  await waitFor(() => expect(screen.getByTestId("sla-pick")).toHaveClass("bg-ok"));
  expect(screen.getByTestId("sla-approve")).toHaveClass("bg-warn");
  expect(screen.getByTestId("sla-settle")).toHaveClass("bg-warn");
  // The prototype's one asymmetry, kept deliberately: a missed return-to-work
  // rate is the tile that means people are still off work, and it is drawn
  // in the error colour rather than the warning one.
  expect(screen.getByTestId("sla-rtw-rate")).toHaveClass("bg-error");
});

test("a tile the server could not compute shows an em dash, never a number (NFR-3)", async () => {
  renderStrip({ sla: SLA_NO_DATA });

  await waitFor(() => expect(screen.getByTestId("sla-pick-value")).toHaveTextContent(/^—$/));
  for (const tile of ["sla-approve", "sla-settle", "sla-rtw-rate"]) {
    expect(screen.getByTestId(`${tile}-value`)).toHaveTextContent(/^—$/);
  }
  // Muted, not green and not red: "we have nothing to measure" is not a
  // verdict, and colouring it as one is how the prototype's 87% happened.
  expect(screen.getByTestId("sla-pick")).not.toHaveClass("bg-ok");
  expect(screen.getByTestId("sla-pick")).not.toHaveClass("bg-warn");
  expect(screen.getByTestId("sla-pick-target")).toHaveTextContent("<1d");
  expect(screen.getByTestId("sla-pick-target")).not.toHaveTextContent("✓");
});

test("a mixed strip computes some tiles and admits the rest", async () => {
  renderStrip({
    sla: {
      status: 200,
      body: {
        ...SLA_STRIP.body,
        settle: { value: null, target: 30, direction: "below", decimals: 0, status: "no_data" },
        rtwRate: { value: null, target: 80, direction: "above", decimals: 0, status: "no_data" },
      },
    },
  });

  // An open book: nothing settled yet, so two tiles have an answer and two
  // say they do not. The strip does not go dark, and the settled tiles do
  // not borrow the open ones' numbers.
  await waitFor(() => expect(screen.getByTestId("sla-pick-value")).toHaveTextContent(/^2\.7d$/));
  expect(screen.getByTestId("sla-settle-value")).toHaveTextContent(/^—$/);
  expect(screen.getByTestId("sla-rtw-rate-value")).toHaveTextContent(/^—$/);
});

test("tiles show skeletons while the strip is in flight", async () => {
  renderStrip({ sla: "pending" });

  await waitFor(() => expect(screen.getAllByTestId("sla-skeleton")).toHaveLength(4));
  expect(screen.queryByTestId("sla-error")).not.toBeInTheDocument();
});

test("a failed request degrades honestly instead of showing stale targets as results", async () => {
  renderStrip({
    // Non-retryable (a proxy 404) so this asserts the rendered branch
    // rather than racing the shared client's 5xx retry backoff.
    sla: {
      status: 404,
      body: { title: "Not Found", status: 404, detail: "no route" },
    },
  });

  await waitFor(() => expect(screen.getByTestId("sla-error")).toBeVisible());
  for (const tile of [
    "sla-pick",
    "sla-approve",
    "sla-settle",
    "sla-rtw-rate",
  ]) {
    expect(screen.getByTestId(`${tile}-value`)).toHaveTextContent(/^—$/);
  }
});

test("every tile carries the prototype's explanatory tooltip, reachable by keyboard (AC 2)", async () => {
  const user = userEvent.setup();
  renderStrip({ sla: SLA_STRIP });

  await waitFor(() => expect(screen.getByTestId("sla-pick-value")).toHaveTextContent(/^2\.7d$/));

  // Focus, not hover: a tooltip only a mouse can reach is not an
  // explanation for everyone who needs one.
  await user.tab();
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "Avg days from FROI to Handler Assignment — target <1 day",
  );
});

test.each([
  ["sla-approve", "Avg days from FROI to Claim Approval — target <5 days"],
  ["sla-settle", "Avg days from FROI to Settlement — target <30 days"],
  ["sla-rtw-rate", "Percentage of settled claims with successful RTW"],
])("%s explains itself on hover", async (tile, tooltip) => {
  const user = userEvent.setup();
  renderStrip({ sla: SLA_STRIP });

  await waitFor(() => expect(screen.getByTestId(tile)).toBeVisible());
  await user.hover(screen.getByTestId(tile));

  expect(await screen.findByRole("tooltip")).toHaveTextContent(tooltip);
});
