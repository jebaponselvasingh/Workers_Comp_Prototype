/**
 * A horizontal bar chart over a finished series — one component, four charts
 * (UX-DR7, AC 1).
 *
 * Recovery status, injury type, employer spend and claims by state are the same
 * drawing over four different series: a label on the left, a track, a fill, and
 * the value at the end of the bar. The prototype writes that out four times as
 * hand-built divs (`renderSV`, lines 1083-1090) with `(v/max*100).toFixed(0)`
 * inline in each. Here the *values* arrive finished from
 * `services/worklist/charts` and Recharts scales them to pixels — which is the
 * line Design Note 4 draws and the only thing a chart library is asked to do.
 *
 * **The formatter is a prop, and that is the whole of the money handling.** The
 * employer chart passes `formatCents` from `lib/money.ts`, the three count
 * charts pass `String`. There is no division by a hundred here and no currency
 * symbol; `lib/money.ts` remains the one place cents become a string.
 *
 * **The truncation caption is text, not arithmetic.** `limit` and
 * `totalCategories` arrive on the series precisely so the sentence can be
 * assembled without the client computing "how many are missing" — and it is
 * rendered only when the server says `truncated`, so a scope with fewer
 * categories than the cap carries no apology for a cut that did not happen.
 *
 * **The figure is `aria-hidden` and a label/value list carries the pairs.**
 * `RiskGauge`'s pattern, and here it is genuinely needed rather than
 * duplicative: unlike the donut's legend, a bar chart's labels and values are
 * drawn *inside* the SVG, so hiding the figure hides them. The list is also
 * what the tests read, which keeps them off Recharts' internal SVG structure
 * and off jsdom measuring anything.
 *
 * **Story 5.5 makes that list visible when the chart is navigable, and this is
 * a deliberate visual addition to Story 5.3's charts.** An `sr-only` list
 * cannot be a sighted user's click target, and a Recharts `<Rectangle>` cannot
 * carry a focus ring — so a bar chart whose only affordance was the bar would be
 * reachable by mouse and by nobody else. The list becomes a compact row of chip
 * buttons under the figure, inside the frame's reserved height, so the four bar
 * surfaces stay exactly as tall as they were (NFR-3) and the chart area shrinks
 * by the rows the chips occupy. Each chip carries the **same text the `sr-only`
 * row carried** — "Fracture: 5" — so the accessible rendering is unchanged and
 * the tests that read it keep reading it; the `aria-label` adds only what
 * pressing it does.
 *
 * The bar itself carries an `onClick` too, and that is redundant with the chip
 * by design — the donut records the same pairing for the same reason: a reader
 * who aims at the bar should not have to discover that the chip beneath it is
 * the control.
 */
import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, XAxis, YAxis } from "recharts";

import { ChartFrame } from "./ChartFrame";
import { CHART_HEIGHT, PALETTE_OVERFLOW_FILL, UNKNOWN_KEY_FILL } from "./chartTheme";

/** How much horizontal room the category labels get, in pixels. */
const LABEL_WIDTH = 118;
/** Room at the right for the on-bar value, so a long figure is not clipped. */
const VALUE_MARGIN = 44;
const BAR_RADIUS = 3;

/** What one bar draws, after the caller has said where its facts live. */
interface Datum {
  /**
   * The item's stable identity, from `keyOf` — the React key, and the lookup
   * for a `Record`-shaped palette. Not the label: see the `keyOf` prop.
   */
  id: string;
  label: string;
  value: number;
  /** The bar's fill — one hue, a palette position, or a keyed colour. */
  fill: string;
}

