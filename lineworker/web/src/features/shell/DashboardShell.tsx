/**
 * Supervisor / analyst shell — the layout frame around the portfolio dashboard.
 *
 * Story 1.3 proved that "Enter Console →" lands the right roles here; Story 5.1
 * fills the section it left holding a placeholder. The shell still owns only
 * the frame: `TopBar` (identity, caseload tiles, SLA strip) above,
 * `DashboardPage` inside, and nothing about either in here.
 *
 * **`aria-label="Portfolio dashboard"` is load-bearing.** It is the section's
 * accessible name, it is how `App.test.tsx` identifies this shell three times
 * over, and it is what makes the dashboard a named landmark rather than an
 * anonymous div. It survives whatever fills the section.
 *
 * The width cap is gone with the placeholder: six KPI cards in a row need the
 * viewport, and Epic 5's charts and tables will need more of it.
 *
 * **`h-screen` plus `min-h-0`, exactly as `WorkspaceShell` has it.** The pair
 * is what makes `overflow-y-auto` on `<main>` mean anything: with `min-h-screen`
 * on the frame and no `min-h-0` on the flex child, `main` has no height to
 * overflow — a flex item's `min-height` floors at its content — so the scroll
 * container never engages and the *document* scrolls instead, carrying `TopBar`
 * off the top. The two shells scroll identically now, and a supervisor keeps
 * the caseload tiles and SLA strip in view the way a handler does.
 */
import { DashboardPage } from "@/features/dashboard/DashboardPage";

import { TopBar } from "./TopBar";

export function DashboardShell() {
  return (
    <div className="flex h-screen flex-col">
      <TopBar />
      <main className="min-h-0 flex-1 overflow-y-auto px-[18px] pt-4 pb-10">
        <section aria-label="Portfolio dashboard" className="w-full">
          <DashboardPage />
        </section>
      </main>
    </div>
  );
}
