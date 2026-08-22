/**
 * The analyst workspace's Fraud section (FR-AN-1, Story 7.1).
 *
 * The first screen in this console that belongs to one persona. Since Epic 5 the
 * analyst has read the supervisor's dashboard byte for byte; this is the section
 * they have and the supervisor does not, reached from the workspace navigation
 * and gated three ways — the route table's `RequireSession allow={["analyst"]}`,
 * an allowlist in `services/worklist/fraud.py`, and, as everywhere,
 * `employer_scope` deciding *which* claims the figures are over (AD-7).
 *
 * **`DashboardPage`'s composition rule, exactly: the page owns the queries and
 * the sections receive `{data, isPending, isError}`.** Four server answers, four
 * costs, four failure modes — a red-flag outage leaves the distribution, the
 * pipeline and the rate tables standing (NFR-3), and each section carries its own
 * `aria-busy` for its own content. The page-level flag tracks only the primary
 * query, `DashboardPage`'s recorded decision: an OR would keep the whole region
 * busy while the distribution sat on screen fully readable.
 *
 * **The flagged-claims list is Story 5.5's list, not a second one.** The story
 * text asks for a "flagged-claims list", and `/dashboard/claims?filter[fraudFlagged]=true`
 * already is one: cursor-paginated, scoped, server-ranked, tested against the KPI
 * card it reconciles with, and rendered by the same `ClaimCard` the handler's
 * queue draws. So this section embeds the first page of it through the existing
 * `useDrillClaims` hook and links to the full view — building a second would fork
 * the component the same task list forbids forking, and would give the console
 * two flagged-claims lists that could come to disagree.
 *
 * **Nothing on this page computes anything.** Every count, every rate, every
 * ordering, the zero-filled band vocabulary and all four thresholds arrive
 * decided; the sort controls set a server parameter and refetch.
 * `noDerivation.test.ts` walks `features/dashboard` recursively and names every
 * file in this folder.
 */
import { useState } from "react";

import { Link, useNavigate } from "react-router";

import {
  DEFAULT_FRAUD_RATE_SORT,
  useDrillClaims,
  useFraudPanel,
  useFraudRates,
  useFraudRedFlags,
  type FraudRateSort,
  type FraudRateSorts,
} from "@/api/dashboard";
import { ClaimCard } from "@/features/queue/ClaimCard";

import {
  claimHref,
  drillHref,
  isFiltered,
  NO_MATCHING_CLAIMS,
  toFilterKey,
  withSegmentation,
  type DrillFilters,
  type DrillOrigin,
} from "../drill/filters";
import { KpiCard, KpiCardSkeleton } from "../KpiCard";
import { pickOption, useSegmentation } from "../segmentation/useSegmentation";

import { FraudDistributionCard } from "./FraudDistributionCard";
import { FraudRateTables, SORT_ORDER } from "./FraudRateTables";
import { RedFlagFrequencyCard } from "./RedFlagFrequencyCard";
import { SiuPipelineCard } from "./SiuPipelineCard";

/**
 * The flagged population's filter set, declared once.
 *
 * The *review* rule — the same one the portfolio's Fraud Flags card counts and
 * the same one the rate tables' `flagged` column is folded on — and deliberately
 * not the SIU referral rule the pipeline above it shows. Two facets over one
 * column pair, thirteen seeded claims against nine, and they are one line apart
 * on this screen: declaring the set once is what stops the entry point and the
 * embedded list opening two different populations.
 */
const FLAGGED: DrillFilters = { fraudFlagged: "true" };

/**
 * Which URL parameter carries each table's order, and in which field.
 *
 * The API's own `sort[…]` names, so the address bar and the request are one
 * string — `filters.ts`' ruling for `filter[…]`, applied to the three controls
 * Story 7.1 deferred to this story. A table rather than three lookups because
 * the read and the write have to agree about the pairing, and a mismatch would
 * put the employer table's order in the handler table's parameter.
 */
