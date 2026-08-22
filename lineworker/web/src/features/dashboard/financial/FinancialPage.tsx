/**
 * The analyst workspace's Financial section (FR-AN-5, Story 7.4).
 *
 * The fourth screen that belongs to one persona, and the first anywhere in this
 * console whose subject is **money over a book**. The portfolio dashboard shows
 * one money figure per employer and two KPI totals; this section decomposes the
 * segmented book three ways — what it has cost, how well it is reserved, and
 * which two clinical and legal facts drive the cost.
 *
 * **`FraudPage`'s composition rule, exactly: the page owns the queries and the
 * cards receive `{data, isPending, isError}`.** Two server answers rather than
 * one, and the split is the same one the server made: the totals are one scoped
 * read and the adequacy distribution is three plus a second rule document, so
 * folding them into one request would make every totals render pay for the donut
 * and would let one outage blank both (NFR-3). Each card carries its own
 * `aria-busy` for its own content, and the page-level flag tracks the primary
 * query only — `DashboardPage`'s recorded decision, because an OR would keep the
 * whole region busy while three tiles sat on screen fully readable.
 *
 * **The section inherits the segmentation bar for free.** `SECTION_ROUTES` in
 * `WorkspaceNav` is both the nav's partition and the bar's mount predicate, so
 * adding this route to that list is what puts the ten pickers above these cards
 * — which is why the shell needed one line and not a component.
 *
 * **Every figure drills, and every drill merges the active segmentation.**
 * `withSegmentation` is the one helper all of them go through, so "the list
 * carries the filter as well as the thing I clicked" has one implementation
 * rather than a spread repeated at eight call sites. Three of the targets were
 * already facets before this story (`surgery`, `litigation`, and whichever
 * dimension the breakdown is grouped by); the fourth, `reserveVerdict`, is the
 * facet this story appended, because a verdict is not a column and a
 * distribution segment had no other way to open its own claims.
 *
 * **Nothing on this page computes anything.** Every cent, every group, the
 * ranking, the cut, both cohort averages, the five zero-filled buckets and both
 * band edges arrive decided; `formatCents` is the only division by a hundred and
 * it is `lib/money.ts`'s. `noDerivation.test.ts` walks `features/dashboard`
 * recursively and names every file in this folder — the alternation it walks
 * with gained `reserveCents`, `projectedCents`, `claimCount`,
 * `averageProjectedCents` and `groupCount` for this story, so `paid + reserve`
 * and `projected / claimCount` are both build failures here.
 */
import {
  DEFAULT_FINANCIAL_PARAMS,
  useFinancials,
  useReserveAdequacy,
  type FinancialParams,
} from "@/api/dashboard";

import { isFiltered } from "../drill/filters";
import { pickOption, useSegmentation } from "../segmentation/useSegmentation";

import { BreakdownCard, GROUP_BY_ORDER } from "./BreakdownCard";
import { CostDriverCard, type CohortCopy } from "./CostDriverCard";
import { ReserveAdequacyCard } from "./ReserveAdequacyCard";
import { TotalsCard } from "./TotalsCard";

/**
 * Which URL parameter carries the breakdown's grouping.
 *
 * The API's own name, so the address bar and the request are one string —
 * `filters.ts`' ruling for `filter[…]`, applied to this section's single
 * control. A **bare** parameter rather than a `filter[…]` one because it narrows
 * nothing: it decides how the money is presented, which is why "Clear all"
 * leaves it alone and why `WorkspaceNav` does not carry it between sections.
 */
const GROUP_BY_PARAM = "groupBy" as const;

/** The two cohorts' names, per driver — this card's own copy. */
const SURGERY_COPY: CohortCopy = {
  withDriver: "Surgery required",
  withoutDriver: "No surgery",
};
const LITIGATION_COPY: CohortCopy = {
  withDriver: "Litigated",
  withoutDriver: "Not litigated",
};

