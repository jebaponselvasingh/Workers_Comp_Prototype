import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { queryKeys } from "@/api/queryKeys";
import { TOPBAR_STATS, UNAUTHENTICATED, stubApi } from "@/test/api-mock";

import App from "./App";

function renderAt(path: string) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SUPERVISOR = {
  status: 200,
  body: { id: 7, name: "David Bline", role: "supervisor", initials: "DB" },
};
const HANDLER = {
  status: 200,
  body: { id: 1, name: "Kaya Johnson", role: "handler", initials: "KJ" },
};
/**
 * The analyst — the persona Story 7.1 stopped being a supervisor clone.
 *
 * Deliberately the *same human* as the supervisor above, because that is what
 * the seed holds: David Bline is one person with two persona rows, and the role
 * is the only thing that differs. It is the sharpest form of the assertion —
 * whatever separates the two shells cannot be the name, the initials or the
 * caseload.
 */
const ANALYST = {
  status: 200,
  body: { id: 8, name: "David Bline", role: "analyst", initials: "DB" },
};

/** Kaya's real seed numbers — deliberately different from the supervisor's. */
const HANDLER_STATS = { status: 200, body: { caseload: 45, activeTx: 15, highRisk: 16 } };

/** The `me` query's status — used to pin down *when* a failure has landed. */
function meQueryStatus(client: QueryClient): string | undefined {
  return client.getQueryCache().find({ queryKey: queryKeys.me })?.state.status;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("an unauthenticated visit to a protected route lands on the login screen", async () => {
  stubApi({}); // /me answers 401
  renderAt("/dashboard");

  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "LINEWORKER" })).toBeInTheDocument(),
  );
  expect(screen.queryByRole("region", { name: /Portfolio dashboard/ })).not.toBeInTheDocument();
});

test("a supervisor session renders the dashboard shell", async () => {
  stubApi({ me: SUPERVISOR });
  renderAt("/dashboard");

  expect(await screen.findByRole("region", { name: /Portfolio dashboard/ })).toBeInTheDocument();
});

test("a handler session renders the workspace shell", async () => {
  stubApi({ me: HANDLER });
  renderAt("/workspace");

  expect(await screen.findByRole("region", { name: /Claim workspace/ })).toBeInTheDocument();
});

test("a role that does not belong on a shell is sent to its own", async () => {
  // Not an error page: the server said this caller is a handler, so the
  // handler shell is the correct destination for /dashboard.
  stubApi({ me: HANDLER });
  renderAt("/dashboard");

  expect(await screen.findByRole("region", { name: /Claim workspace/ })).toBeInTheDocument();
});

test("an analyst sees exactly four workspace destinations", async () => {
  // Epic 5's analyst read the supervisor's dashboard byte for byte. What this
  // asserts is the first thing that stopped being true: a navigation, with
  // exactly the sections that are built. **Exactly** rather than "at least" — an
  // unbuilt section's slot is left unbuilt rather than stubbed-broken, so a
  // fifth link appearing here is a promise the build cannot keep.
  //
  // Story 7.2 amended the expected list from two entries to three and Story 7.4
  // amends it to four, which is the change this test was written to require
  // rather than one it was surprised by. Segmentation (7.3) is deliberately
  // *not* on it: a filter is not a place, so it landed as a bar mounted on these
  // routes rather than as an entry beside them.
  stubApi({ me: ANALYST });
  renderAt("/dashboard");

  const nav = await screen.findByRole("navigation", { name: /Analyst workspace/ });
  expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
    "Portfolio",
    "Fraud",
    "Trends",
    "Financial",
  ]);
  expect(within(nav).getByTestId("nav-portfolio")).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByTestId("nav-fraud")).not.toHaveAttribute("aria-current");
  expect(within(nav).getByTestId("nav-trends")).not.toHaveAttribute("aria-current");
  expect(within(nav).getByTestId("nav-financial")).not.toHaveAttribute("aria-current");
});

test("the fraud route marks the fraud destination and not the portfolio one", async () => {
  stubApi({ me: ANALYST });
  renderAt("/dashboard/fraud");

  const nav = await screen.findByRole("navigation", { name: /Analyst workspace/ });
  expect(within(nav).getByTestId("nav-fraud")).toHaveAttribute("aria-current", "page");
  // `/dashboard` is a prefix of `/dashboard/fraud`, and marking both would be a
  // navigation that cannot say where you are.
  expect(within(nav).getByTestId("nav-portfolio")).not.toHaveAttribute("aria-current");
});