export function DistributionBars<ItemT>({
  testId,
  title,
  series,
  keyOf,
  label,
  value,
  fills,
  formatValue,
  truncationCaption,
  emptyMessage,
  zeroMessage,
  errorMessage,
  isPending,
  isError,
  onSelect,
}: {
  testId: string;
  title: string;
  /** The server's series, or `undefined` while it is unknown. */
  series:
    | {
        items: readonly ItemT[];
        /**
         * Every claim the scope holds, including those in categories the cut
         * dropped. Read only to tell "no claims at all" from "claims that
         * distribute nothing" — see `zeroMessage`.
         */
        total: number;
        totalCategories: number;
        truncated: boolean;
        limit: number | null;
      }
    | undefined;
  /**
   * The item's identity — the React key, and the palette lookup for a
   * `Record`-shaped `fills`.
   *
   * **Not the label, deliberately.** The employer series' label is a
   * `short_name`, and `EmployerPaidResponse`'s own docstring is explicit that a
   * name is not an identity: two employers could be given the same short name
   * tomorrow, which is precisely why `employerId` rides along on the wire.
   * Keying React on a colliding label makes two bars one bar; keying on the id
   * cannot. The two count series have no id and pass their label, which is
   * their identity, and the recovery series passes its enum key.
   */
  keyOf: (item: ItemT) => string;
  /** Where the bar's label lives on an item. */
  label: (item: ItemT) => string;
  /** Where the bar's magnitude lives on an item. */
  value: (item: ItemT) => number;
  /**
   * The bar colours, in one of three shapes.
   *
   * - A **single string** paints every bar one hue — the injury chart, matching
   *   the prototype.
   * - An **array** is indexed by rank position: `CATEGORICAL_FILLS` and nothing
   *   else, and a bar past its end takes `PALETTE_OVERFLOW_FILL` rather than
   *   wrapping round to repeat the first hue. A position is not an identity —
   *   see `CATEGORICAL_FILLS`, which now says so.
   * - A **`Record` keyed by `keyOf`** colours each bar by *what it is* rather
   *   than by where it came in the ranking. That is the recovery chart, and it
   *   is not a nicety: its three categories sit beside a severity donut on the
   *   same row where green means good, so painting "Under Treatment" in the ok
   *   green — which a single fill did — says the opposite of what the bar
   *   means. `RECOVERY_FILL` maps each status to the tone the KPI cards give
   *   it, and a key the map does not hold falls back to `UNKNOWN_KEY_FILL`
   *   rather than to no fill at all.
   */
  fills: string | readonly string[] | Readonly<Record<string, string>>;
  /** `String` for counts, `formatCents` for money. */
  formatValue: (magnitude: number) => string;
  /**
   * The sentence shown when the server truncated the series, built from `limit`
   * and `totalCategories`. A function so the caller owns the wording ("8 of 20
   * injury types"), and only ever called with the server's two numbers.
   */
  truncationCaption: (shown: number, total: number) => string;
  emptyMessage: string;
  /**
   * What to say when the series has rows but distributes nothing — optional,
   * because only one chart can reach the state.
   *
   * The employer series is the case: `_by_employer` keeps a row for an employer
   * whose in-scope claims have all paid zero, so a book can arrive as ten
   * labelled rows summing to `total: 0`. Drawn as a chart that is ten
   * zero-length bars against an empty axis, which reads as a broken chart
   * rather than as an answer. Saying it in words is the answer; the count
   * charts cannot reach it (a category exists because a claim is in it) and
   * pass nothing.
   */
  zeroMessage?: string;
  errorMessage: string;
  isPending: boolean;
  isError: boolean;
  /**
   * Open the claims behind one bar, by its `keyOf` value.
   *
   * Optional: a series with no drill-through behind it keeps the `sr-only`
   * list it had, rather than growing a visible row of chips that go nowhere.
   * All four bar charts on this dashboard supply it.
   */
  onSelect?: (key: string) => void;
}) {
  // `HandlerBenchmarkTable`'s one-predicate ruling — see `DistributionDonut`.
  const isLoading = isPending && series === undefined;

  /**
   * One bar's colour, from whichever of the three shapes `fills` arrived in.
   *
   * The two fallbacks are the point rather than defensive noise: an eleventh
   * employer in an uncapped series and a wire key this build's theme map has
   * never heard of are both states the contract permits, and the previous code
   * answered the first by silently repeating a hue and the second by drawing
   * nothing at all.
   */
  function fillFor(item: ItemT, index: number): string {
    if (typeof fills === "string") return fills;
    if (Array.isArray(fills)) {
      const palette = fills as readonly string[];
      return index < palette.length ? palette[index] : PALETTE_OVERFLOW_FILL;
    }
    return (fills as Readonly<Record<string, string>>)[keyOf(item)] ?? UNKNOWN_KEY_FILL;
  }

  // Field renaming, not derivation: Recharts needs one key to read a magnitude
  // from and the three series spell theirs `count`, `count` and `paidCents`. No
  // arithmetic happens here, nothing is re-ordered, and nothing is dropped —
  // the accessors are supplied by the caller precisely so this file never has
  // to know which field a series carries its figure in.
  const data: Datum[] =
    series?.items.map((item, index) => ({
      id: keyOf(item),
      label: label(item),
      value: value(item),
      fill: fillFor(item, index),
    })) ?? [];

  // Rows that distribute nothing — see `zeroMessage`. `total` is the server's
  // sum for this series, so this is a read of a published figure and not a
  // re-addition of the bars; the emptiness test is the same `=== 0` shape
  // `isEmpty` uses one line below, not a threshold.
  //
  // `!== 0` rather than `> 0`, and that is not a style choice:
  // `noDerivation.test.ts`'s first rule fails the build on any comparison
  // against a numeric literal, because that is what a band cut-off looks like.
  // An emptiness check is not a threshold, and the equality shape is how this
  // codebase says so — the same reason `HandlerBenchmarkTable` reaches for
  // `Intl` `signDisplay` instead of testing a sign.
  const isZero =
    zeroMessage !== undefined &&
    series !== undefined &&
    series.items.length !== 0 &&
    series.total === 0;

  return (
    <ChartFrame
      testId={testId}
      title={title}
      height={CHART_HEIGHT.bars}
      isLoading={isLoading}
      isError={isError}
      isEmpty={series !== undefined && (series.items.length === 0 || isZero)}
      errorMessage={errorMessage}
      emptyMessage={isZero ? (zeroMessage ?? emptyMessage) : emptyMessage}
      footnote={
        series?.truncated === true ? (
          <p data-testid={`${testId}-truncation`} className="truncate">
            {/* `limit` is what the server cut at; `items.length` is what it
                actually sent. They are the same number for every series this
                console produces, and the schema still lets `truncated: true`
                arrive with `limit: null` — in which case a caption keyed on
                `limit` alone rendered *nothing*, which is silent truncation and
                is the one thing these three fields exist to prevent. The
                server now refuses to emit that combination
                (`DistributionResponse`'s validator); this is the client half of
                the same rule, so a caption appears whenever the server says it
                cut, whatever it says about where. */}
            {truncationCaption(series.limit ?? series.items.length, series.totalCategories)}
          </p>
        ) : null
      }
    >
      {series !== undefined && (
        <div className="flex h-full flex-col">
          {/* `flex-1 min-h-0` rather than `h-full`, since Story 5.5: the chip
              row below is a sibling inside the frame's reserved box, so the
              figure takes what is left instead of the whole of it. Without
              `min-h-0` a flex child floors at its content height and the chips
              would push the chart out of the box the frame reserved. */}
          <div aria-hidden className="min-h-0 w-full flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                layout="vertical"
                margin={{ top: 2, right: VALUE_MARGIN, bottom: 2, left: 0 }}
                barCategoryGap="22%"
              >
                {/* Hidden: the axis exists so Recharts can scale the bars, and
                    a visible tick row would print numbers beside the values
                    already on each bar. */}
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={LABEL_WIDTH}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 10, fill: "var(--color-muted-text)" }}
                />
                <Bar dataKey="value" radius={BAR_RADIUS} isAnimationActive={false}>
                  {data.map((datum) => (
                    <Cell
                      key={datum.label}
                      fill={datum.fill}
                      // Pointer parity with the chip below — see the module
                      // docstring. The figure is `aria-hidden`, so this is the
                      // mouse's affordance and the chip is everyone else's.
                      onClick={onSelect === undefined ? undefined : () => onSelect(datum.id)}
                      style={onSelect === undefined ? undefined : { cursor: "pointer" }}
                    />
                  ))}
                  {/* The server's figure, formatted. `formatter` here does no
                      arithmetic — it is `String` or `formatCents`, and a
                      formatter that computed anything would be the AD-1
                      violation this chart exists to avoid.
                      Recharts types the argument as its own `RenderableText`
                      (a string, a number, or `undefined` for a datum with no
                      value), so it is narrowed rather than cast: a bar with no
                      magnitude prints nothing, which cannot arise here because
                      `value()` returns a number for every item — and printing
                      "NaN" if it ever did would be worse than printing
                      nothing. */}
                  <LabelList
                    dataKey="value"
                    position="right"
                    formatter={(magnitude) =>
                      typeof magnitude === "number" ? formatValue(magnitude) : ""
                    }
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      fill: "var(--color-text)",
                    }}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* The accessible rendering of the figure above, and what the tests
              read. `sr-only` while the chart is inert — the labels and values
              *are* on screen, drawn inside the SVG that had to be hidden to
              keep it from being announced as a tree of paths — and a visible
              row of chip buttons once there is somewhere to go. See the module
              docstring for why the visible half had to exist. */}
          <ul
            data-testid={`${testId}-values`}
            aria-label={onSelect === undefined ? undefined : `${title} — show claims`}
            className={
              onSelect === undefined
                ? "sr-only"
                : "mt-1 flex max-h-[54px] flex-wrap gap-[4px] overflow-y-auto"
            }
          >
            {data.map((datum) => (
              <li key={datum.label} data-label={datum.label}>
                {onSelect === undefined ? (
                  <>
                    {datum.label}: {formatValue(datum.value)}
                  </>
                ) : (
                  <button
                    type="button"
                    data-testid={`${testId}-value-link`}
                    data-key={datum.id}
                    data-label={datum.label}
                    // The chip's own words plus what pressing it does, so a
                    // chart click target has discernible text rather than being
                    // announced as a label with no purpose.
                    aria-label={`${datum.label}: ${formatValue(datum.value)}. Show these claims.`}
                    onClick={() => onSelect(datum.id)}
                    className="rounded-full border border-border bg-surface-2 px-[6px] py-px text-[9.5px] text-muted-text hover:bg-surface hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
                  >
                    {datum.label}: {formatValue(datum.value)}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </ChartFrame>
  );
}
