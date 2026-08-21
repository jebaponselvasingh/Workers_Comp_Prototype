/**
 * One metric's line chart — the console's first time series (UX-DR7, AC 1/3).
 *
 * `DistributionDonut` and `DistributionBars` drawn again for a third form, and
 * their two rulings carried over unchanged: the figure is `aria-hidden` and an
 * accessible list beside it is the rendering a screen reader and every test
 * read, and the card takes a finished server answer and computes nothing. What
 * is new is the *shape* of the answer — a series over a window rather than a
 * distribution over a vocabulary — and two of this story's three acceptance
 * criteria live in what that changes.
 *
 * **`connectNulls={false}`, and it is AC 3 in one prop.** A bucket whose mean or
 * rate had an empty denominator arrives as `value: null`, never `0`
 * (`TrendPointResponse` argues why), and `connectNulls` defaulting to *false* is
 * only half the guarantee — the other half is that nothing here coalesces a null
 * on the way into the chart. `?? 0` anywhere between the payload and `<Line>`
 * would draw the misleading zero line the criterion forbids, and it would look
 * like defensive code. The row builder below writes `null` through deliberately,
 * and the accessible list prints an em dash rather than a number.
 *
 * **Emptiness is `seriesTotal`, never `points.length`.** Every series is the
 * full window in the window's order, gaps included, so a book with nothing in it
 * still arrives as twelve points of `0`/`null` — `points.length` is twelve and a
 * length test can never fire. That is the bug Story 7.1 shipped once
 * (`DistributionDonut.tsx:118-127` is the fix) and this surface is the one where
 * it would have been hardest to see: an empty portfolio would have drawn a flat
 * line along the axis, which reads as a real and excellent result.
 *
 * `seriesTotal` is the **metric's** population, so the two SLA cards empty on a
 * window where nothing settled while the three beside them draw — which is why
 * each card is handed its own `emptyMessage` rather than the section owning one
 * sentence: "no claims fall in this window" under a settlement chart of a busy
 * quarter would be a wrong answer to a right question.
 *
 * **The newest bucket is a period in progress and the card says so.** `partial`
 * is the server's, decided against the clock the window was cut on, and it is
 * marked twice for the same two readerships the low-confidence verdict is: a
 * dashed dot in the figure and the words in the accessible list, plus one clause
 * in the footnote naming the period. Nothing here adjusts a figure for it —
 * scaling a part month up to a whole one would invent claims nobody filed.
 *
 * **The low-confidence mark is the server's verdict, drawn.** `lowConfidence` is
 * `0 < claimCount <= trend_periods.lowConfidenceClaimMax`, decided in
 * `services/worklist/trends.py` because a comparison made here would be the one
 * rule on this payload nobody could see change (AD-8). The card renders it two
 * ways for two readers — a hollow ringed dot in the figure, and the words "low
 * confidence" in the list — which is `DistributionBars`' visible/`sr-only` split
 * applied to a per-point property rather than to a whole series.
 *
 * **The footnote states the gaps rather than leaving them to be counted.**
 * `noDataBuckets` is published for exactly that, and counting nulls in the
 * browser is the arithmetic AD-1 removes. The row it sits in is reserved by
 * `ChartFrame` in every state, so the caption's arrival cannot reflow the grid.
 *
 * **The dots are a pointer affordance and nothing more.** They sit inside the
 * `aria-hidden` figure, so a click there is redundant with the period `<select>`
 * and the legend buttons on the page — `DistributionDonut`'s ruling: an SVG
 * `<circle>` gets no focus, no Enter, no Space and no accessible name, and a
 * reader who aims at a point should not have to discover that the control is
 * elsewhere.
 */
import type { ReactNode } from "react";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
  type DotItemDotProps,
} from "recharts";

import type { TrendCohort, TrendPoint, TrendSeries } from "@/api/dashboard";

