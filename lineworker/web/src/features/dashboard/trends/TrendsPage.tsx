/**
 * The analyst workspace's Trends section (FR-AN-2, Story 7.2).
 *
 * The second screen that belongs to one persona, and the first anywhere in this
 * console that answers a question about *time*. Every aggregate before it is a
 * snapshot of today; these five series say whether the portfolio is improving or
 * deteriorating, over a window and an anchor the analyst chooses and the server
 * decides.
 *
 * **`FraudPage`'s composition rule, exactly: the page owns the query and the
 * cards receive `{data, isPending, isError}`.** One server answer rather than
 * five, deliberately and unlike the fraud section: the five metrics are folded
 * from *one scoped read* over one window (`services/worklist/trends.py` has
 * exactly one `await`), so splitting them into five requests would turn one
 * read into five and let two charts on one screen disagree about which window
 * they describe. The consequence is that the five cards load, fail and empty
 * together — which is why `keepPreviousData` is on the hook and why the busy
 * flag below is one flag rather than five.
 *
 * **The selectors are in the URL since Story 7.3, and so is the filter.** This
 * section deferred its own selector state to that story and recorded why:
 * deciding what belongs in the workspace's address bar once for filters and once
 * for selectors is how two schemes end up in one URL. The decision is
 * `useSegmentation`'s — `filter[…]` for the ten dimensions, bare `grain`,
 * `anchor` and `cohort` under the route's own names — so a window is now as
 * shareable as the drill-through it opens.
 *
 * **The controls render the server's echo, not their own last click.** `grain`,
 * `anchor` and `cohort` come back on the payload precisely so a request that
 * 422s or times out cannot leave a `<select>` claiming a grain the chart beside
 * it is not drawn at — `FraudRateTables`' ruling on one input, applied to three.
 *
 * **Three ways to drill, and only one of them is the keyboard's.** The period
 * `<select>` plus "View claims" is the accessible path and the cohort legend
 * buttons are the second; the dots inside each chart are a *redundant* pointer
 * affordance, because the figure is `aria-hidden` and an SVG `<circle>` has no
 * focus, no Enter and no accessible name. That is `DistributionDonut`'s
 * arc-and-legend split, moved onto a time axis.
 *
 * **Nothing on this page computes anything.** Every bucket boundary, every
 * value, every null, the low-confidence verdict, the palette ordinal, both
 * targets and both band edges arrive decided; a selector change sets a server
 * parameter and refetches. `noDerivation.test.ts` walks `features/dashboard`
 * recursively and names every file in this folder.
 */
import { useMemo, useState } from "react";

import { useNavigate } from "react-router";

import {
  DEFAULT_TREND_PARAMS,
  useTrends,
  type PortfolioTrends,
  type TrendAnchor,
  type TrendBucket,
  type TrendCohort,
  type TrendGrain,
  type TrendMetric,
  type TrendParams,
  type TrendSeries,
} from "@/api/dashboard";
import { DISABILITY_LABEL } from "@/features/claim-detail/labels";
import { formatCents } from "@/lib/money";
import { formatBasisPoints } from "@/lib/rate";

import {
  drillHref,
  isFiltered,
  RISK_LABEL_BY_BAND,
  withSegmentation,
  type DrillFilters,
  type FilterKey,
} from "../drill/filters";
import { pickOption, useSegmentation } from "../segmentation/useSegmentation";

import { TrendChartCard } from "./TrendChartCard";
import { cohortFill } from "./trendColors";

/** The three grains, in width order, with the words a header uses. */
const GRAIN_LABEL: Record<TrendGrain, string> = {
  week: "Weekly",
  month: "Monthly",
  quarter: "Quarterly",
};
const GRAIN_ORDER: readonly TrendGrain[] = ["week", "month", "quarter"];

/**
 * The two anchors, named for the date rather than for the column.
 *
 * "FNOL" is the top bar's and the SLA strip's word for `froi_date`, so it is the
 * one an analyst already reads; "Date of injury" is spelled out because "DOI" is
 * an abbreviation this console has never shown. They are different questions and
 * the section says so: a falling FNOL volume against a flat injury-date volume
 * is a reporting lag, not fewer injuries.
 */
