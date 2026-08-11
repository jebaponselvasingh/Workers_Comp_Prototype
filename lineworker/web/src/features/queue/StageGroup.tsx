/**
 * One collapsible stage section — the prototype's `.qgroup` (UX-DR3).
 *
 * Header, count chip, body, and the three things a section owes the user
 * when it has nothing to show:
 *
 * - **Its own empty state.** "No claims in this stage." is a different
 *   sentence from the pane's "no claims at all" and from "nothing matches
 *   this filter" (NFR-3); the pane decides which of the other two applies,
 *   this component only ever says the stage one.
 * - **A truthful count.** The chip shows `total` — the size of the whole
 *   group — not `items.length`, which is one page of it.
 * - **A way to see the rest.** "Show more" appears exactly while the server
 *   reports a `nextCursor`, and the pages it loads are appended. The
 *   cursor comes back from the server; nothing here computes an offset.
 *
 * Both bits of local state are AD-9 UI state, and they expire differently.
 * Collapsing (`open`) is about the stage and starts open: a handler
 * arriving at their caseload should see it, not four closed drawers.
 * Expansion is about a particular list of claims, so it is held *as* the
 * filter it was asked for and expires when that changes.
 */
import { useState } from "react";

import type {
  ClaimCard as ClaimCardData,
  QueueFilter,
  Stage,
  StageGroup as StageGroupData,
} from "@/api/claims";
import { useStageGroupPages } from "@/api/claims";

import { ClaimCard } from "./ClaimCard";

/** The prototype's `STAGE_GROUPS` icons and labels (line 1133). */
export const STAGE_ICON: Record<Stage, string> = {
  intake: "📥",
  investigation: "🔍",
  treatment: "🩺",
  settled: "✅",
};

export const STAGE_LABEL: Record<Stage, string> = {
  intake: "Intake",
  investigation: "Investigation",
  treatment: "Treatment",
  settled: "Settled",
};

/** First occurrence of each claim id wins — the base page before its pages. */
function dedupe(cards: ClaimCardData[]): ClaimCardData[] {
  const seen = new Set<string>();
  const unique: ClaimCardData[] = [];
  for (const card of cards) {
    if (seen.has(card.claimId)) continue;
    seen.add(card.claimId);
    unique.push(card);
  }
  return unique;
}

interface StageGroupProps {
  stage: Stage;
  group: StageGroupData;
  filter: QueueFilter;
  selectedClaimId: string | null;
  onSelect: (claimId: string) => void;
}

export function StageGroup({
  stage,
  group,
  filter,
  selectedClaimId,
  onSelect,
}: StageGroupProps) {
  const [open, setOpen] = useState(true);
  // *Which list* the user asked to see more of, rather than a bare boolean.
  // "Show more" is a statement about one filter's claims, and a filter
  // change makes it a different list — so expansion has to expire with it,
  // or the new filter's group fetches its second page the instant it
  // renders with nobody having clicked anything. Derived rather than reset
  // in an effect on purpose: an effect runs *after* the render that already
  // enabled the query, so the unwanted fetch is in flight (and cached)
  // before the reset lands. Collapse state (`open`) deliberately survives a
  // filter change; that one is about the stage, not about its contents.
  const [expandedFilter, setExpandedFilter] = useState<QueueFilter | null>(null);
  const expanded = expandedFilter === filter;

  const pages = useStageGroupPages(filter, stage, group.nextCursor ?? null, expanded);
  const extra: ClaimCardData[] = pages.data?.pages.flatMap((page) => page.items) ?? [];
  // Deduped by claim id, because the two sources can overlap: the base
  // query refetches (staleness, a window refocus) while accumulated pages
  // sit beside it, and a card in both would be two React children with one
  // key — a warning at best and a dropped row at worst.
  const items = dedupe([...group.items, ...extra]);
  // Before the first "Show more" the server's first-page cursor is the
  // authority; afterwards the infinite query's own `hasNextPage` is — but
  // only once it has answered. `hasNextPage` is false while the first fetch
  // is in flight, so reading it too early unmounts the button on the click
  // that triggered it, and its `disabled` and "Loading…" states become
  // unreachable states nobody can see.
  const hasMore = expanded && pages.isSuccess ? pages.hasNextPage : group.nextCursor !== null;
  // Clamped: the same refetch overlap can leave `items` longer than the
  // `total` the base query last reported, and "Show more (-2)" is a number
  // no server ever sent.
  const remaining = Math.max(0, group.total - items.length);

  return (
    <section data-testid={`queue-group-${stage}`}>
      <h3>
        <button
          type="button"
          data-testid={`queue-group-${stage}-header`}
          aria-expanded={open}
          onClick={() => setOpen((previous) => !previous)}
          className="flex w-full items-center justify-between border-y border-border bg-surface-2 px-[11px] py-2 text-[10.5px] font-bold tracking-[0.3px] text-muted-text uppercase"
        >
          <span>
            <span aria-hidden>{STAGE_ICON[stage]} </span>
            {STAGE_LABEL[stage]}
          </span>
          <span
            data-testid={`queue-group-${stage}-count`}
            className="rounded-[10px] bg-border px-2 py-px text-[10px] font-bold text-text"
          >
            {group.total}
          </span>
        </button>
      </h3>

      {open &&
        (items.length === 0 ? (
          <p
            data-testid={`queue-group-${stage}-empty`}
            className="px-1 py-2.5 text-center text-[11px] text-faint"
          >
            No claims in this stage.
          </p>
        ) : (
          <>
            <ul aria-label={`${STAGE_LABEL[stage]} claims`}>
              {items.map((card) => (
                <ClaimCard
                  key={card.claimId}
                  card={card}
                  selected={card.claimId === selectedClaimId}
                  onSelect={onSelect}
                />
              ))}
            </ul>
            {hasMore && (
              <button
                type="button"
                data-testid={`queue-group-${stage}-more`}
                disabled={pages.isFetchingNextPage || pages.isFetching}
                onClick={() => {
                  // The first click turns the infinite query on, which
                  // fetches `initialPageParam` — the cursor the pane
                  // already holds. Later clicks ask it for one more.
                  if (!expanded) setExpandedFilter(filter);
                  else void pages.fetchNextPage();
                }}
                className="w-full border-b border-border px-[11px] py-2 text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
              >
                {pages.isFetching ? "Loading…" : `Show more (${remaining})`}
              </button>
            )}
            {pages.isError && (
              <p
                role="alert"
                data-testid={`queue-group-${stage}-error`}
                className="px-[11px] py-2 text-[11px] text-error"
              >
                ⚠ The rest of this stage could not be loaded.
              </p>
            )}
          </>
        ))}
    </section>
  );
}