export function FinancialPage() {
  /**
   * The workspace's filter and this section's one control, both from the URL.
   *
   * `filter[…]` for the ten dimensions and a bare `groupBy` for the control —
   * `useSegmentation`'s scheme, which Story 7.3 decided once for the whole
   * workspace so that two schemes never end up in one address bar.
   *
   * Held to `GROUP_BY_ORDER` rather than sent as found, `pickOption`'s recorded
   * reason: a `<select>` whose value is not one of its options renders as *no
   * selection at all*, so a hand-edited `?groupBy=handlerId` would leave a
   * control the analyst cannot read. The server would refuse the value anyway;
   * this is about the control rather than about the request.
   */
  const { segmentation, control, setControl } = useSegmentation();
  const params: FinancialParams = {
    groupBy: pickOption(
      control(GROUP_BY_PARAM),
      GROUP_BY_ORDER,
      DEFAULT_FINANCIAL_PARAMS.groupBy,
    ),
  };
  // Whether the cards below describe the caller's book or an intersection of it
  // — which decides one sentence per card and one caption, and nothing else. The
  // Story 7.3 finding: a surface that says "portfolio" while describing a
  // nine-dimension subset is stating something false about the book, and on this
  // section it would be stating it in dollars.
  const segmented = isFiltered(segmentation);
  const financials = useFinancials(params, segmentation);
  const adequacy = useReserveAdequacy(segmentation);
  /**
   * A new question is outstanding and the previous answer is still drawn.
   *
   * `TrendsPage`'s `isRefreshing` and `FraudPage`'s `pendingSort`, which this
   * section needs for the same reason and had been missing: both hooks carry
   * `placeholderData: keepPreviousData`, so `isPending` is `false` throughout a
   * `groupBy` or segmentation change and a card that reported only `isPending`
   * reported **nothing** while the figures on it were stale. The AC asks for the
   * opposite of that — the previous render stays and the affected card says it
   * is busy — so busy is `isPending || isRefreshing`, per query.
   *
   * Gated on `isPlaceholderData` as well as `isFetching` so an ordinary
   * background refetch on an unchanged key does not re-mark a card minutes
   * after the click.
   */
  const totalsRefreshing = financials.isPlaceholderData && financials.isFetching;
  const adequacyRefreshing = adequacy.isPlaceholderData && adequacy.isFetching;
  const totalsBusy = financials.isPending || totalsRefreshing;
  const adequacyBusy = adequacy.isPending || adequacyRefreshing;

  return (
    <section
      aria-label="Financial decomposition"
      data-testid="financial-decomposition"
      // The totals query alone, `DashboardPage`'s recorded decision: with two
      // independent queries an OR would keep the whole region busy while the
      // tiles sat on screen fully readable, and every card carries its own
      // `aria-busy` for its own content.
      aria-busy={totalsBusy}
    >
      <h2 className="mb-[13px] font-display text-[15px] font-bold text-text">
        Financial decomposition
      </h2>

      {/* Announced rather than only drawn: an analyst using a screen reader gets
          one polite sentence when the figures land, instead of four cards
          appearing silently. `sr-only` because the cards are the visual
          announcement. */}
      <p role="status" aria-live="polite" className="sr-only">
        {financials.isPending
          ? "Loading financial decomposition."
          : financials.isError
            ? // `QueuePane`'s ruling: the sections below carry their own
              // `role="alert"`, and announcing one failure twice — assertively,
              // preempting this polite region — is worse than announcing it once.
              ""
            : "Financial decomposition updated."}
      </p>

      {financials.isError && (
        <p
          role="alert"
          data-testid="financial-totals-error"
          className="mb-[10px] rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ The portfolio financials could not be loaded. Try again in a moment.
        </p>
      )}

      {/* The three money figures first, because the two cards below are both
          decompositions *of* them: the breakdown partitions the projected total
          and the cost drivers partition the population. Three columns, because
          three cards — `FraudPage`'s recorded fix for a grid that reserved four
          and rendered two. */}
      <div className="mb-[10px] grid gap-[10px] sm:grid-cols-3">
        <TotalsCard
          data={financials.data}
          segmentation={segmentation}
          segmented={segmented}
          isError={financials.isError}
        />
      </div>

      <div className="mb-[10px] grid gap-[10px] lg:grid-cols-2">
        <BreakdownCard
          data={financials.data}
          segmentation={segmentation}
          segmented={segmented}
          groupBy={params.groupBy}
          onGroupBy={(next) => {
            setControl(GROUP_BY_PARAM, next);
          }}
          isPending={totalsBusy}
          isRefreshing={totalsRefreshing}
          isError={financials.isError}
        />
        <ReserveAdequacyCard
          data={adequacy.data}
          segmentation={segmentation}
          segmented={segmented}
          isPending={adequacyBusy}
          isError={adequacy.isError}
        />
      </div>

      <div className="grid gap-[10px] lg:grid-cols-2">
        <CostDriverCard
          testId="cost-driver-surgery"
          title="Surgery vs. no surgery"
          copy={SURGERY_COPY}
          pair={financials.data?.surgery}
          segmentation={segmentation}
          segmented={segmented}
          isPending={totalsBusy}
          isError={financials.isError}
        />
        <CostDriverCard
          testId="cost-driver-litigation"
          title="Litigated vs. not litigated"
          copy={LITIGATION_COPY}
          pair={financials.data?.litigation}
          segmentation={segmentation}
          segmented={segmented}
          isPending={totalsBusy}
          isError={financials.isError}
        />
      </div>
    </section>
  );
}
