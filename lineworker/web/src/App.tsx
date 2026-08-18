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
import { DashboardShell } from "@/features/shell/DashboardShell";
import { RequireSession } from "@/features/shell/RequireSession";
import { WorkspaceShell } from "@/features/shell/WorkspaceShell";
import { DASHBOARD_ROUTE, LOGIN_ROUTE, WORKSPACE_ROUTE } from "@/features/shell/routes";

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path={LOGIN_ROUTE} element={<LoginScreen />} />

        <Route element={<RequireSession allow={["supervisor", "analyst"]} />}>
          <Route path={DASHBOARD_ROUTE} element={<DashboardShell />} />
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
