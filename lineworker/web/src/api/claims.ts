/**
 * Server state for the claim queue (FR-H-1, FR-Q-1).
 *
 * AD-1 leaves these hooks nothing to do but fetch. The grouping, the eight
 * filters, the priority order and the 🔺 marker are all decided by
 * `services/worklist` over the caller's scope, so there is no client-side
 * sort, no predicate and no fallback here — a queue that ranked itself in
 * the browser is the prototype behaviour this story replaces.
 *
 * Note what changing the filter does: it changes the **query key**, so
 * TanStack refetches. It does not narrow a cached list. AD-1 again — the
 * client does not hold the persona's whole caseload and must not pretend to.
 */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type ClaimCard = components["schemas"]["ClaimCardResponse"];
export type StageGroup = components["schemas"]["StageGroupResponse"];
export type StageGroups = components["schemas"]["StageGroupsResponse"];
export type ClaimQueue = components["schemas"]["ClaimQueueResponse"];
export type QueueFilter = components["schemas"]["QueueFilter"];
export type Stage = components["schemas"]["Stage"];
export type RiskBand = components["schemas"]["RiskBand"];

/** The four groups in lifecycle order — the order the pane renders them in. */
export const STAGE_ORDER: readonly Stage[] = [
  "intake",
  "investigation",
  "treatment",
  "settled",
];

export function useClaimQueue(filter: QueueFilter) {
  return useQuery({
    queryKey: queryKeys.claims.queue(filter),
    queryFn: async (): Promise<ClaimQueue> => {
      const { data } = await api.GET("/claims/queue", {
        params: { query: { filter } },
      });
      return data!;
    },
    // Shorter than the top bar's 30s: the queue is the surface a handler
    // works from all day, and Epic 2's edits invalidate this key directly
    // when they change something a card shows.
    staleTime: 15_000,
  });
}

/**
 * The pages beyond a stage group's first — the "Show more" affordance.
 *
 * An infinite query rather than a second plain one because the pages
 * *accumulate*: a group expanded twice shows both pages, and the cursor for
 * the third comes off the second. Seeded from the first page's `nextCursor`
 * (which the pane already holds) so the group is never re-fetched just to
 * reach page two.
 *
 * `enabled` is the caller's expansion state. Without it every mounted group
 * with a `nextCursor` would fetch its second page on load, which is exactly
 * the eagerness pagination exists to avoid.
 */
export function useStageGroupPages(
  filter: QueueFilter,
  stage: Stage,
  firstCursor: string | null,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: queryKeys.claims.queuePages(filter, stage),
    initialPageParam: firstCursor,
    queryFn: async ({ pageParam }): Promise<StageGroup> => {
      const { data } = await api.GET("/claims/queue", {
        params: { query: { filter, stage, cursor: pageParam ?? undefined } },
      });
      // Only this group's page is read. The response carries all four
      // groups (every one of them truthful — the server refuses to blank
      // the others), but the caller asked about one.
      return data!.groups[stage];
    },
    getNextPageParam: (last: StageGroup) => last.nextCursor ?? undefined,
    enabled: enabled && firstCursor !== null,
    staleTime: 15_000,
  });
}
