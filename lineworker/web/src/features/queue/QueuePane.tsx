/**
 * The handler's caseload queue — the left pane of the 3-pane workspace
 * (UX-DR3, FR-H-1, FR-Q-1/2/3).
 *
 * Owns three things and nothing else: the query, the filter, and the
 * announcement. Everything on screen was computed by `services/worklist`
 * (AD-1) — this component does not sort, does not group, does not count and
 * does not decide what "high risk" means.
 *
 * **Three empty messages, and the order they are tested in.** NFR-3 asks
 * for loading, error and empty states, but "empty" here is three different
 * facts a handler must be able to tell apart:
 *
 * - *No claims in your caseload* — the book itself is empty. Checked first,
 *   because it is true regardless of the filter, and reporting it as a
 *   filter miss would tell a new handler their filter was wrong when their
 *   assignment is.
 * - *No claims match this filter* — there are claims, none selected by the
 *   current filter. Only reachable with a filter applied.
 * - *No claims in this stage* — per group, owned by `StageGroup`.
 *
 * **Which of the first two applies is the server's answer, not a guess.**
 * `unfilteredTotal` is the size of the caller's scoped book before the
 * filter, and it is the only thing that separates the two: "the groups are
 * empty and the filter is `all`" is not the same fact, and a handler with
 * an empty book who had picked a filter was being told their filter was
 * wrong — the exact mis-diagnosis this note claims to avoid.
 *
 * (`GlossaryPanel` makes the same ordering argument for the same reason: a
 * successful, empty response must never be reported as "your search found
 * nothing".)
 */
import { useMemo } from "react";

import type { ClaimQueue, QueueFilter } from "@/api/claims";
import { STAGE_ORDER, useClaimQueue } from "@/api/claims";

import { FILTER_OPTIONS, FilterSelect } from "./FilterSelect";
import { StageGroup } from "./StageGroup";
import { useSelectedClaim } from "./useSelectedClaim";

/** The first card of the first non-empty group — what auto-select targets. */
export function firstClaimIdOf(queue: ClaimQueue | undefined): string | null {
  if (!queue) return null;
  for (const stage of STAGE_ORDER) {
    const first = queue.groups[stage].items[0];
    if (first) return first.claimId;
  }
  return null;
}

function totalOf(queue: ClaimQueue | undefined): number {
  if (!queue) return 0;
  return STAGE_ORDER.reduce((sum, stage) => sum + queue.groups[stage].total, 0);
}

function CardSkeleton() {
  return (
    <div
      data-testid="queue-skeleton"
      aria-hidden
      className="border-b border-border px-[11px] py-[9px]"
    >
      <span className="block h-[10px] w-1/3 animate-pulse rounded bg-surface-2" />
      <span className="mt-[6px] block h-[13px] w-2/3 animate-pulse rounded bg-surface-2" />
      <span className="mt-[5px] block h-[11px] w-1/2 animate-pulse rounded bg-surface-2" />
    </div>
  );
}

interface QueuePaneProps {
  filter: QueueFilter;
  onFilterChange: (filter: QueueFilter) => void;
}

export function QueuePane({ filter, onFilterChange }: QueuePaneProps) {
  const queue = useClaimQueue(filter);
  const firstClaimId = firstClaimIdOf(queue.data);
  const { selectedClaimId, select } = useSelectedClaim(firstClaimId);

  const total = useMemo(() => totalOf(queue.data), [queue.data]);
  const filtered = filter !== "all";
  // The server's count of the scoped book before the filter. Undefined only
  // while the query has not answered, where neither empty message renders.
  const inScope = queue.data?.unfilteredTotal ?? 0;
  const emptyBook = inScope === 0;

  // One line, spoken once, when the list changes under a screen reader.
  // Deliberately not the list container itself: making the cards live would
  // re-announce forty-five of them on every filter change.
  const announcement = queue.isPending
    ? "Loading your claim queue."
    : queue.isError
      ? // The error paragraph below is a `role="alert"` of its own;
        // announcing the same failure twice is worse than once.
        ""
      : total === 0
        ? emptyBook
          ? "No claims in your caseload."
          : "No claims match this filter."
        : // "1 claims" is the kind of thing a sighted user never sees and a
          // screen-reader user hears every time. And under a filter the
          // count is not the caseload — it is what the filter left, which
          // is the number a handler is actually being told about.
          `${total} ${total === 1 ? "claim" : "claims"} ${
            filtered ? "match this filter." : "in your caseload."
          }`;

  return (
    <section
      aria-label="Claim queue"
      data-testid="queue-pane"
      className="flex w-[290px] flex-shrink-0 flex-col overflow-y-auto border-r border-border bg-surface"
      aria-busy={queue.isPending}
    >
      <div className="border-b border-border px-[11px] py-2">
        <h2 className="mb-[6px] font-display text-[13px] font-bold">My Caseload</h2>
        <FilterSelect value={filter} onChange={onFilterChange} />
      </div>

      <p role="status" aria-live="polite" data-testid="queue-announcement" className="sr-only">
        {announcement}
      </p>

      {queue.isPending ? (
        Array.from({ length: 5 }, (_, index) => <CardSkeleton key={index} />)
      ) : queue.isError ? (
        // Never an empty list on failure. A queue that renders as "no
        // claims" when the server is unreachable tells a handler their
        // caseload is clear, which is the one wrong answer that sends
        // somebody home.
        <p role="alert" data-testid="queue-error" className="px-[11px] py-4 text-xs text-error">
          ⚠ Your claim queue could not be loaded. Try again in a moment.
        </p>
      ) : total === 0 && emptyBook ? (
        <p data-testid="queue-empty-scope" className="px-[11px] py-4 text-xs text-faint">
          No claims in your caseload.
        </p>
      ) : total === 0 ? (
        <p data-testid="queue-empty-filter" className="px-[11px] py-4 text-xs text-faint">
          No claims match this filter. Choose{" "}
          <span className="font-semibold">{FILTER_OPTIONS[0].label}</span> to see everything.
        </p>
      ) : (
        STAGE_ORDER.map((stage) => (
          <StageGroup
            key={stage}
            stage={stage}
            group={queue.data.groups[stage]}
            filter={filter}
            selectedClaimId={selectedClaimId}
            onSelect={select}
          />
        ))
      )}
    </section>
  );
}
