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
 * **Story 5.5 made the segments click targets, and the prediction held**: the
 * component took a finished series and emitted nothing, so drill-through
 * attached one optional handler here and changed no caller that does not want
 * it.
 *
 * **The legend row is the click target, not the arc.** A `<button>` gets
 * keyboard focus, Enter and Space and a visible ring for free; an SVG `<path>`
 * gets none of those, and a `<path>` with a `tabIndex` would be a control with
 * no accessible name inside a figure the whole component deliberately hides
 * from assistive technology. The arc *also* carries an `onClick` — Recharts'
 * `<Cell onClick>` — and that is **redundant with the button by design**: a
 * reader who aims at the slice should not have to discover that the words
 * beside it are the control.
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
  onSelect,
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
  /**
   * Open the claims behind one segment, by its wire key.
   *
   * Optional, so a series with no drill-through behind it renders exactly what
   * it rendered before — the legend stays a plain list rather than becoming a
   * row of buttons that go nowhere. `PortfolioCharts` supplies it for the two
   * donuts and deliberately supplies nothing to `SlaTiles`.
   */
  onSelect?: (key: string) => void;
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
      // Two ways to be empty, and the second one only became reachable when a
      // series arrived **zero-filled**. Every distribution before Story 7.1
      // omitted a category the scope did not contain, so "no rows" and "no
      // claims" were the same fact and `items.length` answered both. The fraud
      // band vocabulary is a rule's rather than a column's, so an empty book
      // arrives as three segments of zero — which drew a donut with no arcs in it
      // over a legend of three "0" rows, and the sentence written for exactly
      // that reader never rendered. The series total is the honest test: a chart
      // of nothing is a chart of nothing however many keys it names.
      isEmpty={series !== undefined && (series.items.length === 0 || series.total === 0)}
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
                        // Pointer parity with the legend button below, and
                        // nothing more: the arc is inside an `aria-hidden`
                        // figure, so this is the mouse's affordance and the
                        // button is the keyboard's and the screen reader's.
                        onClick={onSelect === undefined ? undefined : () => onSelect(item.key)}
                        style={onSelect === undefined ? undefined : { cursor: "pointer" }}
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
            {series.items.map((item) => {
              const text = label[item.key] ?? item.key;
              const row = (
                <>
                  <span
                    aria-hidden
                    style={{ background: fill[item.key] ?? UNKNOWN_KEY_FILL }}
                    className="inline-block h-[9px] w-[9px] shrink-0 rounded-[2px]"
                  />
                  <span className="truncate">{text}:</span>
                  <b className="font-mono text-text">{item.count}</b>
                </>
              );
              return (
                <li
                  key={item.key}
                  data-testid={`${testId}-legend-row`}
                  data-key={item.key}
                  className="flex items-center gap-[6px]"
                >
                  {onSelect === undefined ? (
                    row
                  ) : (
                    <button
                      type="button"
                      data-testid={`${testId}-legend-link`}
                      data-key={item.key}
                      // The segment *and* its figure, so a chart click target
                      // has discernible text rather than being announced as
                      // "button" beside a swatch nobody can see.
                      aria-label={`${text}: ${item.count}. Show these claims.`}
                      onClick={() => onSelect(item.key)}
                      className="flex w-full items-center gap-[6px] rounded text-left hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
                    >
                      {row}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </ChartFrame>
  );
}
