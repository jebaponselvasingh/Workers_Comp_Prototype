/**
 * Server state for the handler's stakeholder emails (Story 4.3, FR-DIARY-3).
 *
 * AD-1 leaves these hooks nothing to do but fetch and write, and here that is
 * stronger than anywhere else in the console: **the merge happens on the
 * server**. `useMergedTemplate` and `useMeetingEmailDraft` return a `{subject,
 * body, recipients, claimId}` the composer renders and the handler edits;
 * nothing in this module — and nothing in `features/diary` — ever interpolates a
 * claim field into a letter. That is why the templates endpoint deliberately
 * does not publish `subjectTemplate`/`bodyTemplate`: a client holding the
 * template text would be one `replace()` from merging in the browser.
 *
 * **These mutations carry neither `claims.writes` nor `meetings.writes`.** See
 * `queryKeys.emails` for both arguments: the first would grey out every editable
 * control on the case file for a write that bumps no claim version, and the
 * second would disable the ✓ Done buttons in the diary while somebody typed an
 * email.
 *
 * **There is no 409 path and no `version`.** `email_log` is append-only — no
 * edit, no delete, no compare-and-swap — so there is no conflict to arbitrate
 * and no fresh entity to install. The one refusal that is not a failure is
 * `/problems/email-not-readable`: the row *was* written and audited, and only
 * the scoped re-read failed. `isEmailWrittenButUnreadable` names it, because a
 * caller who reads that as "the send failed" duplicates a record of a
 * communication that cannot be removed.
 *
 * **Nothing here sends anything anywhere.** `POST /claims-diary/emails` writes a
 * row; the button says "✉ Send Email (logged)" and means it. Real egress is a
 * deferred architecture decision with its own compliance review.
 */
import type { InfiniteData } from "@tanstack/react-query";
import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "./client";
import { problemType } from "./errors";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

/**
 * The 404 that means **the email was logged** — see `_email_not_readable`.
 *
 * Named here for `NOTE_WRITTEN_BUT_UNREADABLE`'s reason, and it matters more:
 * `email_log` has no delete path, so a composer that offered "try again" after
 * this would put a second copy of the same letter in the sent log with no way
 * to take it back.
 */
export const EMAIL_WRITTEN_BUT_UNREADABLE = "/problems/email-not-readable";

/** Whether a failed send actually landed a row. See the constant above. */
export function isEmailWrittenButUnreadable(error: unknown): boolean {
  return problemType(error) === EMAIL_WRITTEN_BUT_UNREADABLE;
}

export type EmailLog = components["schemas"]["EmailLogResponse"];
export type EmailLogList = components["schemas"]["EmailLogListResponse"];
export type EmailTemplate = components["schemas"]["EmailTemplateResponse"];
export type MergedEmail = components["schemas"]["MergedEmailResponse"];
export type NewEmail = components["schemas"]["NewEmailRequest"];
export type EmailPriority = components["schemas"]["EmailPriority"];
/**
 * The recipient vocabulary — **the meeting participant enum, reused**.
 *
 * The server declares one six-value stakeholder set and both aggregates read it,
 * which is what makes convert-to-email an identity mapping rather than a
 * translation table (the prototype substring-matched participant *labels*, which
 * is why "Employer HR" happened to match "employer"). The name is wrong for an
 * email and a rename to `Stakeholder` is recorded as follow-up; one vocabulary
 * with an imperfect name beats two with good ones.
 */
export type EmailRecipient = components["schemas"]["MeetingParticipant"];

/**
 * The six quick templates, in the composer's button order.
 *
 * `staleTime: Infinity` is not an optimisation, it is the truth about the data
 * — `useGlossary`'s ruling: the rows are written by a migration and cannot
 * change within a session.
 *
 * **Gated on the composer actually being open**, which is the correction. The
 * `gcTime` comment here used to justify itself by saying "the composer is
 * mounted with the Emails sub-tab rather than for the life of the tab" — which
 * is not what the code does and has not been since the composer was written:
 * `DiaryTab` mounts the dialog *outside* the sub-tab switch, deliberately, so
 * that ✉ on a meeting card can open it from any of the three. Ungated, this
 * query therefore fired on every workspace load, for every handler who never
 * left 📓 Notes and never composed anything. The `enabled` flag makes the
 * request follow the surface that needs it; `gcTime` stays at the default
 * because six rows are cheap to re-read and the answer cannot go stale.
 */
