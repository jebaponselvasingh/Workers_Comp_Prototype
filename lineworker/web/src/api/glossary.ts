/**
 * Server state for the WC glossary (FR-GLOS-1).
 *
 * The one list in this app that is the same for everybody: no employer
 * scope, no PHI, no persona in the response. That is what lets the panel
 * filter it in the browser instead of asking the server per keystroke —
 * see `GlossaryPanel.tsx` for the AD-7 argument in full.
 *
 * `staleTime: Infinity` is not an optimisation, it is the truth about the
 * data: glossary rows change only when a migration changes them, so a
 * refetch could not produce a different answer within a session.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type GlossaryTerm = components["schemas"]["GlossaryTermResponse"];
export type GlossaryList = components["schemas"]["GlossaryList"];

/**
 * @param enabled gate for "fetched on first open" — the panel is mounted
 * for the whole session but most sessions never open it.
 */
export function useGlossary(enabled = true) {
  return useQuery({
    queryKey: queryKeys.glossary,
    queryFn: async (): Promise<GlossaryList> => {
      const { data } = await api.GET("/glossary");
      return data!;
    },
    enabled,
    staleTime: Infinity,
    // The panel is unmounted while closed, so the query goes inactive on
    // every close. With the default 5-minute gcTime, a handler who opened
    // the glossary before lunch would pay for it again after — for rows
    // that cannot have changed. 25 rows kept for the life of the tab is the
    // cheaper end of that trade; `useLogout` clears the cache wholesale, so
    // this does not outlive a session.
    gcTime: Infinity,
  });
}
