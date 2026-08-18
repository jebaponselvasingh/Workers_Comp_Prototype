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
  /** `GET /dashboard/summary` (Story 5.1) — the portfolio KPI cards. */
  dashboardSummary?: StubRoute;
  /** `GET /dashboard/handler-benchmarks` (Story 5.2) — the ranked handler table. */
  handlerBenchmarks?: StubRoute;
  /** `GET /dashboard/charts` (Story 5.3) — the seven analytics surfaces. */
  dashboardCharts?: StubRoute;
  /**
   * `GET /dashboard/priority-claims` (Story 5.4) — the top-30 worklist.
   *
   * A `StubRouteFor` rather than a `StubRoute`, unlike its three dashboard
   * siblings: this is the only cursor-paged surface on the page, so a "Show
   * more" test needs the second request — the one carrying `?cursor=…` — to
   * answer a different page from the first, and that is a decision about the
   * URL. `claimDetail`'s form, for `claimDetail`'s reason.
   */
  priorityClaims?: StubRouteFor;
  claimsQueue?: StubRouteFor;
  /**
   * `GET /claims/{id}` (Story 2.2). A function so a test can answer
   * differently per claim id — which is how "one claim opens and another
   * 404s" is written without two renders.
   */
  claimDetail?: StubRouteFor;
  /**
   * `GET /claims/{id}/documents/{id}/content` (Story 2.5). A function so a
   * test can answer the FROI sheet for one document id and the summary sheet
   * for another — which is how "the viewer renders what it was sent, not what
   * it inferred from the row it was opened by" is written.
   */
  documentSheet?: StubRouteFor;
  /**
   * `GET /claims/{id}/financials` (Story 3.3) — the Bills & Payments read
   * model. A function for `claimDetail`'s reason: the tab is opened from a
   * case file, so a test that renders two claims needs the two payloads to
   * differ.
   */
  claimFinancials?: StubRouteFor;
  /**
   * `POST /claims/{id}/payments/approvals` (Story 3.4).
   *
   * Matched **before** `claimFinancials` in the router below, because the
   * approval URL contains `/payments/` and not `/financials` — the two are
   * distinguishable by path, but only if the approval is tried first when a
   * test stubs both. Declared as a `StubRouteFor` so a test can answer 200 for
   * one row and 409 for another without re-rendering.
   */
  approvePayment?: StubRouteFor;
  /**
   * `GET /claims/{id}/actions` (Story 3.5) — the auto-generated checklist.
   *
   * A `StubRouteFor` for `claimDetail`'s reason, and matched **before** the
   * case file in the router below because `/api/claims/WC-1/actions` contains
   * `/api/claims/`: a stub that matched the case file first would answer the
   * card's request with a case file, and the card would render an empty list
   * with nothing anywhere saying why.
   */
  claimActions?: StubRouteFor;
  /** `POST /claims/{id}/assessment/approval` (Story 3.5). */
  approveAssessment?: StubRouteFor;
  /** `POST /claims/{id}/documents/{id}/review` (Story 3.5). */
  documentReview?: StubRouteFor;
  /** `POST /claims/{id}/osha-log` (Story 3.5). */
  oshaLog?: StubRouteFor;
  /**
   * `GET /claims-diary/meetings` (Story 4.1) — the handler's diary.
   *
   * Matched before the case file below, which is the right ordering for a
   * different reason than the one this comment used to give. It claimed
   * `/api/claims-diary/meetings` "contains the substring `/api/claims`" and
   * would therefore be swallowed — but the catch-all tests `/api/claims/`
   * **with a trailing slash**, and `/api/claims-diary/…` never contains that.
   * The rationale was repeated five times and was misinformation in all five:
   * anyone who acted on it — by tightening the catch-all, say, on the belief
   * that these routes depended on ordering to escape it — would have found the
   * ordering suddenly load-bearing where it had not been.
   *
   * The order still matters, and here is why: the two diary routes are matched
   * by *prefix* and `/api/claims-diary/meetings` is a prefix of nothing else,
   * while `/actions`, `/financials` and the rest below really are substrings of
   * case-file URLs. Keeping the diary first costs nothing and keeps every
   * `/api/claims…` branch in one block a reader can check in order.
   */
  meetings?: StubRouteFor;
  /** `POST /claims-diary/meetings` (Story 4.1) — matched before the list. */
  scheduleMeeting?: StubRouteFor;
  /** `PATCH /claims-diary/meetings/{id}` (Story 4.1). */
  completeMeeting?: StubRouteFor;
  /** `DELETE /claims-diary/meetings/{id}` (Story 4.1). */
  deleteMeeting?: StubRouteFor;
  /**
   * `GET /claims-diary/notes` (Story 4.2) — the handler's diary.
   *
   * Matched beside the meetings block, and *not* because it would otherwise be
   * swallowed by the case file — see that block on why the collision this used
   * to claim does not exist.
   */
  diaryNotes?: StubRouteFor;
  /** `POST /claims-diary/notes` (Story 4.2) — matched before the list. */
  addDiaryNote?: StubRouteFor;
  /**
   * `GET /claims-diary/email-templates` (Story 4.3) — the six quick templates.
   *
   * Matched before `/api/claims-diary/emails` below only for readability: the
   * two prefixes are disjoint (`email-templates` does not contain `emails`), so
   * unlike the pairs further down this is not load-bearing.
   */
  emailTemplates?: StubRouteFor;
  /**
   * `GET /claims-diary/email-templates/{key}/merged` (Story 4.3).
   *
   * Matched **before** the template list, because the merge URL contains it.
   * A `StubRouteFor` so a test can answer differently per template key — which
   * is how "the recipient set becomes exactly this template's" is written
   * without two renders.
   */
  mergedTemplate?: StubRouteFor;
  /**
   * `GET /claims-diary/meetings/{id}/email-draft` (Story 4.3) — convert-to-email.
   *
   * Matched **before the meetings block**, and this one really is load-bearing:
   * the draft's URL contains `/api/claims-diary/meetings`, so the list would
   * otherwise answer the composer's request with a page of meetings and the
   * modal would open blank with nothing saying why.
   */
  meetingEmailDraft?: StubRouteFor;
  /** `GET /claims-diary/emails` (Story 4.3) — the caller's sent log. */
  emails?: StubRouteFor;
  /** `POST /claims-diary/emails` (Story 4.3) — matched before the list. */
  sendEmail?: StubRouteFor;
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
 * Jennifer Park's real seed portfolio — the ten KPI figures, the chip's two
 * counts and the two thresholds the captions quote.
 *
 * `TOPBAR_STATS`' argument: component tests do not verify these numbers (that
 * is `test_portfolio_summary.py`'s job against the real database), they verify
 * that whatever the server sends is what the cards show. Using her real book
 * anyway keeps a reader from mistaking a stub for a computation — and keeps
 * every figure distinct, so a card wired to the wrong field fails rather than
 * coincidentally matching.
 *
 * `fraudScoreMin` is 55, not the SIU referral rule's 60: the card counts the
 * wider review population, and a fixture that used the other number would make
 * the one wiring mistake this story is exposed to invisible.
 */
export const DASHBOARD_SUMMARY = {
  status: 200,
  body: {
    totalClaims: 27,
    underTreatment: 5,
    settledClosed: 20,
    highRisk: 10,
    totalPaidCents: 63293700,
    totalReserveCents: 8973300,
    fraudFlagged: 3,
    oshaRecordable: 18,
    litigation: 0,
    surgeryRequired: 11,
    employerCount: 3,
    plantCount: 9,
    highRiskSeverityMin: 65,
    fraudScoreMin: 55,
    rulesVersion: 5,
  },
};

/**
 * The same portfolio under a superseded rule document: a raised fraud cut-off,
 * a smaller count, a higher version.
 *
 * Its whole purpose is to be asserted *against* `DASHBOARD_SUMMARY` — a card
 * caption that followed the response reads differently under the two, and one
 * holding a constant of its own reads the same. That is the difference a
 * fixture pair can show and a single fixture cannot.
 */
export const DASHBOARD_SUMMARY_RETUNED = {
  status: 200,
  body: {
    ...DASHBOARD_SUMMARY.body,
    fraudFlagged: 1,
    fraudScoreMin: 80,
    rulesVersion: 6,
  },
};


/**
 * The handler performance table over David Bline's real seeded book — six
 * handlers, their real ranking, their real composites and their real bars.
 *
 * `DASHBOARD_SUMMARY`'s argument, with one addition that matters more on a
 * table than on a card. Component tests do not verify these numbers (that is
 * `test_handler_benchmarks.py`'s job against the real database); they verify
 * that whatever the server sends is what the row shows. Using the real book
 * keeps a reader from mistaking a stub for a computation — and **every figure
 * in a row is distinct from every other figure in that row, and from the same
 * figure in every other row**, which a table needs and the seed does not
 * provide, so a cell wired to the wrong column or the wrong row cannot
 * coincidentally match.
 *
 * **Everything that diverges from the seed, named — all four of them.** A
 * fixture docstring that says figures were "nudged" while a band was flipped is
 * worse than no docstring, because the next reader trusts it:
 *
 * 1. `pendingApprovals` on **every** row. The seed's six, counted from
 *    `seed_data.json` over the statuses `priority_weights` calls pending
 *    (`initial`, `ch_assessment_process`) and listed in this fixture's rank
 *    order — Liam · Kaya · Marcus · Fatima · Sarah · Dante — are
 *    **1 · 2 · 1 · 0 · 1 · 3**, and they become 5 · 9 · 7 · 2 · 6 · 11. Three
 *    seeded handlers carry exactly one, one carries none, and 1 · 2 · 3 collide
 *    with the rank column, so the real figures cannot tell a mis-wired cell
 *    from a correct one. (An earlier draft of this list named five numbers for
 *    six handlers, dropping Sarah's 1 — the failure mode a mandated docstring
 *    has, and the reason this one is counted rather than recalled.)
 * 2. `caseCount` on **Fatima Al-Mansoori's** row (seed: 4 → 11). Her real case
 *    count is her real rank, which is precisely the coincidence this fixture
 *    exists to remove. Every other case count is the seed's — 7 · 38 · 19 · 8 ·
 *    24 — and the server tests assert the real one.
 * 3. `complexityScore` on **Fatima Al-Mansoori's** row (seed: 48 → 38), which
 *    4. **flips her `complexityBand` from `med` to `low`** — a band change, not
 *    a nudge. The seeded portfolio produces only `med` and `high`, so without
 *    this the `low` tone would render in no test at all. The flip is consistent
 *    with the published cut-points (38 is below `complexityMedMin: 40`), which
 *    is the property the retuned fixture below turns into an assertion.
 *
 * Everything else is the seed's: the six names, the rank order, the composites,
 * the bar percentages, the RTW rates, the deviations, the statuses, the
 * portfolio composite and the four thresholds. All three statuses appear
 * (On Track · Watch · Attention) and, thanks to (4), all three complexity bands.
 */
export const HANDLER_BENCHMARKS = {
  status: 200,
  body: {
    items: [
      {
        rank: 1,
        handlerName: "Liam O'Sullivan",
        caseCount: 7,
        cycleSpeedPct: 82,
        compositeDays: 64.1,
        rtwPct: 75,
        complexityScore: 64,
        complexityBand: "med",
        pendingApprovals: 5,
        deviationPct: -11,
        cycleStatus: "on_track",
      },
      {
        rank: 2,
        handlerName: "Kaya Johnson",
        caseCount: 38,
        cycleSpeedPct: 88,
        compositeDays: 68.8,
        rtwPct: 77,
        complexityScore: 62,
        complexityBand: "med",
        pendingApprovals: 9,
        deviationPct: -4,
        cycleStatus: "watch",
      },
      {
        rank: 3,
        handlerName: "Marcus Chen",
        caseCount: 19,
        cycleSpeedPct: 92,
        compositeDays: 72.0,
        rtwPct: 79,
        complexityScore: 60,
        complexityBand: "med",
        pendingApprovals: 7,
        deviationPct: 0,
        cycleStatus: "watch",
      },
      {
        rank: 4,
        handlerName: "Fatima Al-Mansoori",
        caseCount: 11,
        cycleSpeedPct: 93,
        compositeDays: 72.7,
        rtwPct: 100,
        complexityScore: 38,
        complexityBand: "low",
        pendingApprovals: 2,
        deviationPct: 1,
        cycleStatus: "watch",
      },
      {
        rank: 5,
        handlerName: "Sarah Williams",
        caseCount: 8,
        cycleSpeedPct: 95,
        compositeDays: 74.2,
        rtwPct: 67,
        complexityScore: 65,
        complexityBand: "high",
        pendingApprovals: 6,
        deviationPct: 3,
        cycleStatus: "watch",
      },
      {
        rank: 6,
        handlerName: "Dante Reyes",
        caseCount: 24,
        cycleSpeedPct: 100,
        compositeDays: 78.0,
        rtwPct: 69,
        complexityScore: 57,
        complexityBand: "med",
        pendingApprovals: 11,
        deviationPct: 8,
        cycleStatus: "attention",
      },
    ],
    portfolioCompositeDays: 71.9,
    leader: "Liam O'Sullivan",
    laggard: "Dante Reyes",
    onTrackDeviationPctMax: -8,
    attentionDeviationPctMin: 8,
    complexityHighMin: 65,
    complexityMedMin: 40,
    rulesVersion: 1,
  },
};

