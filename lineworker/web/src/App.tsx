/**
 * Route table (Story 1.3).
 *
 * Two protected shells behind one guard, and a catch-all that sends
 * anything else to the login screen — so a stale bookmark or a typo lands
 * somewhere sane rather than on a blank page.
 *
 * **One `ToastProvider` and one `ToastHost`, both here** (Story 4.3). The
 * primitive is a fixed-position stack, so a second host would be a second stack
 * in the same corner; mounting it above the route table means the one element
 * exists for the life of the tab, which is what a `role="status"` needs to be
 * announced at all (a live region that appears with its content frequently is
 * not). It sits above `Routes` rather than inside a shell for the same reason:
 * the shells unmount on a role change and a logout, and a confirmation raised
 * on the way out would go with them.
 */
import { Navigate, Route, Routes } from "react-router";

import { ToastHost, ToastProvider } from "@/components/ui/toast";
import { LoginScreen } from "@/features/login/LoginScreen";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { DrillClaimsPage } from "@/features/dashboard/drill/DrillClaimsPage";
import { ReadOnlyClaimPage } from "@/features/dashboard/drill/ReadOnlyClaimPage";
import { DashboardShell } from "@/features/shell/DashboardShell";
import { RequireSession } from "@/features/shell/RequireSession";
import { WorkspaceShell } from "@/features/shell/WorkspaceShell";
import { DASHBOARD_ROUTE, LOGIN_ROUTE, WORKSPACE_ROUTE } from "@/features/shell/routes";

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path={LOGIN_ROUTE} element={<LoginScreen />} />

        {/* Story 5.5 turns the dashboard into a **layout route**: one shell,
            three views. `DashboardShell` renders the top bar and an `<Outlet/>`,
            and the three children below fill it — the portfolio overview at the
            index, the drill-through list, and one claim read-only.

            All three sit inside the *same* `RequireSession` guard, which is what
            makes "a handler cannot reach a drill-through" a property of the
            route table rather than of three separate checks. Scope is a
            different question and is not answered here at all: it re-resolves
            server-side on every request (AD-7), so a supervisor who edits the
            URL gets an empty list or a 404, never a route the client refused. */}
        <Route element={<RequireSession allow={["supervisor", "analyst"]} />}>
          <Route path={DASHBOARD_ROUTE} element={<DashboardShell />}>
            <Route index element={<DashboardPage />} />
            <Route path="claims" element={<DrillClaimsPage />} />
            {/* The business id, `WC-nnnn`, as the ID convention requires — the
                claim's own identifier in the path rather than a surrogate. */}
            <Route path="claims/:claimId" element={<ReadOnlyClaimPage />} />
          </Route>
        </Route>

        <Route element={<RequireSession allow={["handler"]} />}>
          <Route path={WORKSPACE_ROUTE} element={<WorkspaceShell />} />
        </Route>

        <Route path="*" element={<Navigate to={LOGIN_ROUTE} replace />} />
      </Routes>
      <ToastHost />
    </ToastProvider>
  );
}
