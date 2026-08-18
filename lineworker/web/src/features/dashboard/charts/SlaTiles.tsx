/**
 * The dashboard's four SLA tiles — the top bar's strip, rendered a second time
 * (AC 3, AD-2).
 *
 * **A styled card, not a Recharts chart**, matching the prototype's `.slac`
 * block (`renderSV`, line 1088). There is nothing to plot: four figures, each
 * with its own target and its own verdict, and a chart of four unrelated
 * quantities on four different scales would be a decoration rather than a
 * comparison.
 *
 * **Every declaration this renders is `features/shell/slaTiles.ts`'s**, imported
 * rather than copied: the four specs in the prototype's order, the four tone
 * classes, the value formatter and the target sentence. That is what AC 3 asks
 * for made structural — "the dashboard tiles agree with the top-bar strip" is
 * not two components that happen to format the same way, it is one server value
 * (`sla.strip_of`) through one vocabulary, twice. A second copy of `TILES` here
 * would be free to drift a label or a precision at a time while both surfaces
 * went on claiming to show one number.
 *
 * **Distinct testids.** `chart-sla-pick` rather than `sla-pick`, because both
 * groups are in one DOM at once and the e2e spec's whole job on this AC is to
 * read them separately and compare. The `slug` on the shared spec is what lets
 * each surface compose its own prefix without either owning the other's.
 *
 * **No tooltip, because the explanation is on the card.** `SlaStrip` wraps each
 * tile in a focusable `TooltipTrigger` — "a tooltip only a mouse can open is not
 * an explanation for everyone who needs one" — because a top-bar tile is 40px of
 * chrome with no room for the target sentence. These cards have that room and
 * print it: the `-target` line under every figure is the same sentence
 * `formatTarget` gives the tooltip, visible to everyone without a hover, a focus
 * or a pointer. An earlier draft kept a `title` attribute *as well*, which was
 * the worst of both — hover-only, unfocusable, unannounced, and redundant with
 * copy already on screen. If this ever stops rendering the target line, it owes
 * the reader the focusable trigger instead, not a `title`.
 *
 * The prototype shows three tiles here (pick, approve, settle) and the top bar
 * shows four. Four is the shipped contract — `/stats/sla` publishes the RTW
 * rate and `strip_of` computes it — and a dashboard group missing one of the
 * four could not satisfy "all four metrics show identical values" (AC 3). The
 * addition is recorded as a deliberate departure from the prototype.
 */
import type { SlaStripData } from "@/api/stats";

import {
  NO_VALUE,
  SLA_TILES,
  TONE_CLASS,
  formatTarget,
  formatValue,
  toneOf,
} from "../../shell/slaTiles";
import { ChartFrame } from "./ChartFrame";
import { CHART_HEIGHT } from "./chartTheme";

export function SlaTiles({
  strip,
  isPending,
  isError,
}: {
  /** The server's strip, or `undefined` while it is unknown. */
  strip: SlaStripData | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  const isLoading = isPending && strip === undefined;

  return (
    <ChartFrame
      testId="chart-sla"
      title="SLA performance"
      height={CHART_HEIGHT.donut}
      isLoading={isLoading}
      isError={isError}
      // Never empty: the strip always carries four tiles, and a segment with
      // nothing behind it is a `no_data` tile rather than an absent one — which
      // is Story 1.5's whole point and the reason an empty scope still renders
      // four em dashes here rather than an empty-state sentence.
      isEmpty={false}
      errorMessage="⚠ SLA figures could not be loaded. Try again in a moment."
      emptyMessage=""
    >
      {strip !== undefined && (
        <ul className="grid h-full grid-cols-2 gap-2">
          {SLA_TILES.map((spec) => {
            const metric = strip[spec.key];
            return (
              <li
                key={spec.key}
                data-testid={`chart-sla-${spec.slug}`}
                className={`flex flex-col items-center justify-center rounded-md px-2 py-1 leading-tight ${TONE_CLASS[toneOf(metric, spec)]}`}
              >
                <span
                  // Its own test id so an assertion matches the figure exactly:
                  // "2.7d" contains "2", and a tile-level substring check would
                  // pass on the wrong number.
                  data-testid={`chart-sla-${spec.slug}-value`}
                  className="font-mono text-[17px] font-bold"
                >
                  {/* `metric?.value == null`, `SlaStrip`'s exact guard. A key
                      missing from the strip — a partial serialisation, a proxy
                      rewriting the body, a fifth `SlaMetricKey` added server-side
                      before the client is regenerated — must read as "no data",
                      not throw. An exception here unmounts `PortfolioCharts`
                      inside `DashboardPage`, taking the KPI cards and the handler
                      table with it: the blast radius three independent queries
                      exist to prevent. */}
                  {metric?.value == null
                    ? NO_VALUE
                    : formatValue(metric.value, metric.decimals, spec.unit)}
                </span>
                <span className="text-[9.5px] tracking-[0.3px] opacity-90">
                  {spec.label}
                </span>
                <span
                  data-testid={`chart-sla-${spec.slug}-target`}
                  className="text-[9px] opacity-90"
                >
                  {metric ? formatTarget(metric, spec) : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </ChartFrame>
  );
}
