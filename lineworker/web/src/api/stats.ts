/**
 * Server state for the caseload tiles (FR-TOP-2).
 *
 * AD-1 and AD-9 between them leave this hook nothing to do but fetch: the
 * three numbers are computed by `services/worklist` over the caller's
 * scope, and the SPA renders them verbatim. There is deliberately no
 * client-side counting here to fall back on — a wrong number is worse than
 * an absent one on a screen used to triage injuries.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type TopBarStats = components["schemas"]["TopBarStatsResponse"];
export type SlaStripData = components["schemas"]["SlaStripResponse"];
export type SlaMetric = components["schemas"]["SlaMetricResponse"];

export function useTopBarStats() {
  return useQuery({
    queryKey: queryKeys.stats.topbar,
    queryFn: async (): Promise<TopBarStats> => {
      const { data } = await api.GET("/stats/topbar");
      return data!;
    },
    // Matches `useMe`: the tiles and the identity beside them are read
    // together, so a shared staleness keeps the bar internally consistent.
    // Claim mutations (Epic 2 onwards) invalidate this key when they change
    // something a tile counts.
    staleTime: 30_000,
  });
}

/**
 * The SLA strip (FR-SLA-1) — one request, every role.
 *
 * A separate key from the tiles rather than one combined `/stats` payload:
 * the two aggregates have different costs and, from Epic 3, different
 * invalidation triggers (a payment approval moves a tile; only a
 * settlement moves the strip). Sharing a key would mean recomputing both
 * whenever either changed.
 *
 * Nothing here compares a value to a target. `status` is the server's
 * (AD-1) — a second comparison in the browser is a second rule, and the
 * two would part company the first time a target moved.
 */
export function useSlaStrip() {
  return useQuery({
    queryKey: queryKeys.stats.sla,
    queryFn: async (): Promise<SlaStripData> => {
      const { data } = await api.GET("/stats/sla");
      return data!;
    },
    staleTime: 30_000,
  });
}
