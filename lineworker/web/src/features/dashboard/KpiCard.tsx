/**
 * One portfolio KPI card — the prototype's `.kpi` (styles at line 52), ported
 * onto design tokens.
 *
 * Written by hand rather than composed from the vendored shadcn `Card`, for
 * `CaseCard`'s reason: this console is deliberately dense (12–14px padding,
 * 10px labels) and shadcn's card ships `gap-6 py-6`, so using it would mean
 * overriding every spacing utility it sets and inheriting the ones nobody
 * remembered to.
 *
 * **Purely presentational, and that is the whole design.** It receives a
 * formatted `value` — a string the page produced from the server's figure — a
 * label, a caption and a tone. It compares nothing, totals nothing and knows
 * no threshold; `noDerivation.test.ts` walks this directory and fails a build
 * over any of it.
 *
 * **Ready for Story 5.5 without a redesign.** Drill-through makes every card a
 * click target, and the shape that survives that is the one here: content in,
 * nothing out. Adding an `onClick` (or wrapping the section in a link) then
 * changes this file's element and none of its callers, because no caller
 * passes behaviour today.
 */

/** The prototype's five `.kpi` modifiers, plus its unmodified default. */
export type KpiTone = "steel" | "warn" | "ok" | "error" | "brand" | "plain";

const TONE_CLASS: Record<KpiTone, string> = {
  steel: "text-steel",
  warn: "text-warn",
  ok: "text-ok",
  error: "text-error",
  brand: "text-brand",
  // `.kpi` with no modifier: the figure inherits body text. Spelled out rather
  // than left to a fallback so the map is exhaustive — a tone added to the
  // union becomes a TypeScript error here, which is a better place to find out
  // than a card rendering an unstyled number.
  plain: "text-text",
};

export function KpiCard({
  testId,
  value,
  label,
  caption,
  tone,
}: {
  /** `data-testid` stem, kebab-case: `kpi-<slug>`. */
  testId: string;
  /**
   * The figure, already formatted. Never absent.
   *
   * Not `string | null` with an em-dash fallback, which is `SlaStrip`'s shape
   * for a genuine reason it does not share: the server really does send `null`
   * for an SLA segment with no data, so a tile there has an unknown value to
   * draw. Every field on `/dashboard/summary` is a non-nullable integer, so an
   * unknown figure here is not a card with a dash in it — it is a skeleton
   * (pending) or the error banner (failed), both of which `DashboardPage`
   * renders *instead of* the cards. A fallback would be dead code pretending
   * to be honest degradation.
   */
  value: string;
  label: string;
  caption: React.ReactNode;
  tone: KpiTone;
}) {
  return (
    <section
      data-testid={testId}
      className="rounded-md border border-border bg-surface px-[14px] py-3"
    >
      {/* The figure carries its own test id so assertions match it exactly:
          "100" contains "10", and a card-level substring check would pass on
          the wrong number. */}
      <div
        data-testid={`${testId}-value`}
        className={`font-mono text-[20px] leading-none font-bold ${TONE_CLASS[tone]}`}
      >
        {value}
      </div>
      <div className="mt-1 text-[10px] tracking-[0.4px] text-faint uppercase">
        {label}
      </div>
      <div className="mt-1 text-[10px] text-muted-text">{caption}</div>
    </section>
  );
}

/** A card whose figure has not arrived yet — the row's shape, held open. */
export function KpiCardSkeleton() {
  return (
    <div
      data-testid="kpi-skeleton"
      aria-hidden
      className="h-[74px] animate-pulse rounded-md bg-surface-2"
    />
  );
}
