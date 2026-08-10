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
