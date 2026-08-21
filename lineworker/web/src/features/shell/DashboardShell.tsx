/**
 * Supervisor / analyst shell — the layout frame around the dashboard routes.
 *
 * Story 1.3 proved that "Enter Console →" lands the right roles here; Story 5.1
 * filled the section it left holding a placeholder; **Story 5.5 makes it a
 * layout route**. The shell still owns only the frame: `TopBar` (identity,
 * caseload tiles, SLA strip) above, and whichever dashboard route matched
 * inside — the portfolio overview, a drill-through list, or a read-only claim.
 *
 * **The `aria-label="Portfolio dashboard"` section moved into `DashboardPage`,
 * and that is the point of the change rather than a side effect of it.** It is
 * that page's accessible name, not the shell's: with a child route showing, a
 * landmark here called "Portfolio dashboard" would be naming a region that is
 * currently a filtered claim list or one claim's case file. Each routed view now
 * carries its own name, so the label stays true whatever matched — and
 * `App.test.tsx`, which identifies this shell by that label, still finds it on
 * the index route because that is where the portfolio dashboard actually is.
 *
 * The width cap is gone with the placeholder: six KPI cards in a row need the
 * viewport, and Epic 5's charts and tables need more of it.
 *
 * **Story 7.1 adds a navigation between the two, and only for one role.**
 * `WorkspaceNav` renders the analyst's two destinations — Portfolio and Fraud —
 * and returns `null` for everybody else, so a supervisor's shell is the element
 * tree Epic 5 shipped with nothing inserted into it. The decision lives in that
 * component rather than in a `me.role` branch here, because this file's job is
 * the frame: a shell that knew which personas get a nav would be the place the
 * *next* section's visibility rule went too.
 *
 * **`h-screen` plus `min-h-0`, exactly as `WorkspaceShell` has it.** The pair
 * is what makes `overflow-y-auto` on `<main>` mean anything: with `min-h-screen`
 * on the frame and no `min-h-0` on the flex child, `main` has no height to
 * overflow — a flex item's `min-height` floors at its content — so the scroll
 * container never engages and the *document* scrolls instead, carrying `TopBar`
 * off the top. The two shells scroll identically now, and a supervisor keeps
 * the caseload tiles and SLA strip in view the way a handler does.
 */
import { Outlet } from "react-router";

import { TopBar } from "./TopBar";
import { WorkspaceNav } from "./WorkspaceNav";

export function DashboardShell() {
  return (
    <div className="flex h-screen flex-col">
      <TopBar />
      <WorkspaceNav />
      <main className="min-h-0 flex-1 overflow-y-auto px-[18px] pt-4 pb-10">
        <Outlet />
      </main>
    </div>
  );
}