/**
 * The same six handlers under a different scope and a superseded rule document
 * — a different ranking, different chips, different bands.
 *
 * Its whole purpose is to be asserted *against* `HANDLER_BENCHMARKS`. A table
 * that rendered the server's order reads differently under the two; one that
 * re-sorted on any figure of its own would read the same, and so would a
 * footnote holding constants instead of quoting the response. That is the
 * difference a fixture pair can show and a single fixture cannot.
 *
 * **Written out in full rather than spread-and-mapped from the fixture above,
 * because the point of it is internal coherence.** Every row here is reachable
 * from the cut-points the same object publishes:
 *
 * - The complexity cut-points move to `complexityMedMin: 45` /
 *   `complexityHighMin: 60`, which flips **three** complexity chips against the
 *   fixture above — Liam (64), Kaya (62) and Marcus (60) all go `med` → `high`
 *   — while Fatima (38 → `low`) and Sarah (65 → `high`) stay put and Dante
 *   (57 → `med`) stays too. A contrast fixture that moved only status chips
 *   would leave AC 2's complexity half with no component-level coverage at all.
 * - The deviation bands narrow to `-3` / `3`. Under them Sarah's -4 is On Track
 *   and Kaya's +4 is Attention, where the same two handlers wear Watch chips in
 *   the fixture above (-4 and +3 against bands of -8 / +8). Every chip here is
 *   the band its own `deviationPct` falls in, so the footnote and the chips
 *   agree — which is the thing a reader checks and the first version of this
 *   fixture got wrong.
 * - **It is a genuinely different scope, not the same rows reversed.** Every
 *   composite is new (48.6 · 52.9 · 55.0 · 55.6 · 57.2 · 61.4 against the
 *   fixture above's 64.1 · 68.8 · 72.0 · 72.7 · 74.2 · 78.0), the portfolio they
 *   are measured against is new (55.0d against 71.9d), and the case counts and
 *   pending-approval counts are a different desk's. So the bar denominator, the
 *   deviation denominator and the ranking are all exercised on figures the
 *   other fixture never produces — which is the point of a contrast fixture
 *   whose job is proving the page follows the wire. An earlier version carried
 *   the *identical* six composites, bars, deviations and portfolio composite
 *   with the names reversed, and claimed in this sentence to be a different
 *   scope; it was six labels moved around one set of numbers.
 * - Every derived figure is recomputed from those composites rather than
 *   carried over: `deviationPct` is `(composite − 55.0) / 55.0` as a whole
 *   percentage rounded half away from zero, and `cycleSpeedPct` is
 *   `composite / 61.4` the same way. A reader can check any row with a
 *   calculator, which is what "internally reachable" has to mean.
 *
 * Liam's row also carries `rtwPct: null` — the handler who has settled nothing
 * — which is the one nullable field reachable on a healthy scope and the only
 * place the em dash is drawn.
 */
export const HANDLER_BENCHMARKS_RERANKED = {
  status: 200,
  body: {
    items: [
      {
        rank: 1,
        handlerName: "Dante Reyes",
        caseCount: 12,
        cycleSpeedPct: 79,
        compositeDays: 48.6,
        rtwPct: 69,
        complexityScore: 57,
        complexityBand: "med",
        pendingApprovals: 4,
        deviationPct: -12,
        cycleStatus: "on_track",
      },
      {
        rank: 2,
        handlerName: "Sarah Williams",
        caseCount: 5,
        cycleSpeedPct: 86,
        compositeDays: 52.9,
        rtwPct: 67,
        complexityScore: 65,
        complexityBand: "high",
        pendingApprovals: 1,
        deviationPct: -4,
        cycleStatus: "on_track",
      },
      {
        rank: 3,
        handlerName: "Fatima Al-Mansoori",
        caseCount: 9,
        cycleSpeedPct: 90,
        compositeDays: 55.0,
        rtwPct: 100,
        complexityScore: 38,
        complexityBand: "low",
        pendingApprovals: 6,
        deviationPct: 0,
        cycleStatus: "watch",
      },
      {
        rank: 4,
        handlerName: "Marcus Chen",
        caseCount: 14,
        cycleSpeedPct: 91,
        compositeDays: 55.6,
        rtwPct: 79,
        complexityScore: 60,
        complexityBand: "high",
        pendingApprovals: 8,
        deviationPct: 1,
        cycleStatus: "watch",
      },
      {
        rank: 5,
        handlerName: "Kaya Johnson",
        caseCount: 21,
        cycleSpeedPct: 93,
        compositeDays: 57.2,
        rtwPct: 77,
        complexityScore: 62,
        complexityBand: "high",
        pendingApprovals: 10,
        deviationPct: 4,
        cycleStatus: "attention",
      },
      {
        rank: 6,
        handlerName: "Liam O'Sullivan",
        caseCount: 3,
        cycleSpeedPct: 100,
        compositeDays: 61.4,
        rtwPct: null,
        complexityScore: 64,
        complexityBand: "high",
        pendingApprovals: 2,
        deviationPct: 12,
        cycleStatus: "attention",
      },
    ],
    portfolioCompositeDays: 55.0,
    leader: "Dante Reyes",
    laggard: "Liam O'Sullivan",
    onTrackDeviationPctMax: -3,
    attentionDeviationPctMin: 3,
    complexityHighMin: 60,
    complexityMedMin: 45,
    rulesVersion: 2,
  },
};

/**
 * A scope whose *portfolio* has no data for one of the three cycle-time
 * segments — the state in which nothing can be ranked.
 *
 * The only response the server can send with `rank: null` on a row, and the one
 * fixture that makes the `#` column's contract testable. Every other fixture
 * here is a healthy scope, where the server ranks every row and `items[i].rank`
 * is therefore `i + 1` — so on every one of them a component rendering
 * `{index + 1}` in that column is indistinguishable from one rendering
 * `row.rank`, which is exactly the substitution `noDerivation.test.ts` names as
 * the most likely way to get this table wrong and explicitly delegates to a
 * component test. Here the ranks are `null`, the cells read the em dash, and the
 * index version renders "1" through "3".
 *
 * Coherent with `benchmarks.py`'s contract rather than invented: `rank`,
 * `compositeDays`, `cycleSpeedPct`, `deviationPct` and `cycleStatus` are null
 * *together* — they are one condition on the wire — while `caseCount`,
 * `rtwPct`, the complexity pair and `pendingApprovals` are unaffected, because
 * none of them is derived from a composite. `portfolioCompositeDays` is null for
 * the same reason the rows are, and `leader`/`laggard` are null because the
 * server picks them from the rankable rows and there are none.
 *
 * Three rows rather than six, in **name order** — which is the order the server
 * falls back to when there is no composite to sort on, and another thing an
 * index-based `#` column would silently number as though it were a ranking.
 */
export const HANDLER_BENCHMARKS_UNRANKED = {
  status: 200,
  body: {
    ...HANDLER_BENCHMARKS.body,
    items: [
      {
        rank: null,
        handlerName: "Dante Reyes",
        caseCount: 24,
        cycleSpeedPct: null,
        compositeDays: null,
        rtwPct: 69,
        complexityScore: 57,
        complexityBand: "med",
        pendingApprovals: 11,
        deviationPct: null,
        cycleStatus: null,
      },
      {
        rank: null,
        handlerName: "Fatima Al-Mansoori",
        caseCount: 11,
        cycleSpeedPct: null,
        compositeDays: null,
        rtwPct: 100,
        complexityScore: 38,
        complexityBand: "low",
        pendingApprovals: 2,
        deviationPct: null,
        cycleStatus: null,
      },
      {
        rank: null,
        handlerName: "Marcus Chen",
        caseCount: 19,
        cycleSpeedPct: null,
        compositeDays: null,
        rtwPct: 79,
        complexityScore: 60,
        complexityBand: "med",
        pendingApprovals: 7,
        deviationPct: null,
        cycleStatus: null,
      },
    ],
    portfolioCompositeDays: null,
    leader: null,
    laggard: null,
  },
};

/**
 * A scope with exactly one ranked handler — the callout's other branch.
 *
 * `leader` and `laggard` name the same person, which is what the server sends
 * whenever the rankable set has one member, and the sentence the table draws
 * for it says so rather than claiming a headcount it cannot know. No fixture
 * covered this branch before, so the single-handler wording was rendered by
 * nothing and asserted by nothing.
 *
 * The one row is its own portfolio, so its composite *is* the portfolio
 * composite, its deviation is 0 and its bar is full — the only self-consistent
 * answer for a desk of one, and the numbers a reader would check first.
 */
export const HANDLER_BENCHMARKS_SINGLE = {
  status: 200,
  body: {
    ...HANDLER_BENCHMARKS.body,
    items: [
      {
        rank: 1,
        handlerName: "Fatima Al-Mansoori",
        caseCount: 4,
        cycleSpeedPct: 100,
        compositeDays: 66.5,
        rtwPct: 100,
        complexityScore: 48,
        complexityBand: "med",
        pendingApprovals: 0,
        deviationPct: 0,
        cycleStatus: "watch",
      },
    ],
    portfolioCompositeDays: 66.5,
    leader: "Fatima Al-Mansoori",
    laggard: "Fatima Al-Mansoori",
  },
};

/**
 * A scope with no handlers — the table's defined empty state.
 *
 * The four thresholds and `rulesVersion` are spread through from the fixture
 * above rather than nulled with everything else, because that is what the
 * server sends: "nothing in this book" says nothing about which rules were in
 * force. The **thresholds** are what the band footnote quotes, and they are the
 * only statement of the rules on a screen with no rows to explain — a fixture
 * that emptied them too would have let the component drop the footnote and
 * still pass. `rulesVersion` rides along unrendered, here as everywhere else on
 * this table (and as `/dashboard/summary`'s does): it makes a *stored* response
 * self-describing, and no cell shows it.
 */