const ANCHOR_LABEL: Record<TrendAnchor, string> = {
  fnol: "FNOL date",
  doi: "Date of injury",
};
const ANCHOR_ORDER: readonly TrendAnchor[] = ["fnol", "doi"];
/** The same two, as a fragment a title or a caption can end with. */
const ANCHOR_WORD: Record<TrendAnchor, string> = {
  fnol: "FNOL",
  doi: "injury date",
};

/** The cohort dimensions the AC names, plus "no split" as a stated value. */
const COHORT_LABEL: Record<TrendCohort, string> = {
  none: "No split",
  severity_band: "Severity band",
  disability: "Disability type",
  sector: "Employer sector",
};
const COHORT_ORDER: readonly TrendCohort[] = ["none", "severity_band", "disability", "sector"];

/**
 * Which drill-through facet a cohort value narrows on.
 *
 * The correspondence `PortfolioCharts` states for its six charts, over three
 * dimensions instead: the server split the population on the same column the
 * facet filters on, which is what makes the list reconcile with the line that
 * was clicked. `none` maps to nothing, because an unsplit series *is* the whole
 * window and a facet would narrow a population nobody chose.
 */
const COHORT_FACET: Record<TrendCohort, FilterKey | null> = {
  none: null,
  severity_band: "severityBand",
  disability: "disability",
  sector: "sector",
};

/**
 * The copy for the two cohort dimensions whose values are enums.
 *
 * The Enums convention: the wire carries a token and the browser owns what a
 * human reads, which is why `cohortLabel` is `null` on every series this story
 * ships. Both maps are imported rather than restated so the legend and the
 * drill-through chip say the same words — `RISK_LABEL_BY_BAND` is exported from
 * `drill/filters.ts` for exactly this. Sector is absent because it is free text
 * where the stored value *is* the label.
 */
const COHORT_VALUE_LABEL: Partial<Record<TrendCohort, Record<string, string>>> = {
  severity_band: RISK_LABEL_BY_BAND,
  disability: DISABILITY_LABEL,
};

/** What an unsplit line is called, in a legend and in a footnote. */
const WHOLE_BOOK_LABEL = "All claims";

/**
 * The stage the two SLA metrics are folded over, as the drill facet spells it.
 *
 * The wire value of `Stage.settled`, which `filter[stage]` matches on — a token
 * this console has carried since Story 5.5 rather than a word chosen here. It is
 * not a *rule*: "settled" is a stored column value, so narrowing on it is
 * quoting the population the server folded and not re-deciding it.
 */
const SETTLED_STAGE = "settled";

/**
 * One card's empty sentence, in both readings of "empty" (Story 7.3).
 *
 * Two strings rather than one, because an empty series means two different
 * things with two different fixes: under no filter it is a fact about the
 * *window* the analyst chose and the fix is a wider period; under a segmentation
 * it is a fact about the *filter* and the fix is clearing a chip. "No claims fall
 * in this window" over a nine-dimension intersection sends the reader to the
 * wrong control, on the section where the control is right beside it.
 *
 * Both readings keep the metric's own subject rather than collapsing to
 * `NO_MATCHING_CLAIMS`, which is what `MetricSpec.emptyMessage` exists for: a
 * busy quarter in which nothing settled empties two cards and no others, and one
 * sentence across all five would put that distinction back where it was.
 */
interface EmptyCopy {
  /** What the card says when no segmentation is applied. */
  whole: string;
  /** …and what it says when one is. */
  segmented: string;
}

/** The two empty sentences: one about the window, one about what is in it. */
const NO_CLAIMS: EmptyCopy = {
  whole: "No claims fall in this window.",
  segmented: "No claims match these filters in this window.",
};
const NOTHING_SETTLED: EmptyCopy = {
  whole: "No claims in this window have settled.",
  segmented: "No claims matching these filters have settled in this window.",
};

/** `String`, named, so a count chart's formatter reads as a decision. */
function asCount(magnitude: number): string {
  return String(magnitude);
}

/** Whole days, the precision `sla.targets_for` decides the settlement tile at. */
function asDays(magnitude: number): string {
  return `${String(magnitude)}d`;
}

/** Basis points through the one function allowed to divide them by a hundred. */
function asRate(magnitude: number): string {
  return `${formatBasisPoints(magnitude)}%`;
}