export function useEmailTemplates(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.emails.templates,
    queryFn: async (): Promise<components["schemas"]["EmailTemplateListResponse"]> => {
      const { data } = await api.GET("/claims-diary/email-templates");
      return data!;
    },
    enabled,
    staleTime: Infinity,
  });
}

/**
 * One quick template, merged against one claim — **by the server** (AD-1).
 *
 * **Enabled-gated rather than imperative**, which is what keeps the composer
 * free of effects: the dialog holds "which template was asked for" as ordinary
 * state, this hook turns that into a request, and the fields are *derived* from
 * whatever has arrived. Nothing has to react to data landing.
 *
 * `placeholderData` keeps the previous merge on screen while a new one is in
 * flight. Without it, clicking a second template blanks the subject and body for
 * the length of a round trip and then fills them — a letter visibly disappearing
 * under the handler, for no reason they could name.
 *
 * A claim is **required** and the caller must not send this without one: the six
 * templates are claim-aware by definition, and the prototype's claim-less render
 * produced letters full of holes. The composer disables the buttons with a
 * stated reason instead, so the `enabled` gate below is a belt to that brace.
 *
 * **A merge is not immutable, and `staleTime: Infinity` said it was.** The
 * template list above genuinely cannot change within a session; a merged letter
 * is cut from `stage`, `status`, `injury_type`, `cause`, `icd` and `body_part` —
 * every one of them written by Story 2.3's inline editor, in a pane the handler
 * can reach without closing this modal — and from `days_open`, which moves on
 * its own overnight. Cached for ever, correcting a claim's status on the case
 * file and re-opening the composer inside `gcTime` re-served the *old* status,
 * and that stale sentence is what would have been sent and logged into a table
 * with no edit. Left at the default staleness instead: every open of the
 * composer is a fresh mount (`DiaryTab` keys it on the session), so picking a
 * template re-merges, while `placeholderData` keeps the last letter on screen
 * for the length of the round trip rather than blanking the form.
 */
export function useMergedTemplate(templateKey: string | null, claimId: string | null) {
  return useQuery({
    queryKey: queryKeys.emails.draft("template", `${claimId ?? ""}:${templateKey ?? ""}`),
    queryFn: async (): Promise<MergedEmail> => {
      const { data } = await api.GET("/claims-diary/email-templates/{template_key}/merged", {
        params: {
          path: { template_key: templateKey! },
          query: { claimId: claimId! },
        },
      });
      return data!;
    },
    enabled: templateKey !== null && claimId !== null,
    placeholderData: (previous) => previous,
    // No `staleTime`: the letter is merged from columns this console edits.
    // See the docstring — the number that used to be here was `Infinity`.
    staleTime: 0,
  });
}

/**
 * A meeting's confirmation letter — the convert-to-email path (AC 5).
 *
 * The same payload shape a template merge returns, so the composer has one fill
 * path rather than two. `recipients` is that meeting's participants: both sides
 * are the same six-value vocabulary, so the mapping is the identity.
 *
 * **Read-only with respect to the meeting** (AD-12). Opening the composer from a
 * meeting card changes nothing about the meeting; a subsequent send is what logs
 * anything.
 */
export function useMeetingEmailDraft(meetingId: number | null) {
  return useQuery({
    queryKey: queryKeys.emails.draft("meeting", meetingId ?? 0),
    queryFn: async (): Promise<MergedEmail> => {
      const { data } = await api.GET("/claims-diary/meetings/{meeting_id}/email-draft", {
        params: { path: { meeting_id: meetingId! } },
      });
      return data!;
    },
    enabled: meetingId !== null,
    placeholderData: (previous) => previous,
    // **`0`, for `useMergedTemplate`'s reason applied one step further.** The
    // letter names the meeting's type, date, time, location and agenda, and a
    // meeting can be completed, re-scheduled by delete-and-recreate, or have
    // its claim's stage move underneath an open pane. Caching it across opens
    // would confirm a meeting in the words it had the first time somebody
    // looked. `placeholderData` still covers the round trip, so a re-open shows
    // the previous letter while the current one is fetched rather than blanking.
    staleTime: 0,
  });
}

