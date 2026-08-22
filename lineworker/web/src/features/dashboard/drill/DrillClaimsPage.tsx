/**
 * The drill-through list — the claims behind a dashboard figure (AC 1, AC 4).
 *
 * A routed view under `/dashboard/claims`, reached by clicking a KPI card, a
 * chart segment, a handler row or the worklist's "View all". It is the one
 * genuinely new screen in Epic 5 — readiness advisory #2 flags drill-through as
 * UX with **no prototype precedent** — so the deliberate answer is to invent
 * nothing: the rows are Epic 2's `ClaimCard`, unchanged and unwrapped, and the
 * chips are the chip vocabulary the bills tab and the priority table already
 * share. The only new element on the page is the chip row itself.
 *
 * **The rows are `ClaimCard`, and that is a contract rather than a
 * convenience.** `DrillClaimRowResponse` is `ClaimCardResponse` field for
 * field — a server test asserts the two models' field sets are equal — so this
 * list and the handler's queue cannot come to disagree about what a claim looks
 * like. The card takes `{card, selected, onSelect}`, which is exactly what a
 * flat list needs: nothing here is selected, and `onSelect` navigates.
 *
 * **The filter set lives in the URL and nowhere else** (AD-9). There is no
 * local filter state, so a link pasted into a new tab reconstructs the view from
 * the address alone: the same chips, the same request, the same rows. Scope is
 * *not* in the URL and cannot be — it re-resolves server-side from the session
 * on every request, which is what makes AC 4's escape test a property of the
 * shape rather than of a validator.
 *
 * **Nothing here counts, sorts, filters or cuts.** The result sentence quotes
 * `total` verbatim, the order is the server's, and the "Show more" walk appends
 * whatever the next page contained. `noDerivation.test.ts` walks this directory
 * and names this file.
 */
import { useState } from "react";

import { Link, useNavigate, useSearchParams } from "react-router";

import { useQueryClient } from "@tanstack/react-query";

import { useDrillClaimPages, useDrillClaims, type DrillClaimRow } from "@/api/dashboard";
import { isValidationError } from "@/api/errors";
import { queryKeys } from "@/api/queryKeys";
import { ClaimCard } from "@/features/queue/ClaimCard";
import { DASHBOARD_ROUTE } from "@/features/shell/routes";

import { ageBandLabels } from "../segmentation/ageBands";

import { ExportControl } from "../export/ExportControl";

import { FilterChips } from "./FilterChips";
import {
  appliedFromFilters,
  chipLabel,
  claimHref,
  drillHref,
  fromSearchParams,
  type DrillOrigin,
  toFilterKey,
  toQueryParams,
  toSearchParams,
  type FilterKey,
} from "./filters";

/** How many placeholder rows are held open while the request is in flight. */
const SKELETON_ROWS = 6;

/** First occurrence of each claim id wins — the base page before its pages. */
function dedupe(rows: DrillClaimRow[]): DrillClaimRow[] {
  const seen = new Set<string>();
  const unique: DrillClaimRow[] = [];
  for (const row of rows) {
    if (seen.has(row.claimId)) continue;
    seen.add(row.claimId);
    unique.push(row);
  }
  return unique;
}

function SkeletonRows() {
  return (
    <ul data-testid="drill-skeleton" aria-hidden className="flex flex-col">
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <li key={index} className="border-b border-border px-[11px] py-[9px]">
          <span className="block h-[46px] animate-pulse rounded bg-surface-2" />
        </li>
      ))}
    </ul>
  );
}

