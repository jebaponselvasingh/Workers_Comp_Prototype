/**
 * Fetch stub for component tests.
 *
 * Deliberately a stub and not a mock of our own client: the tests exercise
 * the real generated client, the real problem+json translation, and the
 * real TanStack Query wiring — only the network is replaced. Browser-level
 * behaviour against the real API is the e2e suite's job (AD-15).
 */
import { vi } from "vitest";

/**
 * A stubbed route: a canned response, or "pending" for a request that never
 * settles. Loading states are real states — a stub that resolves
 * immediately makes them unobservable, and a test that cannot see the
 * skeleton cannot tell a skeleton from a zero.
 */
export type StubRoute = { status: number; body: unknown } | "pending";

export interface StubRoutes {
  me?: StubRoute;
  personas?: StubRoute;
  login?: StubRoute;
  stats?: StubRoute;
  sla?: StubRoute;
}

const problem = (status: number, detail: string) => ({
  type: "/problems/unauthenticated",
  title: "Unauthenticated",
  status,
  detail,
});

export const UNAUTHENTICATED = { status: 401, body: problem(401, "Sign in to continue.") };

/** Matches the first supervisor in SEEDED_PERSONAS below. */
export const DEFAULT_LOGIN = {
  status: 200,
  body: { id: 7, name: "David Bline", role: "supervisor", initials: "DB" },
};

export const ME_SUPERVISOR = DEFAULT_LOGIN;
export const ME_HANDLER = {
  status: 200,
  body: { id: 1, name: "Kaya Johnson", role: "handler", initials: "KJ" },
};

/**
 * Jennifer Park's real seed numbers. Component tests do not verify these —
 * that is `test_topbar_stats.py`'s job against the actual database — they
 * verify that whatever the server sends is what the tiles show. Using real
 * numbers anyway keeps a reader from mistaking a stub for a computation.
 */
export const TOPBAR_STATS = {
  status: 200,
  body: { caseload: 27, activeTx: 5, highRisk: 10 },
};

/**
 * Jennifer Park's real seed strip: three missed targets and one made one,
 * so a test can tell pass styling from warn styling without inventing a
 * shape the server never sends. Verdicts and precision are the server's —
 * the component under test must not recompute either.
 */
export const SLA_STRIP = {
  status: 200,
  body: {
    pick: { value: 2.7, target: 1, direction: "below", decimals: 1, status: "warn" },
    approve: { value: 8.1, target: 5, direction: "below", decimals: 1, status: "warn" },
    settle: { value: 62, target: 30, direction: "below", decimals: 0, status: "warn" },
    rtwRate: { value: 95, target: 80, direction: "above", decimals: 0, status: "pass" },
  },
};

/** A brand-new handler's empty book — every segment `no_data` (NFR-3). */
export const SLA_NO_DATA = {
  status: 200,
  body: {
    pick: { value: null, target: 1, direction: "below", decimals: 1, status: "no_data" },
    approve: { value: null, target: 5, direction: "below", decimals: 1, status: "no_data" },
    settle: { value: null, target: 30, direction: "below", decimals: 0, status: "no_data" },
    rtwRate: { value: null, target: 80, direction: "above", decimals: 0, status: "no_data" },
  },
};

export const SEEDED_PERSONAS = {
  status: 200,
  body: {
    items: [
      {
        id: 7,
        name: "David Bline",
        role: "supervisor",
        label: "David Bline — WC Supervisor (Full portfolio)",
      },
      {
        id: 8,
        name: "Jennifer Park",
        role: "supervisor",
        label: "Jennifer Park — WC Supervisor (3M/GM/Toyota)",
      },
      {
        id: 1,
        name: "Kaya Johnson",
        role: "handler",
        label: "Kaya Johnson — Handler (Caterpillar · GE)",
      },
      {
        id: 10,
        name: "David Bline",
        role: "analyst",
        label: "David Bline — WC Supervisor (Analyst view)",
      },
    ],
    nextCursor: null,
    total: 4,
  },
};

function respond(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      "content-type": status >= 400 ? "application/problem+json" : "application/json",
    },
  });
}

/** Never settles — the request stays in flight for the life of the test. */
const pending = (): Promise<Response> => new Promise<Response>(() => {});

function answer(route: StubRoute): Promise<Response> {
  return route === "pending" ? pending() : Promise.resolve(respond(route.status, route.body));
}

/** Install a fetch stub for `/api/*`; unmatched paths answer 404. */
export function stubApi(routes: StubRoutes): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : (input as Request).url;

      if (url.includes("/api/me")) {
        return answer(routes.me ?? UNAUTHENTICATED);
      }
      if (url.includes("/api/personas")) {
        return answer(routes.personas ?? SEEDED_PERSONAS);
      }
      if (url.includes("/api/stats/topbar")) {
        return answer(routes.stats ?? TOPBAR_STATS);
      }
      if (url.includes("/api/stats/sla")) {
        return answer(routes.sla ?? SLA_STRIP);
      }
      if (url.includes("/api/auth/logout")) {
        return respond(204, null);
      }
      if (url.includes("/api/auth/login")) {
        // The default carries a real role: `homeRouteFor` reads it, so an
        // empty body would silently route every login to /dashboard and a
        // future handler-login test would assert against the wrong shell.
        return answer(routes.login ?? DEFAULT_LOGIN);
      }
      return respond(404, problem(404, "Not Found"));
    }),
  );
}
