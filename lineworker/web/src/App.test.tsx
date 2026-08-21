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

test("an analyst sees exactly two workspace destinations", async () => {
  // Epic 5's analyst read the supervisor's dashboard byte for byte. What this
  // asserts is the first thing that stopped being true: a navigation, with
  // exactly two entries. **Exactly** two rather than "at least" — Trends,
  // segmentation and financial decomposition are Stories 7.2-7.4, and the
  // instruction is to leave their slots unbuilt rather than stubbed-broken, so a
  // third link appearing here is a promise the build cannot keep.
  stubApi({ me: ANALYST });
  renderAt("/dashboard");

  const nav = await screen.findByRole("navigation", { name: /Analyst workspace/ });
  expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
    "Portfolio",
    "Fraud",
  ]);
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
