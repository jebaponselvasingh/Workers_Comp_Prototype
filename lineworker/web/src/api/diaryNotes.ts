/**
 * Server state for the handler's diary notes (Story 4.2, FR-DIARY-1).
 *
 * AD-1 leaves these hooks nothing to do but fetch and write. The order is the
 * server's (`notedAt DESC, id DESC`), `total` is the server's, and there is
 * nothing derived to be tempted by — a note has no lifecycle, so unlike a
 * meeting it carries no `status` a browser could recompute.
 *
 * **These mutations carry neither `queryKeys.claims.writes` nor
 * `queryKeys.meetings.writes`.** See `queryKeys.diaryNotes` for both arguments:
 * the first would grey out every editable control on the case file for a write
 * that bumps no claim version, and the second would disable the ✓ Done buttons
 * in the today's-meetings summary while somebody typed a note.
 *
 * **There is no 409 path, because there is no compare-and-swap.** `diary_note`
 * is append-only and carries no `version` — nothing is ever read-modify-written,
 * so there is no conflict to arbitrate and no fresh entity to install. That is
 * the one structural difference from `meetings.ts`, and it is why this module is
 * shorter rather than incomplete.
 */
import type { InfiniteData } from "@tanstack/react-query";
import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "./client";
import { problemType } from "./errors";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

/**
 * The 404 that means **the note was written** — see `_note_not_readable`.
 *
 * It is the one refusal in this module that is not a failure: the row is
 * committed and audited, and only the scoped re-read failed. Named here because
 * two things have to agree about it — the cache, which must stop describing a
 * list that no longer matches the database, and the form, which must stop
 * offering to send the note again into a table with no edit and no delete.
 */
export const NOTE_WRITTEN_BUT_UNREADABLE = "/problems/note-not-readable";

/** Whether a failed save actually landed a row. See the constant above. */
export function isNoteWrittenButUnreadable(error: unknown): boolean {
  return problemType(error) === NOTE_WRITTEN_BUT_UNREADABLE;
}

export type DiaryNote = components["schemas"]["DiaryNoteResponse"];
export type DiaryNoteList = components["schemas"]["DiaryNoteListResponse"];
export type NewDiaryNote = components["schemas"]["NewDiaryNoteRequest"];

/**
 * The caller's notes, page by page, newest first.
 *
 * **An infinite query for `useMeetings`' reason, mirrored.** The server's
 * default page holds fifty; a single-page read of a busy diary would silently
 * show the newest fifty and nothing would say so. Here the truncation is at the
 * *old* end rather than the new one — the sort is descending — which is the
 * kinder direction, and exactly why publishing `total` and a "Show more"
 * matters anyway: a handler looking for what they wrote last month must be able
 * to reach it.
 *
 * `select` flattens the pages into the `{items, total}` the sub-tab renders, so
 * no component knows the list arrived in parts.
 *
 * `staleTime` matches the meetings list's 15s: the two are read together in one
 * pane, and two staleness clocks would let them disagree about a diary nobody
 * touched.
 */
export function useDiaryNotes() {
  return useInfiniteQuery({
    queryKey: queryKeys.diaryNotes.list,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<DiaryNoteList> => {
      const { data } = await api.GET("/claims-diary/notes", {
        params: { query: { cursor: pageParam ?? undefined } },
      });
      return data!;
    },
    // `?? undefined` rather than the raw null: TanStack reads `undefined` as
    // "there is no next page" and would otherwise fetch for ever against a
    // cursor of null — `useStageGroupPages`' rule.
    getNextPageParam: (last: DiaryNoteList) => last.nextCursor ?? undefined,
    select: (data: InfiniteData<DiaryNoteList>) => ({
      items: data.pages.flatMap((page) => page.items),
      total: data.pages[0].total,
    }),
    staleTime: 15_000,
  });
}

