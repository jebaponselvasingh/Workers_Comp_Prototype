import type { UserRole } from "@/api/auth";

export const LOGIN_ROUTE = "/";
export const DASHBOARD_ROUTE = "/dashboard";
export const WORKSPACE_ROUTE = "/workspace";
/**
 * The analyst workspace's Fraud section (Story 7.1).
 *
 * A **child of the dashboard route**, not a sibling of it, and that is the whole
 * shape of Epic 7's first surface: the analyst workspace extends the Epic 5
 * dashboard rather than replacing it (the Capability Map places FR-AN-1..6 in
 * `features/dashboard/`), so the analyst keeps the portfolio overview and gains a
 * section beside it. `homeRouteFor("analyst")` therefore stays `DASHBOARD_ROUTE`:
 * the analyst still *lands* on the portfolio and navigates to Fraud, which is
 * what makes "no Epic 5 regression" true of the landing page as well as of the
 * payloads.
 */
export const FRAUD_ROUTE = "/dashboard/fraud";

/**
 * Where a persona lands after "Enter Console →" (FR-LOGIN-2), or `null`
 * when this build has no shell for the role.
 *
 * **Story 7.1 gives the analyst a section the supervisor does not have and
 * deliberately does not change this function.** Landing an analyst on
 * `/dashboard/fraud` would have been the tempting one-word change and is wrong
 * twice over: the portfolio overview is still that persona's own dashboard (it
 * is scoped for them, not borrowed from the supervisor), and a role that landed
 * *inside* a section would have no obvious way back out of it in a console whose
 * navigation is two entries long. The Fraud section is a destination, not a home.
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
