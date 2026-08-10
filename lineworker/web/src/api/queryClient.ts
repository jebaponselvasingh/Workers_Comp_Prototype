/**
 * The app's single QueryClient (AD-9), and the app's single 401 policy.
 *
 * Task 2 asked for "401 → redirect to login (no toast storm)". That splits
 * into two jobs and both live here:
 *
 * 1. **Never retry a 4xx.** A 401 is the server's considered answer, and
 *    repeating the request produces the same answer more slowly. Only
 *    server-side and transport failures get another go.
 * 2. **Any 401, from any query, means the session is gone.** Not just the
 *    `me` query — an Epic 2 claim list that 401s must reach the login
 *    screen too. The cache-level handler invalidates `me`, and the route
 *    guard turns that into exactly one redirect. One redirect, no toasts,
 *    and no query has to know about auth.
 *
 * The `me` query is excluded from rule 2 on purpose: it is the query whose
 * 401 the guard already handles, and invalidating it from its own error
 * handler would refetch → 401 → invalidate forever.
 */
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError, isUnauthenticated } from "./errors";
import { queryKeys } from "./queryKeys";

export function createQueryClient(): QueryClient {
  // The caches are constructed before the client they belong to, so the
  // handlers reach it through a holder rather than closing over it.
  const ref: { client?: QueryClient } = {};

  const onSessionLost = (error: unknown, isMeQuery: boolean): void => {
    if (!isUnauthenticated(error) || isMeQuery) return;
    void ref.client?.invalidateQueries({ queryKey: queryKeys.me });
  };

  const queryCache = new QueryCache({
    onError: (error, query) => onSessionLost(error, query.queryKey[0] === queryKeys.me[0]),
  });
  const mutationCache = new MutationCache({
    onError: (error) => onSessionLost(error, false),
  });

  const client = new QueryClient({
    queryCache,
    mutationCache,
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status < 500) return false;
          return failureCount < 2;
        },
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });

  ref.client = client;
  return client;
}
