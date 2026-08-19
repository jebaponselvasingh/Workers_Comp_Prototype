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
 * **Story 5.5 made every card a click target, and the shape held.** The
 * prediction written here one story ago was that drill-through would change
 * this file's element and none of its callers, because no caller passed
 * behaviour. That is what happened: the card gained a `drill` prop describing
 * *where* it goes, wraps its contents in a `<Link>` when it has one, and still
 * emits nothing and decides nothing.
 *
 * **A `<Link>` rather than a `div` with an `onClick`.** It is a navigation to a
 * URL a supervisor can copy, so it is a link — which gets keyboard access,
 * middle-click, and an accessible name for free. The focus ring is visible
 * rather than removed: a dense dashboard of ten cards is exactly the surface
 * where a keyboard user needs to see where they are.
 *
 * **The `<section>` stays outside the link, and the link wraps its contents.**
 * The section is the card — its border, its background, its `data-testid` — and
 * a link *around* a landmark would put a region inside an anchor, which is
 * neither valid nor announceable. Inside, the anchor is the whole clickable
 * area and the card keeps its identity for the tests that already read it.
 */

import { Link } from "react-router";

import { drillHref, type DrillFilters } from "./drill/filters";

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
  drill,
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
  /**
   * The filter set this card's claims are behind, or `null` for a card that
   * opens nothing.
   *
   * A whole filter **set** rather than a single facet, because three of the ten
   * cards open the *unfiltered* list: Total Paid and Total Reserve are sums of
   * cents over the whole scoped book, which is exactly what Total Claims
   * counts, so `{}` is the honest answer for all three (see `DashboardPage`).
   * A single-facet type could not say that, and the alternatives — ruling two
   * of ten cards dead ends, or inventing a "filter" that names a card and
   * cannot be cleared — are both worse.
   *
   * `null` is kept even though every card supplies a set today: a future card
   * whose figure has no claim list behind it should be able to say so, rather
   * than link to a list that does not answer it.
   */
  drill: DrillFilters | null;
}) {
  const body = (
    <>
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
    </>
  );

  return (
    <section
      data-testid={testId}
      className="rounded-md border border-border bg-surface px-[14px] py-3"
    >
      {drill === null ? (
        body
      ) : (
        <Link
          data-testid={`${testId}-link`}
          to={drillHref(drill)}
          // The card's own words are the accessible name: "Total Claims, 100".
          // The `<Link>` would otherwise be announced as the concatenation of
          // three unlabelled divs, which reads as a number and a caption with
          // nothing saying what it opens.
          aria-label={`${label}, ${value}`}
          className="block rounded focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
        >
          {body}
        </Link>
      )}
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
