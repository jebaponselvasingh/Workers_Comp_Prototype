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
export function useMeetings(options?: {
  /**
   * Narrow to one calendar date, `YYYY-MM-DD` — the **viewer's local** day
   * from `lib/clock.ts::todayIso`. The Notes sub-tab's today's-meetings
   * summary passes it; the Meetings sub-tab does not.
   *
   * **The filtering happens on the server**, which is the whole reason this is
   * a request parameter and not an `items.filter(…)` here: the list is
   * paginated and sorted ascending, so today's meetings sort *after* every past
   * one and a browser filtering a page would find none of them once a diary
   * outgrows fifty rows. `noDerivation.test.ts` would refuse the comparison in
   * any case.
   */
  day?: string;
}) {
  const day = options?.day;

  return useInfiniteQuery({
    // Two entries under one prefix: the unfiltered diary and today's slice.
    // See `queryKeys.meetings.day` on why the nesting is load-bearing for
    // invalidation.
    queryKey: day === undefined ? queryKeys.meetings.list : queryKeys.meetings.day(day),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<MeetingList> => {
      const { data } = await api.GET("/claims-diary/meetings", {
        params: { query: { cursor: pageParam ?? undefined, day } },
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
      // The greeting's "📅 N upcoming meetings", server-derived by the same
      // rule that decides each card's `status` (AD-10). Off the *first* page
      // because it describes the whole book and every page carries the same
      // number — and it is deliberately unaffected by `day`, so the summary's
      // one request serves the greeting too.
      upcomingCount: data.pages[0].upcomingCount,
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
 * - the **200** path did not, through Story 4.1: the response body already
 *   refreshed the row, and a follow-up GET would re-open the read-after-write
 *   window that returning the entity closes (`useApprovePayment`'s rule). That
 *   premise held while a *row* was all this cache entry contained. Story 4.2
 *   put `upcomingCount` on the envelope — a server-derived aggregate over the
 *   whole book — and a single meeting's response body cannot refresh it, so
 *   `useCompleteMeeting` now refetches too. See its comment; `useScheduleMeeting`
 *   and `useDeleteMeeting` already did.
 *
 * The parameter is kept rather than deleted because the *reason* the two paths
 * differ has not gone away, and a fourth command that answers with its entity
 * and changes no aggregate should be able to use the cheap path again.
 *
 * **Both the row install and the invalidation cover the whole prefix**, which
 * changed in Story 4.2 and is the one 4.1 policy that had to relax. There are
 * now two cache entries over the same rows — the Meetings sub-tab's full list
 * and the Notes sub-tab's `day`-filtered summary — and ✓ Done can be pressed
 * from either. `exact: true` would have refreshed whichever one the handler
 * clicked in and left the other showing the meeting as still upcoming, on
 * screen at the same time, two sub-tabs apart.
 */
function replaceInList(
  client: ReturnType<typeof useQueryClient>,
  fresh: Meeting,
  { refetch }: { refetch: boolean },
): void {
  client.setQueriesData<InfiniteData<MeetingList>>(
    { queryKey: queryKeys.meetings.list },
    (current) =>
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
      // By prefix, not `exact`: Story 4.2 added the day-filtered summary as a
      // sibling entry, and a meeting scheduled for today belongs in both.
      void client.invalidateQueries({ queryKey: queryKeys.meetings.list });
    },
  });
}

/**
 * Mark a meeting done (AC 4).
 *
 * The 200 body is the fresh row and it is written straight into the list, so
 * the card's chip, its ✓ and its muted styling all move together from one
 * object — that part is unchanged and is why the card never flickers.
 *
 * **It refetches as well, which it did not before Story 4.2**, and the reason
 * is `upcomingCount`. Completing a meeting that was upcoming changes a number
 * on the envelope that the row's own response cannot carry, and the browser is
 * forbidden from decrementing it (it is a registered derivation's answer —
 * `noDerivation.test.ts` lists the field). Without the refetch the greeting
 * two elements above the card keeps saying "📅 3 upcoming meetings" over a
 * summary showing two, which is precisely the console-disagreeing-with-itself
 * failure `meeting_horizon.py` exists to prevent, arrived at from the cache
 * instead of from a second `>=`.
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
    onSuccess: (fresh) => replaceInList(client, fresh, { refetch: true }),
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
      // By prefix, for `useScheduleMeeting`'s reason.
      void client.invalidateQueries({ queryKey: queryKeys.meetings.list });
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
