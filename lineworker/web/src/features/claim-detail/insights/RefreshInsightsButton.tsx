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
 *
 * ## It disables itself during a model outage (Story 6.6)
 *
 * `POST /claims/{id}/insights/refresh` reaches the chat client, so it is one of
 * exactly two non-copilot server surfaces that need the model at all. It has
 * answered 503 with a written problem document since Story 6.2 and still does —
 * nothing about that path changed. What changed is that **the 503 stops being
 * the discovery mechanism**: the same "disable exactly the affected input" rule
 * the copilot panel follows applies here, so a handler is told before they
 * press rather than after.
 *
 * The tab's **read** path is deliberately untouched. `GET …/insights` answers
 * 200 from a cache with four `not_generated` cards when nothing has been
 * generated, which is already the honest degraded state and needs no outage
 * handling of its own (AD-10).
 *
 * ## …and a 503 that gets through closes the window behind it
 *
 * The probe is cached server-side and polled every fifteen seconds, so there is
 * a window in which the model has just gone down and this button is still
 * enabled. A press inside it fails with the same 503 as before — the backstop is
 * unchanged — but `useRefreshInsights` then invalidates the availability query,
 * so the control disables while the handler is still looking at it rather than
 * waiting for the next poll to catch up. Otherwise the 503 would keep being the
 * discovery mechanism for as long as a handler kept pressing, which is the
 * behaviour this component was changed to stop.
 *
 * ## The query lives here rather than in `InsightsTab`
 *
 * One hook call in the leaf that needs the answer, rather than a prop threaded
 * through the tab and spelled at both of its call sites. It costs nothing:
 * `useCopilotAvailability` is a single TanStack cache entry with no claim in
 * its key, so this component and the copilot panel read the same entry and the
 * same poll — the second reader adds no request.
 */
import { useCopilotAvailability } from "@/api/copilot";

/**
 * What a disabled Refresh says, in the copilot pane's own words and tokens.
 *
 * Short where `ActionsTab`'s notice is long, because the surrounding context is
 * different: a handler looking at the Insights tab has four cached cards in
 * front of them and needs to know only why one button is grey — not what else
 * still works, which is visibly everything on the screen.
 */
const AI_UNAVAILABLE_NOTE = "AI is unavailable — cached insights are still shown.";

export function RefreshInsightsButton({
  onRefresh,
  isPending,
}: {
  onRefresh: () => void;
  isPending: boolean;
}) {
  const availability = useCopilotAvailability();
  // A pending or failed probe reads as available, `ActionsTab`'s rule: an
  // unknown state must never disable a working model, and the refresh's own
  // 503 remains the backstop for the window in which the two disagree.
  const unavailable = availability.data?.available === false;

  return (
    <div className="flex shrink-0 items-center gap-2">
      {unavailable && (
        <span
          data-testid="insights-refresh-unavailable"
          role="status"
          className="text-[10.5px] text-warn"
        >
          {AI_UNAVAILABLE_NOTE}
        </span>
      )}
      <button
        type="button"
        data-testid="insights-refresh"
        onClick={onRefresh}
        disabled={isPending || unavailable}
        className="shrink-0 rounded border border-border bg-surface-2 px-[9px] py-[4px] text-[11px] font-semibold text-text hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isPending ? "Generating…" : "↻ Refresh"}
      </button>
    </div>
  );
}