export const HANDLER_BENCHMARKS_EMPTY = {
  status: 200,
  body: {
    ...HANDLER_BENCHMARKS.body,
    items: [],
    portfolioCompositeDays: null,
    leader: null,
    laggard: null,
  },
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
 * The seven analytics surfaces over David Bline's real seeded portfolio
 * (Story 5.3).
 *
 * `DASHBOARD_SUMMARY`'s argument: component tests do not verify these numbers
 * (that is `test_portfolio_charts.py`'s job against the real database), they
 * verify that whatever the server sends is what each chart draws. Using the
 * real book keeps a reader from mistaking a stub for a computation.
 *
 * **`sla` is `SLA_STRIP.body` by reference, and that is load-bearing.** AC 3
 * says the dashboard tiles and the top-bar strip show one value; the fixture
 * says so too, so `the dashboard tiles read what the top bar reads` is a test
 * about the two components rather than about two hand-copied literals that
 * happen to agree. A future edit to the strip fixture moves both surfaces.
 *
 * **Everything that diverges from the seed, named.** A fixture docstring that
 * claims "real numbers" while a series was rewritten is worse than no
 * docstring, because the next reader trusts it:
 *
 * 1. `byRecoveryStatus` — the seed is 36 · 17 · 47 and this is **36 · 18 · 46**.
 *    The real 17 is also the severity donut's `low` count, and both are the
 *    *third item of their series*, on the same row of the grid — the single
 *    strongest coincidence available for a mis-wired series to hide behind.
 *    `returned_and_fully_recovered` drops by one so the three still sum to the
 *    scope's hundred.
 * 2. `byInjuryType` — the seed is 13 · 11 · 9 · 7 · 7 · 7 · 5 · 5 and this is
 *    **13 · 12 · 11 · 10 · 9 · 8 · 7 · 5**. Three real bars are tied at seven
 *    and two at five, so a chart that rendered bars 4-6 in the wrong order
 *    would be indistinguishable from a correct one. The eight labels and their
 *    order are the seed's.
 * 3. `byState` — the seed is 11 · 11 · 10 · 8 · 8 · 8 · 7 · 6 · 5 · 5 and this
 *    is **11 · 10 · 9 · 8 · 7 · 6 · 5 · 4 · 3 · 1**, for (2)'s reason: the real
 *    series has three separate ties, including one across the top two rows. The
 *    ten labels and their order are the seed's.
 *
 * **How far the "every figure distinct" rule actually reaches, stated rather
 * than over-claimed.** Every count is distinct *within* its own series, and the
 * ten counts across the two donuts and the recovery bars — the three surfaces
 * that share a row and a shape — are pairwise distinct. Full distinctness
 * across all thirty-one counts is arithmetically impossible here: eighteen
 * distinct positive integers whose two subset sums must each stay under a
 * hundred already consume 1-18, and the row above claims four of those. So the
 * injury and state charts share some integers, and what separates them is the
 * labels — every assertion in `PortfolioCharts.test.tsx` reads label/value
 * *pairs* off each surface's list, so a swapped series fails on the labels
 * before the counts are compared at all.
 *
 * Everything else is the seed's: the stage and severity donuts, all ten
 * employer totals and their order, every label, every `totalCategories`, both
 * truncation flags, both limits and the two published bands.
 */
export const DASHBOARD_CHARTS = {
  status: 200,
  body: {
    byStage: {
      items: [
        { key: "intake", count: 6 },
        { key: "investigation", count: 4 },
        { key: "treatment", count: 28 },
        { key: "settled", count: 62 },
      ],
      total: 100,
      totalCategories: 4,
      truncated: false,
      limit: null,
    },
    bySeverity: {
      items: [
        { key: "high", count: 32 },
        { key: "med", count: 51 },
        { key: "low", count: 17 },
      ],
      total: 100,
      totalCategories: 3,
      truncated: false,
      limit: null,
    },
    byRecoveryStatus: {
      items: [
        { key: "under_treatment", count: 36 },
        { key: "returned_and_under_therapy", count: 18 },
        { key: "returned_and_fully_recovered", count: 46 },
      ],
      total: 100,
      totalCategories: 3,
      truncated: false,
      limit: null,
    },
    byInjuryType: {
      items: [
        { label: "Amputation", count: 13 },
        { label: "Fracture", count: 12 },
        { label: "Carpal Tunnel Syndrome", count: 11 },
        { label: "Crushing", count: 10 },
        { label: "Fall from Height", count: 9 },
        { label: "Vibration White Finger (HAVS)", count: 8 },
        { label: "Eye Injury", count: 7 },
        { label: "Laceration", count: 5 },
      ],
      total: 100,
      totalCategories: 20,
      truncated: true,
      limit: 8,
    },
    byEmployer: {
      items: [
        { employerId: 3, label: "Caterpillar", paidCents: 43502900 },
        { employerId: 9, label: "Toyota", paidCents: 30393700 },
        { employerId: 5, label: "GM", paidCents: 19433300 },
        { employerId: 4, label: "GE", paidCents: 17024800 },
        { employerId: 7, label: "John Deere", paidCents: 14950400 },
        { employerId: 1, label: "3M", paidCents: 13466700 },
        { employerId: 10, label: "Whirlpool", paidCents: 12319200 },
        { employerId: 2, label: "Boeing", paidCents: 8154800 },
        { employerId: 6, label: "Honeywell", paidCents: 4663400 },
        { employerId: 8, label: "Lockheed", paidCents: 3140500 },
      ],
      total: 167049700,
      totalCategories: 10,
      truncated: false,
      limit: null,
    },
    byState: {
      items: [
        { label: "MN", count: 11 },
        { label: "OH", count: 10 },
        { label: "IL", count: 9 },
        { label: "MA", count: 8 },
        { label: "TX", count: 7 },
        { label: "WA", count: 6 },
        { label: "IA", count: 5 },
        { label: "MI", count: 4 },
        { label: "AL", count: 3 },
        { label: "KY", count: 1 },
      ],
      total: 100,
      totalCategories: 17,
      truncated: true,
      limit: 10,
    },
    sla: SLA_STRIP.body,
    highRiskSeverityMin: 65,
    medRiskSeverityMin: 35,
    rulesVersion: 5,
  },
};

/**
 * A genuinely different scope: Jennifer Park's real 27-claim book, under a
 * **superseded** rule document.
 *
 * Its whole purpose is to be asserted *against* `DASHBOARD_CHARTS`. A component
 * that renders what it was sent reads differently under the two; one that
 * sorted, banded or captioned from constants of its own reads the same. Three
 * properties only this fixture can exercise:
 *
 * - **An absent category.** Park's book contains no `intake` claim, so
 *   `byStage` has *three* items rather than four. A component laying out four
 *   fixed rows and looking each up would draw an empty fourth here.
 * - **A ranked series that was not cut.** Her seven states are seven of seven,
 *   so `truncated` is false and the truncation caption must not appear — while
 *   her fourteen injury types still cut to eight and it must. One fixture,
 *   both sides of the same conditional.
 * - **A moved band.** `highRiskSeverityMin` is 70 against the base fixture's
 *   65, and the severity slices are what her book actually splits into at that
 *   cut-off (8 · 13 · 6, against 10 · 15 · 2 under the seeded document) — so
 *   the legend caption and the slice move together, which is what a caption
 *   holding its own constant would fail to do.
 *
 * The one divergence from the seed: `byInjuryType`'s counts are **8 · 7 · 6 ·
 * 5 · 4 · 3 · 2 · 1** where her book has 5 · 4 · 3 · 2 · 2 · 2 · 2 · 1 — four
 * of her eight bars are tied at two, and a fixture cannot tell a mis-ordered
 * render from a correct one through a tie. Labels and order are hers.
 * `byState` keeps its real ties, deliberately: nothing about it is being
 * ordered by this fixture, and its job is `truncated: false`.
 *
 * `sla` is her real strip, and the RTW tile is the reason it is spelled out
 * rather than shared: at 75% against an 80% target it *misses*, and RTW is the
 * one metric whose miss is drawn in the error tone — a colour `SLA_STRIP`'s
 * passing tile never renders.
 */
export const DASHBOARD_CHARTS_SCOPED = {
  status: 200,
  body: {
    byStage: {
      items: [
        { key: "investigation", count: 2 },
        { key: "treatment", count: 5 },
        { key: "settled", count: 20 },
      ],
      total: 27,
      totalCategories: 3,
      truncated: false,
      limit: null,
    },
    bySeverity: {
      items: [
        { key: "high", count: 8 },
        { key: "med", count: 13 },
        { key: "low", count: 6 },
      ],
      total: 27,
      totalCategories: 3,
      truncated: false,
      limit: null,
    },
    byRecoveryStatus: {
      items: [
        { key: "under_treatment", count: 7 },
        { key: "returned_and_under_therapy", count: 5 },
        { key: "returned_and_fully_recovered", count: 15 },
      ],
      total: 27,
      totalCategories: 3,
      truncated: false,
      limit: null,
    },
    byInjuryType: {
      items: [
        // Jennifer Park's real book, recounted from `seed_data.json`: 27 claims
        // across 14 injury types, of which the top eight are shown. The eight
        // counts sum to 21, leaving 6 in the six categories the cut dropped —
        // the invariant `test_every_series_accounts_for_every_claim_in_the_
        // caseload` enforces server-side, and one no fixture may violate.
        //
        // An earlier draft ran 8,7,6,5,4,3,2,1 to make every figure distinct.
        // That sums to 36 against a total of 27, which is a payload the server
        // cannot produce: any later work reading `total` for a share or an
        // "other" remainder would have been developed against an impossibility
        // and would ship computing over 100%. Distinctness is worth having, but
        // not at the price of a fixture that lies about arithmetic — the labels
        // do the disambiguating where the counts have to repeat.
        { label: "Fracture", count: 5 },
        { label: "Crushing", count: 4 },
        { label: "Carpal Tunnel Syndrome", count: 3 },
        { label: "Amputation", count: 2 },
        { label: "Burn — Thermal", count: 2 },
        { label: "Robotic Cell Injury", count: 2 },
        { label: "Strain or Tear — Shoulder", count: 2 },
        { label: "Burn — Arc Flash", count: 1 },
      ],
      total: 27,
      totalCategories: 14,
      truncated: true,
      limit: 8,
    },
    byEmployer: {
      items: [
        { employerId: 9, label: "Toyota", paidCents: 30393700 },
        { employerId: 5, label: "GM", paidCents: 19433300 },
        { employerId: 1, label: "3M", paidCents: 13466700 },
      ],
      total: 63293700,
      totalCategories: 3,
      truncated: false,
      limit: null,
    },
    byState: {
      items: [
        { label: "AL", count: 5 },
        { label: "KY", count: 5 },
        { label: "MN", count: 4 },
        { label: "SD", count: 4 },
        { label: "IN", count: 3 },
        { label: "MI", count: 3 },
        { label: "TX", count: 3 },
      ],
      total: 27,
      totalCategories: 7,
      truncated: false,
      limit: 10,
    },
    sla: {
      pick: { value: 2.7, target: 1, direction: "below", decimals: 1, status: "warn" },
      approve: { value: 8.1, target: 5, direction: "below", decimals: 1, status: "warn" },
      settle: { value: 62, target: 30, direction: "below", decimals: 0, status: "warn" },
      rtwRate: { value: 75, target: 80, direction: "above", decimals: 0, status: "warn" },
    },
    highRiskSeverityMin: 70,
    medRiskSeverityMin: 40,
    rulesVersion: 6,
  },
};

/**
 * A persona with no claim in scope — the NFR-3 zero state for all seven
 * surfaces at once.
 *
 * Every counted series is `items: []` with `total: 0`, **not** a set of zero
 * rows: that is the omission contract taken to its limit, and it is the state
 * the fixed-height empty branch exists for. The SLA tiles are the one surface
 * that still renders four of something — `SLA_NO_DATA`'s em dashes — because a
 * segment with nothing behind it is a `no_data` tile rather than an absent one,
 * which is Story 1.5's ruling and not this story's to revisit.
 *
 * The two bands are still populated. "Nothing in this book" says nothing about
 * which rules were in force, and the server sends them for that reason.
 */
export const DASHBOARD_CHARTS_EMPTY = {
  status: 200,
  body: {
    ...DASHBOARD_CHARTS.body,
    byStage: { items: [], total: 0, totalCategories: 0, truncated: false, limit: null },
    bySeverity: { items: [], total: 0, totalCategories: 0, truncated: false, limit: null },
    byRecoveryStatus: {
      items: [],
      total: 0,
      totalCategories: 0,
      truncated: false,
      limit: null,
    },
    byInjuryType: { items: [], total: 0, totalCategories: 0, truncated: false, limit: 8 },
    byEmployer: { items: [], total: 0, totalCategories: 0, truncated: false, limit: null },
    byState: { items: [], total: 0, totalCategories: 0, truncated: false, limit: 10 },
    sla: SLA_NO_DATA.body,
  },
};

/**
 * The first page of David Bline's real priority worklist (Story 5.4).
 *
 * `DASHBOARD_CHARTS`' argument: component tests do not verify these figures
 * (that is `test_priority_claims.py`'s job against the real database), they
 * verify that whatever the server sends is what the table draws. Using the real
 * book — the real thirty-eight-claim population, the real cap, the real
 * ordering, the real workers, employers and injury types — keeps a reader from
 * mistaking a stub for a computation.
 *
 * **Everything that diverges from a live response, named.** A fixture docstring
 * that claims "real numbers" while a column was rewritten is worse than no
 * docstring, because the next reader trusts it:
 *
 * 1. `nextBestAction` — the live payload answers "Review Return-to-Work policy
 *    & offer letter" for eight of these ten rows, because the same trigger
 *    fires across a population defined as claims in treatment. Ten identical
 *    strings in one column is exactly the fixture that cannot catch a
 *    mis-wired cell, so each row carries a **different** label. Every one of
 *    them is a real label from `services/worklist/actions.py` — the ten trigger
 *    rules and the three padding rows are the whole vocabulary — so the column
 *    still renders text the generator can produce.
 * 2. `WC-20765` — its live `fraudScore` is 22 (a tie with `WC-20459`) and its
 *    live `daysOpen` is 0 (a tie with `WC-21275`). Both are nudged to 25 and 3
 *    so that every figure in each numeric column is distinct: a table that read
 *    the wrong row's fraud score or age would otherwise be indistinguishable
 *    from a correct one on two of the ten rows.
 *
 * Everything else is the seed's: the ten claim ids in the server's priority
 * order, the workers, employers, injury types, severity bands, the three LITIG
 * rows at the top, the fraud flags (two `true`, at scores of 61 and 63, both
 * above the published 55), the stages, `total`, `cap` and all three thresholds.
 *
 * **`severityBand`, `stage` and `fraudFlagged` repeat, and cannot not.** They
 * are three-, four- and two-valued, so the "every figure distinct" rule reaches
 * the numeric columns only — which is what the assertions read: the tests
 * compare label/value *pairs* per row keyed on `claimId`, so a swapped row
 * fails on its id before a band is compared at all.
 */
export const PRIORITY_CLAIMS = {
  status: 200,
  body: {
    items: [
      {
        claimId: "WC-21139",
        worker: "Jeremy Baker",
        employerShortName: "Whirlpool",
        injuryType: "Amputation",
        severityBand: "high",
        fraudScore: 5,
        fraudFlagged: false,
        handlerName: "Kaya Johnson",
        daysOpen: 143,
        nextBestAction: "Review Return-to-Work policy & offer letter",
        stage: "treatment",
        litigationFlag: true,
      },
      {
        claimId: "WC-21275",
        worker: "Monique Allen",
        employerShortName: "Whirlpool",
        injuryType: "Crushing",
        severityBand: "high",
        fraudScore: 4,
        fraudFlagged: false,
        handlerName: "Kaya Johnson",
        daysOpen: 0,
        nextBestAction: "Coordinate the case file with defense counsel",
        stage: "treatment",
        litigationFlag: true,
      },
      {
        claimId: "WC-20646",
        worker: "Gina Hamilton",
        employerShortName: "GE",
        injuryType: "Myocardial Infarction",
        severityBand: "high",
        fraudScore: 18,
        fraudFlagged: false,
        handlerName: "Kaya Johnson",
        daysOpen: 130,
        nextBestAction: "Authorize the surgical pre-approval",
        stage: "treatment",
        litigationFlag: true,
      },
      {
        claimId: "WC-20816",
        worker: "Kenneth Hughes",
        employerShortName: "Honeywell",
        injuryType: "Myocardial Infarction",
        severityBand: "high",
        fraudScore: 23,
        fraudFlagged: false,
        handlerName: "Dante Reyes",
        daysOpen: 121,
        nextBestAction: "Record the injury on the OSHA 300 log",
        stage: "treatment",
        litigationFlag: false,
      },
      {
        claimId: "WC-20459",
        worker: "Gerald Brown",
        employerShortName: "Caterpillar",
        injuryType: "Amputation",
        severityBand: "high",
        fraudScore: 22,
        fraudFlagged: false,
        handlerName: "Kaya Johnson",
        daysOpen: 142,
        nextBestAction: "Review medical bills awaiting approval",
        stage: "treatment",
        litigationFlag: false,
      },
      {
        claimId: "WC-21428",
        worker: "Vanessa Morgan",
        employerShortName: "John Deere",
        injuryType: "Fall from Height",
        severityBand: "high",
        fraudScore: 13,
        fraudFlagged: false,
        handlerName: "Liam O'Sullivan",
        daysOpen: 67,
        nextBestAction: "Confirm the indemnity payment for this week",
        stage: "treatment",
        litigationFlag: false,
      },
      {
        claimId: "WC-20884",
        worker: "Helen Powell",
        employerShortName: "Toyota",
        injuryType: "Fracture",
        severityBand: "high",
        fraudScore: 14,
        fraudFlagged: false,
        handlerName: "Marcus Chen",
        daysOpen: 11,
        nextBestAction: "Schedule the modified-duty review with the plant",
        stage: "treatment",
        litigationFlag: false,
      },
      {
        claimId: "WC-21462",
        worker: "James Harris",
        employerShortName: "John Deere",
        // The longest injury type in the seeded portfolio, kept because this
        // column truncates with CSS and keeps the full string in `title` —
        // a fixture of short labels could not tell the two apart.
        injuryType: "Vibration White Finger (HAVS)",
        severityBand: "low",
        fraudScore: 61,
        fraudFlagged: true,
        handlerName: "Liam O'Sullivan",
        daysOpen: 17,
        nextBestAction: "Approve the claim assessment",
        stage: "investigation",
        litigationFlag: false,
      },
      {
        claimId: "WC-20935",
        worker: "Lucinda West",
        employerShortName: "Toyota",
        injuryType: "Strain or Tear — Shoulder",
        severityBand: "med",
        fraudScore: 63,
        fraudFlagged: true,
        handlerName: "Marcus Chen",
        daysOpen: 65,
        nextBestAction: "Escalate to SIU — fraud indicators on file",
        stage: "investigation",
        litigationFlag: false,
      },
      {
        claimId: "WC-20765",
        worker: "Richard Morris",
        employerShortName: "Honeywell",
        injuryType: "Crushing",
        severityBand: "high",
        // 25 and 3, not the live 22 and 0 — see the docstring on why every
        // figure in a numeric column is distinct here.
        fraudScore: 25,
        fraudFlagged: false,
        handlerName: "Dante Reyes",
        daysOpen: 3,
        nextBestAction: "Log the weekly diary check-in",
        stage: "treatment",
        litigationFlag: false,
      },
    ],
    nextCursor:
      "eyJvIjoxMCwibCI6MTAsInYiOjEsInQiOjUsImEiOjIsImQiOiIyMDI2LTA4LTE4In0",
    total: 38,
    cap: 30,
    truncated: true,
    highRiskSeverityMin: 65,
    medRiskSeverityMin: 35,
    fraudFlagScoreMin: 55,
    rulesVersion: 2,
  },
};

/**
 * The second page of the same walk, with **no** cursor behind it.
 *
 * Three rows rather than ten, deliberately: what the "Show more" test is about
 * is that the pages are *appended and deduped* and that the button disappears
 * when the server stops offering a cursor, and a ten-row second page would make
 * the assertion "twenty rows" rather than "these thirteen, in this order". The
 * three claim ids and their facts are the real page two's first three.
 *
 * `total` and `cap` repeat page one's, which is the contract: the population is
 * counted before the cap and is stable across every page of a walk. A fixture
 * that moved either would let a component publish a caption that changed as the
 * table grew.
 */
export const PRIORITY_CLAIMS_PAGE_TWO = {
  status: 200,
  body: {
    items: [
      {
        claimId: "WC-21530",
        worker: "Lucinda Davis",
        employerShortName: "John Deere",
        injuryType: "Conveyor Entanglement",
        severityBand: "high",
        fraudScore: 6,
        fraudFlagged: false,
        handlerName: "Liam O'Sullivan",
        daysOpen: 1,
        nextBestAction: "Review the case file and confirm the reserve position",
        stage: "treatment",
        litigationFlag: false,
      },
      {
        claimId: "WC-20102",
        worker: "George Lee",
        employerShortName: "Boeing",
        injuryType: "Strain or Tear — Shoulder",
        severityBand: "med",
        fraudScore: 65,
        fraudFlagged: true,
        handlerName: "Dante Reyes",
        daysOpen: 137,
        nextBestAction: "Review the documents on file",
        stage: "treatment",
        litigationFlag: false,
      },
      {
        claimId: "WC-20034",
        worker: "Michelle Cox",
        employerShortName: "Boeing",
        injuryType: "Fall from Height",
        severityBand: "high",
        fraudScore: 16,
        fraudFlagged: false,
        handlerName: "Dante Reyes",
        daysOpen: 78,
        nextBestAction: "Review the indemnity payment schedule",
        stage: "treatment",
        litigationFlag: false,
      },
    ],
    nextCursor: null,
    total: 38,
    cap: 30,
    truncated: true,
    highRiskSeverityMin: 65,
    medRiskSeverityMin: 35,
    fraudFlagScoreMin: 55,
    rulesVersion: 2,
  },
};

/**
 * `PRIORITY_CLAIMS`' ten rows, in a **different order**.
 *
 * Its whole purpose is to be asserted *against* `PRIORITY_CLAIMS`. A component
 * that renders what it was sent reads differently under the two; one that
 * sorted — by severity, by fraud score, by days open, by claim id, by anything
 * — reads the same. The order here is deliberately the reverse of the server's,
 * which is the one permutation that every plausible client-side sort would
 * *undo*: a table that re-imposed the priority ranking would produce
 * `PRIORITY_CLAIMS`' order from this payload, and the test would catch it.
 *
 * `total` and `cap` are unchanged, because re-ordering a page does not change
 * how many claims qualified.
 */
export const PRIORITY_CLAIMS_REORDERED = {
  status: 200,
  body: {
    ...PRIORITY_CLAIMS.body,
    items: [...PRIORITY_CLAIMS.body.items].reverse(),
  },
};

/**
 * A scope whose book needs no priority attention — the table's empty state.
 *
 * Reachable, and it is the one empty state on this dashboard that is good news:
 * a supervisor whose claims are all settled, none litigated and none
 * fraud-flagged has an empty worklist because her portfolio is quiet.
 *
 * The three thresholds and `cap` are spread through from the fixture above
 * rather than nulled with everything else, because that is what the server
 * sends: "nothing in this book" says nothing about which rules were in force.
 * `total` is 0 and so the caption reads "top 30 of 0" — the cap is still the
 * document's answer even when nothing reaches it.
 */
export const PRIORITY_CLAIMS_EMPTY = {
  status: 200,
  body: {
    ...PRIORITY_CLAIMS.body,
    items: [],
    nextCursor: null,
    total: 0,
    // Nothing qualified, so nothing was cut: `truncated` follows the population
    // and not the cap, and an empty book is the limit case of the untruncated
    // caption rather than a third state.
    truncated: false,
  },
};

/**
 * A scoped supervisor's whole worklist — eight rows, under the cap.
 *
 * Jennifer Park's seeded figures, and the fixture the caption's other sentence
 * needs: `total` below `cap` means the cut never happened, so the heading must
 * read "(8 claims)" and never "showing top 30 of 8". `truncated` is the
 * server's answer to that question and this fixture is where the component's
 * handling of `false` is exercised against a non-empty table.
 */
export const PRIORITY_CLAIMS_UNDER_CAP = {
  status: 200,
  body: {
    ...PRIORITY_CLAIMS.body,
    items: PRIORITY_CLAIMS.body.items.slice(0, 8),
    nextCursor: null,
    total: 8,
    truncated: false,
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
function stageGroup(items: unknown[], total = items.length, nextCursor: string | null = null) {
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
    thresholdsVersion: 1,
    unfilteredTotal: 3,
    filteredTotal: 3,
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
    thresholdsVersion: 1,
    unfilteredTotal: 0,
    filteredTotal: 0,
  },
};

/**
 * No group matched, but the handler has 45 claims — a filter miss.
 *
 * `filteredTotal: 0` beside `unfilteredTotal: 45` is the whole distinction,
 * and it is the server's to make: the groups are byte-identical to
 * `CLAIM_QUEUE_EMPTY`'s.
 */
export const CLAIM_QUEUE_NO_MATCH = {
  status: 200,
  body: { ...CLAIM_QUEUE_EMPTY.body, unfilteredTotal: 45, filteredTotal: 0 },
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
    thresholdsVersion: 1,
    unfilteredTotal: 2,
    filteredTotal: 2,
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
    thresholdsVersion: 1,
    unfilteredTotal: 2,
    filteredTotal: 2,
  },
};

/**
 * Case-file fixtures (Story 2.2).
 *
 * Shaped like the real payload and carrying plausible seed-shaped values,
 * for the queue fixtures' reason: component tests verify that whatever the
 * server sends is what the pane renders, and a fixture with invented
 * *structure* would let a test pass against a component that derived
 * something. Every derived value here — `risk`, `phase`, `coordinationStatus`,
 * `costSplit`, the stepper marks, the checklist — is a value the server
 * decided, so the fixtures spell them out rather than computing them.
 */
function stepper(current: string) {
  const order = ["intake", "investigation", "treatment", "settled"];
  const position = order.indexOf(current);
  return order.map((stage, index) => ({
    stage,
    done: index < position,
    current: index === position,
  }));
}

const TIMELINE = [
  { eventDate: "2026-03-22", description: "FNOL received — Fall from Height", tag: "intake" },
  { eventDate: "2026-03-24", description: "C-1 First Report of Injury filed", tag: "froi" },
  { eventDate: "2026-03-25", description: "Handler assigned — Kaya Johnson", tag: "assignment" },
];

/**
 * The editable vocabularies the server sends with every case file (Story
 * 2.3) — the eleven diagram keys, the five recovery windows, the two
 * disability tokens.
 *
 * The real lists, not a subset, because a select is only trustworthy if it
 * offers exactly what the command accepts; a fixture with three body parts
 * would let a test pass against a component that silently dropped the rest.
 * Their *content* is asserted server-side against the prototype
 * (`test_claim_edit_validation.py`); here they only have to be real.
 */
export const EDIT_OPTIONS = {
  bodyParts: [
    { key: "head", label: "Head" },
    { key: "ears", label: "Ears" },
    { key: "shoulder_right", label: "Right Shoulder" },
    { key: "shoulder_left", label: "Left Shoulder" },
    { key: "forearm_right", label: "Right Forearm" },
    { key: "hand_right", label: "Right Hand" },
    { key: "hand_left", label: "Left Hand" },
    { key: "torso", label: "Torso" },
    { key: "lumbar", label: "Lower Back (Lumbar)" },
    { key: "tibia_left", label: "Left Lower Leg" },
    { key: "tibia_right", label: "Right Lower Leg" },
  ],
  recoveryWindows: ["weeks_0_2", "weeks_2_4", "weeks_4_6", "weeks_6_8", "over_1_year"],
  disabilities: ["temporary", "permanent"],
};

/**
 * The injury-diagram block (Story 2.4).
 *
 * Every marker arrives with its `band` already decided, because that is what
 * the server sends: the fixture spells out `high`/`med` rather than deriving
 * them from the scores beside them, so a `BodyMap` that banded its own
 * colours would pass no test here.
 *
 * Two markers, one primary and one secondary, so a single render can see the
 * pulse (primary only), the ✕ (secondary only) and two different band
 * colours. `INJURY_UNKNOWN_KEY` below covers the hotspot fallback.
 */
export const INJURY_DIAGRAM = {
  markers: [
    {
      id: null,
      version: null,
      bodyKey: "lumbar",
      bodyPart: "Lower Back",
      injuryType: "Fall from Height",
      severityScore: 78,
      band: "high",
      primary: true,
    },
    {
      id: 41,
      // Deliberately *not* the claim's version (1). A removal compare-and-
      // swaps on the injury row, and a fixture where the two numbers were
      // equal would let a component that sent the wrong one pass.
      version: 3,
      bodyKey: "hand_left",
      bodyPart: "Left Hand",
      injuryType: "Laceration",
      severityScore: 44,
      band: "med",
      primary: false,
    },
  ],
  icd: "S39.012A",
  icdDesc: "Strain of muscle, fascia and tendon of lower back",
  prognosis: {
    mmi: "6-10 mo",
    rtw: "Modified duty 4-6 mo",
    impairment: "PPD possible 5-15%",
    litigation: "Low",
  },
  treatmentPlan: [
    { stepNo: 1, description: "Head/spine CT protocol if fall >4 feet" },
    { stepNo: 2, description: "Orthopedic evaluation for fracture" },
    { stepNo: 3, description: "PT for musculoskeletal recovery" },
  ],
  contraindications:
    "No elevated work platform access until medically cleared. Fall protection protocol review.",
  defaultSeverityScore: 40,
  captureVersion: 1,
  severityMin: 0,
  severityMax: 100,
};

/**
 * A marker whose region this build does not know — deploy skew, or a key
 * added to the server's vocabulary ahead of the SVG.
 *
 * Its own fixture because the `torso` fallback is otherwise rendered by
 * nothing and asserted by nothing: the select cannot offer such a key and
 * the command refuses one, so only a test can produce it. Story 2.2's code
 * review found the same shape of hole in the settled banner's date clause.
 */
export const INJURY_UNKNOWN_KEY = {
  ...INJURY_DIAGRAM,
  markers: [{ ...INJURY_DIAGRAM.markers[0], bodyKey: "cervical_spine" }, INJURY_DIAGRAM.markers[1]],
};

/**
 * The Documents & ID block (Story 2.5).
 *
 * `path` is `b` and the four forms are Path B's, because that is what the
 * server would send for this claim — the fixture does not *derive* the path
 * from the claim's severity beside it, so a component that classified in the
 * browser would pass no test here. `DOCUMENTS_BLOCK_PATH_A` below is the
 * second path, for the same reason `INJURY_DIAGRAM` carries two bands.
 *
 * Three documents, one of them a FROI, so a single render sees three different
 * type chips and both viewer variants are reachable. The last has no
 * `filedDate`: 101 seeded documents carry a timing note where a date belongs,
 * and the em dash is the honest rendering.
 */
/**
 * The `derivation_thresholds` version the case-file fixtures were cut from.
 *
 * **One constant because the server makes these two fields equal** (code
 * review, 2026-08-12): `documents_block` sets `path_version =
 * thresholds.version`, so `thresholdsVersion` and `pathVersion` agree in every
 * real payload. The fixtures used to carry 2 and 3 — a response the server
 * cannot produce, in a file whose own doctrine is that the fixture *is* the
 * server's answer. Harmless while nothing compares them, and silently fatal to
 * the first test that does.
 *
 * The queue fixtures above are deliberately not switched to this: they are a
 * different endpoint with no `pathVersion` beside them, and their version is
 * free to differ.
 */
const CASE_FILE_RULE_VERSION = 3;

export const DOCUMENTS_BLOCK = {
  path: "b",
  pathVersion: CASE_FILE_RULE_VERSION,
  requiredForms: [
    {
      formCode: "C-3",
      formName: "FROI — Employee Claim for Compensation (C-3)",
      description: "Employee's formal WC claim. Filed when disability exceeds waiting period.",
      timing: "As soon as practicable; carrier within 30 days",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c3.pdf",
    },
    {
      formCode: "RFA-1W",
      formName: "RTW Request for Assistance (RFA-1W)",
      description: "Initiates formal RTW coordination.",
      timing: "When physician grants light duty clearance",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/rfa-1w.pdf",
    },
    {
      formCode: "C-4.3",
      formName: "Maximum Medical Improvement — MMI (C-4.3)",
      description: "Treating physician certifies MMI reached.",
      timing: "When physician determines MMI",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c4_3.pdf",
    },
    {
      formCode: "C-11",
      formName: "Employee Change in Employment Status (C-11)",
      description: "Documents RTW, termination, job change, or retirement.",
      timing: "When employment status changes",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c11.pdf",
    },
  ],
  idCard: {
    employeeBusinessId: "EMP-CAT-2043",
    workerName: "Marcus Delgado",
    workerRole: "Assembly Technician",
    policyNum: "CL-POL-CAT-2024-118",
    doi: "2026-03-22",
    handlerName: "Kaya Johnson",
    plant: "Caterpillar – Peoria, IL",
    state: "IL",
    region: "Midwest",
  },
  documents: [
    { id: 11, name: "C-1 First Report of Injury", docType: "froi", filedDate: "2026-03-24" },
    {
      id: 12,
      name: "Medical Authorization & Release (HIPAA)",
      docType: "medauth",
      filedDate: "2026-03-24",
    },
    { id: 13, name: "Surgical Consent & Operative Report", docType: "legal", filedDate: null },
  ],
};

/** The minor path — a different banner, a different form set, two forms. */
export const DOCUMENTS_BLOCK_PATH_A = {
  ...DOCUMENTS_BLOCK,
  path: "a",
  requiredForms: [
    {
      formCode: "C-2F",
      formName: "Minor Injury Report",
      description: "Employer's first-aid-only / minor injury report.",
      timing: "Within 10 days of incident",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c2F.pdf",
    },
    {
      formCode: "FAR-1",
      formName: "Employee First Aid / Injury Report",
      description: "Employee-completed onsite first aid and injury description.",
      timing: "Same shift or within 24h",
      downloadUrl: "https://www.fnsb.gov/DocumentCenter/View/18587/",
    },
  ],
};

/**
 * A claim whose file holds nothing (AC 5).
 *
 * Unreachable against the dev seed — every seeded claim carries three to eight
 * documents — which is the reason it needs a fixture rather than a claim id.
 * The forms card is *not* empty here: which filings a path requires does not
 * depend on what has been filed, and a state that blanked both would say
 * something the data does not.
 */
export const DOCUMENTS_BLOCK_EMPTY = { ...DOCUMENTS_BLOCK, documents: [] };

/**
 * The Photos block (Story 2.6).
 *
 * Three cards, all of them the seeded shape: `hasBlob: false` and
 * `blobUrl: null`, because the prototype has no image files behind its photo
 * rows and all 293 seeded rows carry a null `blob_key`.
 *
 * **`count` is 3 and is written out rather than spread from the array**, on
 * purpose: the fixture is the server's answer, and the server is the thing
 * that computes it. A component that derived the tab label from
 * `photos.length` would pass against a fixture whose count *was*
 * `photos.length`, which is why `PHOTOS_BLOCK_MISCOUNTED` below exists.
 */
export const PHOTOS_BLOCK = {
  count: 3,
  photos: [
    {
      id: 31,
      caption: "Platform / Scaffolding Fall area — post-incident overview",
      source: "Plant safety, 03/22",
      hasBlob: false,
      blobUrl: null,
    },
    {
      id: 32,
      caption: "OSHA investigation — incident scene documentation",
      source: "OSHA inspector, 03/24",
      hasBlob: false,
      blobUrl: null,
    },
    {
      id: 33,
      caption: "PPE worn at time of injury — Platform / Scaffolding Fall",
      source: "EHS audit, 03/25",
      hasBlob: false,
      blobUrl: null,
    },
  ],
};

/**
 * The empty state (Story 2.6, AC 3, NFR-3).
 *
 * Unreachable against the dev seed — every one of the 100 claims carries two
 * to four photos — which is the reason it needs a fixture rather than a claim
 * id, exactly as `DOCUMENTS_BLOCK_EMPTY` does.
 */
export const PHOTOS_BLOCK_EMPTY = { count: 0, photos: [] };

/**
 * A count that disagrees with the rows — a payload no real server sends.
 *
 * It exists so the tab label has something to be *wrong* against: with a
 * faithful fixture, a component reading `photos.length` and one reading
 * `count` are indistinguishable. This is the fixture that tells them apart.
 */
export const PHOTOS_BLOCK_MISCOUNTED = { ...PHOTOS_BLOCK, count: 9 };

/**
 * The two states a photo with bytes can be in, which is why the server sends
 * two fields rather than one.
 *
 * - `hasBlob` with a `blobUrl` — a presigning store (MinIO): render the image.
 * - `hasBlob` with a null `blobUrl` — a mounted volume, whose `BlobStore.url`
 *   answers `None` *by design*. There is a file; this deployment cannot hand
 *   the browser a direct link to it. A UI that read the null URL as "no photo"
 *   would show the placeholder for a whole grid of real photographs.
 */
export const PHOTOS_BLOCK_WITH_IMAGE = {
  count: 2,
  photos: [
    {
      ...PHOTOS_BLOCK.photos[0],
      hasBlob: true,
      blobUrl: "https://blobs.example/photo-31.jpg",
    },
    { ...PHOTOS_BLOCK.photos[1], hasBlob: true, blobUrl: null },
  ],
};

/**
 * The benefit block (Story 3.1) — one claim's statutory weekly indemnity.
 *
 * Every figure is integer cents and both rates are integer basis points, as
 * they are on the wire: `compRateBp: 6667` is 66.67% of AWW. The values are a
 * *coherent* answer for the Washington claim above (66.67% of a $1,432 wage is
 * $954.71, inside WA's $257–$1,711 range) rather than round numbers, so a
 * component that reformatted a rate or dropped a decimal has something to be
 * visibly wrong against.
 *
 * `isOverridden` is `false` and `compRateBp` equals `defaultCompRateBp`, which
 * is the *un*-overridden state — see `BENEFIT_OVERRIDDEN_AT_DEFAULT` for the
 * fixture that tells "on the default" apart from "overridden to the default".
 */
export const BENEFIT = {
  weeklyCents: 95_471,
  compRateBp: 6667,
  defaultCompRateBp: 6667,
  isOverridden: false,
  indemnityType: "ttd" as const,
  stateCode: "WA",
  stateName: "Washington",
  stateMinCents: 25_700,
  stateMaxCents: 171_100,
  scheduleEffectiveDate: "2026-01-01",
  waitingDays: 7,
  reserveRationale:
    "Reserve set at $22,349 reflects a medium severity fall from height " +
    "(score 52/100) with a 6-8 weeks expected recovery window. Indemnity " +
    "exposure estimated at TTD weekly benefit through projected MMI. " +
    "Reviewed against WA statutory min/max ($257–$1,711/wk).",
  compRateMinBp: 0,
  compRateMaxBp: 15_000,
  paramsVersion: 1,
};

/** The same claim with a handler's override applied — the ↺ state. */
export const BENEFIT_OVERRIDDEN = {
  ...BENEFIT,
  weeklyCents: 100_598,
  compRateBp: 7025,
  isOverridden: true,
  reserveRationale:
    BENEFIT.reserveRationale +
    " Comp rate manually adjusted to 70.25% (default 66.67%) by handler.",
};

/**
 * Overridden to *exactly* the statutory default.
 *
 * The payload a component reading `compRateBp !== defaultCompRateBp` cannot
 * tell from an un-overridden claim — which is why the server publishes
 * `isOverridden` and why the ↺ has to be bound to it. Real: a handler who
 * types 66.67 has made a decision the case file records.
 */
export const BENEFIT_OVERRIDDEN_AT_DEFAULT = {
  ...BENEFIT,
  isOverridden: true,
};

/** A permanent total disability: full wage, and the clamp biting at the top. */
export const BENEFIT_PTD = {
  ...BENEFIT,
  weeklyCents: 171_100,
  compRateBp: 10_000,
  defaultCompRateBp: 10_000,
  indemnityType: "ptd" as const,
};

/**
 * The reserve adequacy verdict (Story 3.2) — the default fixture is `adequate`.
 *
 * Coherent rather than round, for `BENEFIT`'s reason: the exposure is the two
 * terms actually added up, and the ratio is what those figures produce against
 * the reserve ($40,500 of $45,000 is 90%, which sits between the 60% and 115%
 * bands). A component that recomputed the ratio, or dropped one of the two
 * exposure terms, has something visibly wrong to be.
 *
 * `remainingMedicalCents` is a real number here and `null` in the seeded
 * database today — deliberately. The fixture describes the payload the contract
 * promises once Story 3.3 seeds `bill`, and a component tested only against the
 * seam's temporary `null` would be untested for the case it is built for.
 * `RESERVE_CHECK_INDETERMINATE` below is the seam's own shape.
 */
export const RESERVE_CHECK = {
  verdict: "adequate" as const,
  ratioBp: 9_000,
  projectedRemainingCents: 4_050_000,
  remainingIndemnityCents: 2_864_130,
  remainingMedicalCents: 1_185_870,
  // The two halves the remaining indemnity is the difference of, and the pair
  // the card's "Indemnity paid" row renders. Coherent by construction —
  // 4,000,000 − 1,135,870 = 2,864,130 — so a component that swapped them, or
  // that fell back to `overview.paidIndemnityCents`, has something visibly
  // wrong to be.
  scheduledIndemnityCents: 4_000_000,
  disbursedIndemnityCents: 1_135_870,
  // The paid half of the same bill list `remainingMedicalCents` is the unpaid
  // half of: 1,185,870 unpaid + 127,500 paid. The card's "Medical paid" row
  // renders this rather than `overview.paidMedicalCents`, which is 0 on every
  // open seeded claim (code review, 2026-08-14).
  disbursedMedicalCents: 127_500,
  reserveCents: 4_500_000,
  rationale: "Reserve ($45,000) is well aligned with projected remaining exposure ($40,500).",
  bandsVersion: 1,
};

/** Under-reserved — the error-toned verdict, and the one that asks for action. */
export const RESERVE_CHECK_LIGHT = {
  ...RESERVE_CHECK,
  verdict: "light" as const,
  ratioBp: 14_000,
  projectedRemainingCents: 6_300_000,
  remainingIndemnityCents: 5_114_130,
  scheduledIndemnityCents: 6_250_000,
  rationale:
    "Projected remaining exposure ($63,000) exceeds current reserve ($45,000). " +
    "Recommend re-evaluating reserve upward.",
};

/** Over-reserved — a warning rather than an error: capital tied up, not missing. */
export const RESERVE_CHECK_HEAVY = {
  ...RESERVE_CHECK,
  verdict: "heavy" as const,
  ratioBp: 4_000,
  projectedRemainingCents: 1_800_000,
  remainingIndemnityCents: 614_130,
  scheduledIndemnityCents: 1_750_000,
  rationale:
    "Current reserve ($45,000) comfortably exceeds projected remaining exposure " +
    "($18,000). Consider reallocating surplus.",
};

/**
 * A settled claim: judged, but not banded.
 *
 * `ratioBp` is null because no comparison was made — the payload a component
 * that assumed a number would render as "NaN%".
 */
export const RESERVE_CHECK_CLOSED = {
  ...RESERVE_CHECK,
  verdict: "closed_final" as const,
  ratioBp: null,
  projectedRemainingCents: 0,
  remainingIndemnityCents: 0,
  remainingMedicalCents: 0,
  // A settled claim's schedule is fully disbursed, which is why nothing
  // remains — the two halves still agree with the difference above.
  scheduledIndemnityCents: 4_000_000,
  disbursedIndemnityCents: 4_000_000,
  disbursedMedicalCents: 1_185_870,
  rationale: "Claim settled and closed. No further reserve exposure.",
};

/**
 * The shape every open claim actually has today: bills not on file.
 *
 * `remainingMedicalCents: null` is "nobody can see this claim's bills", which
 * is not `0` ("this claim has no unpaid bills"). The exposure therefore has no
 * total — `projectedRemainingCents` is `null` too — and the two verdicts an
 * unknown non-negative term could flip (`adequate`, `heavy`) are withheld.
 * `ratioBp` is `null` because no complete comparison happened.
 */
export const RESERVE_CHECK_INDETERMINATE = {
  ...RESERVE_CHECK,
  verdict: "indeterminate" as const,
  ratioBp: null,
  projectedRemainingCents: null,
  remainingMedicalCents: null,
  rationale:
    "Medical bills are not yet on file, so remaining exposure cannot be totalled " +
    "and the reserve ($45,000) cannot be judged. Scheduled indemnity alone " +
    "accounts for $28,641. A verdict follows once bills are recorded.",
};

/**
 * Under-reserved on indemnity alone — the verdict that survives a missing term.
 *
 * The one case where an incomplete exposure still decides: the unknown medical
 * amount is non-negative, so an exposure that already exceeds 115% of the
 * reserve cannot be brought back under it. The sentence says "before medical
 * bills are counted" so a handler acting on it knows the figure is a floor.
 */
export const RESERVE_CHECK_LIGHT_ON_INDEMNITY = {
  ...RESERVE_CHECK_LIGHT,
  ratioBp: null,
  projectedRemainingCents: null,
  remainingMedicalCents: null,
  rationale:
    "Scheduled indemnity alone ($51,141) already exceeds current reserve ($45,000) " +
    "before medical bills are counted. Recommend re-evaluating reserve upward.",
};

/** The two viewer sheets the content endpoint answers (Story 2.5, AC 4). */
export const DOCUMENT_SHEET_FROI = {
  status: 200,
  body: {
    documentId: 11,
    name: "C-1 First Report of Injury",
    docType: "froi",
    sheetVariant: "froi",
    rows: [
      { label: "Claim ID", text: "WC-20017", cents: null },
      { label: "Policy Number", text: "CL-POL-CAT-2024-118", cents: null },
      { label: "Employee", text: "Marcus Delgado (EMP-CAT-2043)", cents: null },
      { label: "Employer / Plant", text: "Caterpillar – Peoria, IL", cents: null },
      { label: "Date of Injury", text: "2026-03-22", cents: null },
      { label: "Filed", text: "2026-03-24", cents: null },
      { label: "Injury Type", text: "Fall from Height", cents: null },
      { label: "Body Part", text: "Lower Back", cents: null },
      { label: "ICD-10", text: "S39.012A", cents: null },
      { label: "Cause", text: "Fall from Elevated Platform", cents: null },
      { label: "Severity", text: "78", cents: null },
      // The one money row: cents on the wire, formatted by the dialog.
      { label: "AWW", text: null, cents: 143_200 },
      { label: "Handler", text: "Kaya Johnson", cents: null },
      { label: "OSHA Recordable", text: "Yes — OSHA 300 Filed", cents: null },
    ],
    signatures: ["Supervisor / Date", "Adjuster / Date"],
    hasBlob: false,
    blobUrl: null,
  },
};

export const DOCUMENT_SHEET_SUMMARY = {
  status: 200,
  body: {
    documentId: 13,
    name: "Surgical Consent & Operative Report",
    docType: "legal",
    sheetVariant: "summary",
    rows: [
      { label: "Claim ID", text: "WC-20017", cents: null },
      { label: "Policy Number", text: "CL-POL-CAT-2024-118", cents: null },
      { label: "Employee", text: "Marcus Delgado (EMP-CAT-2043)", cents: null },
      { label: "Employer / Plant", text: "Caterpillar – Peoria, IL", cents: null },
      // Undated, as 101 seeded documents are — the dialog renders an em dash.
      { label: "Filed", text: null, cents: null },
      { label: "Status", text: "On file", cents: null },
      { label: "Handler", text: "Kaya Johnson", cents: null },
    ],
    signatures: ["Supervisor / Date", "Adjuster / Date"],
    hasBlob: false,
    blobUrl: null,
  },
};

const HEADER = {
  claimId: "WC-20017",
  workerName: "Marcus Delgado",
  workerRole: "Assembly Technician",
  employerName: "Caterpillar Inc.",
  state: "IL",
  injuryType: "Fall from Height",
  // The stored label and the diagram key are different vocabularies — the
  // fixture keeps them different so a component that confused the two would
  // fail here rather than in a browser.
  bodyPart: "Lower Back",
  bodyKey: "lumbar",
  cause: "Fall from Elevated Platform",
  icd: "S39.012A",
  severityScore: 78,
  risk: "high",
  stage: "treatment",
  // Story 3.5's two. `status` and `stage` are genuinely different facts — a
  // treatment-stage claim can still be awaiting its assessment — and the
  // fixture keeps them different so a component that read one for the other
  // would fail here rather than in a browser.
  status: "ch_assessment_process",
  fraudFlag: true,
  fraudScore: 62,
  litigationFlag: true,
  surgeryRequired: true,
  oshaRecordable: true,
  oshaLogged: false,
};

/** Treatment: both derived states, and every header badge switched on. */
export const CLAIM_DETAIL_TREATMENT = {
  status: 200,
  body: {
    claimId: "WC-20017",
    version: 1,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    benefit: BENEFIT,
    reserveCheck: RESERVE_CHECK,
    requirementsVersion: null,
    header: HEADER,
    stepper: stepper("treatment"),
    overview: {
      stageVariant: "treatment",
      phase: "approaching_mmi",
      phaseNote: "Nearing maximum medical improvement — RTW and closure planning underway.",
      expectedDays: 42,
      daysOpen: 140,
      recovery: "weeks_4_6",
      paidMedicalCents: 1_240_000,
      paidIndemnityCents: 860_000,
      reserveCents: 4_500_000,
      coordinationStatus: "coordination_gap",
      coordinationNote:
        "RTW follow-up is overdue — recommended return date has passed with no confirmed update from employer or claimant.",
      returnStatus: "under_treatment",
      commStatus: "documents_received_and_approved",
      handlerName: "Kaya Johnson",
      timeline: TIMELINE,
      timelineTruncated: true,
    },
  },
};

/** Intake: a checklist with one row of each state, and no badges at all. */
export const CLAIM_DETAIL_INTAKE = {
  status: 200,
  body: {
    claimId: "WC-20003",
    version: 1,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    benefit: BENEFIT,
    reserveCheck: RESERVE_CHECK,
    requirementsVersion: 1,
    header: {
      ...HEADER,
      claimId: "WC-20003",
      workerName: "Priya Raman",
      stage: "intake",
      risk: "low",
      severityScore: 22,
      fraudFlag: false,
      litigationFlag: false,
      surgeryRequired: false,
      oshaRecordable: false,
      status: "initial",
    },
    stepper: stepper("intake"),
    overview: {
      stageVariant: "intake",
      employeeBusinessId: "EMP-1042",
      workerName: "Priya Raman",
      workerRole: "Machine Operator",
      plant: "Peoria Assembly",
      doi: "2026-03-22",
      froiDate: "2026-03-24",
      assignDate: "2026-03-25",
      handlerName: "Kaya Johnson",
      commStatus: "need_for_additional_information",
      injuryType: "Repetitive Strain",
      cause: "Repetitive Motion",
      bodyPart: "Right Wrist",
      severityScore: 22,
      risk: "low",
      awwCents: 118_000,
      reserveCents: 850_000,
      checklist: [
        { docType: "froi", received: true },
        { docType: "incident", received: false },
        { docType: "medauth", received: true },
        { docType: "wage", received: false },
      ],
      timeline: TIMELINE,
    },
  },
};

/** Investigation: payments made, so the cost bar is drawn. */
export const CLAIM_DETAIL_INVESTIGATION = {
  status: 200,
  body: {
    claimId: "WC-20051",
    version: 3,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    benefit: BENEFIT,
    reserveCheck: RESERVE_CHECK,
    requirementsVersion: null,
    header: { ...HEADER, claimId: "WC-20051", stage: "investigation", risk: "med" },
    stepper: stepper("investigation"),
    overview: {
      stageVariant: "investigation",
      injuryType: "Laceration",
      cause: "Contact with Machine Guard",
      bodyPart: "Left Hand",
      icd: "S61.412A",
      icdDesc: "Laceration without foreign body of left wrist",
      disability: "temporary",
      recovery: "weeks_2_4",
      awwCents: 104_000,
      totalPaidCents: 1_000_000,
      reserveCents: 2_200_000,
      policyNum: "WC-POL-88213",
      fraudScore: 18,
      severityScore: 44,
      risk: "med",
      paidIndemnityCents: 400_000,
      paidMedicalCents: 550_000,
      costSplit: { indemnityPct: 40, medicalPct: 55, expensePct: 5 },
      timeline: TIMELINE,
    },
  },
};

/** Investigation with nothing paid — the "Active — payments pending" branch. */
export const CLAIM_DETAIL_INVESTIGATION_UNPAID = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_INVESTIGATION.body,
    overview: {
      ...CLAIM_DETAIL_INVESTIGATION.body.overview,
      totalPaidCents: 0,
      paidIndemnityCents: 0,
      paidMedicalCents: 0,
      costSplit: null,
    },
  },
};