import { ChartFrame } from "../charts/ChartFrame";
import { CHART_HEIGHT } from "../charts/chartTheme";

import { cohortFill } from "./trendColors";

/** What a point with no value reads as — `slaTiles.ts`' em dash, one surface on. */
const NO_VALUE = "—";

/** What the accessible list appends to a bucket the clock has not finished. */
const PARTIAL_NOTE = " (partial period)";

/** An ordinary point's radius, and the ring a low-confidence one is drawn with. */
const DOT_RADIUS = 2.5;
const LOW_CONFIDENCE_RADIUS = 4;
const LOW_CONFIDENCE_STROKE = 1.75;
const DOT_STROKE = 1;

/** The reference line's dash, so a target cannot be mistaken for a series. */
const TARGET_DASH = "4 3";

/**
 * The dash a point drawn from an unfinished period is ringed with.
 *
 * A *broken* outline rather than a fourth hue or a fifth radius: the palette is
 * closed and the two sizes already mean "thin evidence", so the one visual
 * channel left that survives a greyscale print is the line itself. A partial
 * bucket that is also thin gets both, which is two true statements about one
 * point rather than a conflict.
 */
const PARTIAL_DASH = "2 2";

/** How much room the value axis reserves for its formatted ticks. */
const AXIS_WIDTH = 52;
const AXIS_TICK = { fontSize: 9, fill: "var(--color-faint)" } as const;

/**
 * One row of the chart's data — one bucket, every line's value on it.
 *
 * Recharts reads a *row* per x position while the payload publishes a *column*
 * per series, so the two have to be transposed somewhere. Doing it here is
 * `DistributionBars`' ruling restated: this is field renaming, not derivation.
 * Nothing is summed, nothing is re-ordered, nothing is dropped and no null
 * becomes a number — the index alignment is the contract (`points` is always the
 * full window, one entry per bucket, in the window's order), not an assumption
 * this file makes about the data.
 */
interface ChartRow {
  bucket: string;
  bucketKey: string;
  [seriesColumn: string]: string | number | boolean | null;
}

/** The column one line reads its magnitude from, and its low-confidence twin. */
function valueColumn(index: number): string {
  return `v${String(index)}`;
}
function flagColumn(index: number): string {
  return `c${String(index)}`;
}
/** …and the twin that says its bucket had not finished when the window was cut. */
function partialColumn(index: number): string {
  return `p${String(index)}`;
}

/**
 * Whether anything in this card's window is worth drawing.
 *
 * A `for` loop rather than `.some`/`.reduce`, and `!== 0` rather than `> 0`:
 * `noDerivation.test.ts` fails the build on a comparison against a numeric
 * literal, because that is what a band cut-off looks like, and an emptiness
 * check is not a threshold — `DistributionBars` records the same reasoning for
 * the same shape. `seriesTotal` is a *claim* count for all five metrics, which
 * is what makes one predicate answer for a sum and for a mean alike.
 */
function anySeriesHasClaims(series: readonly TrendSeries[]): boolean {
  for (const line of series) {
    if (line.seriesTotal !== 0) return true;
  }
  return false;
}

/**
 * The dot renderer for one line, with that line's colour and flags bound in.
 *
 * A factory at module scope rather than a component declared inside the card's
 * body: Recharts calls this per point, and the closure needs the line's colour,
 * its low-confidence column and its cohort key, none of which a shared component
 * could be handed through Recharts' own props.
 */