const SORT_PARAM = {
  injuryType: "sort[injuryType]",
  employer: "sort[employer]",
  handler: "sort[handler]",
} as const;

/** How many placeholder rows the embedded list holds open. */
const SKELETON_ROWS = 4;

/**
 * The first page of the flagged-claims list, embedded.
 *
 * `DrillClaimsPage`'s rows without its chrome: the same hook, the same
 * `ClaimCard`, the same navigation carrying the list's own URL so Back returns to
 * the flagged list rather than to the whole book. What it deliberately does *not*
 * have is a "Show more" — this is an entry point, and the full walk is one click
 * away on a page built for it.
 */
function FlaggedClaims({ segmentation }: { segmentation: DrillFilters }) {
  const navigate = useNavigate();
  // The workspace's filter *and* the flagged rule, which is what makes this
  // embedded list the same population the two cards above it count — the merge
  // every drill target on this page goes through.
  const filters = withSegmentation(segmentation, FLAGGED);
  const list = useDrillClaims(filters);
  // `HandlerBenchmarkTable`'s one-predicate ruling.
  const isLoading = list.isPending && list.data === undefined;

  return (
    <section
      data-testid="fraud-flagged-claims"
      aria-labelledby="fraud-flagged-claims-heading"
      aria-busy={isLoading}
      className="rounded-lg border border-border bg-surface p-3"
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3
          id="fraud-flagged-claims-heading"
          className="font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
        >
          Flagged claims
        </h3>
        <Link
          data-testid="fraud-flagged-view-all"
          to={drillHref(filters)}
          className="rounded text-[11px] font-semibold text-steel hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
        >
          View all →
        </Link>
      </div>

      {list.isError ? (
        <p
          role="alert"
          data-testid="fraud-flagged-error"
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ The flagged claims could not be loaded. Try again in a moment.
        </p>
      ) : isLoading ? (
        <ul data-testid="fraud-flagged-skeleton" aria-hidden className="flex flex-col">
          {Array.from({ length: SKELETON_ROWS }, (_, index) => (
            <li key={index} className="border-b border-border px-[11px] py-[9px]">
              <span className="block h-[46px] animate-pulse rounded bg-surface-2" />
            </li>
          ))}
        </ul>
      ) : list.data !== undefined && list.data.items.length === 0 ? (
        <p data-testid="fraud-flagged-empty" className="text-[11.5px] text-faint">
          {/* With the workspace narrowed this list is the intersection of the
              flagged rule and the segmentation, so "no claim in this portfolio
              is flagged" would blame the book for what the filter did. See
              `NO_MATCHING_CLAIMS`. */}
          {isFiltered(segmentation)
            ? NO_MATCHING_CLAIMS
            : "No claim in this portfolio is flagged for fraud review."}
        </p>
      ) : (
        <ul aria-label="Flagged claims" className="rounded border border-border">
          {list.data?.items.map((row) => (
            <ClaimCard
              key={row.claimId}
              card={row}
              // Nothing here is "the one you are looking at": the claim view is a
              // route of its own, so there is no selection to reflect.
              selected={false}
              onSelect={(claimId) =>
                void navigate(claimHref(claimId), {
                  // The *drill list's* URL, not this page's: a supervisor who
                  // opened a claim from here should come back to the list she was
                  // reading, which is the flagged list rather than the workspace.
                  state: { from: drillHref(filters) } satisfies DrillOrigin,
                })
              }
            />
          ))}
        </ul>
      )}
    </section>
  );
}