/** Settled, with the null settlement date every seeded claim actually has. */
export const CLAIM_DETAIL_SETTLED = {
  status: 200,
  body: {
    claimId: "WC-20068",
    version: 5,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    benefit: BENEFIT,
    reserveCheck: RESERVE_CHECK_CLOSED,
    requirementsVersion: null,
    header: { ...HEADER, claimId: "WC-20068", stage: "settled", risk: "low" },
    stepper: stepper("settled"),
    overview: {
      stageVariant: "settled",
      settlementDate: null,
      totalPaidCents: 3_100_000,
      paidIndemnityCents: 1_500_000,
      paidMedicalCents: 1_400_000,
      paidExpenseCents: 200_000,
      reserveCents: 0,
      costSplit: { indemnityPct: 48, medicalPct: 45, expensePct: 7 },
      disability: "permanent",
      returnStatus: "returned_and_fully_recovered",
      daysToSettlement: 212,
      litigationFlag: false,
      handlerName: "Kaya Johnson",
      timeline: TIMELINE,
    },
  },
};

/**
 * A settled claim whose settlement event *does* carry a date.
 *
 * No seeded claim does — the prototype writes `Closed` where the date
 * belongs — so without this fixture the banner's date clause was rendered by
 * nothing and asserted by nothing, while its own comment claimed it "gains
 * the date without a change" (code review, 2026-08-12).
 */
