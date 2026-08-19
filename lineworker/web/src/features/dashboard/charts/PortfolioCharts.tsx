/**
 * The seven analytics surfaces below the handler table (UX-DR7, FR-SUP-4/C).
 *
 * The layout contract in one file: the prototype's two chart rows (`renderSV`,
 * lines 1079 and 1092) — four surfaces across, then three — each wired to its
 * slice of one response. Every count, every ordering, every truncation and
 * every threshold on this page came off the wire; this file supplies display
 * labels, colour tokens and the grid, and `noDerivation.test.ts` walks
 * `features/dashboard` and fails the build over anything else.
 *
 * **The label maps are `Record<Enum, string>` lookups over whatever arrived**,
 * not a fixed array of rows. That is the shape the omission contract forces: a
 * scope with no `intake` claim publishes a three-item stage series, so a
 * component that laid out four fixed rows and looked each one up would draw an
 * empty row for a category the server deliberately left out. Iterating the
 * arrived items and looking up a label gets both cases right and cannot invent
 * a zero.
 *
 * **They are also this page's own copy, deliberately diverging from
 * `claim-detail/labels.ts`.** That module spells the stage pill on a case file
 * ("Settled", "Treatment"); the prototype's donut legend reads "Settled &
 * Closed" and "Under Treatment", which are the KPI cards' words directly above
 * it. Two surfaces, two audiences, one enum — the wire value is shared and the
 * copy is not, which is exactly what "the UI owns display labels" means.
 *
 * **The one caption that quotes a number quotes the server's.** The severity
 * legend reads "High (≥ N)" from `highRiskSeverityMin`, so superseding the rule
 * document moves the slice and the caption together. A constant here would be a
 * second copy of a rule the browser cannot see change — and the e2e spec would
 * have no way to tell a caption that follows the document from one that happens
 * to agree with it today.
 *
 * **Takes its query state as props and owns no fetch.** `DashboardPage` calls
 * the hook, for `HandlerBenchmarkTable`'s reason: the section is one of three
 * independent server answers on the page and must fail on its own without
 * blanking the other two.
 */
import { useNavigate } from "react-router";

import type { PortfolioCharts as PortfolioChartsData } from "@/api/dashboard";
import type { ReturnStatus, RiskBand, Stage } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { drillHref, RECOVERY_LABEL_BY_STATUS, type FilterKey } from "../drill/filters";

import { CATEGORICAL_FILLS, RECOVERY_FILL, RISK_FILL, SERIES_FILL, STAGE_FILL } from "./chartTheme";
import { DistributionBars } from "./DistributionBars";
import { DistributionDonut } from "./DistributionDonut";
import { SlaTiles } from "./SlaTiles";

/**
 * The settlement donut's legend copy — the prototype's, not the case file's.
 *
 * `renderSV` merges intake and investigation into one legend line reading
 * "Intake/Invest."; here they are two, because the server publishes two
 * categories and merging them in the browser would be the client-side
 * aggregation AD-1 removes. Recorded as a deliberate departure: four legend
 * rows where the prototype draws three, with the same four arcs behind them.
 */
const STAGE_LABEL: Record<Stage, string> = {
  settled: "Settled & Closed",
  treatment: "Under Treatment",
  intake: "Intake",
  investigation: "Investigation",
};

/**
 * The recovery bars' copy (`renderSV`, line 1094) — imported since Story 5.5.
 *
 * It moved to `drill/filters.ts` when the bars became click targets: a
 * supervisor who clicks "Under Therapy" lands on a filter chip, and the chip
 * and the bar have to say the same words or the drill-through looks like it
 * opened something else. One map, two renderings.
 */
const RECOVERY_LABEL = RECOVERY_LABEL_BY_STATUS;

/**
 * The severity legend, with the High band's boundary read off the response.
 *
 * A function rather than a constant map because one of the three labels quotes
 * a rule value — see the module docstring. The other two carry no number and
 * need none: "Medium" is bounded above by High and below by Low, and stating
 * both edges in a legend row would say more than the slice does.
 */
