/**
 * Handler workspace shell — layout frame only.
 *
 * The queue, case detail and copilot panes are Epics 2 and 6. Note that
 * FR-LOGIN-3's "auto-select the first claim" clause is explicitly Epic 2
 * scope, so there is deliberately nothing selected here yet.
 */
import { TopBar } from "./TopBar";

export function WorkspaceShell() {
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <main className="flex flex-1 items-start justify-center p-6">
        <section
          aria-label="Claim workspace"
          className="w-full max-w-3xl rounded-lg border border-border bg-surface p-4 text-sm text-muted-text"
        >
          Claim queue arrives in Epic 2.
        </section>
      </main>
    </div>
  );
}
