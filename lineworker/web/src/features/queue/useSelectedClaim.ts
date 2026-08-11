/**
 * Which claim is selected — held in the URL, not in a store (AD-9).
 *
 * Selection is local UI state, so it never goes near TanStack Query. Putting
 * it in the query string rather than in React state buys three things a
 * `useState` would not: a handler can send "the claim I am looking at" as a
 * link, a reload lands back on it, and Story 2.2's detail pane reads the
 * same source the queue writes instead of receiving a prop through the
 * shell.
 *
 * **Auto-select replaces rather than pushes.** FR-LOGIN-3 wants the first
 * claim selected on arrival; that is not a navigation the user performed, so
 * putting it in the history stack would make Back a no-op that re-selects
 * the same claim. A *click* pushes, because that one is a navigation and
 * Back should undo it.
 *
 * **A selection the payload does not contain is left alone.** A stale link
 * (`?claim=WC-9999`, a claim that moved out of the caller's scope) shows no
 * highlight and an explanatory placeholder — deliberately not a redirect to
 * the first claim, which would rewrite the URL under someone who was trying
 * to work out why their link was wrong.
 */
import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router";

export const SELECTED_CLAIM_PARAM = "claim";

/**
 * The selected claim id, or `null` — including for `?claim=`, which
 * `URLSearchParams` reports as `""` rather than as absent.
 *
 * A blank value is a claim nobody selected: it comes from a hand-edited
 * URL or a link that lost its id, and it must behave like no selection at
 * all. Left as `""` it is truthy-adjacent enough to break both ends —
 * auto-select skips (the param "exists"), and the detail pane reports the
 * empty string as a claim that is not in the caseload.
 */
function readClaimId(searchParams: URLSearchParams): string | null {
  const raw = searchParams.get(SELECTED_CLAIM_PARAM);
  return raw === null || raw === "" ? null : raw;
}

/** Read-only access, for anything outside the queue that needs the selection. */
export function useSelectedClaimId(): string | null {
  const [searchParams] = useSearchParams();
  return readClaimId(searchParams);
}

/**
 * @param firstClaimId the first card of the first non-empty group, or `null`
 * while the queue is loading, failed, or genuinely empty. Auto-select waits
 * for a real value, so a slow request cannot leave the URL pointing at
 * nothing and an empty caseload never navigates at all.
 */
export function useSelectedClaim(firstClaimId: string | null): {
  selectedClaimId: string | null;
  select: (claimId: string) => void;
} {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedClaimId = readClaimId(searchParams);

  useEffect(() => {
    if (selectedClaimId !== null || firstClaimId === null) return;
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set(SELECTED_CLAIM_PARAM, firstClaimId);
        return next;
      },
      { replace: true },
    );
    // `searchParams` is deliberately not a dependency: the updater form
    // reads the current value itself, and depending on the object would
    // re-run this effect on every unrelated query-string change.
  }, [selectedClaimId, firstClaimId, setSearchParams]);

  const select = useCallback(
    (claimId: string) => {
      // Re-selecting the selected claim is not a navigation. Pushing it
      // anyway stacks identical entries, and Back then appears to do
      // nothing — the user clicks it once per stray click they made.
      if (claimId === selectedClaimId) return;
      setSearchParams((previous) => {
        const next = new URLSearchParams(previous);
        next.set(SELECTED_CLAIM_PARAM, claimId);
        return next;
      });
    },
    [selectedClaimId, setSearchParams],
  );

  return { selectedClaimId, select };
}