export const CLAIM_DETAIL_SETTLED_DATED = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_SETTLED.body,
    overview: { ...CLAIM_DETAIL_SETTLED.body.overview, settlementDate: "2026-07-29" },
  },
};

/** A claim with no history at all — the timeline card's empty state. */
export const CLAIM_DETAIL_NO_TIMELINE = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_SETTLED.body,
    overview: { ...CLAIM_DETAIL_SETTLED.body.overview, timeline: [] },
  },
};

/**
 * The 404 the server answers for a claim outside the caller's scope — and,
 * identically, for one that does not exist. The SPA must not try to tell
 * them apart; the sameness is the point (AD-7).
 */
export const CLAIM_DETAIL_NOT_FOUND = {
  status: 404,
  body: {
    type: "/problems/claim-not-found",
    title: "Not Found",
    status: 404,
    detail: "No claim WC-9999 in your caseload.",
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

/**
 * The Bills & Payments read model (Story 3.3).
 *
 * **Coherent by construction, because that is what makes the tab's assertions
 * mean anything.** Every figure below is consistent with every other: the
 * schedule's five weeks sum to `scheduledIndemnityCents`, the two paid weeks
 * sum to `disbursedIndemnityCents`, the bills' and expenses' `paidCents` are
 * the sums of their paid rows, and `paidToDateCents` is the three paid figures
 * added up. A component that summed the wrong rows, or that fell back to a
 * different notion of "paid", therefore has something visibly wrong to be —
 * which a fixture of unrelated round numbers could not give it.
 *
 * `reserveCheck` is `RESERVE_CHECK` itself rather than a copy: the server
 * publishes the same block on both payloads, computed once, and the fixture
 * says so by identity (AC 4).
 */
const SCHEDULE_WEEKS = [
  {
    weekNo: 1,
    periodStart: "2026-04-05",
    periodEnd: "2026-04-11",
    amountCents: 567_935,
    status: "paid" as const,
    version: 1,
    approvable: false,
  },
  {
    weekNo: 2,
    periodStart: "2026-04-12",
    periodEnd: "2026-04-18",
    amountCents: 567_935,
    status: "paid" as const,
    version: 1,
    approvable: false,
  },
  {
    weekNo: 3,
    periodStart: "2026-04-19",
    periodEnd: "2026-04-25",
    amountCents: 567_935,
    status: "due_this_week" as const,
    version: 1,
    approvable: true,
  },
  {
    weekNo: 4,
    periodStart: "2026-04-26",
    periodEnd: "2026-05-02",
    amountCents: 567_935,
    status: "upcoming" as const,
    version: 1,
    approvable: true,
  },
  {
    weekNo: 5,
    periodStart: "2026-05-03",
    periodEnd: "2026-05-09",
    amountCents: 1_728_260,
    status: "upcoming" as const,
    version: 1,
    approvable: true,
  },
];

const BILL_ITEMS = [
  {
    id: 1,
    category: "initial_treatment" as const,
    label: "Emergency / Initial Treatment",
    amountCents: 127_500,
    status: "paid" as const,
    version: 1,
    approvable: false,
  },
  {
    id: 2,
    category: "imaging" as const,
    label: "Diagnostic Imaging (MRI/CT/X-Ray)",
    amountCents: 174_000,
    status: "under_review" as const,
    version: 1,
    approvable: true,
  },
  {
    id: 3,
    category: "physical_therapy" as const,
    label: "Physical Therapy Session Bundle",
    amountCents: 264_000,
    status: "pending_submission" as const,
    version: 1,
    approvable: false,
  },
];

const EXPENSE_ITEMS = [
  {
    id: 11,
    category: "mileage_travel" as const,
    label: "Mileage & Travel Reimbursement",
    amountCents: 10_450,
    status: "paid" as const,
    version: 1,
    approvable: false,
  },
  {
    id: 12,
    category: "dme" as const,
    label: "Durable Medical Equipment",
    amountCents: 40_800,
    status: "under_review" as const,
    version: 1,
    approvable: true,
  },
];

export const CLAIM_FINANCIALS = {
  status: 200,
  body: {
    summary: {
      // 1,135,870 indemnity + 127,500 paid bills + 10,450 paid expenses.
      paidToDateCents: 1_273_820,
      // Paid to date + the reserve.
      totalClaimProjectedCents: 5_773_820,
      reserveCents: 4_500_000,
      costSplit: { indemnityPct: 89, medicalPct: 10, expensePct: 1 },
      paidIndemnityCents: 1_135_870,
      paidMedicalCents: 127_500,
      paidExpenseCents: 10_450,
      // The live sources answered, which is the case on every open claim.
      paidFromColumns: false,
      weeklyIndemnityCents: 567_935,
      installmentsPaid: 2,
      weekCount: 5,
      // The first `due_this_week` row's start.
      nextPaymentDue: "2026-04-19",
      billsOnFile: 3,
      // The same two figures the treatment Overview card's "Indemnity paid"
      // row renders, from the same server block.
      scheduledIndemnityCents: 4_000_000,
      disbursedIndemnityCents: 1_135_870,
      // Story 3.4: the batch note's date. A Friday, which is one of the two
      // days the default cadence names — restated here rather than derived,
      // because a fixture that recomputed the rule could not disagree with it.
      nextBatchDate: "2026-04-24",
    },
    schedule: SCHEDULE_WEEKS,
    bills: {
      items: BILL_ITEMS,
      count: 3,
      totalCents: 565_500,
      paidCents: 127_500,
    },
    expenses: {
      items: EXPENSE_ITEMS,
      count: 2,
      totalCents: 51_250,
      paidCents: 10_450,
    },
    reserveCheck: RESERVE_CHECK,
  },
};

/**
 * A claim with nothing disbursed — the cost bar's `null` split (NFR-3).
 *
 * Every week awaits approval, so there is no indemnity paid and no bill paid,
 * and the summary's three paid figures are zero. `costSplit: null` is the
 * server saying "this claim has no composition", which the card renders as a
 * sentence rather than as a bar of three zero-width segments.
 */
export const CLAIM_FINANCIALS_UNPAID = {
  status: 200,
  body: {
    ...CLAIM_FINANCIALS.body,
    summary: {
      ...CLAIM_FINANCIALS.body.summary,
      paidToDateCents: 0,
      totalClaimProjectedCents: 4_500_000,
      costSplit: null,
      paidIndemnityCents: 0,
      paidMedicalCents: 0,
      paidExpenseCents: 0,
      installmentsPaid: 0,
      nextPaymentDue: null,
      disbursedIndemnityCents: 0,
    },
    // `approvable` moves with the status, which is what the server does: a
    // week awaiting approval is exactly the case the ✓ button exists for.
    schedule: SCHEDULE_WEEKS.map((week) => ({
      ...week,
      status: "pending_approval" as const,
      approvable: true,
    })),
    bills: {
      items: BILL_ITEMS.map((item) => ({
        ...item,
        status: "under_review" as const,
        approvable: true,
      })),
      count: 3,
      totalCents: 565_500,
      paidCents: 0,
    },
    expenses: { items: [], count: 0, totalCents: 0, paidCents: 0 },
  },
};

/**
 * What the server answers after week 3 has been approved (Story 3.4).
 *
 * The *whole* payload, because that is what the command returns: the week's
 * status and version move, `approvable` goes false, and `nextPaymentDue`
 * advances to week 4 because an approved week is no longer waiting to fall
 * due. Restating all four here rather than patching one field is what makes
 * the component test able to fail — a sheet that read its status from a copy
 * it was holding would keep showing "Due This Week" against this response.
 */
export const CLAIM_FINANCIALS_APPROVED = {
  status: 200,
  body: {
    ...CLAIM_FINANCIALS.body,
    summary: {
      ...CLAIM_FINANCIALS.body.summary,
      nextPaymentDue: "2026-04-26",
    },
    schedule: CLAIM_FINANCIALS.body.schedule.map((week) =>
      week.weekNo === 3
        ? {
            ...week,
            status: "payment_scheduled" as const,
            version: week.version + 1,
            approvable: false,
          }
        : week,
    ),
  },
};

/**
 * The 409 a stale approval gets: a problem document carrying the fresh payload
 * *and* the row's current status.
 *
 * `paymentStatus` is `paid` here, which is the more interesting of the two
 * conflicts — the batch disbursed the week while the sheet was open — because
 * it is the one whose message must not say "someone else approved it".
 */
export const APPROVAL_CONFLICT_PAID = {
  status: 409,
  body: {
    type: "/problems/stale-payment",
    title: "Conflict",
    status: 409,
    detail:
      "This payment has already moved on — it was approved or disbursed while you were looking at it. The current figures are attached.",
    paymentStatus: "paid",
    financials: {
      ...CLAIM_FINANCIALS.body,
      schedule: CLAIM_FINANCIALS.body.schedule.map((week) =>
        week.weekNo === 3
          ? { ...week, status: "paid" as const, version: week.version + 1, approvable: false }
          : week,
      ),
    },
  },
};

/**
 * The auto-generated action checklist (Story 3.5).
 *
 * Six rows, which is the cap — deliberately, so a component test that lost the
 * cap would have to lose it against a payload that is already at it. The
 * contents are chosen to cover every branch the card draws in one render: a
 * command-only row (`approve`), a document row whose command advances with its
 * state, an OSHA row, two enabled deep links, and a disabled seam row with its
 * sentence.
 *
 * **In the server's order, high first.** The card renders `items` as it
 * arrives; a fixture in a different order is how a component that re-sorted
 * would go unnoticed.
 */
export const CLAIM_ACTIONS = {
  status: 200,
  body: {
    cap: 6,
    paddingFloor: 3,
    rulesVersion: 1,
    items: [
      {
        id: "assessment_approval:approve",
        key: "assessment_approval",
        label: "Approve the claim assessment",
        urgency: "high",
        target: "approve",
        enabled: true,
        disabledReason: null,
        command: "approve_assessment",
        documentId: null,
        documentVersion: null,
      },
      {
        id: "siu_escalation:fraud",
        key: "siu_escalation",
        label: "Escalate to SIU — fraud indicators on file",
        urgency: "high",
        target: "fraud",
        enabled: false,
        disabledReason: "Available with AI Insights — Epic 6",
        command: null,
        documentId: null,
        documentVersion: null,
      },
      {
        id: "surgical_pre_auth:documents",
        key: "surgical_pre_auth",
        label: "Authorize the surgical pre-approval",
        urgency: "high",
        target: "documents",
        enabled: true,
        disabledReason: null,
        command: "mark_document_reviewed",
        documentId: 41,
        documentVersion: 2,
      },
      {
        id: "bill_review:bills",
        key: "bill_review",
        label: "Review medical bills awaiting approval",
        urgency: "medium",
        target: "bills",
        enabled: true,
        disabledReason: null,
        command: null,
        documentId: null,
        documentVersion: null,
      },
      {
        id: "osha_log:overview",
        key: "osha_log",
        label: "Record the injury on the OSHA 300 log",
        urgency: "medium",
        target: "overview",
        enabled: true,
        disabledReason: null,
        command: "mark_osha_logged",
        documentId: null,
        documentVersion: null,
      },
      {
        id: "diary_check_in:diary",
        key: "diary_check_in",
        label: "Log the weekly diary check-in",
        urgency: "low",
        target: "diary",
        // **Live since Story 4.2**, which deleted the target from the server's
        // `SEAM_REASONS` — the whole of what enabling a seam costs on that
        // side. On this side it needed one addition to
        // `ActionsCard.NAVIGABLE_FROM_OVERVIEW`, without which an enabled row
        // renders no control at all.
        //
        // `command` stays null, and that is the story's decision rather than an
        // omission: the *note* is the completion, so there is no fifth
        // `ActionCommand` and no ✓ on this row.
        enabled: true,
        disabledReason: null,
        command: null,
        documentId: null,
        documentVersion: null,
      },
    ],
  },
};

/**
 * A checklist with a live `meetings` row — Story 4.1's deep link.
 *
 * `enabled: true` and `disabledReason: null` because Story 4.1 deleted the
 * target from the server's `SEAM_REASONS`, which is the whole of what enabling
 * a seam costs. The SPA has no membership test to update, so this fixture is
 * the only place the change is visible on this side of the wire.
 */
export const CLAIM_ACTIONS_MEETINGS = {
  status: 200,
  body: {
    ...CLAIM_ACTIONS.body,
    items: [
      {
        id: "modified_duty:meetings",
        key: "modified_duty",
        label: "Schedule the modified-duty review with the plant",
        urgency: "medium",
        target: "meetings",
        enabled: true,
        disabledReason: null,
        command: null,
        documentId: null,
        documentVersion: null,
      },
    ],
  },
};

/** A claim with nothing outstanding — the card's empty state (NFR-3). */
export const CLAIM_ACTIONS_EMPTY = {
  status: 200,
  body: { ...CLAIM_ACTIONS.body, items: [] },
};

/**
 * What the server answers after the assessment is approved.
 *
 * The whole case file, because that is what the command returns: the status
 * chip moves and the version increments. Restating both is what makes the
 * component test able to fail — a header rendering a copy it was holding would
 * keep saying "CH Assessment Process".
 */
export const CLAIM_DETAIL_APPROVED = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_TREATMENT.body,
    version: CLAIM_DETAIL_TREATMENT.body.version + 1,
    header: { ...CLAIM_DETAIL_TREATMENT.body.header, status: "ch_approved" },
  },
};

