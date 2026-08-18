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
import { useQuery } from "@tanstack/react-query";

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