interface MetricSpec {
  metric: TrendMetric;
  /** `data-testid` stem — kebab-case, matching the card's subject. */
  testId: string;
  title: (anchor: TrendAnchor) => string;
  /**
   * The facet that narrows a drill to the claims *this* metric was folded from.
   *
   * `undefined` for the three metrics whose population is the bucket, and
   * `{stage: "settled"}` for the two whose is not — `avgSettlementDays` is a
   * mean over settled claims and `rtwRateBp` a rate over them, so a drill
   * carrying only the bucket's dates opens a list several times longer than the
   * figure that was clicked, under a heading quoting that figure.
   *
   * The settlement mean's true denominator is narrower still — settled claims
   * *carrying a recorded duration* — and no facet in the twenty narrows on that.
   * Adding one would put an SLA column's meaning into the drill vocabulary,
   * which is what AD-2 keeps in one module; `stage` is the closest honest
   * narrowing and the card's own footnote already says what the mean is over.
   */
  population?: DrillFilters;
  /**
   * What this card's failure alert names itself.
   *
   * Its own subject rather than the heading interpolated, `FraudRateTables`'
   * ruling: one query feeds five cards, so all five alerts fire at once and a
   * screen reader reads them back to back. Five copies of one sentence is a
   * reader unable to tell an echo from a second failure.
   */
  subject: string;
  format: (value: number) => string;
  note?: (data: PortfolioTrends) => string;
  target?: (data: PortfolioTrends) => { value: number; caption: string };
  /**
   * The sentence this card shows when its own `seriesTotal` is zero.
   *
   * Per metric rather than one for the section, because emptiness is per metric:
   * `seriesTotal` counts the claims that fed *this* series, so a busy window in
   * which nothing settled empties two cards and no others. "No claims fall in
   * this window" under a settlement chart of a hundred-claim quarter would be a
   * true sentence about the wrong population and would read as a broken page.
   */
  emptyMessage: EmptyCopy;
}

/**
 * The five series, in the order the server publishes and the section reads.
 *
 * The order is the reading order the wire enum states — how much work arrived,
 * how old it is, how long it takes to close, how much of it came back to work,
 * what it cost — and it is restated here as a *layout*, never as a sort:
 * `TrendMetric`'s own docstring says the order is decided server-side so no
 * client sorts (AD-1), and this array is the grid it is drawn into.
 */
const METRICS: readonly MetricSpec[] = [
  {
    metric: "volume",
    testId: "trend-volume",
    title: (anchor) => `Claim volume by ${ANCHOR_WORD[anchor]}`,
    subject: "Claim volume",
    format: asCount,
    emptyMessage: NO_CLAIMS,
  },
  {
    metric: "avg_days_open",
    testId: "trend-days-open",
    // **The anchor, because this is the one card where it is not obvious.**
    // `days_open` counts from the claim's FNOL date whichever anchor is
    // selected — there is one computer for it (AD-10) and it takes one date —
    // so under `anchor=doi` this card plots FNOL-derived ages on an
    // injury-date axis. Its two neighbours name the anchor in their titles and
    // this one used to name neither its axis nor its measure, which left the
    // combination readable as "average days since injury". The title says what
    // the buckets are and the note says what the age is measured from.
    title: (anchor) => `Average days open by ${ANCHOR_WORD[anchor]}`,
    subject: "Average days open",
    format: asDays,
    // Design note 3, on screen: `days_open` counts to a day and never freezes at
    // settlement, so this series slopes upward toward older buckets purely
    // because those claims are older. That is a property of the metric rather
    // than a defect, and hiding it would need a second computer for one value
    // (AD-10) — so the card says which day the ages were counted to instead.
    note: (data) => `Ages counted from FNOL to ${data.asOf}`,
    emptyMessage: NO_CLAIMS,
  },
  {
    metric: "avg_settlement_days",
    testId: "trend-settlement",
    // Design note 1, in the title. There is no closure date in the schema, so
    // "settlement cycle time in March" cannot mean "claims that settled in
    // March" — the only defensible reading is "claims *filed* in March took N
    // days", and the reader is told so rather than left to assume the other.
    title: (anchor) => `Settlement cycle time — by ${ANCHOR_WORD[anchor]} cohort`,
    subject: "Settlement cycle time",
    format: asDays,
    target: (data) => ({
      value: data.settleTargetDays,
      caption: `Target ${asDays(data.settleTargetDays)}`,
    }),
    population: { stage: SETTLED_STAGE },
    emptyMessage: NOTHING_SETTLED,
  },
  {
    metric: "rtw_rate_bp",
    testId: "trend-rtw-rate",
    title: () => "Return-to-work rate",
    subject: "Return-to-work rate",
    format: asRate,
    target: (data) => ({
      value: data.rtwTargetBp,
      caption: `Target ${asRate(data.rtwTargetBp)}`,
    }),
    population: { stage: SETTLED_STAGE },
    emptyMessage: NOTHING_SETTLED,
  },
  {
    metric: "paid_cents",
    testId: "trend-paid",
    title: () => "Total paid",
    subject: "Total paid",
    format: formatCents,
    emptyMessage: NO_CLAIMS,
  },
];

