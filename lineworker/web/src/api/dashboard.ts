/**
 * Server state for the portfolio KPI cards (FR-SUP-1/A).
 *
 * AD-1 and AD-9 between them leave this hook nothing to do but fetch, the same
 * as `useTopBarStats`: ten figures, two chip counts and two thresholds, all
 * computed by `services/worklist` over the caller's scope, all rendered
 * verbatim. There is deliberately nothing here to fall back on — the
 * prototype's `renderSV` folds the whole claim array in the browser, and a
 * client-side total is precisely what this replaces.
 *
 * The thresholds ride along with the figures rather than being fetched or
 * known separately, which is what lets two card captions quote a rule number
 * without the SPA holding one.
 */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  toFilterKey,
  toQueryParams,
  toSegmentationParams,
  type DrillFilters,
} from "@/features/dashboard/drill/filters";

import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { components, paths } from "./schema";

export type PortfolioSummary =
  components["schemas"]["PortfolioSummaryResponse"];

export function useDashboardSummary() {
  return useQuery({
    queryKey: queryKeys.dashboard.summary,
    queryFn: async (): Promise<PortfolioSummary> => {
      const { data } = await api.GET("/dashboard/summary");
      return data!;
    },
    // The `/stats/*` aggregates' staleness, and for their reason. Note what
    // this does *not* do: nothing in the app polls, and `refetchOnWindowFocus`
    // is off, so these ten figures are fixed from the moment the dashboard
    // route mounts until it unmounts — the window only decides whether the
    // *next* mount refetches or serves the cache. Thirty seconds is still the
    // right number for that job. It matches the other aggregate hooks, so the
    // dashboard and the top bar above it go stale on one schedule instead of
    // two; and it bounds how old a figure can be when a supervisor navigates
    // away and back, without re-folding a hundred rows every time she does.
    // Polling is out of scope for this story — the dashboard is a read of a
    // portfolio that changes on the scale of days.
    staleTime: 30_000,
  });
}

export type HandlerBenchmarks =
  components["schemas"]["HandlerBenchmarksResponse"];
export type HandlerBenchmark =
  components["schemas"]["HandlerBenchmarkResponse"];
/** The complexity grade's three values — snake_case tokens; the UI owns labels. */
export type ComplexityBand = components["schemas"]["ComplexityBand"];
/** On Track / Watch / Attention, likewise. */
export type CycleStatus = components["schemas"]["CycleStatus"];

/**
 * Server state for the handler performance table (FR-SUP-2/3/B).
 *
 * `useDashboardSummary`'s shape and its emptiness, for the same reason: every
 * figure in the table — the rank, the composite, the bar's percentage, the
 * deviation, the RTW rate, the complexity score and both chips — is decided by
 * `services/worklist` over the caller's scope, and this hook exists to fetch
 * them and nothing else.
 *
 * **No `select`.** That is deliberate rather than incidental: a `select` here
 * would be the single most plausible home for a re-derived ranking or a peer
 * ratio worked out in the browser, and it is the reason `api/meetings.ts` and
 * `api/emails.ts` had to join `noDerivation.test.ts`'s `ROOT_FILES`. With no
 * transform there is nothing for that guard to have to read.
 *
 * The same `staleTime` as the summary beside it, so the two halves of the
 * dashboard go stale together rather than one of them refetching under the
 * other. Nothing polls; the window only decides whether the *next* mount
 * refetches or serves the cache.
 */
export function useHandlerBenchmarks() {
  return useQuery({
    queryKey: queryKeys.dashboard.handlerBenchmarks,
    queryFn: async (): Promise<HandlerBenchmarks> => {
      const { data } = await api.GET("/dashboard/handler-benchmarks");
      return data!;
    },
    staleTime: 30_000,
  });
}

export type PortfolioCharts =
  components["schemas"]["PortfolioChartsResponse"];
/**
 * An enum-keyed distribution — the two donuts and the recovery bars.
 *
 * Read off `PortfolioCharts` rather than named directly, because the generated
 * schema name for a Pydantic generic is
 * `DistributionResponse_CategoryCountResponse_` — a mangling that is stable but
 * unreadable, and one that would put the code generator's naming convention in
 * every consuming component's import list.
 */
