/**
 * One cost driver, as two cohorts side by side (Story 7.4, AC 3).
 *
 * Surgery vs. non-surgery and litigation vs. non-litigation are the same drawing
 * twice, so this is one component rendered twice — `SiuPipelineCard`'s
 * arrangement, and for its reason: two hand-built comparisons would be two
 * chances for one of them to put a numerator beside the wrong denominator.
 *
 * **The headline is the *average* projected cost, not the total, and the seeded
 * book is why.** There are three litigated claims and ninety-seven without, so a
 * totals comparison says litigation is a rounding error — which is true of the
 * sum and false of the claim. An average is the figure the story's word
 * "quantify" actually means, and the counts sit beside it so nobody reads a
 * three-claim mean as a portfolio fact. All three totals are on the card too,
 * because a reader asking *why* one cohort costs more is answered by which of
 * paid and reserve moved.
 *
 * **`null` is rendered as an em dash and never as `$0`.** The server sends
 * `averageProjectedCents: null` for a cohort with no claims — a mean over an
 * empty set is not zero — and an impossible segmentation makes both cohorts
 * empty, which is an ordinary outcome of a nine-dimension AND rather than an
 * edge case. A `?? 0` here would report that surgical claims cost nothing.
 *
 * **The two figures are not compared here.** No delta, no ratio, no "×2.4
 * more": the card states two averages and the reader does the comparison,
 * because a difference between two server figures computed in the browser is
 * exactly the derivation `noDerivation.test.ts` walks this folder for — and it
 * is not a figure any endpoint publishes, so it would be a third number with no
 * owner.
 *
 * **Both sides drill.** `surgery` and `litigation` were already `DrillFilters`
 * facets before this story, so a cohort's own `key` (`"true"` / `"false"`) is
 * the value its facet takes and nothing here composes a parameter name — the
 * server publishes `facet` beside the pair for exactly that reason. Merged with
 * the active segmentation, like every drill target on this page.
 */
import { useNavigate } from "react-router";

import type { CostDriverCohort, CostDriverPair } from "@/api/dashboard";
import { formatCents } from "@/lib/money";

import {
  drillHref,
  NO_MATCHING_CLAIMS,
  toSegmentationParams,
  withSegmentation,
  type DrillFilters,
  type FilterKey,
} from "../drill/filters";
import { ExportControl } from "../export/ExportControl";

/** The unknown-value glyph, `HandlerBenchmarkTable`'s. */
const EM_DASH = "—";

/** What each side of one pair is called, in `with`/`without` order. */
export interface CohortCopy {
  withDriver: string;
  withoutDriver: string;
}

function Cohort({
  testId,
  name,
  cohort,
  href,
  onOpen,
  tone,
}: {
  testId: string;
  name: string;
  cohort: CostDriverCohort;
  href: string;
  onOpen: () => void;
  tone: string;
}) {
  return (
    <li className="min-w-0 flex-1">
      {/* A button rather than a link, unlike `KpiCard`'s: the page owns the
          navigation and both halves of a pair navigate the same way, so the
          handler is passed in — and the `href` is still on the element as a
          `data-` attribute the tests read, so an assertion is about the URL that
          would be opened rather than about a click having happened. */}
      <button
        type="button"
        data-testid={testId}
        data-href={href}
        aria-label={`${name}. Show these claims.`}
        onClick={onOpen}
        className="block w-full rounded-md border border-border bg-surface px-[12px] py-[10px] text-left hover:border-brand focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
      >
        <div className="text-[10px] tracking-[0.4px] text-faint uppercase">{name}</div>
        <div
          data-testid={`${testId}-average`}
          className={`mt-1 font-mono text-[18px] leading-none font-bold ${tone}`}
        >
          {/* `null` is a cohort with no claims — an em dash, never `$0`. */}
          {cohort.averageProjectedCents === null
            ? EM_DASH
            : formatCents(cohort.averageProjectedCents)}
        </div>
        <div className="mt-1 text-[10px] text-muted-text">
          average projected · {cohort.claimCount} claims
        </div>
        <dl className="mt-[6px] grid grid-cols-3 gap-[4px] text-[9.5px] text-faint">
          <div>
            <dt>Paid</dt>
            <dd data-testid={`${testId}-paid`} className="font-mono text-muted-text">
              {formatCents(cohort.totals.paidCents)}
            </dd>
          </div>
          <div>
            <dt>Reserve</dt>
            <dd data-testid={`${testId}-reserve`} className="font-mono text-muted-text">
              {formatCents(cohort.totals.reserveCents)}
            </dd>
          </div>
          <div>
            <dt>Projected</dt>
            <dd data-testid={`${testId}-projected`} className="font-mono text-muted-text">
              {formatCents(cohort.totals.projectedCents)}
            </dd>
          </div>
        </dl>
      </button>
    </li>
  );
}

