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
  DEFAULT_FRAUD_RATE_SORTS,
  useDrillClaims,
  useFraudPanel,
  useFraudRates,
  useFraudRedFlags,
  type FraudRateSort,
  type FraudRateSorts,
} from "@/api/dashboard";
import { ClaimCard } from "@/features/queue/ClaimCard";

import { claimHref, drillHref, type DrillFilters, type DrillOrigin } from "../drill/filters";
import { KpiCard, KpiCardSkeleton } from "../KpiCard";

import { FraudDistributionCard } from "./FraudDistributionCard";
import { FraudRateTables } from "./FraudRateTables";
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
function FlaggedClaims() {
  const navigate = useNavigate();
  const list = useDrillClaims(FLAGGED);
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
          to={drillHref(FLAGGED)}
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
          No claim in this portfolio is flagged for fraud review.
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
                  state: { from: drillHref(FLAGGED) } satisfies DrillOrigin,
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
  const panel = useFraudPanel();
  const redFlags = useFraudRedFlags();
  /**
   * Which order each rate table is being read in.
   *
   * Local UI state, `AD-9`'s split: server state is TanStack Query's and nothing
   * else, and "which of five orders is this analyst looking at" is neither a
   * server fact nor part of the claim's identity. It is deliberately **not** in
   * the URL — three sort parameters would triple the surface area of a shareable
   * link for a preference that carries no data, and Story 7.3's segmentation is
   * the story that owns what belongs in this workspace's address bar.
   */
  const [sorts, setSorts] = useState<FraudRateSorts>(DEFAULT_FRAUD_RATE_SORTS);
  const rates = useFraudRates(sorts);

  function setSort(table: keyof FraudRateSorts, next: FraudRateSort) {
    setSorts((current) => ({ ...current, [table]: next }));
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
          referral population is what the pipeline shows. */}
      <div className="mb-[10px] grid gap-[10px] sm:grid-cols-2 lg:grid-cols-4">
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
              drill={FLAGGED}
            />
            <KpiCard
              testId="fraud-kpi-siu"
              value={String(panel.data.siuClaims)}
              label="SIU review"
              caption={`Score ≥ ${String(panel.data.siuFraudScoreMin)} — referred`}
              tone="warn"
              // The *referral* rule, and deliberately not the card beside it.
              drill={{ siuReview: "true" }}
            />
          </>
        )}
      </div>

      <div className="mb-[10px] grid gap-[10px] sm:grid-cols-2 lg:grid-cols-3">
        <FraudDistributionCard
          data={panel.data}
          isPending={panel.isPending}
          isError={panel.isError}
        />
        <SiuPipelineCard
          data={panel.data}
          isPending={panel.isPending}
          isError={panel.isError}
        />
      </div>

      <div className="mb-[14px] grid gap-[10px] lg:grid-cols-2">
        <FlaggedClaims />
        <RedFlagFrequencyCard
          data={redFlags.data}
          isPending={redFlags.isPending}
          isError={redFlags.isError}
        />
      </div>

      <FraudRateTables
        data={rates.data}
        sorts={sorts}
        onSort={setSort}
        isPending={rates.isPending}
        isError={rates.isError}
      />
    </section>
  );
}
