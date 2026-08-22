/**
 * Projected cost by any one segmentation dimension (Story 7.4, AC 1).
 *
 * `DistributionBars` over money, which is the drawing the portfolio dashboard's
 * total-paid-by-employer chart already is — same component, same `formatCents`
 * formatter, same chip row of click targets. What is new is that the *dimension*
 * is a control: the same ten the segmentation bar narrows by, so an analyst can
 * ask "what does this cost by sector" and then "…by ICD-10 code" without leaving
 * the card.
 *
 * **The selector writes the URL and the server answers it.** `groupBy` is a bare
 * named parameter under the route's own spelling (`useSegmentation`'s
 * `ControlName`), so a grouping is as shareable as the filter beside it — and
 * changing it issues a request rather than re-folding rows in the browser, which
 * is what makes "the browser groups nothing" observable rather than merely
 * claimed. The control renders the **server's echo** once an answer has landed,
 * `FraudRateTables`' ruling: a request that 422s or times out must not leave a
 * `<select>` claiming a grouping the bars beside it are not in.
 *
 * **The bars are ranked by projected cost, and the ranking is the server's.**
 * Not paid: the paid columns are zero on every open claim in this book
 * (`deferred-work.md`'s finding), so a paid ranking would put the entire open
 * portfolio in a tie at the bottom and cut the tail arbitrarily inside it.
 * `services/worklist/decomposition._breakdown` carries the argument, and this
 * file does not sort.
 *
 * **The truncation caption quotes the server's two numbers.** `limit` and
 * `groupCount` arrive on the payload precisely so the sentence can be assembled
 * without the client computing how many groups are missing — and it renders only
 * when the server says `truncated`, so a dimension with fewer groups than the
 * cap carries no apology for a cut that did not happen.
 *
 * **Each bar drills to its own group**, on the *grouped dimension's own facet*:
 * a group's `key` is the wire value that facet takes, which is why the server
 * publishes the key rather than a label and why nothing here composes a
 * parameter name. Merged with the active segmentation, like every drill target
 * on this page, so the list opens the intersection the bar was folded from.
 */
import { useNavigate } from "react-router";

import type { BreakdownDimension, FinancialDecomposition } from "@/api/dashboard";
import { formatCents } from "@/lib/money";

import { SERIES_FILL } from "../charts/chartTheme";
import { DistributionBars } from "../charts/DistributionBars";
import {
  drillHref,
  FILTER_LABEL,
  NO_MATCHING_CLAIMS,
  SEGMENTATION_KEYS,
  valueLabel,
  withSegmentation,
  type DrillFilters,
  type FilterKey,
} from "../drill/filters";

/**
 * The ten groupings the control offers, in the chip row's order.
 *
 * **`SEGMENTATION_KEYS`, reused rather than restated**, and that is the whole
 * reason `BreakdownDimension`'s members are the facet names: the picker's
 * vocabulary, the URL's `filter[…]` names and the group keys the server returns
 * are one list with one order. A second array here would be an eleventh name for
 * ten things and would be free to drift the day a dimension was added.
 *
 * The cast is the narrow one this file cannot avoid and is checked at the
 * boundary rather than asserted here: `SEGMENTATION_KEYS` is a literal tuple of
 * `FilterKey`s and `BreakdownDimension` is the generated union of the same ten
 * strings, so a divergence is a *server* change and shows up as a 422 the card
 * degrades on. `FinancialPage.test.tsx` asserts the two tuples are equal, which
 * is where the claim belongs.
 */
export const GROUP_BY_ORDER = SEGMENTATION_KEYS as readonly BreakdownDimension[];

/** What a grouping is called — the chip row's own words, reused. */
function groupByLabel(dimension: BreakdownDimension): string {
  return FILTER_LABEL[dimension as FilterKey];
}

