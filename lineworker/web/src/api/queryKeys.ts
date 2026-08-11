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
     * The pages a stage group has been expanded through ("Show more").
     *
     * Keyed by filter *and* stage but not by cursor: this is an infinite
     * query, so the cursors are its page params and live inside the entry
     * rather than beside it. A cursor in the key would make every page its
     * own cache entry and lose the accumulated list on the way back.
     */
    queuePages: (filter: string, stage: string) =>
      ["claims", "queue", filter, "pages", stage] as const,
  },
} as const;
