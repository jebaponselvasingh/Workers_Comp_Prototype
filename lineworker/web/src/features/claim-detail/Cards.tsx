/**
 * The case file's shared primitives — the prototype's `.card`, `.kv`, `.tl`
 * and `.cbar`, ported onto design tokens.
 *
 * Written by hand rather than composed from the vendored shadcn `Card` for
 * the reason Story 2.1's `ClaimCard` was: this console is deliberately
 * dense (10–12px padding, 11.5px labels, hairline dividers), and shadcn's
 * card ships `gap-6 py-6`, so using it would mean overriding every spacing
 * utility it sets and inheriting the ones nobody remembered to.
 *
 * **Nothing in this file decides anything.** No threshold, no comparison
 * against a score, no arithmetic on a payload figure — the cost bar receives
 * three percentages the server computed, the timeline receives the slice the
 * server cut. `noDerivation.test.ts` walks this directory and fails a build
 * over any of it.
 */
import type { CostSplit, RiskBand, TimelineEntry } from "@/api/claims";

/** A section card: dense border box with a small uppercase heading. */
export function CaseCard({
  title,
  children,
  className = "",
  testId,
}: {
  title: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      className={`rounded-lg border border-border bg-surface p-3 ${className}`}
    >
      <h3 className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
        {title}
      </h3>
      {children}
    </section>
  );
}

/** The prototype's `.kv` row: label left, value right, hairline between. */
export function Kv({
  label,
  children,
  testId,
  valueClassName = "",
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-hairline py-[5px] last:border-b-0">
      <span className="shrink-0 text-[11px] text-muted-text">{label}</span>
      <span
        data-testid={testId}
        className={`min-w-0 text-right text-[11.5px] text-text ${valueClassName}`}
      >
        {children}
      </span>
    </div>
  );
}

/** The two-column card grid the prototype calls `.g2`. */
export function CardGrid({ children }: { children: React.ReactNode }) {
  return <div className="mb-[10px] grid gap-[10px] lg:grid-cols-2">{children}</div>;
}

/** Severity band → the token a figure or a gauge arc is drawn in. */
export const RISK_TEXT: Record<RiskBand, string> = {
  high: "text-error",
  med: "text-warn",
  low: "text-ok",
};

/**
 * An ISO date, or an em dash.
 *
 * The dash is load-bearing rather than cosmetic: 62 seeded settlement events
 * and 101 documents carry no date at all, because the prototype writes a
 * timing note (`Closed`, `Post-surgery`) where a date belongs. Rendering the
 * absence is the honest answer (NFR-3); inventing one would be worse than
 * the prototype's own behaviour, which at least prints the note.
 */
export function formatDate(iso: string | null): string {
  return iso ?? "—";
}

/**
 * The three-segment paid-cost bar.
 *
 * The three widths arrive from the server already summing to exactly 100 —
 * see `services/derivations/claim_money.py`, which rounds two and gives the
 * third the remainder. The prototype rounds each independently and can
 * total 99 or 101, which under- or overflows the track it is drawn in.
 */
export function CostBar({
  split,
  showExpense = false,
  legend,
}: {
  split: CostSplit;
  showExpense?: boolean;
  legend: React.ReactNode;
}) {
  return (
    <div className="mt-[10px]">
      <div
        data-testid="cost-bar"
        className="flex h-[7px] overflow-hidden rounded-full bg-surface-2"
      >
        <span
          data-testid="cost-bar-indemnity"
          style={{ width: `${split.indemnityPct}%` }}
          className="block bg-steel"
        />
        <span
          data-testid="cost-bar-medical"
          style={{ width: `${split.medicalPct}%` }}
          className="block bg-brand"
        />
        {showExpense && (
          <span
            data-testid="cost-bar-expense"
            style={{ width: `${split.expensePct}%` }}
            className="block bg-faint"
          />
        )}
      </div>
      <div className="mt-[6px] flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-text">
        {legend}
      </div>
    </div>
  );
}

export function CostLegendItem({
  swatch,
  children,
}: {
  swatch: string;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span aria-hidden className={`inline-block size-2 rounded-[2px] ${swatch}`} />
      {children}
    </span>
  );
}

/**
 * The case timeline card.
 *
 * The entries are exactly what the server sent — the treatment variant
 * receives the recent slice, every other variant the whole log, and *which*
 * is a decision made in `services/claims/detail.py` (AD-1). This component
 * does not slice, sort or count.
 *
 * The empty state is its own branch (NFR-3): a card that rendered an empty
 * list would read as "nothing has happened on this claim", which for a
 * settled claim headed "Summary of actions taken" is a lie about the file.
 */
export function TimelineCard({
  title,
  entries,
  markDone = false,
  testId = "timeline-card",
}: {
  title: React.ReactNode;
  entries: TimelineEntry[];
  markDone?: boolean;
  testId?: string;
}) {
  return (
    <CaseCard title={title} testId={testId}>
      {entries.length === 0 ? (
        <p data-testid="timeline-empty" className="text-[11.5px] text-faint">
          No timeline events on file.
        </p>
      ) : (
        <ul className="flex flex-col">
          {entries.map((entry, index) => (
            <li
              // The log is append-only and has no id on the wire; two events
              // can share a date, a tag and even a description (a repeated
              // follow-up), so position is the only stable key — and it is
              // stable precisely because the order is the server's append
              // order rather than anything this component decides.
              key={`${index}-${entry.tag}`}
              data-testid="timeline-entry"
              className="flex items-baseline gap-2 border-b border-hairline py-[5px] text-[11.5px] last:border-b-0"
            >
              <span
                data-testid="timeline-entry-date"
                className="w-[86px] shrink-0 font-mono text-[10px] text-faint"
              >
                {markDone && entry.eventDate ? "✓ " : ""}
                {formatDate(entry.eventDate)}
              </span>
              <span className="min-w-0 flex-1 text-text">{entry.description}</span>
              <span
                data-testid="timeline-entry-tag"
                className="shrink-0 rounded-[2px] bg-surface-2 px-[5px] py-px text-[9px] font-bold tracking-[0.3px] text-muted-text uppercase"
              >
                {entry.tag}
              </span>
            </li>
          ))}
        </ul>
      )}
    </CaseCard>
  );
}