export function CostDriverCard({
  testId,
  title,
  copy,
  pair,
  segmentation,
  segmented,
  isPending,
  isError,
}: {
  testId: string;
  title: string;
  /** The two cohorts' names — the card's own copy, per driver. */
  copy: CohortCopy;
  /** The server's pair, or `undefined` while it is unknown. */
  pair: CostDriverPair | undefined;
  /**
   * The workspace's active filter, merged into both drill targets.
   *
   * Passed in rather than read here, `DashboardPage`'s composition rule.
   */
  segmentation: DrillFilters;
  segmented: boolean;
  isPending: boolean;
  isError: boolean;
}) {
  const navigate = useNavigate();
  // `HandlerBenchmarkTable`'s one-predicate ruling — one flag for `aria-busy`
  // and for the skeleton, so a section cannot say it is busy while its figures
  // are on screen.
  const isLoading = isPending && pair === undefined;
  // Emptiness is decided on the **counts**, never on the presence of the two
  // cohorts: they are always both there (a pair partitions the segment, so an
  // empty segment is two cohorts of zero), which is the same shape the
  // zero-filled donut has and the same reason `DistributionDonut` tests a total.
  //
  // Two equalities `&&`-ed rather than one sum compared to zero, and that is not
  // a style choice: `noDerivation.test.ts` fails the build on arithmetic over a
  // derived payload field, and adding two server counts is exactly that — the
  // one line away from "…and here is what share the surgery cohort is". An
  // emptiness check is not a sum, and the equality shape is how this codebase
  // says so (`DistributionBars`' `series.total === 0`).
  const isEmpty =
    pair !== undefined &&
    pair.withDriver.claimCount === 0 &&
    pair.withoutDriver.claimCount === 0;

  /**
   * The drill target for one cohort — the server's own `facet` and the cohort's
   * own `key`, never a string this file composed. See the module docstring.
   */
  function hrefFor(facet: string, key: string): string {
    return drillHref(withSegmentation(segmentation, { [facet as FilterKey]: key }));
  }

  return (
    <section
      data-testid={testId}
      aria-labelledby={`${testId}-heading`}
      aria-busy={isLoading}
      className="rounded-lg border border-border bg-surface p-3"
    >
      {/* The heading row with the control on its right — `FraudRateTables`' slot
          shape, and `ChartFrame`'s no-reflow rule applied by hand because this
          card draws its own frame. **One control on each of the two cards and one
          file behind both**: `decomposition_of` folds surgery and litigation in
          one pass, so the export carries all four cohorts and either card's
          button downloads the pair-of-pairs. Two buttons for one answer is the
          honest arrangement when the two cards are two views of one fold. */}
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <h3
          id={`${testId}-heading`}
          className="font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
        >
          {title}
        </h3>
        <ExportControl
          testId={testId}
          label="the cost-driver comparison"
          path="/dashboard/financials/export"
          params={{ ...toSegmentationParams(segmentation), table: "costDrivers" }}
        />
      </div>

      {isLoading ? (
        <div data-testid={`${testId}-skeleton`} aria-hidden className="flex gap-[10px]">
          <span className="block h-[104px] flex-1 animate-pulse rounded bg-surface-2" />
          <span className="block h-[104px] flex-1 animate-pulse rounded bg-surface-2" />
        </div>
      ) : isError ? (
        <p
          role="alert"
          data-testid={`${testId}-error`}
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ {title} could not be loaded. Try again in a moment.
        </p>
      ) : isEmpty ? (
        <p data-testid={`${testId}-empty`} className="text-[11.5px] text-faint">
          {/* The same zero means "your book is empty" under no filter and "your
              filter is empty" under one, and only the second has a way out —
              `NO_MATCHING_CLAIMS`' recorded reason. */}
          {segmented ? NO_MATCHING_CLAIMS : "No claims in this portfolio yet."}
        </p>
      ) : (
        pair !== undefined && (
          <ul className="flex flex-wrap gap-[10px]">
            <Cohort
              testId={`${testId}-with`}
              name={copy.withDriver}
              cohort={pair.withDriver}
              href={hrefFor(pair.facet, pair.withDriver.key)}
              onOpen={() => {
                void navigate(hrefFor(pair.facet, pair.withDriver.key));
              }}
              tone="text-warn"
            />
            <Cohort
              testId={`${testId}-without`}
              name={copy.withoutDriver}
              cohort={pair.withoutDriver}
              href={hrefFor(pair.facet, pair.withoutDriver.key)}
              onOpen={() => {
                void navigate(hrefFor(pair.facet, pair.withoutDriver.key));
              }}
              tone="text-steel"
            />
          </ul>
        )
      )}
    </section>
  );
}
