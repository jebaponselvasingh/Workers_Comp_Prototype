/**
 * The portfolio's reserve-adequacy distribution (Story 7.4, AC 2).
 *
 * `DistributionDonut` with no variant, `FraudDistributionCard`'s arrangement:
 * the severity donut and the fraud-band donut are the idiom an analyst already
 * reads, and this is the same drawing over Epic 3's verdict. The counts, the
 * ordering, the zero-fill and both band edges arrived decided from
 * `services/worklist/decomposition.py`, which itself only *counted* what
 * `services/financials/reserve.py` answered (AD-2).
 *
 * **The labels and the colours are the case file's, imported and never
 * restated.** `RESERVE_VERDICT_LABEL` is the map the treatment card and the
 * Bills summary render, and `RESERVE_VERDICT_FILL` is the chart-side twin of
 * `reserveAccent.ts`' class map. AD-10's "one semantic, every surface" is a
 * statement about the words and the tones as much as about the figure: an
 * analyst clicking "Reserve Light" here lands on a claim whose chip says
 * "Reserve Light" in the same red. A local copy of five strings would be five
 * strings free to drift on the one surface that sits between a chart and a case
 * file.
 *
 * **Five segments, and two of them are not band answers.** The AC names
 * Light/Adequate/Heavy; the shipped vocabulary has five, and on the seeded book
 * `closed_final` alone holds most of it. A three-segment donut would have summed
 * to a fraction of `claimsInScope` under a heading reading "portfolio", which is
 * precisely the class of finding Story 7.3's review produced twice. So all five
 * are drawn and the footnote says what the two non-band buckets mean — a settled
 * claim has no exposure left to judge, and a claim whose bills are not on file
 * has not failed a check but has not had one.
 *
 * **The footnote also states the one thing this card is not.** The portfolio
 * path reads *stored* schedule rows and never materializes them, because an
 * analyst route is read-only by role capability and the claim-level path's
 * refresh is a write. A claim whose week boundary has passed since anyone last
 * opened it can therefore sit one step behind its own case file until the next
 * single-claim read refreshes it. `reserve_checks_for_claims` carries the
 * argument; the card says so rather than implying a freshness it does not have.
 *
 * **Clicking a segment opens `filter[reserveVerdict]`** — the facet Story 7.4
 * appended for exactly this, and the only one in the whole vocabulary whose
 * value the server cannot reach from a claim row alone. It is matched through
 * the same computation the segment was counted with, so the list reconciles with
 * the arc.
 */
import { useNavigate } from "react-router";

import type { ReserveAdequacy } from "@/api/dashboard";
import { RESERVE_VERDICT_LABEL } from "@/features/claim-detail/labels";

import { RESERVE_VERDICT_FILL } from "../charts/chartTheme";
import { DistributionDonut } from "../charts/DistributionDonut";
import {
  drillHref,
  NO_MATCHING_CLAIMS,
  toSegmentationParams,
  withSegmentation,
  type DrillFilters,
} from "../drill/filters";
import { ExportControl } from "../export/ExportControl";

/** The unknown-value glyph, `HandlerBenchmarkTable`'s. */
const EM_DASH = "—";

/**
 * Basis points as a ratio a reader can check against the card — `lib/rate.ts`'s
 * two-decimal rendering, deliberately not reused.
 *
 * That helper prints a *rate* and this prints a *ratio of a reserve*, and the
 * two want different words: 11 500 is "1.15×", not "115.00". One `Intl`
 * formatter over a number the server sent, no comparison and no threshold — the
 * only arithmetic is the unit conversion the wire's basis points require, which
 * is the same conversion `formatCents` performs one file over and is the reason
 * both are named functions rather than expressions at a call site.
 *
 * `undefined` prints an em dash, never a number this file chose. The donut
 * short-circuits on an absent series so the fallback is unreachable today, which
 * is exactly why it must not be a `?? 0` — one refactor from a footnote claiming
 * the bands are at zero, which is a rule claim the server never made.
 */
const RATIO = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const BASIS_POINTS_PER_UNIT = 10_000;

function formatRatio(basisPoints: number | undefined): string {
  return basisPoints === undefined
    ? EM_DASH
    : `${RATIO.format(basisPoints / BASIS_POINTS_PER_UNIT)}×`;
}

