/**
 * Which stage groups the handler has asked to see more of (AD-9 UI state).
 *
 * **Why it is not held inside `StageGroup`.** Expansion decides what the
 * queue pane renders *and* what the detail pane can honestly say about the
 * selected claim — a claim revealed by "Show more" is on screen, and a pane
 * that did not know about it reported the claim as not shown while the card
 * beside it sat highlighted. One piece of state, read by both panes, held
 * above both of them.
 *
 * **Why it expires with the filter, and how.** "Show more" is a statement
 * about one list of claims; a filter change makes it a different list. Held
 * as a remembered filter (`expandedFilter === filter`) it did not expire, it
 * *slept*: expand under `all`, switch away, switch back, and the group was
 * expanded again with nobody having clicked anything — re-enabling the
 * infinite query and, past its `staleTime`, refetching. `reset()` is called
 * by whoever changes the filter, in the same handler, so there is exactly
 * one place the two can get out of step and it is three lines long.
 *
 * Collapsing a *section* is a different thing and deliberately not here: it
 * is about the stage rather than its contents, so it survives a filter
 * change and lives inside `StageGroup` where it is used.
 */
import { useCallback, useMemo, useState } from "react";

import type { Stage } from "@/api/claims";

const NONE: ReadonlySet<Stage> = new Set<Stage>();

export interface StageExpansion {
  expanded: ReadonlySet<Stage>;
  expand: (stage: Stage) => void;
  /** Used on the recovery path: a refused page collapses the group. */
  collapse: (stage: Stage) => void;
  /** Every group back to its first page — what a filter change means. */
  reset: () => void;
}

export function useStageExpansion(): StageExpansion {
  const [expanded, setExpanded] = useState<ReadonlySet<Stage>>(NONE);

  const expand = useCallback((stage: Stage) => {
    setExpanded((previous) => new Set(previous).add(stage));
  }, []);

  const collapse = useCallback((stage: Stage) => {
    setExpanded((previous) => {
      const next = new Set(previous);
      next.delete(stage);
      return next;
    });
  }, []);

  const reset = useCallback(() => setExpanded(NONE), []);

  return useMemo(
    () => ({ expanded, expand, collapse, reset }),
    [expanded, expand, collapse, reset],
  );
}