/**
 * The one pass over the response the whole page reads from.
 *
 * **A selection and a transposition, never a derivation.** The payload publishes
 * one flat list of series in metric-major, cohort-minor order; five cards each
 * need their own metric's slice, the legend needs one representative per cohort
 * value, and the period control needs the window's buckets. All three are
 * answered by walking the list once in the order it arrived — nothing is sorted,
 * nothing is filtered out, nothing is counted and nothing is combined. Each
 * series is placed by the identity it publishes (`metric`, `cohortKey`), which
 * is a fact the server decided and this file only reads.
 *
 * A `Map` rather than five separate scans of the list, and that is not only
 * about the guard's rules: five passes over one list would be five places for a
 * metric to
 * be mistyped, and a card whose slice came back empty would render its empty
 * state — the sentence written for a scope with nothing in it — over a window
 * that was full.
 */
interface TrendPlan {
  byMetric: Map<TrendMetric, TrendSeries[]>;
  /** One series per cohort value, in the server's palette-slot order. */
  cohorts: TrendSeries[];
  /**
   * Every bucket in the window, by key — the drill-through's date bounds.
   *
   * **Read off `data.buckets` since Story 7.3, not off a series' points.** The
   * payload now publishes the window's vocabulary independently of the fold, and
   * the reason is a defect this section had: an empty window under a cohort split
   * published *no series at all*, so this map came back empty, the period
   * `<select>` had nothing to offer and "View claims" went dead — while the same
   * empty window under `cohort=none` left both live because zero-filled points
   * still exist. The availability of the keyboard's only drill path depended on
   * an unrelated selector, which was tolerable while an empty result was rare and
   * is not now that a ten-dimension AND makes one ordinary.
   */
  buckets: Map<string, TrendBucket>;
  /** The same buckets in the window's order, for the period control. */
  periods: TrendBucket[];
}

function planOf(data: PortfolioTrends | undefined): TrendPlan {
  const byMetric = new Map<TrendMetric, TrendSeries[]>();
  const cohorts: TrendSeries[] = [];
  const seenCohorts = new Set<string>();
  const buckets = new Map<string, TrendBucket>();
  const periods: TrendBucket[] = [];

  for (const bucket of data?.buckets ?? []) {
    buckets.set(bucket.bucketKey, bucket);
    periods.push(bucket);
  }

  for (const line of data?.series ?? []) {
    const held = byMetric.get(line.metric);
    if (held === undefined) byMetric.set(line.metric, [line]);
    else held.push(line);

    if (line.cohortKey !== null && !seenCohorts.has(line.cohortKey)) {
      seenCohorts.add(line.cohortKey);
      cohorts.push(line);
    }
  }

  return { byMetric, cohorts, buckets, periods };
}

/** One line's name: the server's label, this console's copy, or the raw key. */
function nameOf(cohort: TrendCohort, line: TrendSeries): string {
  if (line.cohortKey === null) return WHOLE_BOOK_LABEL;
  return line.cohortLabel ?? COHORT_VALUE_LABEL[cohort]?.[line.cohortKey] ?? line.cohortKey;
}

