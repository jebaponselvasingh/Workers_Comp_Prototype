/**
 * Handler workspace — the 3-pane layout UX-DR3 specifies: queue · case
 * detail · copilot.
 *
 * Story 2.1 filled the left pane and framed the other two. Story 2.2 fills
 * the centre with the real case file; the right still names its story (Epic
 * 6) rather than sitting blank.
 *
 * **The shell no longer knows anything about the selected claim.** Story 2.1
 * had to: the queue payload was the only evidence available, so this file
 * held a three-state `presenceOf` — present, absent, unconfirmed — because a
 * filtered or partially-paged queue cannot rule a claim out. `GET
 * /claims/{id}` can, so `ClaimDetailPane` asks the server and this file went
 * back to being a layout. `useLoadedClaimIds`, which existed only to feed
 * that guess, went with it: two answers to "is this claim in my caseload" is
 * the disagreement AD-10 exists to prevent, and the one being retired is the
 * one that guessed.
 *
 * What stays here is what is genuinely shared between the panes: the filter
 * (the queue payload is keyed by it) and stage expansion (a claim revealed
 * by "Show more" is on screen, and `StageGroup` holding that state made two
 * components disagree about a claim the handler was looking at).
 */
import { useCallback, useState } from "react";

import type { QueueFilter } from "@/api/claims";
import { ClaimDetailPane } from "@/features/claim-detail/ClaimDetailPane";
import { QueuePane } from "@/features/queue/QueuePane";
import { useStageExpansion } from "@/features/queue/useStageExpansion";

import { TopBar } from "./TopBar";

export function WorkspaceShell() {
  const [filter, setFilter] = useState<QueueFilter>("all");
  const expansion = useStageExpansion();

  // The one place a filter changes, and therefore the one place expansion
  // expires. "Show more" is a statement about one list of claims; the new
  // filter is a different list, and a group that carried its expansion
  // across would fetch a second page with nobody having clicked anything.
  const { reset } = expansion;
  const changeFilter = useCallback(
    (next: QueueFilter) => {
      setFilter(next);
      reset();
    },
    [reset],
  );

  return (
    <div className="flex h-screen flex-col">
      <TopBar />
      <main className="flex min-h-0 flex-1">
        {/* The accessible name `App.test.tsx` and the 1.3 specs look for.
            It stays on the row rather than moving to one pane: "the claim
            workspace" is the three of them together. */}
        <section aria-label="Claim workspace" className="flex min-h-0 flex-1">
          <QueuePane
            filter={filter}
            onFilterChange={changeFilter}
            expandedStages={expansion.expanded}
            onExpandStage={expansion.expand}
            onCollapseStage={expansion.collapse}
          />
          <ClaimDetailPane />
          <aside
            aria-label="Copilot"
            data-testid="copilot-pane"
            className="hidden w-[300px] flex-shrink-0 overflow-y-auto border-l border-border bg-surface p-4 text-sm text-muted-text xl:block"
          >
            The AI copilot arrives in Epic 6.
          </aside>
        </section>
      </main>
    </div>
  );
}
