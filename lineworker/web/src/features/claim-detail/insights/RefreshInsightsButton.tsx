/**
 * The tab's one control — extracted so the error branch can have it too.
 *
 * `InsightsTab`'s error branch used to be a sentence and nothing else, which
 * left a handler whose insights request had failed with no way to do the one
 * thing that fixes it: regenerate. That matters more here than on most surfaces,
 * because the most likely cause of a failed *read* is a stored narrative the
 * server can no longer validate — and a refresh overwrites exactly that row
 * (review of Story 6.2, M3).
 *
 * It is one component rather than a copied `<button>` for the usual reason and
 * one specific to this tab: the e2e spec asserts that the whole panel holds
 * exactly one button (FR-H-9 — nothing on this tab is editable), so a second
 * copy that drifted in label or testid would be a second control by any
 * assertion that counts them.
 */

export function RefreshInsightsButton({
  onRefresh,
  isPending,
}: {
  onRefresh: () => void;
  isPending: boolean;
}) {
  return (
    <button
      type="button"
      data-testid="insights-refresh"
      onClick={onRefresh}
      disabled={isPending}
      className="shrink-0 rounded border border-border bg-surface-2 px-[9px] py-[4px] text-[11px] font-semibold text-text hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
    >
      {isPending ? "Generating…" : "↻ Refresh"}
    </button>
  );
}