export function BreakdownCard({
  data,
  segmentation,
  segmented,
  groupBy,
  onGroupBy,
  isPending,
  isRefreshing,
  isError,
}: {
  /** The server's decomposition, or `undefined` while it is unknown. */
  data: FinancialDecomposition | undefined;
  /**
   * The workspace's active filter, merged into every drill target below.
   *
   * Passed in rather than read here, `DashboardPage`'s composition rule.
   */
  segmentation: DrillFilters;
  segmented: boolean;
  /** The grouping that was *asked* for — the URL's, held to the ten. */
  groupBy: BreakdownDimension;
  onGroupBy: (next: BreakdownDimension) => void;
  isPending: boolean;
  /**
   * A new grouping is outstanding and the previous answer is still drawn.
   *
   * Separate from `isPending` because the two decide different things: busy is
   * what the card *reports*, and this is what the select *shows* — see `shown`.
   */
  isRefreshing: boolean;
  isError: boolean;
}) {
  const navigate = useNavigate();
  // The server's echo once an answer has landed, and the asked-for value while
  // one is outstanding — `TrendsPage`'s `shown`, and the `isRefreshing` term is
  // the half that was missing. With `keepPreviousData`, `data` during a regroup
  // is the *previous* dimension's answer, so `data?.… ?? groupBy` put the old
  // grouping back into the select under the analyst's hand: pick "ICD-10" and
  // the control snaps back to "Employer", the heading disagrees with the URL,
  // and a bar clicked in that window drills the wrong facet. While a new answer
  // is outstanding the control shows what was *asked*; once it lands it shows
  // what the server says it applied, so a coerced parameter still cannot leave
  // the control disagreeing with the chart beside it.
  const shown = data === undefined || isRefreshing ? groupBy : data.breakdown.dimension;

  return (
    <section
      data-testid="financial-breakdown"
      aria-labelledby="financial-breakdown-heading"
      // Its own flag for its own content — `FraudRateTables`' rule. It is `true`
      // for a first load *and* for a regroup, because on a regroup the bars
      // below are the previous grouping's and saying nothing would let stale
      // figures read as fresh.
      aria-busy={isPending}
      className="flex flex-col gap-2"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <h3 id="financial-breakdown-heading" className="sr-only">
          Projected cost by {groupByLabel(shown)}
        </h3>
        <div className="flex flex-col gap-[3px]">
          <label
            htmlFor="financial-group-by-select"
            className="font-display text-[9.5px] font-bold tracking-[0.3px] text-faint uppercase"
          >
            Break down by
          </label>
          <select
            id="financial-group-by-select"
            data-testid="financial-group-by"
            value={shown}
            onChange={(event) => {
              onGroupBy(event.target.value as BreakdownDimension);
            }}
            className="rounded border border-border bg-surface px-[6px] py-[2px] text-[11px] text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
          >
            {GROUP_BY_ORDER.map((dimension) => (
              <option key={dimension} value={dimension}>
                {groupByLabel(dimension)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <DistributionBars
        testId="financial-breakdown-bars"
        title={`Projected cost by ${groupByLabel(shown)}`}
        series={
          data === undefined
            ? undefined
            : {
                items: data.breakdown.items,
                // The **portfolio** total rather than the kept groups' sum, and
                // deliberately so: `DistributionBars` reads this only to tell "no
                // claims at all" from "claims that distribute nothing", and the
                // honest denominator for that question is what the segment costs
                // rather than what the twelve drawn bars cost. It is a published
                // figure read, never a re-addition of the rows.
                total: data.totals.projectedCents,
                totalCategories: data.breakdown.groupCount,
                truncated: data.breakdown.truncated,
                limit: data.breakdown.limit,
              }
        }
        keyOf={(item) => item.key}
        // The server's label where it sent one — which is `employerId` alone,
        // because an id is not a name — and this console's copy otherwise, from
        // the same resolver a chip uses. One vocabulary: a bar reading "High" and
        // a chip reading "Severity: High" are one string with two renderings.
        label={(item) => valueLabel(shown as FilterKey, item.key, item.label)}
        value={(item) => item.totals.projectedCents}
        // One hue for every bar, `SERIES_FILL`'s recorded argument: the groups
        // carry no ordering and no severity of their own, so ten hues would
        // invite a reader to look for a meaning that is not there. The bars are
        // already ranked, which is where the ordering is.
        fills={SERIES_FILL}
        formatValue={formatCents}
        truncationCaption={(shownCount, total) =>
          `Top ${String(shownCount)} of ${String(total)} by projected cost`
        }
        emptyMessage={
          segmented ? NO_MATCHING_CLAIMS : "No claims in this portfolio yet."
        }
        // Reachable, unlike on most of these charts: a book whose claims are all
        // settled with nothing paid and nothing reserved arrives as real groups
        // summing to zero, which draws as bars of no length against an empty
        // axis. Saying it in words is the answer — `DistributionBars.zeroMessage`.
        zeroMessage="These claims carry no paid or reserved amount yet."
        errorMessage="⚠ The cost breakdown could not be loaded."
        isPending={isPending}
        isError={isError}
        onSelect={(key) =>
          void navigate(drillHref(withSegmentation(segmentation, { [shown as FilterKey]: key })))
        }
      />
    </section>
  );
}