function dotRendererFor(
  colour: string,
  valueKey: string,
  flagKey: string,
  partialKey: string,
  cohortKey: string | null,
  onSelectPoint: ((bucketKey: string, cohortKey: string | null) => void) | undefined,
): (props: DotItemDotProps) => ReactNode {
  return (props) => {
    const { cx, cy } = props;
    // Recharts leaves the coordinates undefined for a point it cannot place —
    // which is every `null` value, since `connectNulls` is off. Returning
    // nothing is the gap.
    if (typeof cx !== "number" || typeof cy !== "number") return null;
    const row = props.payload as Partial<ChartRow> | undefined;
    const low = row?.[flagKey] === true;
    const partial = row?.[partialKey] === true;
    const bucketKey = typeof row?.bucketKey === "string" ? row.bucketKey : null;
    // **A gap is not a click target**, stated here rather than inferred from
    // Recharts having placed the dot. A point with no value has no population
    // behind it on this metric, so the list it would open is a list the figure
    // never described — and on the settlement series that is the difference
    // between "the claims this mean was taken over" and "everything filed that
    // month". The coordinate check above happens to cover it today; this does
    // not depend on that staying true across a chart library upgrade.
    const drawn = row?.[valueKey] ?? null;
    const clickable = onSelectPoint !== undefined && bucketKey !== null && drawn !== null;
    return (
      <circle
        cx={cx}
        cy={cy}
        r={low ? LOW_CONFIDENCE_RADIUS : DOT_RADIUS}
        // Hollow, ringed and larger for a low-confidence bucket: the mark has to
        // survive being printed in grey, so it is a *shape* difference rather
        // than a second hue — the palette is closed and a fourth tone here would
        // read as a fourth cohort.
        fill={low ? "var(--color-surface)" : colour}
        stroke={colour}
        strokeWidth={low ? LOW_CONFIDENCE_STROKE : DOT_STROKE}
        strokeDasharray={partial ? PARTIAL_DASH : undefined}
        onClick={clickable ? () => onSelectPoint(bucketKey, cohortKey) : undefined}
        style={clickable ? { cursor: "pointer" } : undefined}
      />
    );
  };
}