export function TrendsPage() {
  const navigate = useNavigate();

  /**
   * Which window the analyst is asking about, and over which claims — the URL.
   *
   * Held to each control's own option list rather than sent as found,
   * `pickOption`'s recorded reason: a `<select>` whose value is not one of its
   * options renders as *no selection at all*, so a hand-edited `?grain=fortnight`
   * would leave a control the analyst cannot read. The server would refuse the
   * value anyway; this is about the control rather than about the request.
   */
  const { segmentation, control, setControl } = useSegmentation();
  // Whether the five cards below are describing the caller's book or an
  // intersection of it — which decides one sentence per card and nothing else.
  // See `EmptyCopy`.
  const segmented = isFiltered(segmentation);
  const params: TrendParams = {
    grain: pickOption(control("grain"), GRAIN_ORDER, DEFAULT_TREND_PARAMS.grain),
    anchor: pickOption(control("anchor"), ANCHOR_ORDER, DEFAULT_TREND_PARAMS.anchor),
    cohort: pickOption(control("cohort"), COHORT_ORDER, DEFAULT_TREND_PARAMS.cohort),
  };
  const trends = useTrends(params, segmentation);
  /**
   * A new selector set is outstanding and the previous charts are on screen.
   *
   * `FraudPage`'s `pendingSort` predicate, and it counts only while the
   * *placeholder* is being drawn: gating on `isPlaceholderData` as well as
   * `isFetching` keeps an ordinary background refetch — same key, same window —
   * from re-marking the section busy minutes after a click.
   */
  const isRefreshing = trends.isPlaceholderData && trends.isFetching;
  const data = trends.data;
  const plan = useMemo(() => planOf(data), [data]);

  /**
   * The selectors as they are rendered: the server's answer once there is one.
   *
   * `FraudRateTables`' `shownSort`, three inputs wide. While a new set is
   * outstanding the controls show what was *asked* for, because that is the
   * question in flight; once it lands they show what the server says it applied,
   * so a refused or coerced parameter cannot leave a control disagreeing with
   * the chart beside it.
   */
  const shown: TrendParams =
    data === undefined || isRefreshing
      ? params
      : { grain: data.grain, anchor: data.anchor, cohort: data.cohort };

  /** Which period the drill controls open — the last bucket until one is picked. */
  const [period, setPeriod] = useState<string>("");
  const lastPeriod = plan.periods.at(-1)?.bucketKey ?? "";
  // A grain change replaces the whole vocabulary of bucket keys, so the previous
  // selection routinely stops existing. Falling back to the window's last bucket
  // rather than clearing keeps "View claims" answerable at every moment — and
  // the *last* rather than the first because that is the period a reader asking
  // "how are we doing" means.
  const selectedPeriod = plan.buckets.has(period) ? period : lastPeriod;

  function select<K extends keyof TrendParams>(key: K, value: TrendParams[K]): void {
    setControl(key, value);
  }

  /**
   * The filter set behind one bucket of one line of one metric, or `null`.
   *
   * The **date bounds are the bucket's own** `bucketFrom`/`bucketTo`, copied
   * from the point the server folded — never a boundary this file re-derived
   * from a bucket key — and they go on the pair belonging to the anchor the
   * response was bucketed by, because a point on the injury-date series opened
   * with `filter[fnolFrom]` returns a plausible list of the wrong claims.
   *
   * **The metric's own population narrows it further**, which is the difference
   * between a list that reconciles with the figure clicked and one that merely
   * contains it: a settlement mean folded from three claims in a bucket of seven
   * opens seven rows under a heading reading "71 days" unless `stage=settled`
   * goes on the URL too. See `MetricSpec.population` for why that facet and not
   * a narrower one.
   *
   * **`null` rather than `{}` when the bucket is unknown**, and that is the
   * whole of the fix: `drillHref({})` is a link to the caller's entire book, so
   * a lookup that failed used to answer a narrowing request with the widest
   * possible list. A no-op has to be a no-op.
   */
  function filtersFor(
    bucketKey: string,
    cohortKey: string | null,
    population: DrillFilters | undefined,
  ): DrillFilters | null {
    const point = plan.buckets.get(bucketKey);
    if (point === undefined || data === undefined) return null;
    // The workspace's filter first, then the metric's population, then the
    // bucket's own bounds — `withSegmentation`'s merge, with the *gesture* last
    // for that helper's recorded reason.
    const bounds: DrillFilters =
      data.anchor === "fnol"
        ? withSegmentation(segmentation, {
            ...population,
            fnolFrom: point.bucketFrom,
            fnolTo: point.bucketTo,
          })
        : withSegmentation(segmentation, {
            ...population,
            doiFrom: point.bucketFrom,
            doiTo: point.bucketTo,
          });
    const facet = COHORT_FACET[data.cohort];
    if (cohortKey === null || facet === null) return bounds;
    return { ...bounds, [facet]: cohortKey };
  }

  function openClaims(
    bucketKey: string,
    cohortKey: string | null,
    population?: DrillFilters,
  ): void {
    const filters = filtersFor(bucketKey, cohortKey, population);
    if (filters === null) return;
    void navigate(drillHref(filters));
  }

  const cohortOf = data?.cohort ?? shown.cohort;
  /**
   * The anchor the lines **on screen** were bucketed by, for the card titles.
   *
   * Deliberately `data`'s and not `shown`'s. `shown` is the question *in
   * flight* while a new selector set is outstanding, which is right for a
   * `<select>` — the control has to show what was asked for — and wrong for a
   * title: `keepPreviousData` leaves the previous payload's lines drawn
   * underneath, so a title read off the request would caption an FNOL chart
   * "by injury date" for as long as the refetch takes. `filtersFor` above reads
   * `data.anchor` for exactly the same reason, one URL down.
   */
  const drawnAnchor = data?.anchor ?? shown.anchor;

  return (
    <section
      aria-label="Trend analytics"
      data-testid="trend-analytics"
      // One query, five cards: the section is busy while its one answer is in
      // flight or while a new window is outstanding, and the cards keep the
      // previous lines on screen underneath (AC 7). `FraudPage`'s flag tracks
      // one of four queries for the opposite reason — there, three other
      // surfaces were readable; here there is one answer and it is this one.
      aria-busy={trends.isPending || isRefreshing}
    >
      <h2 className="mb-[13px] font-display text-[15px] font-bold text-text">
        Trend &amp; cohort analytics
      </h2>

      {/* Announced rather than only drawn, `FraudPage`'s rule: an analyst using
          a screen reader gets one polite sentence when the series land, instead
          of five charts appearing silently. `sr-only` because the charts are the
          visual announcement, and silent on failure because the five cards below
          carry their own `role="alert"`. */}
      <p role="status" aria-live="polite" className="sr-only">
        {trends.isPending
          ? "Loading trend analytics."
          : trends.isError
            ? ""
            : "Trend analytics updated."}
      </p>

      <div
        data-testid="trend-controls"
        className="mb-[10px] flex flex-wrap items-end gap-[10px] rounded-lg border border-border bg-surface p-3"
      >
        <Selector
          testId="trend-grain"
          label="Period"
          value={shown.grain}
          options={GRAIN_ORDER}
          optionLabel={(option) => GRAIN_LABEL[option]}
          onChange={(next) => {
            select("grain", next);
          }}
        />
        <Selector
          testId="trend-anchor"
          label="Bucket by"
          value={shown.anchor}
          options={ANCHOR_ORDER}
          optionLabel={(option) => ANCHOR_LABEL[option]}
          onChange={(next) => {
            select("anchor", next);
          }}
        />
        <Selector
          testId="trend-cohort"
          label="Cohort"
          value={shown.cohort}
          options={COHORT_ORDER}
          optionLabel={(option) => COHORT_LABEL[option]}
          onChange={(next) => {
            select("cohort", next);
          }}
        />

        {/* The keyboard's drill path: a period, then a button. Every figure in
            the charts sits inside an `aria-hidden` SVG, so without these two
            controls the drill-through would be reachable by mouse only. */}
        <div className="flex flex-col gap-[3px]">
          <label
            htmlFor="trend-period-select"
            className="font-display text-[9.5px] font-bold tracking-[0.3px] text-faint uppercase"
          >
            Drill period
          </label>
          <select
            id="trend-period-select"
            data-testid="trend-period"
            value={selectedPeriod}
            disabled={plan.periods.length === 0}
            onChange={(event) => {
              setPeriod(event.target.value);
            }}
            className="rounded border border-border bg-surface px-[6px] py-[2px] text-[11px] text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
          >
            {plan.periods.map((bucket) => (
              <option key={bucket.bucketKey} value={bucket.bucketKey}>
                {bucket.bucketLabel}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          data-testid="trend-view-claims"
          disabled={selectedPeriod === ""}
          onClick={() => {
            openClaims(selectedPeriod, null);
          }}
          className="rounded border border-border bg-surface-2 px-[9px] py-[3px] text-[11px] font-semibold text-steel hover:bg-surface hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none disabled:opacity-50"
        >
          View claims →
        </button>

        {/* The two figures the window's honesty depends on, stated rather than
            implied: a window describing eleven of a hundred claims is not wrong,
            but a reader who thinks it describes a hundred is. */}
        {data !== undefined && (
          <p
            data-testid="trend-window-caption"
            className="ml-auto text-[10px] text-faint"
          >
            {data.windowFrom} to {data.windowTo} · {data.bucketCount} periods ·{" "}
            {data.claimsInWindow} of {data.claimsInScope} claims · as of {data.asOf}
          </p>
        )}
      </div>

      {/* The cohort legend, and it is one legend for the whole section rather
          than one per card. That is the visible form of AC 2: a cohort's colour
          is its identity across all five charts, so drawing five legends would
          invite a reader to check whether they agree. Each entry is a button
          because it is a click target — the same population the line describes,
          in the selected period. */}
      {plan.cohorts.length !== 0 && (
        <ul
          data-testid="trend-legend"
          aria-label={`${COHORT_LABEL[cohortOf]} — show claims`}
          className="mb-[10px] flex flex-wrap gap-[6px]"
        >
          {plan.cohorts.map((line) => {
            const text = nameOf(cohortOf, line);
            return (
              <li key={line.cohortKey}>
                <button
                  type="button"
                  data-testid="trend-legend-link"
                  data-key={line.cohortKey ?? ""}
                  data-fill={cohortFill(cohortOf, line.cohortKey, line.paletteSlot)}
                  aria-label={`${text}. Show these claims.`}
                  onClick={() => {
                    openClaims(selectedPeriod, line.cohortKey);
                  }}
                  className="flex items-center gap-[5px] rounded-full border border-border bg-surface px-[8px] py-[2px] text-[10.5px] text-muted-text hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
                >
                  <span
                    aria-hidden
                    style={{
                      background: cohortFill(cohortOf, line.cohortKey, line.paletteSlot),
                    }}
                    className="inline-block h-[8px] w-[8px] shrink-0 rounded-[2px]"
                  />
                  {text}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="grid gap-[10px] lg:grid-cols-2">
        {METRICS.map((spec) => (
          <TrendChartCard
            key={spec.metric}
            testId={spec.testId}
            title={spec.title(drawnAnchor)}
            cohort={cohortOf}
            series={data === undefined ? undefined : (plan.byMetric.get(spec.metric) ?? [])}
            bucketCount={data?.bucketCount}
            label={(line) => nameOf(cohortOf, line)}
            formatValue={spec.format}
            note={data === undefined ? undefined : spec.note?.(data)}
            target={data === undefined ? undefined : spec.target?.(data)}
            emptyMessage={spec.emptyMessage[segmented ? "segmented" : "whole"]}
            errorMessage={`⚠ ${spec.subject} could not be loaded. Try again in a moment.`}
            isPending={trends.isPending}
            isError={trends.isError}
            onSelectPoint={(bucketKey, cohortKey) => {
              openClaims(bucketKey, cohortKey, spec.population);
            }}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * One labelled `<select>` over a closed server enum.
 *
 * Three controls with one shape, `FraudRateTables`' select idiom: the vocabulary
 * is the generated union, so a fourth grain or a fifth cohort fails the build
 * here rather than rendering an option the route would 422.
 */
function Selector<T extends string>({
  testId,
  label,
  value,
  options,
  optionLabel,
  onChange,
}: {
  testId: string;
  label: string;
  value: T;
  options: readonly T[];
  optionLabel: (option: T) => string;
  onChange: (next: T) => void;
}) {
  return (
    <div className="flex flex-col gap-[3px]">
      <label
        htmlFor={`${testId}-select`}
        className="font-display text-[9.5px] font-bold tracking-[0.3px] text-faint uppercase"
      >
        {label}
      </label>
      <select
        id={`${testId}-select`}
        data-testid={testId}
        value={value}
        onChange={(event) => {
          onChange(event.target.value as T);
        }}
        className="rounded border border-border bg-surface px-[6px] py-[2px] text-[11px] text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabel(option)}
          </option>
        ))}
      </select>
    </div>
  );
}
