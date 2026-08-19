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
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  toFilterKey,
  toQueryParams,
  type DrillFilters,
} from "@/features/dashboard/drill/filters";

import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

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