/** The same, for the OSHA entry: the flag moves and the version increments. */
export const CLAIM_DETAIL_OSHA_LOGGED = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_TREATMENT.body,
    version: CLAIM_DETAIL_TREATMENT.body.version + 1,
    header: { ...CLAIM_DETAIL_TREATMENT.body.header, oshaLogged: true },
  },
};

/**
 * The 409 a stale completion gets — a problem document carrying the fresh
 * case file, exactly as Story 2.3's inline edit does.
 */
export const ASSESSMENT_CONFLICT = {
  status: 409,
  body: {
    type: "/problems/stale-write",
    title: "Conflict",
    status: 409,
    detail:
      "This claim was changed by someone else while you were editing. The current values are attached.",
    claim: CLAIM_DETAIL_APPROVED.body,
  },
};

/**
 * Two meetings, one ahead and one behind — Story 4.1's list fixture.
 *
 * `status` is a field rather than something the card works out, which is the
 * point of the pair: `done` on a meeting whose date is *in the future* is a
 * shape only the server can produce, and a component that re-derived the
 * status from `meetingDate` would render it as upcoming and fail the test.
 */
export const MEETING_UPCOMING = {
  id: 501,
  claimId: "WC-20017",
  workerName: "Marcus Webb",
  meetingType: "rtw_conference",
  meetingDate: "2099-09-01",
  meetingTime: "10:30:00",
  location: "Phone",
  notes: "Confirm light-duty availability and physician clearance status.",
  participants: ["employee", "employer_hr"],
  isDone: false,
  version: 1,
  createdAt: "2026-08-17T09:00:00Z",
  status: "upcoming",
};

