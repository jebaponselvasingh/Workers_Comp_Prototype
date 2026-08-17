/**
 * Server state for the handler's diary (Story 4.1, FR-DIARY-2).
 *
 * AD-1 leaves these hooks nothing to do but fetch and write. The sort is the
 * server's, `total` is the server's, and the Upcoming/Done styling comes from
 * a server-derived `status` field (AD-10) rather than from a comparison
 * written here — the prototype's `m.date >= today && !m.done` inside the
 * component that draws the card is exactly what that replaces.
 *
 * **These mutations do not carry `queryKeys.claims.writes`.** See
 * `queryKeys.meetings` for the argument: that key disables every editable
 * control on the case file, and a meeting write bumps no claim version.
 *
 * **A 409 is never retried.** The write was made against a row that has since
 * moved; re-sending it would complete or delete something the handler has not
 * looked at. The fresh entity rides the problem document's `meeting` extension
 * member, is installed into the list, and the list key is invalidated beside
 * it — leaving that invalidation out was the defect that bit Stories 3.4 and
 * 3.5, once each.
 */
import type { InfiniteData } from "@tanstack/react-query";
import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "./client";
import { problemExtension } from "./errors";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type Meeting = components["schemas"]["MeetingResponse"];
export type MeetingList = components["schemas"]["MeetingListResponse"];
export type MeetingType = components["schemas"]["MeetingType"];
export type MeetingParticipant = components["schemas"]["MeetingParticipant"];
export type MeetingStatus = components["schemas"]["MeetingStatus"];
export type NewMeeting = components["schemas"]["NewMeetingRequest"];

/**
 * The caller's meetings, page by page.
 *
 * **An infinite query, because the alternative loses rows silently.** The
 * server's default page holds fifty and its sort is `(meetingDate, id)`
 * *ascending*, so a single-page read does not merely show less of a busy
 * diary — it drops the *newest* meetings off the end, with no count, no
 * control and no error. A handler who schedules their fifty-first meeting
 * would watch it not appear. `queryKeys.meetings.list` was written for this
 * shape from the start (no cursor in the key, so the accumulated pages stay in
 * one entry), and `useStageGroupPages` is the same hook one aggregate over.
 *
 * `select` flattens the pages into the `{items, total}` the sub-tab renders,
 * so no component knows the list arrived in parts. `total` is the *whole*
 * diary's size and comes off the first page — it is the number beside the
 * sub-tab, and `items.length` is the page's size rather than the diary's.
 *
 * `staleTime` matches the case file's 15s: the two panes are read together and
 * two different staleness clocks would let them disagree about a claim nobody
 * touched.
 */
export function useMeetings() {
  return useInfiniteQuery({
    queryKey: queryKeys.meetings.list,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<MeetingList> => {
      const { data } = await api.GET("/claims-diary/meetings", {
        params: { query: { cursor: pageParam ?? undefined } },
      });
      return data!;
    },
    // `?? undefined` rather than the raw null: TanStack reads `undefined` as
    // "there is no next page" and would otherwise fetch for ever against a
    // cursor of null — `useStageGroupPages`' rule.
    getNextPageParam: (last: MeetingList) => last.nextCursor ?? undefined,
    select: (data: InfiniteData<MeetingList>) => ({
      items: data.pages.flatMap((page) => page.items),
      total: data.pages[0].total,
    }),
    staleTime: 15_000,
  });
}

/**
 * A 409's `meeting` member, if it really is a meeting.
 *
 * `problemExtension` is an unchecked cast over something that crossed a
 * network, and a truncated body written into the list cache would render a
 * card with no type and no date. Two structural probes are enough to tell the
 * entity from a fragment — `freshClaimFrom`'s discipline in `claims.ts`.
 */
function freshMeetingFrom(error: unknown): Meeting | undefined {
  const fresh = problemExtension<Meeting>(error, "meeting");
  return fresh && typeof fresh.id === "number" && fresh.meetingType ? fresh : undefined;
}

/**
 * Install a fresh entity into the cached list without a round trip.
 *
 * **The single writer of this cache entry**, which is the point: two hooks
 * spelling the same `setQueryData` out inline is two places to get the page
 * walk wrong when a fourth command arrives. The row is replaced in whichever
 * page holds it and the list is re-sorted by nothing — a completion changes
 * `isDone` and `version`, and neither is a sort key.
 *
 * `refetch` is the one thing the two call sites genuinely disagree about, so
 * it is a parameter rather than a second copy of the body:
 *
 * - the **409** path refetches. The entity moved, so the list that offered the
 *   button was generated from where it used to be — and the move may have been
 *   an ordering one this cannot see (leaving that invalidation out was the
 *   defect that bit Stories 3.4 and 3.5, once each).
 * - the **200** path does not. The response body already refreshed the row, and
 *   a follow-up GET would re-open the read-after-write window that returning
 *   the entity closes (`useApprovePayment`'s rule). The key is still marked
 *   stale, so the next mount re-reads it.
 */
