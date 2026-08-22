/**
 * The fraud-score distribution — three bands over every claim in scope (AC 1).
 *
 * `DistributionDonut` with no variant and no wrapper of its own, which is the
 * point rather than an economy: the severity distribution one route over is the
 * idiom an analyst already reads, and this is the same drawing over a different
 * column. The counts, the ordering, the zero-fill and both band edges arrived
 * decided from `services/worklist/fraud.py` (AD-1); this file supplies three
 * display labels, three colour tokens and a click handler.
 *
 * **Every band is always drawn, including one with no claims in it.** That is
 * the server's zero-fill and it is the one place this dashboard's distributions
 * disagree with each other: `byStage` omits a stage the scope does not contain,
 * because a stage is a column, while a band is a *rule's* answer over a score
 * every claim carries. "No claim in this book scored into the high band" is the
 * single most valuable thing this chart can say, and a two-segment donut is
 * indistinguishable from a build that forgot to draw the third.
 *
 * The one exception is a book with **no claims at all**, where three zeroes say
 * nothing a sentence does not say better: `DistributionDonut` renders the empty
 * message when the series total is zero, which is a state the zero-fill made
 * reachable and a row-count test could never see.
 *
 * **The legend quotes the server's edges.** "High (≥ N)" reads `fraudBandHighMin`
 * off the response, exactly as the severity legend reads `highRiskSeverityMin`,
 * so superseding the rule document moves the segment and the caption together. A
 * constant here would be a second copy of a rule the browser cannot see change —
 * and it would be a *particularly* bad one on this surface, because the band's
 * high edge and the Fraud Flags card's review threshold carry the same number
 * today and are two different rules.
 *
 * **Clicking a legend row opens `filter[fraudBand]`**, never `filter[fraudFlagged]`.
 * They name different populations — a high-scoring claim nobody triaged is in
 * this band and in neither fraud population — so a segment wired to the flagged
 * facet would open a shorter list than the arc it was clicked on, with every
 * step of it looking defensible.
 */
import { useNavigate } from "react-router";

import type { FraudPanel } from "@/api/dashboard";

import { FRAUD_BAND_FILL } from "../charts/chartTheme";
import { DistributionDonut } from "../charts/DistributionDonut";
import {
  drillHref,
  isFiltered,
  NO_MATCHING_CLAIMS,
  toSegmentationParams,
  withSegmentation,
  type DrillFilters,
} from "../drill/filters";
import { ExportControl } from "../export/ExportControl";

/** The unknown-value glyph, `HandlerBenchmarkTable`'s. */
const EM_DASH = "—";

/**
 * The three band labels, with both edges read off the response.
 *
 * A function rather than a constant map because two of the three quote a rule
 * value — `PortfolioCharts.riskLabels`' shape and its reason. `Low` carries no
 * number and needs none: it is bounded above by Medium, and stating both edges
 * of a bottom band says more than the segment does.
 *
 * `undefined` prints an em dash, never a number this file chose. The donut
 * short-circuits on an absent series so the fallback is unreachable today, which
 * is exactly why it must not be a `?? 0` — one refactor from a legend reading
 * "High (≥ 0)", which is a rule claim the server never made.
 */
function bandLabels(
  highMin: number | undefined,
  medMin: number | undefined,
): Record<string, string> {
  const high = highMin === undefined ? EM_DASH : String(highMin);
  const med = medMin === undefined ? EM_DASH : String(medMin);
  return {
    high: `High (≥ ${high})`,
    medium: `Medium (≥ ${med})`,
    low: "Low",
  };
}

export function FraudDistributionCard({
  data,
  segmentation,
  isPending,
  isError,
}: {
  /** The server's panel, or `undefined` while it is unknown. */
  data: FraudPanel | undefined;
  /**
   * The workspace's active filter, merged into every drill target below.
   *
   * Passed in rather than read here, `DashboardPage`'s composition rule: the page
   * owns the URL and the sections receive what it decided, so a section cannot
   * come to disagree with the page about which filter it is describing.
   */
  segmentation: DrillFilters;
  isPending: boolean;
  isError: boolean;
}) {
  const navigate = useNavigate();
  // Read from the filter this card was handed rather than passed in beside it:
  // the page owns the URL and this section receives what it decided, so the
  // caption and the drill target cannot come to describe two different filters.
  const segmented = isFiltered(segmentation);

  return (
    <DistributionDonut
      testId="fraud-band-distribution"
      title="Fraud score distribution"
      // The arcs, as three rows. `toSegmentationParams` on the filter the *page*
      // handed down rather than a second read of the URL, `DashboardPage`'s
      // composition rule: the caption, the drill target and the file all
      // describe one narrowing or none of them do.
      action={
        <ExportControl
          testId="fraud-band-distribution"
          label="the fraud score distribution"
          path="/dashboard/fraud/export"
          params={{ ...toSegmentationParams(segmentation), table: "bands" }}
        />
      }
      series={data?.byBand}
      label={bandLabels(data?.fraudBandHighMin, data?.fraudBandMedMin)}
      fill={FRAUD_BAND_FILL}
      // The population the centre figure counts, named rather than assumed. With
      // a segmentation applied it is the intersection, and a caption reading
      // "in this portfolio" over a nine-dimension subset is the one number on
      // this card an analyst would quote at somebody.
      centreCaption={segmented ? "Claims matching these filters" : "Claims in this portfolio"}
      // Reached for a scope with no claims at all — and only reachable because
      // `DistributionDonut` tests the series *total* rather than its row count.
      // The zero-fill means an empty book still arrives as three segments, so a
      // row-count test never fired here and the analyst got a donut with no arcs
      // over three "0" legend rows instead of this sentence.
      //
      // Two sentences since Story 7.3, for the reason `NO_MATCHING_CLAIMS`
      // records: the same zero means "your book is empty" under no filter and
      // "your filter is empty" under one, and only the second has a way out.
      emptyMessage={segmented ? NO_MATCHING_CLAIMS : "No claims in this portfolio yet."}
      errorMessage="⚠ The fraud score distribution could not be loaded."
      isPending={isPending}
      isError={isError}
      onSelect={(band) =>
        void navigate(drillHref(withSegmentation(segmentation, { fraudBand: band })))
      }
    />
  );
}
