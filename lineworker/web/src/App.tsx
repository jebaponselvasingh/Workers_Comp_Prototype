/**
 * Route table (Story 1.3).
 *
 * Two protected shells behind one guard, and a catch-all that sends
 * anything else to the login screen — so a stale bookmark or a typo lands
 * somewhere sane rather than on a blank page.
 */
import { Navigate, Route, Routes } from "react-router";

import { LoginScreen } from "@/features/login/LoginScreen";
import { DashboardShell } from "@/features/shell/DashboardShell";
import { RequireSession } from "@/features/shell/RequireSession";
import { WorkspaceShell } from "@/features/shell/WorkspaceShell";
import { DASHBOARD_ROUTE, LOGIN_ROUTE, WORKSPACE_ROUTE } from "@/features/shell/routes";

export default function App() {
  return (
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
  );
}
