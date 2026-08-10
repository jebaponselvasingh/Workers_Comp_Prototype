import type { UserRole } from "@/api/auth";

export const LOGIN_ROUTE = "/";
export const DASHBOARD_ROUTE = "/dashboard";
export const WORKSPACE_ROUTE = "/workspace";

/**
 * Where a persona lands after "Enter Console →" (FR-LOGIN-2), or `null`
 * when this build has no shell for the role.
 *
 * The *routing* is client-side, but the input is not: `role` comes from
 * `/api/me`, resolved server-side from `app_user`. The SPA never decides
 * what a role is, only which shell renders it (AD-1).
 *
 * Exhaustive on purpose. A catch-all `else → /dashboard` looks harmless
 * until `user_role` gains a member the deployed bundle predates: the guard
 * rejects the role, redirects to the catch-all, the same guard rejects it
 * again, and React Router loops until it throws "Maximum update depth
 * exceeded". Returning `null` lets callers say what happened instead.
 */
export function homeRouteFor(role: UserRole): string | null {
  switch (role) {
    case "handler":
      return WORKSPACE_ROUTE;
    case "supervisor":
    case "analyst":
      return DASHBOARD_ROUTE;
    default:
      return null;
  }
}