function riskLabels(highRiskSeverityMin: number | undefined): Record<RiskBand, string> {
  // `undefined` prints an em dash, never a number this file chose.
  //
  // The previous form was `?? 0`, which is unreachable only because the donut
  // short-circuits on `series === undefined` — one refactor from a legend
  // reading "High (≥ 0)", which is a rule claim the server never made. The
  // whole argument for putting this boundary on the wire is that the caption
  // cannot drift from the document that produced the slice; a local fallback is
  // exactly the second copy that argument forbids.
  const boundary = highRiskSeverityMin === undefined ? EM_DASH : String(highRiskSeverityMin);
  return {
    high: `High (≥ ${boundary})`,
    med: "Medium",
    low: "Low",
  };
}

/** The unknown-value glyph, `HandlerBenchmarkTable`'s. */
const EM_DASH = "\u2014";

/** `String`, named, so a count chart's formatter reads as a decision. */
function asCount(magnitude: number): string {
  return String(magnitude);
}

export function PortfolioCharts({
  data,
  isPending,
  isError,
}: {
  /** The server's seven surfaces, or `undefined` while they are unknown. */
  data: PortfolioChartsData | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  // Every surface is fed the same three flags: they are one request, so they
  // load together, fail together and are empty together. Passing them down
  // rather than branching here is what keeps each surface's fixed-height box
  // the thing that holds the layout open (NFR-3).
  const state = { isPending, isError };

  const navigate = useNavigate();
  /**
   * One chart's click handler: open the claims behind a segment (Story 5.5).
   *
   * The **filter key is the chart's**, and the value is whatever the segment's
   * own `keyOf`/`key` produced — the enum's wire value for the three enum-keyed
   * series, the exact stored string for injury type and state, and the employer
   * *id* for the spend bars. That correspondence is what makes the list
   * reconcile with the segment: the server's facet for each of the six is the
   * same fold key `services/worklist/charts.py` grouped on.
   *
   * Curried per chart so each surface is handed a function that already knows
   * its dimension, rather than every surface being handed the same one and
   * having to name its own key at the call site — where a copy-pasted chart
   * would inherit the neighbour's.
   */
  function openClaims(key: FilterKey): (value: string) => void {
    return (value) => void navigate(drillHref({ [key]: value }));
  }

  return (
    <section
      data-testid="portfolio-charts"
      aria-labelledby="portfolio-charts-heading"
      className="mb-[14px]"
    >
      <h2
        id="portfolio-charts-heading"
        className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
      >
        Portfolio analytics
      </h2>

      {/* The prototype's `crow cr4`. Four across on a wide dashboard, two on a
          tablet, one on a phone — responsive within the dashboard column, which
          is the whole of the layout requirement. */}
      <div className="mb-[10px] grid gap-[10px] sm:grid-cols-2 lg:grid-cols-4">
        <DistributionDonut
          testId="chart-settlement-status"
          title="Settlement status"
          series={data?.byStage}
          label={STAGE_LABEL}
          fill={STAGE_FILL}
          centreCaption="Claims in this portfolio"
          emptyMessage="No claims in this portfolio yet."
          errorMessage="⚠ Settlement status could not be loaded."
          onSelect={openClaims("stage")}
          {...state}
        />

        <DistributionDonut
          testId="chart-severity"
          title="Severity distribution"
          series={data?.bySeverity}
          label={riskLabels(data?.highRiskSeverityMin)}
          fill={RISK_FILL}
          centreCaption="Claims in this portfolio"
          emptyMessage="No claims in this portfolio yet."
          errorMessage="⚠ Severity distribution could not be loaded."
          onSelect={openClaims("severityBand")}
          {...state}
        />

        {/* **Nothing is passed to `SlaTiles`, and the four tiles are ruled
            non-navigable.** Two reasons, both structural rather than
            preferences. `test_nothing_outside_the_worklist_aggregation_reads_
            the_sla_source_columns` confines the three duration columns to
            `sla.py`, so a `filter[slaBreach]` would have to be evaluated inside
            that module over rows the drill-through aggregate does not read — a
            second scoped read on a route whose whole discipline is one. And the
            tile UI does not distinguish *which* clock a cohort belongs to, so
            the filter would need a vocabulary this surface cannot express. The
            honest fix is a cohort predicate exported from `sla.py` and a
            thirteenth facet, made once; it is recorded in `deferred-work.md`
            with that recommendation rather than half-built here. */}
        <SlaTiles strip={data?.sla} {...state} />

        <DistributionBars
          testId="chart-recovery-status"
          title="Recovery status"
          series={data?.byRecoveryStatus}
          keyOf={(item) => item.key}
          label={(item) => RECOVERY_LABEL[item.key as ReturnStatus] ?? item.key}
          value={(item) => item.count}
          // The whole map, keyed by status, so each bar carries its own
          // meaning: ok-green only for Fully Recovered, warn-amber for Under
          // Treatment, brand-orange for Under Therapy. Passing one hue here
          // painted a bar of people still off work in the same green the
          // severity donut and the Settled & Closed card use to mean "good".
          fills={RECOVERY_FILL}
          formatValue={asCount}
          truncationCaption={() => ""}
          emptyMessage="No claims in this portfolio yet."
          errorMessage="⚠ Recovery status could not be loaded."
          onSelect={openClaims("recoveryStatus")}
          {...state}
        />
      </div>

      {/* The prototype's `crow cr3`. */}
      <div className="grid gap-[10px] lg:grid-cols-3">
        <DistributionBars
          testId="chart-injury-type"
          title="Injury type distribution — manufacturing specific"
          series={data?.byInjuryType}
          keyOf={(item) => item.label}
          label={(item) => item.label}
          value={(item) => item.count}
          // One hue, matching the prototype: injury types carry no ordering of
          // their own, so ten colours would invite a meaning that is not there.
          fills={SERIES_FILL}
          formatValue={asCount}
          // The prototype writes "(top 8)" into the heading as a literal. Here
          // the sentence is assembled from `limit` and `totalCategories` and
          // appears only when the server truncated, so a scope with fewer than
          // eight injury types carries no apology for a cut that did not happen.
          truncationCaption={(shown, total) =>
            `Showing ${String(shown)} of ${String(total)} injury types.`
          }
          emptyMessage="No claims in this portfolio yet."
          // The exact stored string, which is what `keyOf` returns here and
          // what the server matches on — no trim, no case-fold, no merge.
          onSelect={openClaims("injuryType")}
          errorMessage="⚠ Injury type distribution could not be loaded."
          {...state}
        />

        <DistributionBars
          testId="chart-employer-paid"
          title="Total paid by employer"
          series={data?.byEmployer}
          // The id, not the label: two employers may share a short name.
          keyOf={(item) => String(item.employerId)}
          label={(item) => item.label}
          value={(item) => item.paidCents}
          fills={CATEGORICAL_FILLS}
          // The only money on this page, and `lib/money.ts` is the only place
          // that divides by a hundred.
          formatValue={formatCents}
          truncationCaption={(shown, total) =>
            `Showing ${String(shown)} of ${String(total)} employers.`
          }
          // `isEmpty` is `items.length === 0`, and per `_by_employer` an
          // employer whose claims have all paid nothing keeps its row — so the
          // only way to get here is a scope with no claims at all. The copy has
          // to answer that condition, not the one it sounds like. The genuine
          // all-zero-spend case has its own sentence below.
          emptyMessage="No claims in this portfolio yet."
          zeroMessage="No spend recorded against these employers yet."
          // The id, matching `keyOf` above: two employers may share a short
          // name, and a drill-through must filter on something that cannot
          // collide.
          //
          // **This is the one navigable segment whose bar is not a count**, the
          // same asymmetry the Total Paid and Total Reserve cards carry: the bar
          // distributes *cents*, so clicking "$1.2M" opens the list of Boeing's
          // claims and the two numbers are about different things. The list is
          // still exactly the segment's claim set — which is what the drill
          // promises — but a supervisor reading "24 in view" under a bar she
          // read as money is owed the note, and the server's own
          // `test_the_employer_drill_through_covers_the_employer_series` says
          // the same thing from the other side.
          onSelect={openClaims("employerId")}
          errorMessage="⚠ Employer spend could not be loaded."
          {...state}
        />

        <DistributionBars
          testId="chart-state"
          title="Claims by US state"
          series={data?.byState}
          keyOf={(item) => item.label}
          label={(item) => item.label}
          value={(item) => item.count}
          fills={CATEGORICAL_FILLS}
          formatValue={asCount}
          truncationCaption={(shown, total) =>
            `Showing ${String(shown)} of ${String(total)} states.`
          }
          emptyMessage="No claims in this portfolio yet."
          onSelect={openClaims("state")}
          errorMessage="⚠ Claims by state could not be loaded."
          {...state}
        />
      </div>
    </section>
  );
}
