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
 * the eagerness pagination exists to avoid. A **disabled** call still
 * subscribes to the entry and reads whatever is in it — which is how
 * `useLoadedClaimIds` below looks at the pages the queue pane fetched
 * without fetching anything itself.
 */
export function useStageGroupPages(
  filter: QueueFilter,
  stage: Stage,
  firstCursor: string | null,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: queryKeys.claims.queuePages(filter, stage, firstCursor),
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

/**
 * What the workspace is currently holding, for one filter — the claim ids on
 * screen, and whether that is the whole book.
 *
 * The queue pane and the detail pane have to agree about this or they tell a
 * handler two different things about one claim (AD-9's "three states of one
 * claim"): the card is highlighted in the list on the left while the pane on
 * the right says the claim is not shown. Reading the base payload alone got
 * that wrong the moment anyone clicked "Show more" — the revealed claim was
 * rendered by the queue and unknown to the detail.
 *
 * So this reads the *same cache entries the pages queries write*, keyed the
 * same way, with `enabled: false` throughout — no request is made here, only
 * a subscription, so the answer moves when a page lands. The four calls are
 * spelled out rather than mapped over `STAGE_ORDER` because they are hooks
 * and their order has to be a property of the source, not of an array.
 *
 * `complete` is the other half of the answer and the reason this returns a
 * pair. "Not in the ids" is only evidence of absence when every group has
 * been read to its end; a group with a page nobody has asked for yet leaves
 * the claim unaccounted for, not missing.
 */
export interface LoadedClaims {
  ids: ReadonlySet<string>;
  complete: boolean;
}

export function useLoadedClaimIds(
  queue: ClaimQueue | undefined,
  filter: QueueFilter,
  expandedStages: ReadonlySet<Stage>,
): LoadedClaims {
  const cursorOf = (stage: Stage) => queue?.groups[stage].nextCursor ?? null;
  const pages = {
    intake: useStageGroupPages(filter, "intake", cursorOf("intake"), false),
    investigation: useStageGroupPages(filter, "investigation", cursorOf("investigation"), false),
    treatment: useStageGroupPages(filter, "treatment", cursorOf("treatment"), false),
    settled: useStageGroupPages(filter, "settled", cursorOf("settled"), false),
  };

  const ids = new Set<string>();
  let complete = queue !== undefined;
  for (const stage of STAGE_ORDER) {
    if (!queue) break;
    for (const card of queue.groups[stage].items) ids.add(card.claimId);

    // Only a group the handler actually expanded contributes its pages —
    // the same condition `StageGroup` renders under. A cache entry left
    // behind by an expansion that has since expired is not on screen, and
    // counting it here would re-open the disagreement from the other side.
    const expanded = expandedStages.has(stage);
    const group = pages[stage];
    if (expanded) {
      for (const page of group.data?.pages ?? []) {
        for (const card of page.items) ids.add(card.claimId);
      }
    }

    const exhausted =
      cursorOf(stage) === null || (expanded && group.isSuccess && !group.hasNextPage);
    if (!exhausted) complete = false;
  }

  return { ids, complete };
}
