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
import { FinancialPage } from "@/features/dashboard/financial/FinancialPage";
import { FraudPage } from "@/features/dashboard/fraud/FraudPage";
import { TrendsPage } from "@/features/dashboard/trends/TrendsPage";
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

            {/* Stories 7.1 and 7.2's analyst workspace, behind an **inner**
                guard.

                Nested rather than declared beside the dashboard block, so it
                inherits `DashboardShell` — the analyst gains a section within
                their own console rather than a second shell — and wrapped in a
                narrower `RequireSession` so the role check is the route table's
                and not a component's. A supervisor who types the URL is bounced
                to `homeRouteFor("supervisor")`, which is `/dashboard`: the
                guard's existing behaviour, applied one level down.

                That the endpoints behind it are *also* gated
                (`services/worklist/fraud.py`) is not redundancy. This guard
                decides which screen renders; that one decides who may read the
                figures, and scope decides which figures they are (AD-7). A
                client-side check is a courtesy, never a control. */}
            <Route element={<RequireSession allow={["analyst"]} />}>
              <Route path="fraud" element={<FraudPage />} />
              {/* Story 7.2's second section, inside the **same** guard rather
                  than beside it. One `RequireSession allow={["analyst"]}` for
                  the workspace is what makes "the analyst workspace is
                  analyst-only" a property of the route table; a second guard
                  wrapping one route would be the same rule written twice, free
                  to disagree the first time somebody widened one of them. */}
              <Route path="trends" element={<TrendsPage />} />
              {/* Story 7.4's third section, inside the **same** guard for the
                  reason stated one route up: one `RequireSession
                  allow={["analyst"]}` for the whole workspace is what makes
                  "the analyst workspace is analyst-only" a property of the route
                  table, and a third guard wrapping a third route would be the
                  same rule written three times, free to disagree the first time
                  somebody widened one of them.

                  `financials`, plural, matching the API path and `FINANCIAL_ROUTE`
                  — see that constant on why the folder beside it is singular. */}
              <Route path="financials" element={<FinancialPage />} />
            </Route>
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
