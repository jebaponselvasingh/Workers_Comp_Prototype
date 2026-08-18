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