export function TrendChartCard({
  testId,
  title,
  cohort,
  series,
  bucketCount,
  label,
  formatValue,
  note,
  target,
  emptyMessage,
  errorMessage,
  isPending,
  isError,
  onSelectPoint,
}: {
  testId: string;
  title: string;
  /** Which dimension the lines are split by — what `cohortFill` keys on. */
  cohort: TrendCohort;
  /**
   * This metric's series, in the server's order, or `undefined` while unknown.
   *
   * A slice of the one response rather than the response itself, because the
   * page holds the query and every card would otherwise repeat the same
   * selection five times — see `TrendsPage`. The order is the server's: cohorts
   * ascend by wire key, which is the order `paletteSlot` was assigned in.
   */
  series: readonly TrendSeries[] | undefined;
  /** How many buckets the window holds — the server's count, for the caption. */
  bucketCount: number | undefined;
  /** What one line is called: `cohortLabel ?? UI copy ?? cohortKey`. */
  label: (line: TrendSeries) => string;
  /** The metric's own unit, applied to a value the server decided. */
  formatValue: (value: number) => string;
  /** A sentence this metric owes its reader — the anchor, or the clock. */
  note?: string;
  /**
   * The rules-tier target this metric is graded against, if it has one.
   *
   * Drawn as a dashed reference line and stated in the footnote. Off the
   * response (`settleTargetDays`, `rtwTargetBp`) rather than named here, because
   * a constant in this file would be a second copy of a deployment's SLA config
   * — `PortfolioCharts`' rule about the severity legend, applied to a target.
   */
  target?: { value: number; caption: string };
  emptyMessage: string;
  errorMessage: string;
  isPending: boolean;
  isError: boolean;
  /** Open the claims behind one bucket of one line (Story 5.5's list). */
  onSelectPoint?: (bucketKey: string, cohortKey: string | null) => void;
}) {
  // `HandlerBenchmarkTable`'s one-predicate ruling — see `DistributionDonut`.
  const isLoading = isPending && series === undefined;
  const isEmpty = series !== undefined && !anySeriesHasClaims(series);

  // The window, read off the first line: every series in the response spans the
  // same buckets in the same order, which is what makes two lines on one chart
  // agree about where March is.
  const windowBuckets = series?.[0]?.points ?? [];

  const rows: ChartRow[] = windowBuckets.map((point, index) => {
    const row: ChartRow = { bucket: point.bucketLabel, bucketKey: point.bucketKey };
    for (const [line, column] of columnsOf(series ?? [])) {
      // Indexed defensively even though the contract says every series is the
      // full window: the window is read off `series[0]`, so a payload whose
      // lines were not the same length would throw *here*, during render, and
      // there is no `ErrorBoundary` between this card and the page — five
      // charts and the whole section would go white over one short line. A
      // missing cell draws no point on that line, which is what a gap is.
      const cell: TrendPoint | undefined = line.points[index];
      if (cell === undefined) continue;
      // `?? null`, never `?? 0`: a bucket a line could not answer for is a gap.
      row[valueColumn(column)] = cell.value;
      row[flagColumn(column)] = cell.lowConfidence;
      row[partialColumn(column)] = cell.partial;
    }
    return row;
  });

  return (
    <ChartFrame
      testId={testId}
      title={title}
      height={CHART_HEIGHT.trend}
      isLoading={isLoading}
      isError={isError}
      isEmpty={isEmpty}
      errorMessage={errorMessage}
      emptyMessage={emptyMessage}
      footnote={
        <p data-testid={`${testId}-footnote`} className="truncate">
          {captionOf(series ?? [], bucketCount, label, note, target)}
        </p>
      }
    >
      {series !== undefined && (
        <div className="flex h-full flex-col">
          <div aria-hidden className="min-h-0 w-full flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid
                  vertical={false}
                  stroke="var(--color-hairline)"
                  strokeDasharray="3 3"
                />
                <XAxis
                  dataKey="bucket"
                  tickLine={false}
                  axisLine={false}
                  tick={AXIS_TICK}
                  interval="preserveStartEnd"
                  minTickGap={12}
                />
                <YAxis
                  width={AXIS_WIDTH}
                  tickLine={false}
                  axisLine={false}
                  tick={AXIS_TICK}
                  // **Every value on this payload is an integer** — `TrendMetric`
                  // says so by construction, since each metric's name carries
                  // its unit (whole days, cents, basis points, a count). Left to
                  // itself Recharts fits ticks to the range, so a weekly volume
                  // chart topping out at two claims draws gridlines at 0.5 and
                  // 1.5 — half a claim, which is not a quantity this console can
                  // have. It is a tick *domain* rather than a formatting choice,
                  // which is why the formatter below cannot fix it: rounding the
                  // label would print "0" and "1" twice on two different lines.
                  allowDecimals={false}
                  // A formatter, never a scale: the ticks are Recharts' own
                  // positions rendered in the metric's unit, which is the same
                  // thing `DistributionBars`' `LabelList` does with the server's
                  // magnitudes.
                  tickFormatter={(value: number) => formatValue(value)}
                />
                {target !== undefined && (
                  <ReferenceLine
                    y={target.value}
                    stroke="var(--color-faint)"
                    strokeDasharray={TARGET_DASH}
                    ifOverflow="extendDomain"
                  />
                )}
                {columnsOf(series).map(([line, column]) => {
                  const colour = cohortFill(cohort, line.cohortKey, line.paletteSlot);
                  return (
                    <Line
                      key={valueColumn(column)}
                      type="linear"
                      dataKey={valueColumn(column)}
                      stroke={colour}
                      strokeWidth={1.75}
                      // AC 3, in one prop — see the module docstring.
                      connectNulls={false}
                      isAnimationActive={false}
                      dot={dotRendererFor(
                        colour,
                        valueColumn(column),
                        flagColumn(column),
                        partialColumn(column),
                        line.cohortKey,
                        onSelectPoint,
                      )}
                      activeDot={false}
                    />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* The accessible rendering of the figure above, and what the tests
              read — never Recharts' SVG. `sr-only` because the labels and values
              *are* on screen, drawn inside the hidden figure; the visible click
              targets are the page's period control and cohort legend, which is
              `DistributionBars`' split between the two readerships. */}
          <ul data-testid={`${testId}-series`} className="sr-only">
            {columnsOf(series).map(([line, column]) => (
              <li
                key={valueColumn(column)}
                data-testid={`${testId}-line`}
                data-cohort-key={line.cohortKey ?? ""}
                data-fill={cohortFill(cohort, line.cohortKey, line.paletteSlot)}
              >
                <span>{label(line)}</span>
                <ul>
                  {line.points.map((point) => (
                    <li
                      key={point.bucketKey}
                      data-testid={`${testId}-point`}
                      data-bucket-key={point.bucketKey}
                      data-cohort-key={line.cohortKey ?? ""}
                      data-low-confidence={point.lowConfidence ? "true" : "false"}
                      data-partial={point.partial ? "true" : "false"}
                    >
                      {point.bucketLabel}:{" "}
                      {point.value === null ? NO_VALUE : formatValue(point.value)}
                      {point.lowConfidence ? " (low confidence)" : ""}
                      {point.partial ? PARTIAL_NOTE : ""}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ChartFrame>
  );
}

/**
 * Each line paired with the column index it occupies, in the server's order.
 *
 * The index is a *position in this card's own chart*, not a rank and not the
 * palette slot — Recharts needs one `dataKey` per line and the payload does not
 * carry one. It is deliberately never used to pick a colour: `cohortFill` reads
 * `paletteSlot`, which is the server's ordinal over the *vocabulary*, so a
 * cohort keeps its hue on a chart where it happens to be drawn second.
 */
function columnsOf(series: readonly TrendSeries[]): [TrendSeries, number][] {
  return series.map((line, index) => [line, index]);
}

/**
 * The footnote: how sparse this window is, plus whatever this metric owes.
 *
 * `noDataBuckets` and `bucketCount` are both the server's, stated rather than
 * counted (AD-1). One clause per line, because a cohort split can be sparse in
 * one cohort and dense in another and a single number over all of them would
 * describe neither — the row is a single `truncate`d line, so a nine-sector
 * split cannot grow the card (NFR-3).
 */
function captionOf(
  series: readonly TrendSeries[],
  bucketCount: number | undefined,
  label: (line: TrendSeries) => string,
  note: string | undefined,
  target: { value: number; caption: string } | undefined,
): string {
  if (bucketCount === undefined) return "";
  const total = String(bucketCount);
  const clauses: string[] = [];
  for (const line of series) {
    const gaps = String(line.noDataBuckets);
    clauses.push(
      line.cohortKey === null
        ? `${gaps} of ${total} periods have no data`
        : `${label(line)} ${gaps}/${total} without data`,
    );
  }
  const unfinished = partialLabelOf(series);
  if (unfinished !== null) clauses.push(`${unfinished} is a part period`);
  if (target !== undefined) clauses.push(target.caption);
  if (note !== undefined) clauses.push(note);
  return clauses.join(" · ");
}

/**
 * The name of the first bucket the clock had not finished, or `null`.
 *
 * **Read off the payload, not inferred from the window's shape.** "It is always
 * the last one" is true of every window whose `to` is today and false of one
 * asked for further ahead, and a caption that named the last bucket regardless
 * would caveat a finished period on the first URL somebody shared. `partial` is
 * the server's verdict against the clock it cut the window on, and the label is
 * its own rendering of its own period.
 *
 * One clause rather than one per line: every series in a card spans the same
 * buckets, so the first line answers for all of them — and a cohort split would
 * otherwise repeat one sentence nine times in a row that has to stay on one
 * line.
 */
function partialLabelOf(series: readonly TrendSeries[]): string | null {
  for (const point of series[0]?.points ?? []) {
    if (point.partial) return point.bucketLabel;
  }
  return null;
}