export function ReserveAdequacyCard({
  data,
  segmentation,
  segmented,
  isPending,
  isError,
}: {
  /** The server's distribution, or `undefined` while it is unknown. */
  data: ReserveAdequacy | undefined;
  /**
   * The workspace's active filter, merged into every drill target below.
   *
   * Passed in rather than read here, `DashboardPage`'s composition rule: the
   * page owns the URL and the sections receive what it decided.
   */
  segmentation: DrillFilters;
  segmented: boolean;
  isPending: boolean;
  isError: boolean;
}) {
  const navigate = useNavigate();

  return (
    <section
      data-testid="financial-adequacy"
      aria-labelledby="financial-adequacy-heading"
      // `BreakdownCard`'s flag, for its reason: this card answers a second query
      // and a segmentation change re-asks it, so it reports its own busy rather
      // than inheriting the section's.
      aria-busy={isPending}
      className="flex flex-col"
    >
      {/* `BreakdownCard`'s pattern, and this card had been naming its region with
          the footnote instead: `aria-labelledby` pointed at the three-sentence
          paragraph below, so a screen reader announced the whole band
          explanation as the region's name. The heading is the name; the footnote
          stays a footnote. */}
      <h3 id="financial-adequacy-heading" className="sr-only">
        Reserve adequacy
      </h3>
      <DistributionDonut
        testId="reserve-adequacy-distribution"
        title="Reserve adequacy"
        // Its **own** route rather than the section's, because the distribution
        // is its own query: it costs three scoped reads and a second rule
        // document where the totals cost one and one, and one file carrying both
        // would make every breakdown export pay for it.
        action={
          <ExportControl
            testId="reserve-adequacy-distribution"
            label="the reserve adequacy distribution"
            path="/dashboard/financials/reserve-adequacy/export"
            params={toSegmentationParams(segmentation)}
          />
        }
        series={
          data === undefined
            ? undefined
            : {
                // A rename, not a transform: the donut reads `{key, count}` and
                // the wire spells the first field `verdict` because that is what
                // it is. Nothing is sorted, filtered, combined or counted here —
                // the five arrive in the enum's declaration order, zero-filled.
                items: data.items.map((item) => ({ key: item.verdict, count: item.count })),
                total: data.total,
              }
        }
        label={RESERVE_VERDICT_LABEL}
        fill={RESERVE_VERDICT_FILL}
        centreCaption={
          segmented ? "Claims matching these filters" : "Claims in this portfolio"
        }
        // Reached only for a book with no claims at all — and only reachable
        // because `DistributionDonut` tests the series *total* rather than its
        // row count. The zero-fill means an empty segment still arrives as five
        // segments, so a row-count test would never fire here and the analyst
        // would get a donut with no arcs over five "0" legend rows.
        //
        // Two sentences, for `NO_MATCHING_CLAIMS`' reason: the same zero means
        // "your book is empty" under no filter and "your filter is empty" under
        // one, and only the second has a way out.
        emptyMessage={segmented ? NO_MATCHING_CLAIMS : "No claims in this portfolio yet."}
        errorMessage="⚠ The reserve adequacy distribution could not be loaded."
        isPending={isPending}
        isError={isError}
        onSelect={(verdict) =>
          void navigate(drillHref(withSegmentation(segmentation, { reserveVerdict: verdict })))
        }
      />

      {/* Three sentences the donut cannot say, and each is load-bearing rather
          than decoration — see the module docstring. The two edges are read off
          the response, so superseding `reserve_bands` moves the segments and
          this line together; a constant here would be a second copy of a rule
          the browser cannot see change. */}
      <p
        id="financial-adequacy-note"
        data-testid="reserve-adequacy-note"
        className="mt-1 text-[9.5px] leading-[13px] text-faint"
      >
        Light above {formatRatio(data?.lightRatioBp)} of reserve, heavy below{" "}
        {formatRatio(data?.heavyRatioBp)}. Settled claims are “Closed — Final” and have no
        exposure left to judge; “Awaiting Bill Data” means a claim’s bills are not on file, not
        that it failed a check. Verdicts are read from stored schedules, so a claim opened since
        its last week elapsed may read one step fresher on its own case file.
      </p>
    </section>
  );
}