test("the trends route marks the trends destination and nothing else", async () => {
  // The partition's other half, and the single most likely defect in Story 7.2's
  // client slice: `owns` is a partition, and Portfolio's predicate is the
  // *complement of every section*. Adding a Trends entry without subtracting its
  // route from that complement leaves two entries marked current here — a
  // navigation saying you are in two places at once, which is a quieter failure
  // than the blank one 7.1 fixed.
  stubApi({ me: ANALYST });
  renderAt("/dashboard/trends");

  const nav = await screen.findByRole("navigation", { name: /Analyst workspace/ });
  expect(
    within(nav)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page")
      .map((link) => link.textContent),
  ).toEqual(["Trends"]);
});

test("the financial route marks the financial destination and nothing else", async () => {
  // The partition's third arm, and the single most likely defect in Story 7.4's
  // client slice: `owns` is a partition and Portfolio's predicate is the
  // *complement of every section*, so adding a Financial entry without adding
  // its route to `SECTION_ROUTES` leaves two entries marked current here — a
  // navigation saying you are in two places at once, which is the quieter of the
  // two failures this partition has already had.
  stubApi({ me: ANALYST });
  renderAt("/dashboard/financials");

  const nav = await screen.findByRole("navigation", { name: /Analyst workspace/ });
  expect(
    within(nav)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page")
      .map((link) => link.textContent),
  ).toEqual(["Financial"]);
});

test("an analyst reaches the financial workspace", async () => {
  stubApi({ me: ANALYST });
  renderAt("/dashboard/financials");

  expect(
    await screen.findByRole("region", { name: /Financial decomposition/ }),
  ).toBeInTheDocument();
});

test("a supervisor is bounced from the financial route to their own dashboard", async () => {
  // The route table's guard on the third analyst section — and the client half
  // of the server's 403 on both financial endpoints. A redirect rather than an
  // error page: the server said this caller is a supervisor, so their own
  // dashboard is the correct destination.
  stubApi({ me: SUPERVISOR });
  renderAt("/dashboard/financials");

  expect(await screen.findByRole("region", { name: /Portfolio dashboard/ })).toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: /Financial decomposition/ }),
  ).not.toBeInTheDocument();
});

test("an analyst reaches the trends workspace", async () => {
  stubApi({ me: ANALYST });
  renderAt("/dashboard/trends");

  expect(await screen.findByRole("region", { name: /Trend analytics/ })).toBeInTheDocument();
});

test("a supervisor is bounced from the trends route to their own dashboard", async () => {
  // The route table's guard, one route deeper — and the client half of the
  // server's 403. A redirect rather than an error page: the server said this
  // caller is a supervisor, so their own dashboard is the correct destination.
  stubApi({ me: SUPERVISOR });
  renderAt("/dashboard/trends");

  expect(await screen.findByRole("region", { name: /Portfolio dashboard/ })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: /Trend analytics/ })).not.toBeInTheDocument();
});

test("a drill route still has exactly one destination marked current", async () => {
  // The route the Fraud section's own charts and tables navigate to. The nav is
  // rendered on it — `DashboardShell` wraps every dashboard child — and it used
  // to mark **neither** entry: Portfolio matched exactly and Fraud matched by
  // prefix, and `/dashboard/claims` is neither. An analyst who clicked a band
  // segment landed on a page whose navigation had gone blank.
  stubApi({ me: ANALYST });
  renderAt("/dashboard/claims?filter[fraudBand]=high");

  const nav = await screen.findByRole("navigation", { name: /Analyst workspace/ });
  expect(
    within(nav)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page")
      .map((link) => link.textContent),
  ).toEqual(["Portfolio"]);
});

test("an analyst reaches the fraud workspace", async () => {
  stubApi({ me: ANALYST });
  renderAt("/dashboard/fraud");

  expect(await screen.findByRole("region", { name: /Fraud analytics/ })).toBeInTheDocument();
});

test("a supervisor sees no navigation and is bounced from the fraud route", async () => {
  // Both halves in one test, because they are one property: the supervisor's
  // console is what Epic 5 shipped, with nothing added to the shell and nothing
  // new reachable from the address bar. A redirect rather than an error page —
  // the server said this caller is a supervisor, so their own dashboard is the
  // correct destination, which is `RequireSession`'s existing behaviour applied
  // one route deeper.
  stubApi({ me: SUPERVISOR });
  renderAt("/dashboard/fraud");

  expect(await screen.findByRole("region", { name: /Portfolio dashboard/ })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: /Analyst workspace/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: /Fraud analytics/ })).not.toBeInTheDocument();
});