export const MEETING_DONE = {
  id: 502,
  claimId: "WC-20017",
  workerName: "Marcus Webb",
  meetingType: "claim_review_supervisor",
  meetingDate: "2099-09-02",
  meetingTime: "15:00:00",
  location: "Video Call",
  notes: "Review treatment progress and confirm reserve adequacy.",
  participants: ["ncm", "supervisor"],
  isDone: true,
  version: 3,
  createdAt: "2026-08-17T09:00:00Z",
  status: "done",
};

export const MEETINGS = {
  status: 200,
  body: { items: [MEETING_UPCOMING, MEETING_DONE], nextCursor: null, total: 2, upcomingCount: 1 },
};

/**
 * The default meetings route — **and it answers `?day=` differently**.
 *
 * The stub used to ignore every query parameter, which made a whole class of
 * request wrong-but-green: deleting `{ day: today }` from `NotesSubTab` (or
 * `{ asOf }` from `MeetingsSubTab`) left all eighty-two diary tests passing,
 * because the stub answered the same body either way and the components render
 * whatever they are handed. Branching on the parameter is what gives those
 * requests something to be wrong about — a day-filtered read that came back
 * with the whole book would now render two cards under "Today's Meetings" and
 * fail on the count.
 *
 * A test that wants one specific body still passes `meetings:` explicitly; this
 * is only the default.
 */
export const MEETINGS_BY_DAY = (url: string) =>
  url.includes("day=") ? MEETINGS_TODAY : MEETINGS;

export const MEETINGS_EMPTY = {
  status: 200,
  body: { items: [], nextCursor: null, total: 0, upcomingCount: 0 },
};

/**
 * One meeting on the day the Notes sub-tab asks for — Story 4.2's summary.
 *
 * `upcomingCount` is deliberately *larger* than `items.length`: it is the whole
 * book's count and the list is one filtered day of it, so a component that
 * counted the rows instead of reading the field renders 1 where the server said
 * 3 and this fixture fails it.
 */
export const MEETINGS_TODAY = {
  status: 200,
  body: {
    items: [{ ...MEETING_UPCOMING, id: 601, meetingDate: "2099-01-01" }],
    nextCursor: null,
    total: 1,
    upcomingCount: 3,
  },
};

/** What the ✓ answers: the row with `isDone`, `version` and `status` moved. */
export const MEETING_COMPLETED = {
  status: 200,
  body: { ...MEETING_UPCOMING, isDone: true, version: 2, status: "done" },
};

export const MEETING_CREATED = {
  status: 201,
  body: { ...MEETING_UPCOMING, id: 503, version: 1 },
};

/**
 * The 409 a stale ✓ or ✕ gets — a problem document carrying the fresh meeting
 * under `meeting`, exactly as Story 2.3's inline edit carries a claim.
 */
export const MEETING_CONFLICT = {
  status: 409,
  body: {
    type: "/problems/stale-write",
    title: "Conflict",
    status: 409,
    detail:
      "This meeting was changed by someone else while you were looking at it. The current version is attached.",
    meeting: MEETING_COMPLETED.body,
  },
};

/**
 * Two diary notes, newest first — Story 4.2's list fixture.
 *
 * One tagged and one not, because the tag is nullable and the untagged branch
 * is the one a component is most likely to render as `📎 null`.
 */
export const DIARY_NOTE_TAGGED = {
  id: 901,
  claimId: "WC-20017",
  workerName: "Marcus Webb",
  noteText: "Called the plant; light duty available from Monday.",
  notedAt: "2026-08-17T14:05:00Z",
};

export const DIARY_NOTE_UNTAGGED = {
  id: 900,
  claimId: null,
  workerName: null,
  noteText: "Team huddle: reserve review moved to Thursday.",
  notedAt: "2026-08-16T09:00:00Z",
};

export const DIARY_NOTES = {
  status: 200,
  body: { items: [DIARY_NOTE_TAGGED, DIARY_NOTE_UNTAGGED], nextCursor: null, total: 2 },
};

export const DIARY_NOTES_EMPTY = {
  status: 200,
  body: { items: [], nextCursor: null, total: 0 },
};

export const DIARY_NOTE_CREATED = {
  status: 201,
  body: { ...DIARY_NOTE_TAGGED, id: 902, noteText: "A brand new note." },
};

/** The 422 an empty or unstorable note gets — inline at the input, never a dialog. */
export const DIARY_NOTE_INVALID = {
  status: 422,
  body: {
    type: "/problems/invalid-patch",
    title: "Unprocessable Content",
    status: 422,
    detail: "noteText cannot be empty",
  },
};

/**
 * The 404 a claim tag outside the caller's book gets.
 *
 * A refusal that will answer the same way for ever, which is why the input must
 * not render it as "try again in a moment" — the whole point of routing note
 * refusals through `feedbackFromError`.
 */
export const DIARY_NOTE_CLAIM_NOT_FOUND = {
  status: 404,
  body: {
    type: "/problems/note-claim-not-found",
    title: "Not Found",
    status: 404,
    detail: "No claim WC-20017 in your caseload.",
  },
};

/**
 * The 404 a note that **was written** gets — `/problems/note-not-readable`.
 *
 * The one refusal on this route that is not a failure: the row is committed and
 * audited and only the scoped re-read failed, so the console must clear the
 * draft and re-read the list rather than leave a Save button pointed at a
 * duplicate in a table with no delete.
 */
export const DIARY_NOTE_WRITTEN_NOT_READABLE = {
  status: 404,
  body: {
    type: "/problems/note-not-readable",
    title: "Not Found",
    status: 404,
    detail:
      "Note 902 was saved, but it can no longer be read back from your diary — " +
      "your caseload changed while it was being written. Do not write it again; reload the list.",
  },
};