function replaceInList(
  client: ReturnType<typeof useQueryClient>,
  fresh: Meeting,
  { refetch }: { refetch: boolean },
): void {
  client.setQueryData<InfiniteData<MeetingList>>(queryKeys.meetings.list, (current) =>
    current === undefined
      ? current
      : {
          ...current,
          pages: current.pages.map((page) => ({
            ...page,
            items: page.items.map((item) => (item.id === fresh.id ? fresh : item)),
          })),
        },
  );
  void client.invalidateQueries({
    queryKey: queryKeys.meetings.list,
    exact: true,
    refetchType: refetch ? undefined : "none",
  });
}

/**
 * Schedule a meeting (AC 1).
 *
 * **Nothing optimistic.** AD-9 permits an optimistic update for a
 * user-entered scalar; a new row is not one — the server assigns its `id`, its
 * `createdAt` and its `status`, and it may refuse the claim link outright. A
 * card that appeared and then vanished would be worse than one that appears a
 * moment later.
 *
 * The response *is* the created meeting, but the list is invalidated rather
 * than appended to: where the new row sorts is the server's answer
 * (`meetingDate`, then `id`), and a client that spliced it in would be
 * deciding an ordering AD-1 puts on the other side of the wire.
 */
export function useScheduleMeeting() {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.meetings.writes,
    mutationFn: async (body: NewMeeting): Promise<Meeting> => {
      const { data } = await api.POST("/claims-diary/meetings", { body });
      return data!;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.meetings.list, exact: true });
    },
  });
}

/**
 * Mark a meeting done (AC 4).
 *
 * The 200 body is the fresh row and it is written straight into the list, so
 * the card's chip, its ✓ and its muted styling all move together from one
 * object. The key is then marked stale **without** an immediate refetch: the
 * response already refreshed it, and a follow-up GET would re-open the
 * read-after-write window that returning the entity closes
 * (`useApprovePayment`'s rule).
 */
export function useCompleteMeeting() {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.meetings.writes,
    mutationFn: async (variables: {
      meetingId: number;
      expectedVersion: number;
    }): Promise<Meeting> => {
      const { data } = await api.PATCH("/claims-diary/meetings/{meeting_id}", {
        params: { path: { meeting_id: variables.meetingId } },
        body: { expectedVersion: variables.expectedVersion },
      });
      return data!;
    },
    onError: (error) => {
      const fresh = freshMeetingFrom(error);
      if (fresh) replaceInList(client, fresh, { refetch: true });
    },
    onSuccess: (fresh) => replaceInList(client, fresh, { refetch: false }),
  });
}

/**
 * Delete a meeting (AC 4).
 *
 * The version travels in the query string, which is what the route declares —
 * a DELETE body is permitted but widely dropped by proxies, and a
 * compare-and-swap whose guard can be silently discarded is not a guard.
 *
 * **204, so there is nothing to install** and the list is refetched. That is
 * the one place this aggregate's writes differ from the case file's, and the
 * reason is the same one that makes 204 right: a removed row has no fresh
 * state to render.
 */
export function useDeleteMeeting() {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.meetings.writes,
    mutationFn: async (variables: {
      meetingId: number;
      expectedVersion: number;
    }): Promise<void> => {
      await api.DELETE("/claims-diary/meetings/{meeting_id}", {
        params: {
          path: { meeting_id: variables.meetingId },
          query: { expectedVersion: variables.expectedVersion },
        },
      });
    },
    onError: (error) => {
      const fresh = freshMeetingFrom(error);
      if (fresh) replaceInList(client, fresh, { refetch: true });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.meetings.list, exact: true });
    },
  });
}

/**
 * True while **any** meeting command is in flight.
 *
 * The one flag every meeting control disables itself with, for
 * `useClaimWriteInFlight`'s reason one aggregate over: `expectedVersion` comes
 * from the cached list and no mutation here advances it optimistically, so two
 * overlapping writes send the same version and the second is refused with a
 * message about somebody else — about the handler's own click.
 */
export function useMeetingWriteInFlight(): boolean {
  return useIsMutating({ mutationKey: queryKeys.meetings.writes }) > 0;
}
