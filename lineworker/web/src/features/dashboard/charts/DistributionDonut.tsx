/**
 * A donut over a finished enum-keyed series — the prototype's `buildDonut`,
 * drawn by Recharts (UX-DR7, AC 1).
 *
 * The prototype builds the arcs by hand: `buildDonut` (line 995) walks the
 * values, converts each to a sweep angle, and emits an SVG path per slice. This
 * component hands the same values to a `<Pie>` and lets Recharts do the
 * trigonometry, which is the *only* thing it lets Recharts do. The counts, the
 * ordering, the centre total and which categories exist at all arrived decided
 * from `services/worklist/charts` (AD-1).
 *
 * **No `label` and no `percent` prop on `Pie`.** Both would put a number on
 * screen that no service computed — `percent` in particular divides a slice by
 * a total Recharts summed itself, which is the browser aggregating a scope it
 * does not hold. The prototype shows counts in its legend and the scope total
 * in the hole, and no percentage appears anywhere in the design contract, which
 * is what makes this line easy to hold. `noDerivation.test.ts` bans the two
 * props by name.
 *
 * **The figure is `aria-hidden` and the legend is the accessible rendering.**
 * `RiskGauge`'s pattern, extended from one value to a series: the SVG says
 * nothing to a screen reader, and the label/count rows beneath it — which are
 * the prototype's own legend and are *visible* — carry every pair. That is
 * deliberately one mechanism rather than two. An `sr-only` list beside a
 * visible legend saying the same words would be announced twice, which is the
 * duplication this codebase has now rejected in `QueuePane`'s error branch and
 * in `HandlerBenchmarkTable`'s cycle-speed cell. The bar charts, whose labels
 * are drawn *inside* the hidden SVG, do carry an `sr-only` list — same
 * vocabulary, applied where the visible text is unreachable.
 *
 * **Inert.** Segment click drill-through is Story 5.5. The component takes a
 * finished series as a prop and emits nothing, so 5.5 attaches a handler here
 * and changes no caller.
 */
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

import { ChartFrame } from "./ChartFrame";
import { CHART_HEIGHT, UNKNOWN_KEY_FILL } from "./chartTheme";

/** The generated shape of an enum-keyed series — `{ key, count }` items. */
export interface CategorySeries {
  items: readonly { key: string; count: number }[];
  total: number;
}

/** The donut's hole and rim, as fractions of the available radius. */
const INNER_RADIUS = "58%";
const OUTER_RADIUS = "94%";

/**
 * The opacity the prototype draws its arcs at (`buildDonut`, line 997).
 *
 * Kept because the arcs sit on a `--p2` track and full-strength error red beside
 * full-strength ok green reads as a warning light rather than as a chart.
 */
const ARC_OPACITY = 0.85;

export function DistributionDonut({
  testId,
  title,
  series,
  label,
  fill,
  centreCaption,
  emptyMessage,
  errorMessage,
  isPending,
  isError,
}: {
  testId: string;
  title: string;
  /** The server's series, or `undefined` while it is unknown. */
  series: CategorySeries | undefined;
  /** Wire key → display label. UI-owned copy over snake_case wire values. */
  label: Record<string, string>;
  /** Wire key → CSS colour, from `chartTheme`. */
  fill: Record<string, string>;
  /** What the centre number counts, for the accessible name. */
  centreCaption: string;
  emptyMessage: string;
  errorMessage: string;
  isPending: boolean;
  isError: boolean;
}) {
  // One predicate for `aria-busy` and for the skeleton, `HandlerBenchmarkTable`'s
  // ruling: a section that says it is busy while a chart is on screen is
  // describing something nobody sees, and the conjunction keeps a query status
  // without a payload and a payload without a status from splitting the two.
  const isLoading = isPending && series === undefined;

  return (
    <ChartFrame
      testId={testId}
      title={title}
      height={CHART_HEIGHT.donut}
      isLoading={isLoading}
      isError={isError}
      isEmpty={series !== undefined && series.items.length === 0}
      errorMessage={errorMessage}
      emptyMessage={emptyMessage}
    >
      {series !== undefined && (
        <div className="flex h-full items-center gap-3">
          <div className="relative h-full w-[46%] shrink-0">
            <div aria-hidden className="h-full w-full">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={[...series.items]}
                    dataKey="count"
                    nameKey="key"
                    innerRadius={INNER_RADIUS}
                    outerRadius={OUTER_RADIUS}
                    // The prototype's first arc starts at twelve o'clock and
                    // runs clockwise; Recharts' default is counter-clockwise
                    // from three o'clock.
                    startAngle={90}
                    endAngle={-270}
                    isAnimationActive={false}
                    stroke="none"
                  >
                    {series.items.map((item) => (
                      <Cell
                        key={item.key}
                        fill={fill[item.key] ?? UNKNOWN_KEY_FILL}
                        fillOpacity={ARC_OPACITY}
                      />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
            </div>
            {/* The scope total, in the hole. Absolutely positioned rather than
                a Recharts `<Label>`, so the one number in the middle of this
                chart is plainly a value off the response and not something a
                render prop computed. */}
            <span
              data-testid={`${testId}-total`}
              className="pointer-events-none absolute inset-0 grid place-items-center font-mono text-[13px] font-bold text-text"
            >
              <span className="sr-only">{centreCaption}: </span>
              {series.total}
            </span>
          </div>

          {/* The prototype's legend, and the accessible rendering of the arcs
              beside it — one list, visible, read once. */}
          <ul
            data-testid={`${testId}-legend`}
            className="flex min-w-0 flex-1 flex-col gap-[6px] text-[11px] text-muted-text"
          >
            {series.items.map((item) => (
              <li
                key={item.key}
                data-testid={`${testId}-legend-row`}
                data-key={item.key}
                className="flex items-center gap-[6px]"
              >
                <span
                  aria-hidden
                  style={{ background: fill[item.key] ?? UNKNOWN_KEY_FILL }}
                  className="inline-block h-[9px] w-[9px] shrink-0 rounded-[2px]"
                />
                <span className="truncate">{label[item.key] ?? item.key}:</span>
                <b className="font-mono text-text">{item.count}</b>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ChartFrame>
  );
}