export type CategoryDistribution = PortfolioCharts["byStage"];
/** A free-text distribution — injury types and states. */
export type LabelDistribution = PortfolioCharts["byInjuryType"];
/** The employer spend series, whose items carry cents rather than a count. */
export type EmployerDistribution = PortfolioCharts["byEmployer"];

/**
 * Server state for the seven analytics surfaces (FR-SUP-4/C).
 *
 * `useDashboardSummary`'s shape and its emptiness, for the same reason: every
 * figure in every chart — each count, the orderings, the two top-N cuts, the
 * employer totals and the four SLA verdicts — is decided by `services/worklist`
 * over the caller's scope, and this hook exists to fetch them and nothing else.
 *
 * **No `select`**, deliberately, and it matters more here than on either
 * sibling: a `select` over this payload is the single most plausible home for a
 * re-sorted series, an "other" bucket rolled up from the truncated tail, or a
 * percentage worked out from `items` and `total` — the three things AD-1
 * forbids and the three things the response is shaped to make unnecessary. With
 * no transform there is nothing for `noDerivation.test.ts` to have to read, and
 * `api/dashboard.ts` stays out of its `ROOT_FILES`.
 *
 * The same `staleTime` as the two hooks above, so the three halves of the
 * dashboard go stale together rather than one of them refetching under the
 * others. Nothing polls; the window only decides whether the *next* mount
 * refetches or serves the cache.
 */
export function useDashboardCharts() {
  return useQuery({
    queryKey: queryKeys.dashboard.charts,
    queryFn: async (): Promise<PortfolioCharts> => {
      const { data } = await api.GET("/dashboard/charts");
      return data!;
    },
    staleTime: 30_000,
  });
}

export type PriorityClaims =
  components["schemas"]["PriorityClaimsResponse"];
export type PriorityClaimRow =
  components["schemas"]["PriorityClaimRowResponse"];
/** The severity chip's three values — snake_case tokens; the UI owns labels. */
export type RiskBand = PriorityClaimRow["severityBand"];

/**
 * Server state for the top-30 priority worklist (FR-SUP-5/D).
 *
 * `useHandlerBenchmarks`' shape and its emptiness, for the same reason: the
 * population, the ordering, the cap, every severity band, the fraud tint and
 * the whole Priority Next Best Action column are decided by `services/worklist`
 * over the caller's scope, and this hook exists to fetch them and nothing else.
 *
 * **No `select`**, deliberately, and it matters as much here as on the charts:
 * a `select` over this payload is the single most plausible home for a re-sorted
 * table, a client-side cut at thirty, or a fraud tint decided from `fraudScore`
 * and the `fraudFlagScoreMin` published beside it — the three things AD-1
 * forbids and the three things the response is shaped to make unnecessary. With
 * no transform there is nothing for `noDerivation.test.ts` to have to read, and
 * `api/dashboard.ts` stays out of its `ROOT_FILES`.
 *
 * The same `staleTime` as the three hooks above, so the four sections of the
 * dashboard go stale together rather than one of them refetching under the
 * others. Nothing polls; the window only decides whether the *next* mount
 * refetches or serves the cache.
 */
export function usePriorityClaims() {
  return useQuery({
    queryKey: queryKeys.dashboard.priorityClaims,
    queryFn: async (): Promise<PriorityClaims> => {
      const { data } = await api.GET("/dashboard/priority-claims");
      return data!;
    },
    staleTime: 30_000,
  });
}

/**
 * The pages "Show more" has walked, accumulated — `useStageGroupPages`' shape.
 *
 * The only other cursor-paged surface in the console, copied deliberately
 * rather than reinvented: `initialPageParam` is the cursor the base query
 * already holds, `getNextPageParam` reads the server's `nextCursor` and nothing
 * computes an offset. `enabled` is the caller's, so the request goes out on the
 * first "Show more" rather than on mount — the first page is already in
 * `usePriorityClaims`' entry, and fetching it twice would be a second read of
 * the whole scoped book.
 *
 * `firstCursor !== null` in the `enabled` conjunction because a worklist that
 * fits on one page has no cursor to start from, and `initialPageParam: null`
 * would ask the endpoint for page one again.
 */
