import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { ME_HANDLER, ME_SUPERVISOR, TOPBAR_STATS, stubApi } from "@/test/api-mock";

import { TopBar } from "./TopBar";

function renderTopBar(routes: Parameters<typeof stubApi>[0]) {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <TopBar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the role badge, name and role label come from /me (UX-DR2)", async () => {
  renderTopBar({ me: ME_SUPERVISOR, stats: TOPBAR_STATS });

  await waitFor(() => expect(screen.getByText("David Bline")).toBeVisible());
  expect(screen.getByTestId("role-badge")).toHaveTextContent("👔 Supervisor");
  expect(screen.getByTestId("user-chip")).toHaveTextContent("WC Supervisor");
  // Initials are the server's (`/me` derives them); the chip only renders them.
  expect(screen.getByTestId("user-avatar")).toHaveTextContent("DB");
});

test("a handler gets the handler badge and label, not the supervisor's", async () => {
  renderTopBar({ me: ME_HANDLER, stats: TOPBAR_STATS });

  await waitFor(() => expect(screen.getByText("Kaya Johnson")).toBeVisible());
  expect(screen.getByTestId("role-badge")).toHaveTextContent("📋 Handler");
  expect(screen.getByTestId("user-chip")).toHaveTextContent("Claims Handler");
  expect(screen.getByTestId("user-avatar")).toHaveTextContent("KJ");
});

test("an analyst gets the analyst badge and label", async () => {
  renderTopBar({
    me: { status: 200, body: { id: 10, name: "David Bline", role: "analyst", initials: "DB" } },
    stats: TOPBAR_STATS,
  });

  await waitFor(() => expect(screen.getByTestId("role-badge")).toHaveTextContent("📊 Analyst"));
  expect(screen.getByTestId("user-chip")).toHaveTextContent("Data Analyst");
});

test("the three tiles render the numbers the server sent, and nothing computed here", async () => {
  renderTopBar({ me: ME_SUPERVISOR, stats: TOPBAR_STATS });

  // Exact matches: "10" contains "0" and "15" contains "5", so a substring
  // assertion here would pass on the wrong tile's number.
  await waitFor(() => expect(screen.getByTestId("stat-caseload-value")).toHaveTextContent(/^27$/));
  expect(screen.getByTestId("stat-active-tx-value")).toHaveTextContent(/^5$/);
  expect(screen.getByTestId("stat-high-risk-value")).toHaveTextContent(/^10$/);

  expect(screen.getByTestId("stat-caseload")).toHaveTextContent(/Caseload/i);
  expect(screen.getByTestId("stat-active-tx")).toHaveTextContent(/Active Tx/i);
  expect(screen.getByTestId("stat-high-risk")).toHaveTextContent(/High Risk/i);
});

test("tiles show a skeleton while the stats request is in flight (NFR-3)", async () => {
  // A stats route that never settles: the loading state is the whole point,
  // so it must be observable rather than raced against a fast stub.
  renderTopBar({ me: ME_SUPERVISOR, stats: "pending" });

  await waitFor(() => expect(screen.getByText("David Bline")).toBeVisible());
  expect(screen.getAllByTestId("stat-skeleton")).toHaveLength(3);
  expect(screen.queryByTestId("stats-error")).not.toBeInTheDocument();
});

test("a failed stats request degrades honestly instead of showing a zero", async () => {
  renderTopBar({
    me: ME_SUPERVISOR,
    // A non-retryable failure (a proxy 404 — the case `client.ts` calls
    // out) so this asserts the rendered branch rather than racing the
    // retry backoff a 5xx would trigger.
    stats: { status: 404, body: { title: "Not Found", status: 404, detail: "no route" } },
  });

  await waitFor(() => expect(screen.getByTestId("stats-error")).toBeVisible());
  // "0 high-risk claims" and "we could not count your high-risk claims" are
  // very different statements to a handler triaging a caseload.
  for (const tile of ["stat-caseload", "stat-active-tx", "stat-high-risk"]) {
    expect(screen.getByTestId(`${tile}-value`)).toHaveTextContent(/^—$/);
  }
});

test("the glossary seam is present but disabled until Story 1.6 wires it", async () => {
  renderTopBar({ me: ME_SUPERVISOR, stats: TOPBAR_STATS });

  const glossary = await screen.findByRole("button", { name: /Glossary/ });
  expect(glossary).toBeDisabled();
  expect(glossary).toHaveAttribute("title", expect.stringMatching(/glossary/i));
});

test("the SLA strip slot is rendered empty for Story 1.5 to fill", async () => {
  renderTopBar({ me: ME_SUPERVISOR, stats: TOPBAR_STATS });

  const slot = await screen.findByTestId("sla-strip-slot");
  expect(slot).toBeEmptyDOMElement();
});

test("Switch survives an unresolved session (the bar renders before /me answers)", async () => {
  renderTopBar({ me: "pending", stats: "pending" });

  expect(await screen.findByRole("button", { name: /Switch/ })).toBeEnabled();
  expect(screen.getByText("LINEWORKER")).toBeVisible();
});
