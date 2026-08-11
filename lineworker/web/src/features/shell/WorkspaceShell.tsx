/**
 * Handler workspace — the 3-pane layout UX-DR3 specifies: queue · case
 * detail · copilot.
 *
 * Story 2.1 fills the left pane and frames the other two. The centre names
 * the selected claim and says who fills it (Story 2.2); the right does the
 * same for the copilot (Epic 6). Both are placeholders that *say so*, rather
 * than blank columns a reader would have to test to understand.
 *
 * The filter lives here rather than inside `QueuePane` for one reason: the
 * centre pane needs the same queue payload — *and the filter it was fetched
 * under* — to say anything honest about the selected claim, and the payload
 * is keyed by filter. Both panes call `useClaimQueue(filter)` and TanStack
 * serves the second from the first's cache entry — one request, one source
 * of truth, no prop drilling of server state (AD-9).
 *
 * Stage *expansion* is here for the same reason and it is the same argument
 * one level down: a claim revealed by "Show more" is on screen, so the pane
 * that decides whether the selected claim is shown has to know which groups
 * were expanded. Held inside `StageGroup`, the two panes disagreed about a
 * claim the handler was looking at.
 */
import { useCallback, useState } from "react";

import type { LoadedClaims, QueueFilter, Stage } from "@/api/claims";
import { useClaimQueue, useLoadedClaimIds } from "@/api/claims";
import { QueuePane } from "@/features/queue/QueuePane";
import { useSelectedClaimId } from "@/features/queue/useSelectedClaim";
import { useStageExpansion } from "@/features/queue/useStageExpansion";

import { TopBar } from "./TopBar";

/**
 * What the shell can honestly say about the selected claim.
 *
 * Three answers, not two. What the workspace holds is *some pages of one
 * filter*, so "it is not in there" is only sometimes evidence that it is not
 * in the caseload:
 *
 * - `present` — it is on screen. The only positive answer, and it counts the
 *   pages "Show more" accumulated as well as the base payload: a claim the
 *   queue is rendering with a highlight on it must never be described here
 *   as not shown.
 * - `absent` — the unfiltered queue answered, every group has been read to
 *   its end, and the claim is in none of them. Then and only then is "not in
 *   this caseload" a fact.
 * - `unconfirmed` — everything else: the request is in flight, the request
 *   failed, a filter is narrowing what came back, or a group has pages
 *   nobody has asked for. A claim the handler can reach by clearing the
 *   filter or clicking "Show more" is not missing, and telling them it is
 *   sends them looking for a claim that is three feet away.
 */
type Presence = "present" | "absent" | "unconfirmed";

function presenceOf(
  claimId: string,
  filter: QueueFilter,
  answered: boolean,
  loaded: LoadedClaims,
): Presence {
  if (loaded.ids.has(claimId)) return "present";
  if (!answered) return "unconfirmed";
  // A filter narrows what came back; unfetched pages truncate it. Either
  // way the workspace is looking at a subset and cannot rule the claim out.
  if (filter !== "all") return "unconfirmed";
  if (!loaded.complete) return "unconfirmed";
  return "absent";
}

function DetailPlaceholder({
  filter,
  expandedStages,
}: {
  filter: QueueFilter;
  expandedStages: ReadonlySet<Stage>;
}) {
  const queue = useClaimQueue(filter);
  const loaded = useLoadedClaimIds(queue.data, filter, expandedStages);
  const selectedClaimId = useSelectedClaimId();
  const presence =
    selectedClaimId === null
      ? null
      : presenceOf(selectedClaimId, filter, queue.data !== undefined, loaded);

  return (
    <section
      aria-label="Claim detail"
      data-testid="detail-pane"
      className="flex-1 overflow-y-auto p-6"
    >
      {selectedClaimId === null ? (
        <p data-testid="detail-none" className="text-sm text-muted-text">
          Select a claim to open its case file.
        </p>
      ) : presence === "present" || queue.isPending ? (
        // While the queue is in flight the claim is drawn rather than
        // questioned: the id came from the URL, it is what 2.2 will render
        // against, and flashing a doubt on every reload would be noise.
        <div className="rounded-lg border border-border bg-surface p-4">
          <p data-testid="detail-selected" className="font-mono text-sm font-bold">
            {selectedClaimId}
          </p>
          <p className="mt-1 text-sm text-muted-text">
            The case header, stage stepper and Overview arrive in Story 2.2.
          </p>
        </div>
      ) : queue.isError ? (
        // A failed queue is not an absent claim. The pane beside this one
        // already alerts; this says what it means *here* — we cannot check.
        <p data-testid="detail-unchecked" className="text-sm text-muted-text">
          <span className="font-mono">{selectedClaimId}</span> could not be checked — your claim
          queue did not load.
        </p>
      ) : presence === "unconfirmed" ? (
        <p data-testid="detail-unlisted" className="text-sm text-muted-text">
          <span className="font-mono">{selectedClaimId}</span> is not in the part of your caseload
          shown here. Clear the filter, or load the rest of a stage, to look for it.
        </p>
      ) : (
        // Not a redirect and not a silent fallback to the first claim: a
        // stale link deserves an explanation, and rewriting the URL under
        // someone investigating one is how a wrong link becomes invisible.
        <p data-testid="detail-unknown" className="text-sm text-muted-text">
          <span className="font-mono">{selectedClaimId}</span> is not in this caseload.
        </p>
      )}
    </section>
  );
}

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
          <DetailPlaceholder filter={filter} expandedStages={expansion.expanded} />
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