export function FraudPage() {
  /**
   * The workspace's filter and its controls, both from the URL (Story 7.3).
   *
   * Story 7.1 held the three sorts in component state and recorded why: this
   * story owns what belongs in the workspace's address bar, and deciding it once
   * for filters and once for sorts is how two schemes end up in one URL. So the
   * decision is made — `filter[…]` for segmentation, the API's own `sort[…]`
   * names for these three — and "send me this table sorted by most flagged" is a
   * link.
   */
  const { segmentation, control, setControl } = useSegmentation();
  const panel = useFraudPanel(segmentation);
  const redFlags = useFraudRedFlags(segmentation);
  /**
   * Which order each rate table is being read in — read out of the URL.
   *
   * Held to `SORT_ORDER` rather than sent as found, `pickOption`'s recorded
   * reason: a `<select>` whose value is not one of its options renders as *no
   * selection at all*, so a hand-edited `?sort[employer]=severity` would leave a
   * control the analyst cannot read. The server would refuse the value anyway;
   * this is about the control rather than about the request.
   */
  const sorts: FraudRateSorts = {
    injuryType: pickOption(control(SORT_PARAM.injuryType), SORT_ORDER, DEFAULT_FRAUD_RATE_SORT),
    employer: pickOption(control(SORT_PARAM.employer), SORT_ORDER, DEFAULT_FRAUD_RATE_SORT),
    handler: pickOption(control(SORT_PARAM.handler), SORT_ORDER, DEFAULT_FRAUD_RATE_SORT),
  };
  /**
   * Which table's order was asked about last — the one a re-fetch belongs to.
   *
   * All three tables ride one query keyed on the whole sort set, so a control
   * change re-keys it and every table is technically "in flight". That is a fact
   * about the request and not about the screen: two of the three were not touched
   * and their rows have not moved. Remembering which control was used is what
   * lets exactly one section flip `aria-busy` — the alternative announced a load
   * for two tables nobody asked about, to the reader least able to see that
   * nothing changed.
   */
  const [requestedSort, setRequestedSort] = useState<keyof FraudRateSorts | null>(null);
  const rates = useFraudRates(sorts, segmentation);
  /**
   * …and it is forgotten when the *filter* changes, because a filter is not a sort.
   *
   * The flag answers "which table was the reader asking about", and a
   * segmentation change is a question about all three at once — every row in
   * every table moves. Left standing, the last-sorted table alone reported busy
   * while the other two swapped their rows silently, which is the announcement
   * inverted: the one table whose `aria-busy` a screen reader trusts was the one
   * saying the least useful thing.
   *
   * **Adjusted during render against a remembered key**, React's own pattern for
   * state derived from a prop ("You Might Not Need an Effect") and the same one
   * `SegmentationBar` uses for its refusal notice: an effect would clear the flag
   * *after* the paint that already showed one table busy. The key is
   * `toFilterKey`'s ordered serialisation, so two renders that set the same
   * dimensions in a different order are one filter and do not reset anything.
   */
  const filterKey = toFilterKey(segmentation);
  const [sortedUnder, setSortedUnder] = useState(filterKey);
  if (sortedUnder !== filterKey) {
    setSortedUnder(filterKey);
    setRequestedSort(null);
  }
  /**
   * …and it only counts while the *placeholder* is on screen.
   *
   * `isPlaceholderData` is true exactly while a new key's answer is outstanding
   * and the previous one is being drawn, which is the state this flag describes.
   * Gating on it rather than on `isFetching` alone keeps an ordinary background
   * refetch — same key, same order — from re-marking whichever table was sorted
   * last, minutes after the click.
   */
  const pendingSort = rates.isPlaceholderData && rates.isFetching ? requestedSort : null;

  function setSort(table: keyof FraudRateSorts, next: FraudRateSort) {
    setRequestedSort(table);
    setControl(SORT_PARAM[table], next);
  }

  return (
    <section
      aria-label="Fraud analytics"
      data-testid="fraud-analytics"
      // The panel alone, `DashboardPage`'s recorded decision: with four
      // independent queries an OR would keep the whole region busy while the
      // distribution sat on screen fully readable, and every section carries its
      // own `aria-busy` for its own content.
      aria-busy={panel.isPending}
    >
      <h2 className="mb-[13px] font-display text-[15px] font-bold text-text">
        Fraud analytics
      </h2>

      {/* Announced rather than only drawn: an analyst using a screen reader gets
          one polite sentence when the figures land, instead of four cards
          appearing silently. `sr-only` because the cards are the visual
          announcement. */}
      <p role="status" aria-live="polite" className="sr-only">
        {panel.isPending
          ? "Loading fraud analytics."
          : panel.isError
            ? // `QueuePane`'s ruling: the sections below carry their own
              // `role="alert"`, and announcing one failure twice — assertively,
              // preempting this polite region — is worse than announcing it once.
              ""
            : "Fraud analytics updated."}
      </p>

      {/* The two populations, as figures, before the charts that distribute
          them. Both are the server's counts of *different rules* over one column
          pair, which is why they are two cards rather than one with a caption:
          the review population is what the flagged list below holds, and the
          referral population is what the pipeline shows.

          **Two columns, because two cards.** The portfolio dashboard's KPI row
          declares four and renders ten, which is why the idiom was copied
          wholesale; here it reserved four and rendered two, so on a wide viewport
          the right half of the row sat empty — which reads as two cards that
          failed to load, on a page whose failure state is *also* "the cards are
          not there" (`panel.isError` below). The grid sizes to what it renders,
          and grows when 7.2 gives it something to grow for. */}
      <div className="mb-[10px] grid gap-[10px] sm:grid-cols-2">
        {panel.isError ? (
          // **Nothing at all**, not a skeleton and not a zeroed pair. A pulsing
          // placeholder under the distribution card's own `role="alert"` would be
          // two states on one screen saying opposite things, and it would keep
          // saying "loading" for as long as the reader stayed. The failure is
          // announced once, by the surface that failed.
          null
        ) : panel.data === undefined ? (
          <>
            <KpiCardSkeleton />
            <KpiCardSkeleton />
          </>
        ) : (
          <>
            <KpiCard
              testId="fraud-kpi-flagged"
              value={String(panel.data.flaggedClaims)}
              label="Flagged for review"
              // The cut-off the count was produced at, from the document that
              // decided it — the KPI cards' rule on a third surface.
              caption={`Score ≥ ${String(panel.data.fraudFlagScoreMin)} — review needed`}
              tone="error"
              drill={withSegmentation(segmentation, FLAGGED)}
            />
            <KpiCard
              testId="fraud-kpi-siu"
              value={String(panel.data.siuClaims)}
              label="SIU review"
              caption={`Score ≥ ${String(panel.data.siuFraudScoreMin)} — referred`}
              tone="warn"
              // The *referral* rule, and deliberately not the card beside it —
              // merged with the workspace's filter, like every drill target on
              // this page, so the list opens the intersection the card counted.
              drill={withSegmentation(segmentation, { siuReview: "true" })}
            />
          </>
        )}
      </div>

      <div className="mb-[10px] grid gap-[10px] sm:grid-cols-2 lg:grid-cols-3">
        <FraudDistributionCard
          data={panel.data}
          segmentation={segmentation}
          isPending={panel.isPending}
          isError={panel.isError}
        />
        <SiuPipelineCard
          data={panel.data}
          segmentation={segmentation}
          isPending={panel.isPending}
          isError={panel.isError}
        />
      </div>

      <div className="mb-[14px] grid gap-[10px] lg:grid-cols-2">
        <FlaggedClaims segmentation={segmentation} />
        <RedFlagFrequencyCard
          data={redFlags.data}
          // The card publishes a coverage figure — "N of M claims … have a
          // cached fraud narrative" — and M is the *segmented* book since this
          // story. Without knowing that, the sentence blames a cold AI cache for
          // what the filter did.
          segmented={isFiltered(segmentation)}
          isPending={redFlags.isPending}
          isError={redFlags.isError}
        />
      </div>

      <FraudRateTables
        data={rates.data}
        segmentation={segmentation}
        sorts={sorts}
        onSort={setSort}
        pendingSort={pendingSort}
        isPending={rates.isPending}
        isError={rates.isError}
      />
    </section>
  );
}
