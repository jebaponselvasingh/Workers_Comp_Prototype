/**
 * Supervisor / analyst shell — layout frame only.
 *
 * The KPI cards, charts and worklist that fill it are Epic 5; this story
 * only has to prove that "Enter Console →" lands the right roles here.
 */
import { TopBar } from "./TopBar";

export function DashboardShell() {
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <main className="flex flex-1 items-start justify-center p-6">
        <section
          aria-label="Portfolio dashboard"
          className="w-full max-w-3xl rounded-lg border border-border bg-surface p-4 text-sm text-muted-text"
        >
          Portfolio dashboard arrives in Epic 5.
        </section>
      </main>
    </div>
  );
}