test("a handler is bounced from the fraud route to their own workspace", async () => {
  stubApi({ me: HANDLER });
  renderAt("/dashboard/fraud");

  expect(await screen.findByRole("region", { name: /Claim workspace/ })).toBeInTheDocument();
});

test("an unknown path falls back to the login screen", async () => {
  stubApi({});
  renderAt("/no-such-page");

  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "LINEWORKER" })).toBeInTheDocument(),
  );
});

test("a server failure on /me does NOT log a valid session out", async () => {
  // A 502 or a database outage is a failure to find out who you are, not a
  // statement that you are nobody. Bouncing it to the login screen would
  // silently sign out a working session with nothing explaining why.
  stubApi({ me: { status: 502, body: { detail: "upstream is down" } } });
  renderAt("/dashboard");

  const notice = await screen.findByRole("alert", {}, { timeout: 8000 });
  expect(notice).toHaveTextContent(/could not confirm your session/i);
  expect(screen.queryByRole("button", { name: /Enter Console/ })).not.toBeInTheDocument();
}, 15000);

test("a role this build has no shell for explains itself instead of looping", async () => {
  stubApi({ me: { status: 200, body: { id: 99, name: "Ada Lovelace", role: "auditor", initials: "AL" } } });
  renderAt("/dashboard");

  const notice = await screen.findByRole("alert");
  expect(notice).toHaveTextContent(/no workspace for the role/i);
});

test("a second persona never inherits the first one's cached numbers (code review)", async () => {
  // One QueryClient throughout — no reload. The session ends *without* a
  // logout (TTL expiry, database reset, admin revocation, another tab), so
  // nothing cleared the cache on the way out, and the guard bounces the
  // user to the login screen with the previous persona's server state
  // still sitting in it.
  stubApi({
    me: SUPERVISOR,
    stats: { status: 200, body: { caseload: 100, activeTx: 28, highRisk: 32 } },
  });
  const client = createQueryClient();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("stat-caseload-value")).toHaveTextContent("100"));

  // The session dies; the next revalidation 401s and the guard redirects.
  stubApi({ me: UNAUTHENTICATED, login: HANDLER, stats: HANDLER_STATS });
  await client.refetchQueries({ queryKey: queryKeys.me });
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "LINEWORKER" })).toBeInTheDocument(),
  );

  // A different persona signs in. The stats entry cached above is still
  // *fresh* (`staleTime: 30_000`), so without a clear on login the handler
  // would be shown the supervisor's 100 under their own name.
  fireEvent.click(screen.getByRole("radio", { name: /Claims Handler/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: /Enter Console/ })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Enter Console/ }));

  await waitFor(() => expect(screen.getByTestId("user-chip")).toHaveTextContent("Kaya Johnson"));
  await waitFor(() => expect(screen.getByTestId("stat-caseload-value")).toHaveTextContent("45"));
});

test("a failed background refetch keeps the shell instead of blanking it (code review)", async () => {
  stubApi({ me: SUPERVISOR, stats: TOPBAR_STATS });
  const client = createQueryClient();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("user-chip")).toHaveTextContent("David Bline"));

  // A revalidation that fails with a *non-401* — a proxy misroute, not a
  // sign-out. `useMe` keeps its data and sets `error`; the guard used to
  // read that as "could not confirm your session" and replace the whole
  // working screen. A 404 rather than a 502 so the error state settles
  // immediately: 5xx is retried twice with backoff, and the assertion
  // would otherwise be racing the retry timer rather than testing the
  // render branch.
  stubApi({ me: { status: 404, body: { title: "Not Found", status: 404 } }, stats: TOPBAR_STATS });
  await client.refetchQueries({ queryKey: queryKeys.me });

  await waitFor(() => expect(meQueryStatus(client)).toBe("error"));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByTestId("user-chip")).toHaveTextContent("David Bline");
});

test("an expired session shows the login form, not a redirect loop (code review)", async () => {
  stubApi({ me: SUPERVISOR, stats: TOPBAR_STATS });
  const client = createQueryClient();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("user-chip")).toHaveTextContent("David Bline"));

  // The session expires server-side and the next revalidation 401s.
  stubApi({ me: UNAUTHENTICATED, stats: TOPBAR_STATS });
  await client.refetchQueries({ queryKey: queryKeys.me });

  // `useMe` still holds the last good user alongside the 401. Treating that
  // as "already signed in" sent this screen back to the shell, whose guard
  // sent it back here — "Maximum update depth exceeded" and a blank page.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Enter Console/ })).toBeInTheDocument(),
  );
  expect(screen.getByRole("heading", { name: "LINEWORKER" })).toBeInTheDocument();
});
