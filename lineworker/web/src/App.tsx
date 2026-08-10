/**
 * SPA shell — Story 1.1.
 *
 * Brand mark plus an empty layout frame demonstrating the information-dense
 * card style (UX-DR12). No routes or screens yet: login is Story 1.3.
 */
export default function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-2">
        <span
          aria-hidden
          className="flex size-7 items-center justify-center rounded bg-brand font-display text-sm font-bold text-white"
        >
          LW
        </span>
        <h1 className="font-display text-lg font-semibold tracking-tight">LINEWORKER</h1>
        <span className="text-xs text-faint">Workers&rsquo; Comp Claims Console</span>
      </header>

      <main className="flex flex-1 items-start justify-center p-6">
        <section
          aria-label="Sample claim card"
          className="w-full max-w-sm rounded-lg border border-border bg-surface shadow-sm"
        >
          <div className="flex items-center justify-between px-3 py-2">
            <span className="font-mono text-xs font-medium text-steel">WC-0000</span>
            <span className="rounded bg-ok-soft px-1.5 py-0.5 text-[11px] font-medium text-ok">
              on track
            </span>
          </div>
          <div className="h-px bg-hairline" />
          <div className="grid grid-cols-3 gap-2 px-3 py-2">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-faint">Reserve</div>
              <div className="font-mono text-sm">—</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-faint">Paid</div>
              <div className="font-mono text-sm">—</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-faint">SLA</div>
              <div className="flex gap-1 pt-0.5">
                <span className="size-2 rounded-full bg-ok" title="ok" />
                <span className="size-2 rounded-full bg-warn" title="warning" />
                <span className="size-2 rounded-full bg-error" title="error" />
              </div>
            </div>
          </div>
          <div className="h-px bg-hairline" />
          <p className="px-3 py-2 text-xs text-muted-text">
            Empty shell — screens arrive with their stories. This card demonstrates the dense
            console style: tight paddings, terse labels, hairline dividers.
          </p>
        </section>
      </main>
    </div>
  );
}