export function usePriorityClaimPages(firstCursor: string | null, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.dashboard.priorityClaimPages(firstCursor),
    initialPageParam: firstCursor,
    queryFn: async ({ pageParam }): Promise<PriorityClaims> => {
      const { data } = await api.GET("/dashboard/priority-claims", {
        params: { query: { cursor: pageParam ?? undefined } },
      });
      return data!;
    },
    getNextPageParam: (last: PriorityClaims) => last.nextCursor ?? undefined,
    enabled: enabled && firstCursor !== null,
    staleTime: 15_000,
  });
}

export type DrillClaims = components["schemas"]["DrillClaimsResponse"];
export type DrillClaimRow = components["schemas"]["DrillClaimRowResponse"];
export type AppliedFilter = components["schemas"]["AppliedFilterResponse"];

/**
 * Server state for one drill-through list (FR-SUP-D).
 *
 * `usePriorityClaims`' shape and its emptiness, for the same reason: the
 * population, the ordering, the marker, every risk band and every badge on
 * every row are decided by `services/worklist` over the caller's scope, and
 * this hook exists to fetch them and nothing else.
 *
 * **The filter set is in the key and in the request, from one source.**
 * `toFilterKey` and `toQueryParams` both read the same `DrillFilters` object,
 * so the cache entry and the query string cannot describe different questions —
 * which they would the first time somebody keyed on `JSON.stringify` and sent
 * an ordered `URLSearchParams`.
 *
 * **No `select`**, deliberately, and it matters as much here as on the four
 * hooks above: a `select` over this payload is the single most plausible home
 * for a client-side re-filter — the browser holds a *page of a filtered list*
 * and the filter set that produced it, so "just narrow it a bit more here"
 * looks like one line and is the AD-1 violation this whole endpoint exists to
 * make unnecessary. With no transform there is nothing for
 * `noDerivation.test.ts` to have to read, and `api/dashboard.ts` stays out of
 * its `ROOT_FILES`.
 *
 * The same `staleTime` as the four hooks above, so the dashboard and the list
 * it opens go stale on one schedule. Nothing polls.
 */
export function useDrillClaims(filters: DrillFilters) {
  return useQuery({
    queryKey: queryKeys.dashboard.drillClaims(toFilterKey(filters)),
    queryFn: async (): Promise<DrillClaims> => {
      const { data } = await api.GET("/dashboard/claims", {
        params: { query: toQueryParams(filters) },
      });
      return data!;
    },
    staleTime: 30_000,
  });
}

/**
 * The pages "Show more" has walked, accumulated — `usePriorityClaimPages`' shape.
 *
 * Copied deliberately rather than reinvented: `initialPageParam` is the cursor
 * the base query already holds, `getNextPageParam` reads the server's
 * `nextCursor`, and nothing computes an offset. `enabled` is the caller's, so
 * the request goes out on the first "Show more" rather than on mount — the
 * first page is already in `useDrillClaims`' entry, and fetching it twice would
 * be a second read of the whole scoped book.
 *
 * **The filter set travels with the cursor**, both in the key and in the
 * request. The server refuses a cursor replayed under a different filter set
 * (see `Cursor`), so sending the cursor alone would 400 every "Show more" on
 * every filtered list — and keying without the filters would hand one filter's
 * accumulated pages to another.
 */
export function useDrillClaimPages(
  filters: DrillFilters,
  firstCursor: string | null,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: queryKeys.dashboard.drillClaimPages(toFilterKey(filters), firstCursor),
    initialPageParam: firstCursor,
    queryFn: async ({ pageParam }): Promise<DrillClaims> => {
      const { data } = await api.GET("/dashboard/claims", {
        params: {
          query: { ...toQueryParams(filters), cursor: pageParam ?? undefined },
        },
      });
      return data!;
    },
    getNextPageParam: (last: DrillClaims) => last.nextCursor ?? undefined,
    enabled: enabled && firstCursor !== null,
    staleTime: 15_000,
  });
}

/**
 * The fraud panel's payload — the band distribution and the SIU pipeline.
 */
export type FraudPanel = components["schemas"]["FraudPanelResponse"];
/**
 * A per-handler slice of the SIU pipeline.
 *
 * Read off `FraudPanel` rather than named directly, `CategoryDistribution`'s
 * reason: the generated schema name for a Pydantic generic is
 * `DistributionResponse_HandlerCountResponse_`, a mangling that is stable and
 * unreadable and would put the code generator's naming convention in every
 * consuming component's import list.
 */