/**
 * Save a diary note (AC 2).
 *
 * **Nothing optimistic.** AD-9 permits an optimistic update for a user-entered
 * scalar; a new row is not one — the server assigns its `id` and its `notedAt`,
 * and it may refuse the claim tag or the text outright. A note that appeared at
 * the top of the list and then vanished would be worse than one that appears a
 * moment later, and worse *here* than anywhere else in the console: the whole
 * point of the feature is that what a handler wrote is safe.
 *
 * The response *is* the created note, but the list is invalidated rather than
 * prepended to: where the new row sorts is the server's answer, and a client
 * that spliced it in would be deciding an ordering AD-1 puts on the other side
 * of the wire. It refetches, because `total` moved and no single row's response
 * can carry a count.
 *
 * **It also invalidates the tagged claim's action checklist, which is AC 5's
 * on-screen half.** Saving a note is a completion — the only one in the console
 * with no button, by design (`services/worklist/actions.py`: there is no
 * "completed actions" store, so the entity *is* the record). The server stops
 * emitting `diary_check_in` the moment the row exists; without this line the
 * browser never asks again, because `refetchOnWindowFocus` is off and the
 * checklist is its own cache key. The handler follows "Log Diary Entry →",
 * writes the note, watches the row they just satisfied stay exactly where it
 * was, and writes it again. Every other completion path goes through
 * `afterChecklistWrite` in `claims.ts` and invalidates the same key for the same
 * reason; this one is not that helper only because a note bumps no claim version
 * and there is no fresh case file to install.
 *
 * The claim comes off the **response**, not off the request: the server's tag is
 * the one that was stored, and it is what decides whose checklist moved. An
 * untagged note satisfies nothing, and invalidates nothing.
 */
export function useAddDiaryNote() {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.diaryNotes.writes,
    mutationFn: async (body: NewDiaryNote): Promise<DiaryNote> => {
      const { data } = await api.POST("/claims-diary/notes", { body });
      return data!;
    },
    onSuccess: (note) => refreshAfterNote(client, note.claimId),
    // **The one error path that has to touch the cache**, and leaving it out
    // was a console whose every affordance contradicted its own message.
    // `/problems/note-not-readable` is answered *after* the row is committed
    // and audited, and it says "Do not write it again; reload the list." —
    // while the list went unrefreshed (so the note never appeared), the
    // checklist row the note satisfies stayed on screen, and the draft stayed
    // in the box under an enabled Save. Every one of those points at the
    // duplicate the wording forbids. The claim comes off the *request* here
    // rather than off a response there is none of; it is the tag the server
    // accepted, because the write got past `insert_diary_note`'s scope check
    // to have committed at all.
    onError: (error, variables) => {
      if (!isNoteWrittenButUnreadable(error)) return;
      refreshAfterNote(client, variables.claimId ?? null);
    },
  });
}

/** The two keys a written note invalidates — success and the 404 that lands one. */
function refreshAfterNote(
  client: ReturnType<typeof useQueryClient>,
  claimId: string | null,
): void {
  void client.invalidateQueries({ queryKey: queryKeys.diaryNotes.list, exact: true });
  if (claimId === null) return;
  // `exact`, for `afterChecklistWrite`'s reason: the checklist key is nested
  // under the claim's own segment, and a prefix invalidation here would drag
  // the whole case file and its financials along for a write that changed
  // neither.
  void client.invalidateQueries({ queryKey: queryKeys.claims.actions(claimId), exact: true });
}

/**
 * True while the add-note command is in flight.
 *
 * One hook rather than reading `isPending` off the mutation, for
 * `useMeetingWriteInFlight`'s reason and one narrower one: the input, its Save
 * button and the form's `onSubmit` all have to agree about being busy, and a
 * double submit would write the same note twice — an append-only table has no
 * version to refuse the second one with.
 */
export function useDiaryNoteWriteInFlight(): boolean {
  // `!== 0`, for `useMeetingWriteInFlight`'s reason and to match it.
  return useIsMutating({ mutationKey: queryKeys.diaryNotes.writes }) !== 0;
}
