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
import { drillHref } from "../drill/filters";

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
  isPending,
  isError,
}: {
  /** The server's panel, or `undefined` while it is unknown. */
  data: FraudPanel | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  const navigate = useNavigate();

  return (
    <DistributionDonut
      testId="fraud-band-distribution"
      title="Fraud score distribution"
      series={data?.byBand}
      label={bandLabels(data?.fraudBandHighMin, data?.fraudBandMedMin)}
      fill={FRAUD_BAND_FILL}
      centreCaption="Claims in this portfolio"
      // Reached for a scope with no claims at all — and only reachable because
      // `DistributionDonut` tests the series *total* rather than its row count.
      // The zero-fill means an empty book still arrives as three segments, so a
      // row-count test never fired here and the analyst got a donut with no arcs
      // over three "0" legend rows instead of this sentence.
      emptyMessage="No claims in this portfolio yet."
      errorMessage="⚠ The fraud score distribution could not be loaded."
      isPending={isPending}
      isError={isError}
      onSelect={(band) => void navigate(drillHref({ fraudBand: band }))}
    />
  );
}