export type HandlerCountDistribution = FraudPanel["siuByHandler"];
/** The three fraud-score bands — snake_case tokens; the UI owns labels. */
export type FraudBand = NonNullable<
  paths["/dashboard/claims"]["get"]["parameters"]["query"]
>["filter[fraudBand]"];

/**
 * Server state for the analyst workspace's fraud panel (FR-AN-1, Story 7.1).
 *
 * `useDashboardCharts`' shape and its emptiness, for the same reason: every
 * figure — each band count, the zero-fill, the pipeline's ordering, both
 * populations and all four published thresholds — is decided by
 * `services/worklist` over the caller's scope, and this hook exists to fetch
 * them and nothing else.
 *
 * **No `select`**, deliberately, and this payload is the strongest invitation to
 * one on the whole dashboard: the browser is handed a band distribution *and*
 * the two edges it was banded at *and* a separate flagged population that
 * happens to equal the high band on today's data. A `select` is where somebody
 * would "just" derive one from the others — and the two numbers are only equal
 * by coincidence of a rule document, which is exactly what
 * `services/derivations/fraud_score_band.py` exists to keep apart. With no
 * transform there is nothing for `noDerivation.test.ts` to have to read, and
 * `api/dashboard.ts` stays out of its `ROOT_FILES`.
 *
 * The same `staleTime` as its five siblings, so the dashboard and the workspace
 * go stale on one schedule. Nothing polls.
 */
export function useFraudPanel(segmentation: DrillFilters) {
  return useQuery({
    queryKey: queryKeys.dashboard.fraud(toFilterKey(segmentation)),
    placeholderData: keepPreviousData,
    queryFn: async (): Promise<FraudPanel> => {
      const { data } = await api.GET("/dashboard/fraud", {
        params: { query: toSegmentationParams(segmentation) },
      });
      return data!;
    },
    staleTime: 30_000,
  });
}

export type FraudRates = components["schemas"]["FraudRatesResponse"];
/** The five orders a rate breakdown may be asked for — a closed server enum. */
export type FraudRateSort = components["schemas"]["FraudRateSort"];
/**
 * One row of each breakdown.
 *
 * Read off `FraudRates` rather than named directly, `CategoryDistribution`'s
 * reason: the generated schema name for a Pydantic generic is
 * `RateBreakdownResponse_InjuryTypeRateResponse_`, a mangling that is stable and
 * unreadable and would put the code generator's naming convention in every
 * consuming component's import list.
 */
export type InjuryTypeRate = FraudRates["byInjuryType"]["items"][number];
export type EmployerRate = FraudRates["byEmployer"]["items"][number];
export type HandlerRate = FraudRates["byHandler"]["items"][number];

/**
 * Which order each of the three rate breakdowns is being read in.
 *
 * One field per table rather than a single shared order, because the server
 * takes three independent parameters and the acceptance criterion is that
 * sorting one table leaves the other two alone. A shared value could not express
 * it and a shared *default* would make it untestable.
 */
export interface FraudRateSorts {
  injuryType: FraudRateSort;
  employer: FraudRateSort;
  handler: FraudRateSort;
}

/**
 * The order every breakdown starts in — the server's own default, restated.
 *
 * Restated rather than left unsent, and the difference matters for the cache
 * key: an omitted parameter and an explicit `rate_desc` are the same request to
 * the server and would be two different `sortKey`s here. Sending all three
 * always keeps one entry per *visible* order.
 */
export const DEFAULT_FRAUD_RATE_SORT: FraudRateSort = "rate_desc";

export const DEFAULT_FRAUD_RATE_SORTS: FraudRateSorts = {
  injuryType: DEFAULT_FRAUD_RATE_SORT,
  employer: DEFAULT_FRAUD_RATE_SORT,
  handler: DEFAULT_FRAUD_RATE_SORT,
};

/**
 * A stable string identifying one sort set, for a TanStack Query key.
 *
 * Built from the same object the request is built from — `toFilterKey`'s
 * arrangement one folder over — so the cache entry and the query string cannot
 * describe different questions. Written out field by field rather than
 * `JSON.stringify`, because that is key-insertion-ordered and two renders
 * setting the same three values in a different order would produce two entries
 * for one resource with nothing anywhere to say so.
 */
export function toFraudRateSortKey(sorts: FraudRateSorts): string {
  return `${sorts.injuryType}|${sorts.employer}|${sorts.handler}`;
}

