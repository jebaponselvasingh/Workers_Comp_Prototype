/**
 * The ✉ Emails sub-tab — the sent log and its four states (Story 4.3, AC 4,
 * UX-DR9).
 *
 * The prototype's `renderEmails` (line 2027) with a table behind it: a
 * reverse-chronological list of `.email-card`s carrying `✉ {subject}` with a
 * "Sent {date}" badge, a `To: {recipients}` line that gains ` · {worker}` when
 * the email names a claim, and the first hundred characters of the body.
 *
 * **Loading, error, empty and populated are four branches, not three** (NFR-3),
 * and `isError` is tested **after** the cache for `MeetingsSubTab`'s reason:
 * TanStack keeps `data` when a *refetch* or a later infinite page fails, so
 * testing `isError` first would blank a populated log on a transient failure —
 * including in the second after a successful send, which invalidates the list.
 * With rows in hand the list stays and the failure is a strip above it.
 *
 * **Everything on a card arrived decided.** The order is the server's (`sentAt`
 * descending), `total` is the server's and comes off the first page alone
 * (`list_email_logs` counts only when no cursor was supplied), and the "Sent"
 * badge is `sentAt` formatted — **not a delivery state**. There is no delivery:
 * the button says "✉ Send Email (logged)" and the row is the whole of what
 * happens.
 *
 * **A card opens nothing, deliberately.** The prototype's `.email-card` carries
 * `cursor: pointer` and no handler; there is no `GET /emails/{id}`, no thread
 * and no reply, so a clickable card would be the dead click NFR-3 forbids.
 *
 * **The composer is not mounted here** — `DiaryTab` owns it, so ✉ on a meeting
 * card can open it without dragging the pane to this sub-tab. What this
 * component owns is the pinned ＋ button and the list.
 */
import { useEmailLogs } from "@/api/emails";
import { useDiaryNav } from "@/features/diary/DiaryNav";
import { formatSentAt } from "@/lib/clock";

import { EMAIL_PRIORITY_LABEL, EMAIL_PRIORITY_TONE, PARTICIPANT_TAG_LABEL } from "./labels";

/**
 * How much of the letter a card shows — the prototype's `substring(0, 100)`.
 *
 * Named rather than inline so the trailing `…` and the cut agree, and so the
 * number is one edit away from being a design decision somebody can change.
 */
const SNIPPET_LENGTH = 100;

/**
 * The first hundred characters, with the prototype's trailing ellipsis.
 *
 * The ellipsis is **conditional**, which the prototype's is not: it appends `…`
 * to every snippet, so a two-word email reads as if it had been truncated. A
 * body shorter than the cut is shown whole and says so by not trailing off.
 *
 * Spread rather than `slice`, `NotesSubTab.clamped`'s rule: `String.length`
 * counts UTF-16 code units, so cutting on units can leave half a surrogate pair
 * — a lone surrogate rendered as a replacement character in the middle of a
 * sentence.
 */
function snippet(body: string): string {
  const characters = [...body];
  return characters.length <= SNIPPET_LENGTH
    ? body
    : `${characters.slice(0, SNIPPET_LENGTH).join("")}…`;
}

export function EmailsSubTab() {
  const emails = useEmailLogs();
  const { openComposer } = useDiaryNav();

  return (
    <div data-testid="emails-subtab" className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto p-[8px_10px]">
        {emails.isPending ? (
          <p data-testid="emails-loading" className="text-[11.5px] text-faint">
            Loading sent emails…
          </p>
        ) : emails.isError && emails.data === undefined ? (
          <p role="alert" data-testid="emails-error" className="text-[11.5px] text-error">
            ⚠ Your sent emails could not be loaded. Try again in a moment.
          </p>
        ) : emails.data === undefined ? null : emails.data.items.length === 0 ? (
          /* The prototype's own sentence. A pane that rendered an empty list
             would read as "we have not checked", which is a different fact. */
          <p data-testid="emails-empty" className="text-[11.5px] text-faint">
            No emails sent yet. Use the button below to compose.
          </p>
        ) : (
          <>
            {/* The stale strip: rows in hand, and the last refresh failed. */}
            {emails.isError && (
              <p
                role="alert"
                data-testid="emails-stale"
                className="mb-[6px] text-[11px] text-error"
              >
                ⚠ Could not refresh — showing the emails last loaded.
              </p>
            )}
            {/* The server's `total`, not `items.length`: what is on screen is one
                page, and the two numbers differ exactly when the "Show more"
                below matters. `null` on a page the server did not count, which
                cannot happen for `pages[0]` but is what the type admits. */}
            {emails.data.total !== null && (
              <p
                data-testid="emails-count"
                className="mb-[6px] font-display text-[9.5px] font-bold tracking-[0.3px] text-muted-text uppercase"
              >
                {emails.data.total === 1 ? "1 email" : `${emails.data.total} emails`}
              </p>
            )}

            <div className="flex flex-col gap-[6px]">
              {emails.data.items.map((email) => (
                <article
                  key={email.id}
                  data-testid="email-card"
                  data-email-id={email.id}
                  data-claim-id={email.claimId ?? ""}
                  data-priority={email.priority}
                  className="rounded border border-steel/30 bg-steel-soft p-[8px_10px]"
                >
                  <h4
                    data-testid="email-subject"
                    className="flex flex-wrap items-center gap-[6px] text-[12px] font-semibold text-steel"
                  >
                    <span>✉ {email.subject}</span>
                    <span
                      data-testid="email-sent-badge"
                      className="rounded-full bg-ok-soft px-[6px] py-px text-[9.5px] font-semibold text-ok"
                    >
                      Sent {formatSentAt(email.sentAt)}
                    </span>
                    {/* Normal is unaccented and unchipped — most email is
                        normal, and a chip on every card would say nothing. */}
                    {email.priority !== "normal" && (
                      <span
                        data-testid="email-priority"
                        className={`rounded-full px-[6px] py-px text-[9.5px] font-semibold ${EMAIL_PRIORITY_TONE[email.priority]}`}
                      >
                        {EMAIL_PRIORITY_LABEL[email.priority]}
                      </span>
                    )}
                  </h4>

                  <p data-testid="email-recipients" className="mt-[2px] text-[11px] text-text">
                    To: {email.recipients.map((role) => PARTICIPANT_TAG_LABEL[role]).join(", ")}
                    {email.workerName === null ? "" : ` · ${email.workerName}`}
                  </p>

                  {email.body !== null && (
                    <p data-testid="email-snippet" className="mt-[3px] text-[10.5px] text-faint">
                      {snippet(email.body)}
                    </p>
                  )}
                </article>
              ))}
            </div>

            {/* Present exactly while the server says there is another page —
                `hasNextPage` follows `nextCursor`, so this carries no count the
                browser worked out for itself (`StageGroup`'s rule). */}
            {emails.hasNextPage && (
              <button
                type="button"
                data-testid="emails-more"
                disabled={emails.isFetchingNextPage}
                onClick={() => void emails.fetchNextPage()}
                className="mt-[6px] w-full rounded border border-border py-[5px] text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
              >
                {emails.isFetchingNextPage ? "Loading…" : "Show more"}
              </button>
            )}
          </>
        )}
      </div>

      <div className="border-t border-border p-[8px_10px]">
        <button
          type="button"
          data-testid="email-compose-open"
          onClick={() => openComposer()}
          className="w-full rounded border border-dashed border-border bg-surface-2 p-[8px] text-[12px] font-semibold text-muted-text hover:border-brand hover:text-brand"
        >
          ＋ Compose Email to Stakeholders
        </button>
      </div>
    </div>
  );
}
