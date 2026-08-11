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
 *   group — not `items.length`, which is one page of it. Nothing here does
 *   arithmetic on either: the numbers a queue reports are the server's
 *   (AD-1), and `noDerivation.test.ts` fails a build over a subtraction.
 * - **A way to see the rest.** "Show more" appears exactly while the server
 *   reports a `nextCursor`, and the pages it loads are appended. The
 *   cursor comes back from the server; nothing here computes an offset.
 *
 * **And a way back out of a refusal.** A cursor can be rejected — a rule
 * document supersedes the one that ranked the page, and the server answers
 * 400 rather than serving a page cut from a list that no longer exists.
 * Retrying replays the same rejected cursor for ever, so the error offers
 * *Reload this stage*: drop the accumulated pages, collapse the group, and
 * invalidate the base query so a fresh first-page cursor arrives. The one
 * button that does something the retry could not.
 *
 * Expansion state is the shell's (`useStageExpansion`) because the detail
 * pane reads it too. Collapsing (`open`) is this component's and starts
 * open: a handler arriving at their caseload should see it, not four closed
 * drawers.
 */
import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type {
  ClaimCard as ClaimCardData,
  QueueFilter,
  Stage,
  StageGroup as StageGroupData,
} from "@/api/claims";
import { useStageGroupPages } from "@/api/claims";
import { queryKeys } from "@/api/queryKeys";

import { ClaimCard } from "./ClaimCard";
import { STAGE_ICON, STAGE_LABEL } from "./stageLabels";

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
  expanded: boolean;
  onExpand: (stage: Stage) => void;
  onCollapse: (stage: Stage) => void;
  selectedClaimId: string | null;
  onSelect: (claimId: string) => void;
}

export function StageGroup({
  stage,
  group,
  filter,
  expanded,
  onExpand,
  onCollapse,
  selectedClaimId,
  onSelect,
}: StageGroupProps) {
  const [open, setOpen] = useState(true);
  const queryClient = useQueryClient();
  const firstCursor = group.nextCursor ?? null;

  const pages = useStageGroupPages(filter, stage, firstCursor, expanded);
  // Gated on `expanded` rather than merely on whether the entry has data:
  // an expansion that expired (a filter change) leaves its pages in the
  // cache, and rendering them would show claims the handler never asked to
  // see — and put the detail pane, which reads the same entries under the
  // same condition, back out of step with this list.
  const extra: ClaimCardData[] = expanded
    ? (pages.data?.pages.flatMap((page) => page.items) ?? [])
    : [];
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
  const hasMore = expanded && pages.isSuccess ? pages.hasNextPage : firstCursor !== null;

  function reload() {
    // Three steps, and each is needed. Drop the accumulated pages, or the
    // rejected cursor is still the entry's last page param. Collapse, or
    // the query re-enables against it immediately. Invalidate the base
    // query, because the cursor it handed out is the one that was refused —
    // only a fresh first page can produce one the server will accept.
    queryClient.removeQueries({
      queryKey: queryKeys.claims.queuePages(filter, stage, firstCursor),
    });
    onCollapse(stage);
    void queryClient.invalidateQueries({ queryKey: queryKeys.claims.queue(filter) });
  }

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
            {pages.isError ? (
              <>
                <p
                  role="alert"
                  data-testid={`queue-group-${stage}-error`}
                  className="px-[11px] py-2 text-[11px] text-error"
                >
                  ⚠ The rest of this stage could not be loaded.
                </p>
                <button
                  type="button"
                  data-testid={`queue-group-${stage}-reload`}
                  onClick={reload}
                  className="w-full border-b border-border px-[11px] py-2 text-[11px] font-semibold text-steel hover:bg-surface-2"
                >
                  Reload this stage
                </button>
              </>
            ) : (
              hasMore && (
                <button
                  type="button"
                  data-testid={`queue-group-${stage}-more`}
                  disabled={pages.isFetchingNextPage || pages.isFetching}
                  onClick={() => {
                    // The first click turns the infinite query on, which
                    // fetches `initialPageParam` — the cursor the pane
                    // already holds. Later clicks ask it for one more.
                    if (!expanded) onExpand(stage);
                    else void pages.fetchNextPage();
                  }}
                  className="w-full border-b border-border px-[11px] py-2 text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
                >
                  {pages.isFetching ? "Loading…" : "Show more"}
                </button>
              )
            )}
          </>
        ))}
    </section>
  );
}