/**
 * Server state for the three fraud-rate breakdowns (FR-AN-1, AC 3).
 *
 * **The sort set is in the key and in the request, from one source** —
 * `useDrillClaims`' rule applied to an order rather than to a filter. That is
 * what makes "the browser sorts nothing" observable rather than merely claimed:
 * changing a control changes the key, which issues a request, which returns rows
 * the server ordered. A client-side sort would show the same rows in the same
 * order and would never touch the network.
 *
 * **No `select`**, deliberately: a `select` over this payload is where a
 * re-order would live, and it would look like one line.
 *
 * **`placeholderData: keepPreviousData`, and it is the one option in this module
 * that is not `staleTime`.** The file's standing policy is minimal options and no
 * transform, so an addition here needs a reason the policy does not already
 * cover, and this one has it: all *three* tables ride this single query, keyed on
 * the whole sort set. Without it, changing one table's order re-keys the query,
 * drops `data` to `undefined`, and blanks the two tables nobody touched — three
 * skeleton blocks and three `aria-busy` flips announcing a load for two tables
 * whose rows have not changed and whose order has not been asked about. Keeping
 * the previous data is what lets `FraudPage` mark exactly the table that is
 * waiting, and it is *not* a transform: the rows on screen are still a server
 * answer, just the previous one, and `isPlaceholderData` says so.
 *
 * That does not weaken "the browser sorts nothing": the placeholder is the old
 * order because it is the old *response*, the awaited table says it is busy, and
 * the `<select>` renders the server's echoed `sort` once the answer lands
 * (`FraudRateTables.tsx`).
 *
 * The same `staleTime` as its siblings. Nothing polls.
 */
export function useFraudRates(sorts: FraudRateSorts, segmentation: DrillFilters) {
  return useQuery({
    queryKey: queryKeys.dashboard.fraudRates(
      toFraudRateSortKey(sorts),
      toFilterKey(segmentation),
    ),
    placeholderData: keepPreviousData,
    queryFn: async (): Promise<FraudRates> => {
      const { data } = await api.GET("/dashboard/fraud/rates", {
        params: {
          query: {
            "sort[injuryType]": sorts.injuryType,
            "sort[employer]": sorts.employer,
            "sort[handler]": sorts.handler,
            ...toSegmentationParams(segmentation),
          },
        },
      });
      return data!;
    },
    staleTime: 30_000,
  });
}

export type FraudRedFlags = components["schemas"]["FraudRedFlagsResponse"];

/**
 * Server state for the ranked red-flag clauses (AC 2, AD-10).
 *
 * `useFraudPanel`'s shape and its emptiness. What it fetches is **cached model
 * output**, not claim data: the ranking, the coverage figures, the generation
 * range and the model names all describe `ai_insight` rows, and the card that
 * renders them says so. Nothing here refreshes anything — `services/rag` owns
 * the writes (AD-12), and a cold cache is a 200 with an empty ranking rather
 * than work this hook could start.
 *
 * **No `select`**, deliberately: grouping, counting and ranking clauses in the
 * browser is precisely the classification the server refuses to make, and a
 * `select` is where a well-meaning "just merge the near-duplicates" would go.
 *
 * The same `staleTime` as its siblings, and no polling — a narrative that is
 * thirty seconds old is a dated answer rendered with its own timestamp, which is
 * the whole of AD-10's contract.
 */
export function useFraudRedFlags(segmentation: DrillFilters) {
  return useQuery({
    queryKey: queryKeys.dashboard.fraudRedFlags(toFilterKey(segmentation)),
    placeholderData: keepPreviousData,
    queryFn: async (): Promise<FraudRedFlags> => {
      const { data } = await api.GET("/dashboard/fraud/red-flags", {
        params: { query: toSegmentationParams(segmentation) },
      });
      return data!;
    },
    staleTime: 30_000,
  });
}

export type PortfolioTrends = components["schemas"]["PortfolioTrendsResponse"];
/** How wide one bucket is — a closed server enum; the UI owns the labels. */
export type TrendGrain = components["schemas"]["TrendGrain"];
/** Which date a claim is bucketed by: its FNOL date or its date of injury. */
export type TrendAnchor = components["schemas"]["TrendAnchor"];
/** The single dimension the five metrics may be split by, or `none`. */
export type TrendCohort = components["schemas"]["TrendCohort"];
/** The five series, in the order the server publishes and the section reads. */
export type TrendMetric = components["schemas"]["TrendMetric"];
/**
 * One line on one chart.
 *
 * Read off `PortfolioTrends` rather than named directly, `CategoryDistribution`'s
 * reason: naming `TrendSeriesResponse` here would put the code generator's
 * convention in every consuming component's import list, and the two would then
 * have to be renamed together.
 */
