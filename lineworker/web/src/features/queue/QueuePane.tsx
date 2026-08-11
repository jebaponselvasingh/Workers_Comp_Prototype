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
 * **Both numbers are the server's answer, not a guess.** `unfilteredTotal`
 * is the size of the caller's scoped book before the filter and
 * `filteredTotal` is what the filter left; the pair is what separates the
 * two sentences. "The groups are empty and the filter is `all`" is not the
 * same fact, and a handler with an empty book who had picked a filter was
 * being told their filter was wrong — the exact mis-diagnosis this note
 * claims to avoid. Neither number is computed here: the four group totals
 * used to be summed in this file, which is a derivation over a payload and
 * the thing AD-1 keeps server-side.
 *
 * **One sentence, not four empty sections.** When nothing is on screen the
 * pane replaces the whole list with a single line rather than rendering
 * four "No claims in this stage." repetitions under four zero chips. AC 1's
 * "four sections with counts" describes the populated case; NFR-3 asks for
 * three *distinguishable* messages, and stacking the stage message four
 * times says the same thing four times without answering which of the
 * three situations the handler is in. `QueuePane.test.tsx` asserts the
 * collapse so it reads as a decision rather than an oversight.
 *
 * (`GlossaryPanel` makes the same ordering argument for the same reason: a
 * successful, empty response must never be reported as "your search found
 * nothing".)
 */
import type { ClaimQueue, QueueFilter, Stage } from "@/api/claims";
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
  expandedStages: ReadonlySet<Stage>;
  onExpandStage: (stage: Stage) => void;
  onCollapseStage: (stage: Stage) => void;
}

export function QueuePane({
  filter,
  onFilterChange,
  expandedStages,
  onExpandStage,
  onCollapseStage,
}: QueuePaneProps) {
  const queue = useClaimQueue(filter);
  const firstClaimId = firstClaimIdOf(queue.data);
  const { selectedClaimId, select } = useSelectedClaim(firstClaimId);

  // Both counts arrive on the wire. `?? 0` is a default for "the query has
  // not answered", which is a state where neither empty message renders.
  const total = queue.data?.filteredTotal ?? 0;
  const inScope = queue.data?.unfilteredTotal ?? 0;
  const filtered = filter !== "all";
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
            expanded={expandedStages.has(stage)}
            onExpand={onExpandStage}
            onCollapse={onCollapseStage}
            selectedClaimId={selectedClaimId}
            onSelect={select}
          />
        ))
      )}
    </section>
  );
}