export function DrillClaimsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const filters = fromSearchParams(searchParams);
  const filterKey = toFilterKey(filters);
  const list = useDrillClaims(filters);

  // `HandlerBenchmarkTable`'s one-predicate idiom and its argument: the section
  // is busy and the rows are placeholders in the same states TanStack Query
  // produces today, which is precisely why any drift between two separate
  // conditions would be silent.
  const isLoading = list.isPending && list.data === undefined;

  const [expanded, setExpanded] = useState(false);
  const firstCursor = list.data?.nextCursor ?? null;
  const pages = useDrillClaimPages(filters, firstCursor, expanded);

  // Gated on `expanded` rather than merely on whether the entry has data: an
  // expansion that expired (the base query refetched onto a new first cursor)
  // leaves its pages in the cache, and rendering them would show claims the
  // supervisor never asked to see, cut from a ranking that no longer applies.
  const extra: DrillClaimRow[] = expanded
    ? (pages.data?.pages.flatMap((page) => page.items) ?? [])
    : [];
  const rows = dedupe([...(list.data?.items ?? []), ...extra]);
  // Before the first "Show more" the server's first-page cursor is the
  // authority; afterwards the infinite query's own `hasNextPage` is — but only
  // once it has answered. `hasNextPage` is false while the first fetch is in
  // flight, so reading it too early unmounts the button on the click that
  // triggered it, and its disabled and "Loading…" states become unreachable.
  const hasMore = expanded && pages.isSuccess ? pages.hasNextPage : firstCursor !== null;

  // The server's echo where there is one, and the URL only where there can
  // never be one. A refused request (a stale link whose enum value moved:
  // `?filter[stage]=banana` → 422) returns no payload, and chips drawn only
  // from the payload vanish in exactly the state where clearing one is the fix,
  // leaving a permanent error whose only exit is the address bar.
  //
  // Gated on the 422 specifically, not on `isError` and not on the absence of
  // data. Not `isError`, because a 404 or a 500 is not a filter the supervisor
  // can clear and a chip would offer a fix that is not one. Not "no data",
  // because that also covers the in-flight render: the URL half cannot resolve
  // the two id facets to names, so a chip drawn while loading would read
  // "Employer: #2" and become "Employer: Boeing" a moment later. A degraded
  // chip is right when it is the only answer and wrong when the real one is
  // already on its way.
  const applied =
    list.data?.appliedFilters ?? (isValidationError(list.error) ? appliedFromFilters(filters) : []);
  /**
   * The one facet whose label is neither the server's nor a constant (Story 7.3).
   *
   * `ageGroup`'s wire values are ordinal words carrying no numbers at all, so
   * "Age group: 45–54" can only be composed from the document's own edges — and
   * this payload publishes them precisely so that it can be composed *here*
   * too. Before that it could not, and the consequence was one value under two
   * names one click apart: an analyst filtering by age on the workspace read
   * "45–54" on the bar and "older" on the list the chart click opened, which is
   * the thing AC 2's "survives the click as a clearable chip" is about.
   *
   * The same `ageBandLabels` the workspace bar calls, over the same three
   * integers from the same document — one composer, so the two chips cannot
   * come to disagree. Absent while the request is in flight or after a refusal,
   * where the chip falls back to the ordinal word: honest, and the alternative
   * would be a second request for a caption.
   */
  const composed = list.data === undefined ? undefined : { ageGroup: ageBandLabels(list.data) };
  /** What an empty state names: the filters the server says it applied. */
  const appliedText = applied
    .map((item) => chipLabel(item.key as FilterKey, item.value, item.display, composed))
    .join(" · ");

  /**
   * Move the URL, and let the render follow.
   *
   * A push rather than a replace, `useSelectedClaim`'s ruling: removing a chip
   * is a navigation the user performed, so Back should put it back. Collapsing
   * the accumulation with it is not optional — a different filter set is a
   * different ranking, and the pages walked under the old one describe a list
   * that no longer starts where they think it does.
   */
  function moveTo(next: URLSearchParams) {
    setExpanded(false);
    setSearchParams(next);
  }

  function removeFilter(key: FilterKey) {
    const next = toSearchParams(filters);
    next.delete(`filter[${key}]`);
    moveTo(next);
  }

  function reload() {
    // Three steps, and each is needed — `PriorityClaimsTable.reload`'s argument
    // over this list. Drop the accumulated pages, or the rejected cursor is
    // still the entry's last page param. Collapse, or the query re-enables
    // against it immediately. Invalidate the base query, because the cursor it
    // handed out is the one that was refused — only a fresh first page can
    // produce one the server will accept. Retrying is deliberately not offered:
    // it would replay the same rejected cursor for ever.
    queryClient.removeQueries({
      queryKey: queryKeys.dashboard.drillClaimPages(filterKey, firstCursor),
    });
    setExpanded(false);
    void queryClient.invalidateQueries({
      queryKey: queryKeys.dashboard.drillClaims(filterKey),
    });
  }

  return (
    <section
      data-testid="drill-claims"
      aria-labelledby="drill-claims-heading"
      aria-busy={isLoading}
      className="w-full"
    >
      <div className="mb-[10px] flex flex-wrap items-baseline justify-between gap-2">
        <h2
          id="drill-claims-heading"
          className="font-display text-[15px] font-bold text-text"
        >
          Claims
          {/* The server's number, quoted. Never `rows.length`, which is a page
              and not a population — the sentence would shrink as the reader
              scrolled. */}
          {list.data !== undefined && !list.isError && (
            <span data-testid="drill-count" className="ml-2 text-[11px] font-normal text-faint">
              {list.data.total} in view
            </span>
          )}
        </h2>
        <div className="flex items-center gap-3">
          {/* **The whole list, not the page on screen.** This surface pages with
              a cursor and the file does not: `drill_through.select` returns the
              entire ranked population, so the export is every page of it. The
              count beside the heading is what the row count in the file has to
              equal, which is how a reader checks it without opening anything. */}
          <ExportControl
            testId="drill-claims"
            label="this claim list"
            path="/dashboard/claims/export"
            params={toQueryParams(filters)}
          />
          <Link
            data-testid="drill-back"
            to={DASHBOARD_ROUTE}
            className="rounded text-[11px] font-semibold text-steel hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
          >
            ← Back to dashboard
          </Link>
        </div>
      </div>

      <FilterChips
        applied={applied}
        composed={composed}
        onRemove={removeFilter}
        onClearAll={() => moveTo(new URLSearchParams())}
      />

      {list.isError ? (
        // Inline, never a dialog (NFR-3), and in place of the list rather than
        // above an empty one: a headless list reads as "no claims match", which
        // is a different and much quieter lie than a failure.
        <p
          role="alert"
          data-testid="drill-error"
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          {isValidationError(list.error)
            ? // A 4xx is final: `createQueryClient` never retries below 500, so
              // "try again in a moment" would promise a recovery the client has
              // already ruled out. What the supervisor can do is drop the facet
              // that was refused, and the chips above this alert are how.
              "⚠ This filter is not one these claims can be narrowed by. Clear it above to see the list."
            : "⚠ These claims could not be loaded. Try again in a moment."}
        </p>
      ) : isLoading ? (
        <SkeletonRows />
      ) : rows.length === 0 ? (
        // The empty state (NFR-3), **naming the active filters** — "no claims"
        // above a screen a supervisor arrived at by clicking Litigation is a
        // sentence she cannot act on, and the one thing she needs to know is
        // which narrowing produced it.
        <p data-testid="drill-empty" className="text-[11.5px] text-faint">
          {appliedText === ""
            ? "No claims in this portfolio."
            : `No claims match ${appliedText}.`}
        </p>
      ) : (
        <>
          <ul aria-label="Claims" className="rounded-lg border border-border bg-surface">
            {rows.map((row) => (
              <ClaimCard
                key={row.claimId}
                card={row}
                // Nothing on this list is "the one you are looking at": the
                // claim view is a route of its own, so there is no selection to
                // reflect and `aria-current` would be a lie about a control.
                selected={false}
                // The list's own URL travels with the navigation, so Back
                // returns to *this* filtered list rather than to the whole
                // book. `drillHref(filters)` rather than `location.search`:
                // one builder owns the spelling, so the two cannot drift.
                onSelect={(claimId) =>
                  void navigate(claimHref(claimId), {
                    state: { from: drillHref(filters) } satisfies DrillOrigin,
                  })
                }
              />
            ))}
          </ul>

          {pages.isError ? (
            <>
              <p
                role="alert"
                data-testid="drill-page-error"
                className="px-1 py-2 text-[11px] text-error"
              >
                ⚠ The rest of this list could not be loaded.
              </p>
              <button
                type="button"
                data-testid="drill-reload"
                onClick={reload}
                className="w-full border-t border-border px-1 py-2 text-[11px] font-semibold text-steel hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
              >
                Reload this list
              </button>
            </>
          ) : (
            hasMore && (
              <button
                type="button"
                data-testid="drill-more"
                disabled={pages.isFetchingNextPage || pages.isFetching}
                onClick={() => {
                  // The first click turns the infinite query on, which fetches
                  // `initialPageParam` — the cursor the page already holds.
                  // Later clicks ask it for one more.
                  if (!expanded) setExpanded(true);
                  else void pages.fetchNextPage();
                }}
                className="w-full border-t border-border px-1 py-2 text-[11px] font-semibold text-steel hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none disabled:opacity-60"
              >
                {pages.isFetching ? "Loading…" : "Show more"}
              </button>
            )
          )}
        </>
      )}
    </section>
  );
}