/**
 * The caller's sent log, page by page, newest first.
 *
 * **An infinite query for `useDiaryNotes`' reason, mirrored.** The server's
 * default page holds fifty and the sort is `sentAt DESC, id DESC`, so the
 * truncation is at the *old* end — the kinder direction, and exactly why
 * publishing `total` and a "Show more" still matters: a handler looking for what
 * they sent last month has to be able to reach it.
 *
 * `total` comes off the **first page alone**, which is the server's contract
 * rather than a client convenience: `list_email_logs` issues its `COUNT(*)` only
 * when no cursor was supplied and answers `null` otherwise, so a later page has
 * no count to read. `?? null` makes "the server did not count this page" and
 * "the field was absent" the same absence to the sub-tab.
 *
 * `staleTime` matches the meetings and notes lists' 15s: all three are read in
 * one pane, and three staleness clocks would let them disagree about a diary
 * nobody touched.
 */
export function useEmailLogs() {
  return useInfiniteQuery({
    queryKey: queryKeys.emails.list,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<EmailLogList> => {
      const { data } = await api.GET("/claims-diary/emails", {
        params: { query: { cursor: pageParam ?? undefined } },
      });
      return data!;
    },
    // `?? undefined` rather than the raw null: TanStack reads `undefined` as
    // "there is no next page" and would otherwise fetch for ever against a
    // cursor of null — `useStageGroupPages`' rule.
    getNextPageParam: (last: EmailLogList) => last.nextCursor ?? undefined,
    select: (data: InfiniteData<EmailLogList>) => ({
      items: data.pages.flatMap((page) => page.items),
      total: data.pages[0].total ?? null,
    }),
    staleTime: 15_000,
  });
}

/**
 * Log a stakeholder email (AC 4) — and send nothing.
 *
 * **Nothing optimistic.** AD-9 permits an optimistic update for a user-entered
 * scalar; a new row is not one — the server assigns its `id` and its `sentAt`,
 * and it may refuse the claim reference, the template key or the recipient set
 * outright. A card that appeared at the top of the log and then vanished would
 * be worse than one that appears a moment later, and worse *here* than anywhere
 * else: the point of the feature is that what a handler sent is on the record.
 *
 * The response *is* the created row, but the list is invalidated rather than
 * prepended to: where it sorts is the server's answer, and `total` moved in a
 * way no single row's body can carry.
 */
export function useSendEmail() {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.emails.writes,
    mutationFn: async (body: NewEmail): Promise<EmailLog> => {
      const { data } = await api.POST("/claims-diary/emails", { body });
      return data!;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.emails.list, exact: true });
    },
    // **The one error path that has to touch the cache.**
    // `/problems/email-not-readable` is answered *after* the row is committed
    // and audited, and it says "Do not send it again; reload the list." — while
    // the list went unrefreshed, so the email never appeared and the composer
    // stayed open over an enabled Send pointed at a duplicate.
    // `useAddDiaryNote`'s ruling, on a table with even less recourse.
    onError: (error) => {
      if (!isEmailWrittenButUnreadable(error)) return;
      void client.invalidateQueries({ queryKey: queryKeys.emails.list, exact: true });
    },
  });
}

/**
 * True while the send command is in flight.
 *
 * One hook rather than reading `isPending` off the mutation, for
 * `useDiaryNoteWriteInFlight`'s reason: the composer's fields, its Send button
 * and the form's `onSubmit` all have to agree about being busy, and a double
 * submit would log the same letter twice into a table with no version to refuse
 * the second with and no delete to undo it.
 */
export function useEmailWriteInFlight(): boolean {
  // `!== 0`, `useMeetingWriteInFlight`'s rule: `noDerivation.test.ts` scans this
  // file and refuses a comparison against a numeric literal anywhere it reads,
  // bluntly and on purpose. There is no threshold here and this says so without
  // arguing with the guard.
  return useIsMutating({ mutationKey: queryKeys.emails.writes }) !== 0;
}
