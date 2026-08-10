/**
 * Server state for identity: who am I, who could I be, and the two
 * mutations that change the answer.
 *
 * AD-9: TanStack Query owns this state. No component keeps a copy of the
 * current user in React state — `useMe()` is the single reader, so a
 * logout invalidates it everywhere at once.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type Me = components["schemas"]["Me"];
export type Persona = components["schemas"]["Persona"];
export type UserRole = components["schemas"]["UserRole"];

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async (): Promise<Me> => {
      const { data } = await api.GET("/me");
      return data!;
    },
    // A 401 here is the answer ("nobody"), not a transient failure worth
    // hammering the server over — the route guard reads it and redirects.
    retry: false,
    staleTime: 30_000,
  });
}

export function usePersonas() {
  return useQuery({
    queryKey: queryKeys.personas,
    queryFn: async (): Promise<Persona[]> => {
      const { data } = await api.GET("/personas");
      return data!.items;
    },
    // The seeded persona list changes only when the database is re-seeded.
    staleTime: Infinity,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (personaId: number): Promise<Me> => {
      const { data } = await api.POST("/auth/login", { body: { personaId } });
      return data!;
    },
    onSuccess: (me) => {
      // Clear before seeding, symmetrically with `useLogout` (code review,
      // 2026-08-10). Logout is not the only way a session ends: a TTL
      // expiry, a database reset, an admin revocation or another tab
      // logging out all end it with the cache left intact, and the guard
      // then redirects here. Without this, logging in as a second persona
      // reuses the first one's still-fresh entries — their caseload tiles
      // would render for up to `staleTime` under the new name, which is
      // exactly the cross-persona leak AD-7 exists to prevent, arriving
      // via the client instead of the server.
      queryClient.clear();
      // Seed `me` from the login response so the guard on the next route
      // does not have to round-trip before rendering.
      queryClient.setQueryData(queryKeys.me, me);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      await api.POST("/auth/logout");
    },
    // Always clear, even if the request failed: the intent is to stop
    // showing this user's data on this screen. The server-side session is
    // what actually ends the session; this just stops the SPA from
    // rendering a cache the next persona should not see.
    onSettled: () => {
      queryClient.clear();
    },
  });
}
