/**
 * Every TanStack Query key in the app, declared here and nowhere else (AD-9).
 *
 * The reason this module exists rather than inline key arrays: mutations
 * have to invalidate exactly the keys that hold the affected entity, and
 * that is only checkable if the keys are enumerable in one place. Keys are
 * entity + business id — `claim("WC-1042")`, not `["claim", index]`.
 *
 * Story 1.3 starts it with the two identity keys; later stories add
 * siblings here as they add screens.
 *
 * Note what the stats key does *not* carry: no persona, no employer, no
 * scope. The server answers `/stats/topbar` for whoever holds the session
 * cookie (AD-7), so putting an identity in the key would imply the client
 * chooses whose numbers it sees. Switching personas clears the whole cache
 * (`useLogout`), which is what keeps one persona's tiles out of the next
 * one's shell.
 */
export const queryKeys = {
  me: ["me"] as const,
  personas: ["personas"] as const,
  stats: {
    topbar: ["stats", "topbar"] as const,
    sla: ["stats", "sla"] as const,
  },
  // Reference data (Story 1.6): one key with nothing under it, because
  // there is one glossary. No search term in the key either — the panel
  // filters the cached list in the browser (the sanctioned AD-7 exception
  // for scope-free, PHI-free reference data), so a query per keystroke
  // would be a cache entry per keystroke for the same 25 rows.
  glossary: ["glossary"] as const,
  claims: {
    /**
     * The queue, keyed by filter (Story 2.1).
     *
     * The filter *is* in the key, unlike the glossary's search term, and
     * for the opposite reason: a filtered queue is a different server
     * answer, computed under the caller's scope over data the client does
     * not hold in full (AD-1 forbids re-filtering a cached superset). Two
     * filters are two resources, so they are two cache entries — and
     * switching back to one already fetched is instant, which is the
     * behaviour a handler flipping between "All" and "Litigation" expects.
     *
     * Still no scope in the key, for the reason the stats keys record: the
     * server answers for whoever holds the cookie, and putting an identity
     * here would imply the client chooses whose caseload it sees.
     */
    queue: (filter: string) => ["claims", "queue", filter] as const,
    /**
     * Every queue entry, whatever its filter or page — the prefix a
     * mutation invalidates (Story 2.3).
     *
     * Declared rather than spelled inline at the mutation, because it is
     * exactly the kind of key that goes wrong silently: invalidating
     * `queue(filter)` would refresh the filter the handler happens to be
     * looking at and leave every other cached filter — and every "Show
     * more" page under it — showing the injury type they just corrected.
     * TanStack matches keys by prefix, so this one covers both.
     */
    queues: ["claims", "queue"] as const,
    /**
     * The pages a stage group has been expanded through ("Show more").
     *
     * Keyed by filter, stage, and **the cursor the accumulation starts
     * from** — but not by the cursors after it. Those are the infinite
     * query's own page params and live inside the entry; putting them in
     * the key would make every page its own cache entry and lose the
     * accumulated list on the way back.
     *
     * The *first* cursor is different in kind: it is not a page param the
     * query produced, it is an input handed in from the base queue query,
     * and it encodes the offset the accumulation is anchored at. When the
     * base query refetches and the group has changed underneath it, the
     * first cursor changes — and pages accumulated from the old one
     * describe a list that no longer starts where they think it does. In
     * the key, that is a different entry and the group reloads; out of it,
     * page 1 and page 2 came from two different orderings and nothing
     * anywhere would say so.
     */
    queuePages: (filter: string, stage: string, firstCursor: string | null) =>
      ["claims", "queue", filter, "pages", stage, firstCursor] as const,
    /**
     * One claim's case file (Story 2.2), keyed by its business id.
     *
     * Entity + business id, which is the rule this module opens with: Story
     * 2.3's inline edits invalidate exactly this key plus the queue's, so
     * the header, the gauge and the card that names the same claim move
     * together. Keying on an array index or on the selected-claim state
     * would leave a mutation with nothing precise to invalidate.
     *
     * **Not keyed by filter**, unlike the queue. A case file is the same
     * case file whichever filter the handler happened to arrive through —
     * putting the filter in the key would fetch the identical payload again
     * every time they flipped the queue's dropdown.
     */
    detail: (claimId: string) => ["claims", "detail", claimId] as const,
    /**
     * The **mutation** key every write against one claim carries (Story 2.4).
     *
     * Not a query key: nothing is cached under it. It exists so that
     * `useIsMutating` can answer "is any command against this claim in
     * flight?" across the four hooks that write one — the field patch, the
     * severity score, and the add and remove of a secondary injury.
     *
     * That question has to be answerable in one place because of how
     * `expectedVersion` works: every one of those hooks reads it from the
     * same cached case file and none of them advances it optimistically, so
     * a second commit launched before the first settles carries a version
     * the first has already consumed. The server answers 409 and the handler
     * is told somebody else changed the claim — about their own edit.
     * Story 2.3 fixed that *within* one hook by disabling its inputs while it
     * was busy; Story 2.4 put three more hooks on the same surface, and four
     * independent `isPending` flags is the same bug with more moving parts.
     */
    writes: (claimId: string) => ["claims", "write", claimId] as const,
  },
} as const;