export type TrendSeries = PortfolioTrends["series"][number];
/** One bucket of one series — `value` is `null`, never `0`, when absent. */
export type TrendPoint = TrendSeries["points"][number];
/**
 * One period on the x-axis, from the window's own vocabulary.
 *
 * Published beside the series rather than only inside them (Story 7.3), so a
 * period control stays live when a filter or a cohort split empties the fold —
 * see `TrendBucketResponse` on the server for the defect that closes.
 */
export type TrendBucket = PortfolioTrends["buckets"][number];

/**
 * The three selectors the Trends header sets.
 *
 * **Three fields rather than one, and no date range.** The route also takes
 * `from`/`to`, and this story deliberately does not send them: the window is
 * `defaultBuckets` ending in the server's own `asOf` bucket, which is the answer
 * `windowFrom`/`windowTo` publish and the caption quotes. A date picker is a
 * control with its own refusals (`/problems/trend-range-too-wide`) and its own
 * URL question, and Story 7.3 owns what belongs in this workspace's address bar
 * — Story 7.1 deferred its own sort state there for the same reason. Adding the
 * two fields here later is an addition to this interface and to
 * `toTrendParamsKey`, and nothing else.
 */
export interface TrendParams {
  grain: TrendGrain;
  anchor: TrendAnchor;
  cohort: TrendCohort;
}

/**
 * The selectors the section starts on — the server's own defaults, restated.
 *
 * Restated rather than left unsent, `DEFAULT_FRAUD_RATE_SORTS`' ruling: an
 * omitted parameter and an explicit `month` are the same request to the server
 * and would be two different `paramsKey`s here. Sending all three always keeps
 * one cache entry per *visible* selector set.
 */
export const DEFAULT_TREND_PARAMS: TrendParams = {
  grain: "month",
  anchor: "fnol",
  cohort: "none",
};

/**
 * A stable string identifying one selector set, for a TanStack Query key.
 *
 * Built from the same object the request is built from — `toFraudRateSortKey`'s
 * arrangement — so the cache entry and the query string cannot describe
 * different questions. Written out field by field rather than `JSON.stringify`,
 * because that is key-insertion-ordered and two renders setting the same three
 * values in a different order would produce two entries for one resource with
 * nothing anywhere to say so.
 */
export function toTrendParamsKey(params: TrendParams): string {
  return `${params.grain}|${params.anchor}|${params.cohort}`;
}

/**
 * Server state for the analyst workspace's Trends section (FR-AN-2, Story 7.2).
 *
 * `useFraudPanel`'s shape and its emptiness, for the same reason: every bucket
 * boundary, every point, every null, every zero-fill, the low-confidence
 * verdict, the palette ordinal, both targets and both band edges are decided by
 * `services/worklist/trends.py` over the caller's scope, and this hook exists to
 * fetch them and nothing else.
 *
 * **No `select`**, deliberately, and this payload is a stronger invitation to
 * one than the fraud panel was: the browser is handed five metrics × N cohorts ×
 * M buckets, each point carrying a value *and* the claim count behind it, plus
 * the ceiling that decided `lowConfidence` and the two targets a reference line
 * is drawn at. A `select` is where "just fold the cohorts back together", "just
 * count the nulls" or "just re-band the low-confidence points" would live, and
 * each is one line. With no transform there is nothing for
 * `noDerivation.test.ts` to have to read, and `api/dashboard.ts` stays out of
 * its `ROOT_FILES`.
 *
 * **The selector set is in the key and in the request, from one source** —
 * `useFraudRates`' rule applied to a grain rather than to an order. That is what
 * makes "the browser buckets nothing" observable rather than merely claimed:
 * changing a control changes the key, which issues a request, which returns
 * points the server bucketed. A client-side re-grain would redraw the same claims
 * and never touch the network.
 *
 * **`placeholderData: keepPreviousData`**, `useFraudRates`' second option and
 * for its reason, one selector wider: all five charts ride this single query,
 * keyed on the whole selector set. Without it, changing the grain re-keys the
 * query, drops `data` to `undefined`, and blanks five charts at once — five
 * skeletons and five `aria-busy` flips for a reader who asked one question. The
 * previous charts stay on screen and only the section that was asked reports
 * busy (AC 7), and that is *not* a transform: the lines on screen are still a
 * server answer, just the previous one, and `isPlaceholderData` says so.
 *
 * The same `staleTime` as its six siblings, so the dashboard and the workspace
 * go stale on one schedule. Nothing polls.
 */
