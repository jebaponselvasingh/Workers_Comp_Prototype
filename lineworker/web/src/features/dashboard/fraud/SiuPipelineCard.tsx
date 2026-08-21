/**
 * The SIU pipeline — the referred population, by stage and by handler (AC 1).
 *
 * Two `DistributionBars`, unwrapped, for `FraudDistributionCard`'s reason: bars
 * are the idiom this dashboard already draws a keyed distribution in, and the
 * counts, the orderings and the omissions all arrived decided.
 *
 * **Grouped on `stage`, and the acceptance criterion's word is "status".** They
 * are not the same column and the difference is eight claims: `status` carries
 * six seeded values that mix lifecycle with disposition, `stage` is the
 * four-value lifecycle every other surface on this console groups on, and
 * `stage` is the facet the drill list accepts. A pipeline grouped on `status`
 * would produce segments no click could resolve. `summary.py` records the
 * ruling; this is the third surface to inherit it.
 *
 * **A stage or a handler with no referred claim is absent**, unlike the band
 * distribution beside it. That is the server's rule rather than a rendering
 * choice: a band's vocabulary is a rule's and is always complete, while a stage
 * the pipeline does not reach is not an empty segment of it. The denominator a
 * reader wants for a handler — how many claims that desk holds — is the rate
 * table below, published there with its own `claims` column.
 *
 * **Both segments drill with two facets.** `filter[siuReview]=true` plus the
 * segment's own key, because a segment of *this* chart is "the referred claims
 * in that stage" and not "every claim in that stage". Dropping the first would
 * open a list several times longer than the bar that was clicked, which is the
 * one failure a drill-through exists not to have.
 *
 * **`filter[siuReview]`, never `filter[fraudFlagged]`.** This pipeline is the
 * queue's SIU *referral* rule and the Fraud Flags card is the wider *review*
 * one — 9 seeded claims against 13. Both are on the same screen a card apart,
 * which is exactly the arrangement that makes the wrong facet look right.
 */
import { useNavigate } from "react-router";

import type { Stage } from "@/api/claims";
import type { FraudPanel } from "@/api/dashboard";

import { CATEGORICAL_FILLS, STAGE_FILL } from "../charts/chartTheme";
import { DistributionBars } from "../charts/DistributionBars";
import { drillHref } from "../drill/filters";

/**
 * The stage bars' copy — the settlement donut's words, restated here.
 *
 * Restated rather than imported from `charts/PortfolioCharts.tsx`, which
 * declares its own `STAGE_LABEL` as module-private: two surfaces owning one
 * enum's copy is what "the UI owns display labels" means, and reaching into a
 * sibling component's private constant to avoid three lines would make this
 * card's wording a property of that file's layout decisions. The words are
 * deliberately the *same* words, because a reader clicking "Under Treatment"
 * here and reading "Under Treatment" on the chip has to recognise them.
 */
const STAGE_LABEL: Record<Stage, string> = {
  settled: "Settled & Closed",
  treatment: "Under Treatment",
  intake: "Intake",
  investigation: "Investigation",
};

/** `String`, named, so a count chart's formatter reads as a decision. */
function asCount(magnitude: number): string {
  return String(magnitude);
}

export function SiuPipelineCard({
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
  const state = { isPending, isError };

  return (
    <>
      <DistributionBars
        testId="siu-pipeline-stage"
        title="SIU pipeline by stage"
        series={data?.siuByStage}
        keyOf={(item) => item.key}
        label={(item) => STAGE_LABEL[item.key as Stage] ?? item.key}
        value={(item) => item.count}
        // Keyed by stage rather than by rank, `RECOVERY_FILL`'s argument: these
        // four sit a card away from a settlement donut painted in the same
        // tokens, and a positional palette would give Investigation a different
        // colour on two charts of one column.
        fills={STAGE_FILL}
        formatValue={asCount}
        // The stage series is never cut — four members — so the caption is
        // unreachable. Supplied because the prop is required, and returning an
        // empty string rather than a sentence nobody sees is what the recovery
        // chart already does.
        truncationCaption={() => ""}
        emptyMessage="No claim in this portfolio is under SIU review."
        errorMessage="⚠ The SIU pipeline could not be loaded."
        onSelect={(stage) =>
          void navigate(drillHref({ siuReview: "true", stage }))
        }
        {...state}
      />

      <DistributionBars
        testId="siu-pipeline-handler"
        title="SIU pipeline by handler"
        series={data?.siuByHandler}
        // The id, not the name: two handlers may share a display name, and a
        // drill-through keyed on the name would merge two desks into one list
        // while looking perfectly correct. `benchmarks.py` has grouped on the id
        // since Story 5.2 for the same reason.
        keyOf={(item) => String(item.handlerId)}
        label={(item) => item.handlerName}
        value={(item) => item.count}
        fills={CATEGORICAL_FILLS}
        formatValue={asCount}
        truncationCaption={() => ""}
        emptyMessage="No handler in this portfolio carries an SIU review."
        errorMessage="⚠ The SIU pipeline could not be loaded."
        onSelect={(handlerId) =>
          void navigate(drillHref({ siuReview: "true", handlerId }))
        }
        {...state}
      />
    </>
  );
}