/**
 * The six seeded quick templates — Story 4.3's reference data.
 *
 * Keys, labels and default recipient sets verbatim from the seed migration, so
 * a component test asserting "the button row is the server's six, in the
 * server's order" is asserting against the real vocabulary. The template *text*
 * is deliberately absent from this payload, exactly as it is on the wire: a
 * client holding it would be one `replace()` from merging in the browser.
 *
 * Every `defaultRecipients` list is in the **vocabulary's own order** —
 * `employee, employer_hr, ncm, treating_physician, supervisor, attorney` — which
 * is how the rows are seeded and how the command stores every set it is handed.
 * Two of the migration's six were written out of that order and this fixture was
 * not, which made "verbatim" false in exactly the place a reader would trust it;
 * the migration is the half that moved.
 */
export const EMAIL_TEMPLATES = {
  status: 200,
  body: {
    items: [
      {
        templateKey: "three_point_contact",
        label: "3-Point Contact",
        defaultRecipients: ["employee", "employer_hr", "treating_physician"],
      },
      {
        templateKey: "rtw_offer",
        label: "RTW Offer",
        defaultRecipients: ["employee", "employer_hr", "ncm"],
      },
      {
        templateKey: "ncm_referral",
        label: "NCM Referral",
        defaultRecipients: ["employer_hr", "ncm", "treating_physician"],
      },
      {
        templateKey: "status_update",
        label: "Status Update",
        defaultRecipients: ["employer_hr", "supervisor"],
      },
      {
        templateKey: "ime_request",
        label: "IME Request",
        defaultRecipients: ["ncm", "treating_physician"],
      },
      {
        templateKey: "settlement_notice",
        label: "Settlement Notice",
        defaultRecipients: ["employee", "attorney"],
      },
    ],
  },
};

/**
 * One template, merged — what `GET …/merged` answers for the RTW Offer.
 *
 * **Merged, with no `{{…}}` anywhere**, which is the whole contract: the server
 * resolved every placeholder or refused, and the SPA renders what came back. The
 * square-bracketed prompt is left literal on purpose — it is handler-fill text,
 * not a merge field (AD-2), and a component test that saw it disappear would be
 * seeing a browser-side merge nobody asked for.
 *
 * `recipients` deliberately differs from the composer's blank default (Employee
 * alone) and from `MERGED_STATUS_UPDATE` below, so "the checkbox set is replaced
 * by exactly this template's" has something to be wrong about.
 */
export const MERGED_RTW_OFFER = {
  status: 200,
  body: {
    claimId: "WC-20017",
    subject: "Return-to-Work Offer — WC Claim WC-20017",
    body:
      "Dear Marcus Webb,\n\nFollowing your Fall from Height injury of 2026-03-22, " +
      "we are pleased to offer transitional duty.\n\n" +
      "[Transitional Duty — to be completed by supervisor]\n\nKaya Johnson",
    recipients: ["employee", "employer_hr", "ncm"],
  },
};

/** A second merge, so a test can watch one template replace another. */
export const MERGED_STATUS_UPDATE = {
  status: 200,
  body: {
    claimId: "WC-20017",
    subject: "Claim Status Update — WC-20017 (140 days open)",
    body:
      "Current stage: Treatment. Current status: CH Assessment Process.\n\n" +
      "[Please add current activity summary]\n\nKaya Johnson",
    recipients: ["employer_hr", "supervisor"],
  },
};

/**
 * A meeting's confirmation letter — the convert-to-email prefill (AC 5).
 *
 * `recipients` is that meeting's participants, which is the identity mapping the
 * shared vocabulary buys: the prototype substring-matched participant *labels*,
 * which is why "Employer HR" happened to match "employer".
 */
export const MEETING_EMAIL_DRAFT = {
  status: 200,
  body: {
    claimId: "WC-20017",
    subject: "Meeting Confirmation: RTW Conference — WC-20017",
    body:
      "This confirms the RTW Conference scheduled for Tuesday, September 1, 2099 at 10:30 AM.\n" +
      "Location: Phone\n\nPlease confirm your attendance.\n\nKaya Johnson",
    recipients: ["employee", "employer_hr"],
  },
};

/**
 * Two logged emails, newest first — Story 4.3's list fixture.
 *
 * One claim-linked and one free-composed, because `claimId`/`workerName` are
 * nullable together (the ERD's `CLAIM |o--o{ EMAIL_LOG`) and the untagged branch
 * is the one a card is most likely to render as `· null`. The free one also
 * carries `body: null` and `priority: "urgent"`, so the two branches a card can
 * take on a single row are both exercised in one render.
 */
export const EMAIL_LOG_TAGGED = {
  id: 801,
  claimId: "WC-20017",
  workerName: "Marcus Webb",
  templateKey: "rtw_offer",
  subject: "Return-to-Work Offer — WC Claim WC-20017",
  body:
    "Dear Marcus Webb, following your Fall from Height injury we are pleased to offer " +
    "transitional duty beginning Monday, subject to your treating physician's clearance.",
  priority: "normal",
  recipients: ["employee", "employer_hr", "ncm"],
  sentAt: "2026-08-18T14:05:00Z",
};

export const EMAIL_LOG_FREE = {
  id: 800,
  claimId: null,
  workerName: null,
  templateKey: null,
  subject: "Plant walkthrough next Tuesday",
  body: null,
  priority: "urgent",
  recipients: ["supervisor"],
  sentAt: "2026-08-17T09:00:00Z",
};

export const EMAIL_LOGS = {
  status: 200,
  body: { items: [EMAIL_LOG_TAGGED, EMAIL_LOG_FREE], nextCursor: null, total: 2 },
};

export const EMAIL_LOGS_EMPTY = {
  status: 200,
  body: { items: [], nextCursor: null, total: 0 },
};

export const EMAIL_CREATED = {
  status: 201,
  body: { ...EMAIL_LOG_TAGGED, id: 802, subject: "A brand new letter." },
};

/**
 * The 404 an email that **was logged** gets — `/problems/email-not-readable`.
 *
 * `DIARY_NOTE_WRITTEN_NOT_READABLE`'s twin, on the table with the least
 * recourse: the row is committed and audited by the time this is answered and
 * only the scoped re-read failed, so a composer that treated it as a refusal
 * would leave an intact draft over an enabled Send — and `email_log` has no edit
 * and no delete to take the second copy back. The `detail` is the server's own,
 * and it is the sentence the console must not contradict: *do not send it
 * again.*
 */
export const EMAIL_WRITTEN_NOT_READABLE = {
  status: 404,
  body: {
    type: "/problems/email-not-readable",
    title: "Not Found",
    status: 404,
    detail:
      "Email 802 was logged, but it can no longer be read back from your sent log — " +
      "your caseload changed while it was being written. Do not send it again; reload the list.",
  },
};

/**
 * The 404 a claim reference outside the caller's book gets on a send.
 *
 * A refusal that will answer the same way for ever, and the one that proves the
 * composer routes 404s through `feedbackFromError`'s readable set rather than
 * flattening them to "Could not save. Try again in a moment."
 */
export const EMAIL_CLAIM_NOT_FOUND = {
  status: 404,
  body: {
    type: "/problems/email-claim-not-found",
    title: "Not Found",
    status: 404,
    detail: "No claim WC-20017 in your caseload.",
  },
};

/** The 422 an empty or unstorable composition gets — inline, never a dialog. */
export const EMAIL_INVALID = {
  status: 422,
  body: {
    type: "/problems/invalid-patch",
    title: "Unprocessable Content",
    status: 422,
    detail: "Subject cannot be empty",
  },
};

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
    // `init` is read for the **method**, which nothing needed until Story 4.1
    // put four routes behind two URLs. `openapi-fetch` calls
    // `fetch(new Request(...))`, so in practice the method arrives on the
    // request object rather than here; both are read so a caller that passes a
    // string URL with an init is handled too.
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
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
      if (url.includes("/api/dashboard/summary")) {
        return answer(routes.dashboardSummary ?? DASHBOARD_SUMMARY);
      }
      // Story 5.2's. Order against the summary above is not load-bearing — the
      // two paths are disjoint, unlike the `/api/claims…` block below — but it
      // keeps the dashboard's two routes readable together.
      if (url.includes("/api/dashboard/handler-benchmarks")) {
        return answer(routes.handlerBenchmarks ?? HANDLER_BENCHMARKS);
      }
      // Story 5.3's. Disjoint from both dashboard paths above, so the order is
      // readability rather than routing.
      if (url.includes("/api/dashboard/charts")) {
        return answer(routes.dashboardCharts ?? DASHBOARD_CHARTS);
      }
      // Story 5.4's. Disjoint from the three dashboard paths above, so the
      // order is readability rather than routing — but `answerFor` rather than
      // `answer`, because this route is paged and a stub has to be able to see
      // the cursor. The default answers page one whatever the URL says, which
      // is what every test that does not walk the table wants.
      if (url.includes("/api/dashboard/priority-claims")) {
        return answerFor(routes.priorityClaims ?? PRIORITY_CLAIMS, url);
      }
      // Story 4.1's four, before every `/api/claims` case below. **Not because
      // the case file would swallow them**: the catch-all tests `/api/claims/`
      // with a trailing slash and `/api/claims-diary/meetings` does not contain
      // that — a rationale this file asserted five times and which was never
      // true. They are first because they are the most specific prefixes and
      // the block reads in order. Method is read as well as path, because all
      // four routes share two URLs.
      // Story 4.3's five, **before the meetings block**. That ordering is
      // load-bearing for exactly one of them: `/claims-diary/meetings/{id}/
      // email-draft` contains `/api/claims-diary/meetings`, so the list below
      // would otherwise answer the composer's request with a page of meetings.
      // The rest are here to keep the 4.3 routes readable in one place.
      if (url.includes("/email-draft")) {
        return answerFor(routes.meetingEmailDraft ?? MEETING_EMAIL_DRAFT, url);
      }
      if (url.includes("/api/claims-diary/email-templates")) {
        // The merge first: its URL *contains* the template list's.
        if (url.includes("/merged")) {
          return answerFor(routes.mergedTemplate ?? MERGED_RTW_OFFER, url);
        }
        return answerFor(routes.emailTemplates ?? EMAIL_TEMPLATES, url);
      }
      if (url.includes("/api/claims-diary/emails")) {
        const method =
          typeof input === "string" || input instanceof URL
            ? (init?.method ?? "GET")
            : (input as Request).method;
        // POST before GET, the meetings block's arrangement: both share one URL.
        if (method === "POST") {
          return answerFor(routes.sendEmail ?? EMAIL_CREATED, url);
        }
        return answerFor(routes.emails ?? EMAIL_LOGS, url);
      }
      if (url.includes("/api/claims-diary/meetings")) {
        const method =
          typeof input === "string" || input instanceof URL
            ? (init?.method ?? "GET")
            : (input as Request).method;
        if (method === "POST") {
          return answerFor(routes.scheduleMeeting ?? MEETING_CREATED, url);
        }
        if (method === "PATCH") {
          return answerFor(routes.completeMeeting ?? MEETING_COMPLETED, url);
        }
        if (method === "DELETE") {
          return answerFor(routes.deleteMeeting ?? { status: 204, body: null }, url);
        }
        return answerFor(routes.meetings ?? MEETINGS_BY_DAY, url);
      }
      // Story 4.2's two, beside the meetings block and for its reason.
      if (url.includes("/api/claims-diary/notes")) {
        const method =
          typeof input === "string" || input instanceof URL
            ? (init?.method ?? "GET")
            : (input as Request).method;
        if (method === "POST") {
          return answerFor(routes.addDiaryNote ?? DIARY_NOTE_CREATED, url);
        }
        return answerFor(routes.diaryNotes ?? DIARY_NOTES, url);
      }
      if (url.includes("/api/claims/queue")) {
        // The whole URL, query string included, so a stub can branch on the
        // filter or the cursor — see `StubRouteFor`.
        return answerFor(routes.claimsQueue ?? CLAIM_QUEUE, url);
      }
      // Before the case file, for the mirror of the reason the queue is: the
      // document sheet's URL *contains* a claim path, so a stub matching the
      // case file first would answer a viewer's request with a case file.
      if (url.includes("/documents/") && url.includes("/content")) {
        return answerFor(routes.documentSheet ?? DOCUMENT_SHEET_FROI, url);
      }
      // Before the case file too, and for exactly that reason:
      // `/api/claims/WC-1/financials` contains `/api/claims/`.
      // Before the financials read, because an approval answers *with* a
      // financials payload and a stub that matched the read first would make
      // every approval look like it had succeeded.
      if (url.includes("/payments/approvals")) {
        return answerFor(routes.approvePayment ?? CLAIM_FINANCIALS_APPROVED, url);
      }
      if (url.includes("/financials")) {
        return answerFor(routes.claimFinancials ?? CLAIM_FINANCIALS, url);
      }
      // Story 3.5's four, all before the case file and all for the same
      // reason the financials read is: every one of their URLs contains
      // `/api/claims/`, so the case file would swallow them.
      if (url.includes("/assessment/approval")) {
        return answerFor(routes.approveAssessment ?? CLAIM_DETAIL_APPROVED, url);
      }
      if (url.includes("/osha-log")) {
        return answerFor(routes.oshaLog ?? CLAIM_DETAIL_OSHA_LOGGED, url);
      }
      if (url.includes("/documents/") && url.includes("/review")) {
        return answerFor(routes.documentReview ?? CLAIM_DETAIL_TREATMENT, url);
      }
      if (url.includes("/actions")) {
        return answerFor(routes.claimActions ?? CLAIM_ACTIONS, url);
      }
      // After the queue, deliberately: the two share a prefix, and the
      // server resolves the same ambiguity the same way (the queue route is
      // declared first). A stub that matched detail first would answer the
      // queue's request with a case file and no test would say why.
      if (url.includes("/api/claims/")) {
        return answerFor(routes.claimDetail ?? CLAIM_DETAIL_TREATMENT, url);
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