export function useTrends(params: TrendParams, segmentation: DrillFilters) {
  return useQuery({
    queryKey: queryKeys.dashboard.trends(toTrendParamsKey(params), toFilterKey(segmentation)),
    placeholderData: keepPreviousData,
    queryFn: async (): Promise<PortfolioTrends> => {
      const { data } = await api.GET("/dashboard/trends", {
        params: {
          query: {
            grain: params.grain,
            anchor: params.anchor,
            cohort: params.cohort,
            ...toSegmentationParams(segmentation),
          },
        },
      });
      return data!;
    },
    staleTime: 30_000,
  });
}

export type SegmentationValues = components["schemas"]["SegmentationValuesResponse"];
/** One dimension's options — `key` is the `filter[…]` name a picker writes. */
export type DimensionValues = SegmentationValues["dimensions"][number];
/** One option: the wire value, and a label for the single id-valued dimension. */
export type DimensionValue = DimensionValues["values"][number];
/** The four ordinal age bands — a closed server enum; the UI composes the range. */
export type AgeBand = NonNullable<
  NonNullable<paths["/dashboard/fraud"]["get"]["parameters"]["query"]>["filter[ageGroup]"]
>;

/**
 * The three age cut-offs a band label is composed from.
 *
 * A structural slice of the values payload rather than the payload itself, so
 * `ageBandLabels` takes exactly what it reads — and so a test can hand it three
 * integers without building a response.
 *
 * **Two payloads satisfy it, and that is the point since Story 7.3.**
 * `/dashboard/claims` publishes the same three integers from the same document,
 * so the drill list can compose the same "45–54" the workspace bar composes —
 * through the same function, over the same edges, rather than through a second
 * label decided server-side. A structural type is what lets one composer take
 * either response without either of them knowing about the other.
 */
export type SegmentationEdges = Pick<
  SegmentationValues,
  "ageYoungerMin" | "ageOlderMin" | "ageOldestMin"
>;

/**
 * Server state for the segmentation control (FR-AN-4, Story 7.3).
 *
 * `useFraudPanel`'s shape and its emptiness: the options each picker offers, the
 * chips the bar draws, the two counts a zero-result state is decided on and the
 * three age edges a band label is composed from are all decided by
 * `services/worklist` over the caller's scope, and this hook exists to fetch them
 * and nothing else.
 *
 * **No `select`**, deliberately, and this payload is a real invitation to one:
 * the browser is handed every value in the caller's book *and* the filter that
 * is active, so "just narrow the options to what is still reachable" is one line
 * — and it is precisely the behaviour the server refuses, because a picker cut
 * by its own filter cannot be used to widen it.
 *
 * **The filter is in the key and in the request, from one source.** Two of the
 * three things on this payload move with it (the chips and `claimsMatching`), so
 * a cached response keyed only on the options would put a stale chip row beside
 * fresh figures.
 *
 * **`placeholderData: keepPreviousData`**, `useFraudRates`' second option and for
 * its reason: this hook feeds a control that is on screen while its own filter
 * changes, so dropping `data` to `undefined` would empty every picker at the
 * moment the analyst is using one.
 *
 * The same `staleTime` as its siblings, so the workspace goes stale on one
 * schedule. Nothing polls.
 */
export function useSegmentationValues(segmentation: DrillFilters) {
  return useQuery({
    queryKey: queryKeys.dashboard.segmentationValues(toFilterKey(segmentation)),
    placeholderData: keepPreviousData,
    queryFn: async (): Promise<SegmentationValues> => {
      const { data } = await api.GET("/dashboard/segmentation/values", {
        params: { query: toSegmentationParams(segmentation) },
      });
      return data!;
    },
    staleTime: 30_000,
  });
}
