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
} as const;
