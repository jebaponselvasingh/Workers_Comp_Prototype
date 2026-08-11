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

/**
 * A route that may answer differently depending on what was asked.
 *
 * The queue needs this and the other endpoints do not: `?filter=litigation`
 * and `?filter=all` are two different server answers, and "changing the
 * filter refetches rather than narrowing a cached list" (AD-1) is only
 * observable if the stub can tell the two requests apart.
 */
export type StubRouteFor = StubRoute | ((url: string) => StubRoute);

export interface StubRoutes {
  me?: StubRoute;
  personas?: StubRoute;
  login?: StubRoute;
  stats?: StubRoute;
  sla?: StubRoute;
  glossary?: StubRoute;
  claimsQueue?: StubRouteFor;
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

/**
 * Six of the 25 seeded glossary terms, verbatim from
 * `server/data/seed/glossary_terms.json` and in its order.
 *
 * A subset rather than the whole file: component tests check the *predicate
 * and the states*, not the contents of the reference data — that is
 * `test_glossary.py`'s job against the database and the e2e spec's against
 * the seed file. The six are chosen so one query can hit each searchable
 * field on its own: "havs" only an abbreviation, "maximum medical" only a
 * term, "audiogram" only a definition.
 */
export const GLOSSARY_TERMS = {
  status: 200,
  body: {
    items: [
      {
        abbreviation: "FNOL",
        term: "First Notice of Loss",
        definition:
          "Initial injury report to employer/carrier, starting notice-deadline clock and claims workflow.",
      },
      {
        abbreviation: "MMI",
        term: "Maximum Medical Improvement",
        definition:
          "Condition has stabilized and won't improve further. Triggers PPD rating and settlement discussions.",
      },
      {
        abbreviation: "HAVS",
        term: "Hand-Arm Vibration Syndrome",
        definition:
          "Occupational disease from prolonged vibrating tool use. Causes Raynaud's phenomenon (Vibration White Finger) in manufacturing workers.",
      },
      {
        abbreviation: "NIHL",
        term: "Noise-Induced Hearing Loss",
        definition:
          "Permanent hearing loss from manufacturing floor noise exposure — measurable by audiogram.",
      },
      // Both prototype quirks, so a test can see that neither was cleaned
      // up: a multi-word abbreviation, and a term that is its own.
      {
        abbreviation: "OSHA 300",
        term: "OSHA Recordkeeping Log",
        definition:
          "Federal Form 300 — mandatory recording of workplace injuries/illnesses for manufacturers with 10+ employees.",
      },
      {
        abbreviation: "Apportionment",
        term: "Apportionment",
        definition:
          "Dividing disability liability between current occupational injury and pre-existing/non-occupational conditions.",
      },
    ],
    nextCursor: null,
    total: 6,
  },
};

/**
 * A *successful* response carrying nothing — 0006 applied without 0007, a
 * `downgrade 0006`, a table someone emptied. Not a hypothetical: it is the
 * one way the panel can be handed no terms without any error to report, and
 * the branch it takes decides whether a handler is told the glossary is
 * unavailable or told their term does not exist.
 */
export const GLOSSARY_EMPTY = {
  status: 200,
  body: { items: [], nextCursor: null, total: 0 },
};

/**
 * Queue fixtures built from Kaya Johnson's real seeded book — her stage
 * counts are intake 3, investigation 1, treatment 15, settled 26.
 *
 * Component tests do not verify these numbers (that is
 * `test_claims_queue.py`'s job against the database, and the e2e spec's
 * against the seed file); they verify that whatever the server sends is
 * what the pane renders. Real numbers are used anyway so a reader cannot
 * mistake a stub for a computation — and because a card carrying invented
 * flags would let a test pass against a component that derived them.
 */
function stageGroup(
  items: unknown[],
  total = items.length,
  nextCursor: string | null = null,
) {
  return { items, nextCursor, total };
}

/** One fully-populated card: every badge on, marker on, high risk. */
export const LOUD_CARD = {
  claimId: "WC-20017",
  daysOpen: 140,
  risk: "high",
  workerName: "Marcus Delgado",
  injuryType: "Fall from Height",
  stage: "treatment",
  employerShortName: "Caterpillar",
  fraudFlag: true,
  litigationFlag: true,
  paymentDue: true,
  siuReview: true,
  rtwBlocked: true,
  priorityScore: 187.2,
  priorityMarker: true,
};

/** Its opposite: no badge, no marker, low risk — so a test can see absence. */
export const QUIET_CARD = {
  claimId: "WC-20044",
  daysOpen: 0,
  risk: "low",
  workerName: "Ana Ruiz",
  injuryType: "Laceration",
  stage: "treatment",
  employerShortName: "GE",
  fraudFlag: false,
  litigationFlag: false,
  paymentDue: false,
  siuReview: false,
  rtwBlocked: false,
  priorityScore: 4.5,
  priorityMarker: false,
};

export const INTAKE_CARD = {
  ...QUIET_CARD,
  claimId: "WC-20003",
  stage: "intake",
  workerName: "Priya Raman",
  injuryType: "Repetitive Strain",
  priorityScore: 31.4,
  priorityMarker: true,
};

/** Intake 1 · investigation 0 · treatment 2 · settled 0 — an empty stage
 * and a populated one in the same payload. */
export const CLAIM_QUEUE = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([INTAKE_CARD]),
      investigation: stageGroup([]),
      treatment: stageGroup([LOUD_CARD, QUIET_CARD]),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    unfilteredTotal: 3,
  },
};

/**
 * The same shape with nothing in it, and an **empty book behind it** —
 * `unfilteredTotal: 0` is what makes this the scope-empty payload rather
 * than the filter-empty one. The two are otherwise byte-identical, which is
 * exactly why the server has to say which it is: see `CLAIM_QUEUE_NO_MATCH`.
 */
export const CLAIM_QUEUE_EMPTY = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([]),
      investigation: stageGroup([]),
      treatment: stageGroup([]),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    unfilteredTotal: 0,
  },
};

/** No group matched, but the handler has 45 claims — a filter miss. */
export const CLAIM_QUEUE_NO_MATCH = {
  status: 200,
  body: { ...CLAIM_QUEUE_EMPTY.body, unfilteredTotal: 45 },
};

/** A treatment group with more claims than its page — "Show more" appears. */
export const CLAIM_QUEUE_PAGED = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([]),
      investigation: stageGroup([]),
      treatment: stageGroup([LOUD_CARD], 2, "cursor-page-2"),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    unfilteredTotal: 2,
  },
};

/** What `?cursor=cursor-page-2` answers for that group. */
export const CLAIM_QUEUE_PAGE_TWO = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([]),
      investigation: stageGroup([]),
      treatment: stageGroup([QUIET_CARD], 2),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    unfilteredTotal: 2,
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

function answerFor(route: StubRouteFor, url: string): Promise<Response> {
  return answer(typeof route === "function" ? route(url) : route);
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
      if (url.includes("/api/glossary")) {
        return answer(routes.glossary ?? GLOSSARY_TERMS);
      }
      if (url.includes("/api/claims/queue")) {
        // The whole URL, query string included, so a stub can branch on the
        // filter or the cursor — see `StubRouteFor`.
        return answerFor(routes.claimsQueue ?? CLAIM_QUEUE, url);
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
