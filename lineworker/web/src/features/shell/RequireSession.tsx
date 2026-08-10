/**
 * Route guard (AC 3, AC 4 as the SPA experiences it).
 *
 * The guard asks the server who the caller is instead of trusting anything
 * the client holds — there is no client-side role variable to tamper with,
 * and a direct URL or a back-button press after logout goes through the
 * same `/api/me` check as a fresh page load.
 *
 * Only a **401** means "not signed in". A 502 from the proxy, a database
 * outage turning `/me` into a 500, or a dropped connection are *failures to
 * find out* — bouncing those to the login screen would silently log out a
 * perfectly valid session and show nothing explaining why.
 */
import { Navigate, Outlet } from "react-router";

import { useMe, type UserRole } from "@/api/auth";
import { isUnauthenticated } from "@/api/errors";

import { LOGIN_ROUTE, homeRouteFor } from "./routes";

function FullScreenNotice({ children, role }: { children: React.ReactNode; role: "status" | "alert" }) {
  return (
    <div
      role={role}
      aria-live="polite"
      className="flex min-h-screen items-center justify-center px-6 text-center text-sm text-muted-text"
    >
      {children}
    </div>
  );
}

export function RequireSession({ allow }: { allow: readonly UserRole[] }) {
  const { data: me, isPending, error } = useMe();

  if (isPending) {
    return <FullScreenNotice role="status">Checking your session…</FullScreenNotice>;
  }

  if (isUnauthenticated(error)) {
    return <Navigate to={LOGIN_ROUTE} replace />;
  }

  // Known user wins over a failed *refetch* (code review, 2026-08-10).
  // TanStack Query keeps `data` while setting `error` when a background
  // revalidation fails, so `error || !me` tore down a working shell on one
  // unlucky 502 — replacing the screen the user was mid-task on with a
  // notice saying they had not been signed out. Only the case where there
  // is no known user at all is worth a full-screen message.
  if (!me) {
    return (
      <FullScreenNotice role="alert">
        Could not confirm your session — the server did not answer. Check your connection and
        reload; you have not been signed out.
      </FullScreenNotice>
    );
  }

  if (!allow.includes(me.role)) {
    // A supervisor who bookmarks /workspace gets their own shell, not an
    // error: the role decides the destination, and the role came from the
    // server.
    const home = homeRouteFor(me.role);
    if (home === null) {
      // A role this build has no shell for. Navigating anywhere would just
      // hit another guard that also rejects it — an infinite redirect
      // instead of an explanation.
      return (
        <FullScreenNotice role="alert">
          This console has no workspace for the role “{me.role}”. It is probably running an older
          build than the server — reload, and tell an administrator if it persists.
        </FullScreenNotice>
      );
    }
    return <Navigate to={home} replace />;
  }

  return <Outlet />;
}
