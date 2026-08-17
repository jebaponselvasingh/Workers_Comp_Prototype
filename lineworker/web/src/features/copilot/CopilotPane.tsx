/**
 * The copilot panel shell — the right column of the three-pane workspace
 * (Story 4.1, AC 1, UX-DR8).
 *
 * The prototype's `.copilot` header and tab strip (lines 504-509), which is
 * everything except the assistant: a pulse dot, the title, a claim-context
 * sub-line, and two tabs. **⚡ Actions renders disabled** and stays that way
 * until Epic 6 brings the assistant-ui LangGraph runtime over SSE — the epic
 * is explicit that no chat plumbing is stubbed here. 📓 Diary is the tab this
 * story fills.
 *
 * It lives in `features/copilot/` rather than `features/diary/` because the
 * *shell* is the copilot's and the diary is a tenant of it: Epic 6 fills the
 * other tab without touching the diary, and Stories 4.2 and 4.3 fill the diary
 * without touching this file.
 *
 * **The shell reads the selected claim from the URL** (`useSelectedClaimId`),
 * not from a prop threaded through `WorkspaceShell` — the same source the
 * queue writes and the detail pane reads, which is what keeps the three panes
 * from owning each other. The case file it looks the worker's name up in is
 * already in the cache under the detail pane's key, so the sub-line and the
 * scheduler's read-only claim field cost no extra request.
 *
 * **Disabled-with-a-tooltip is Story 3.5's pattern, reused verbatim**: the
 * trigger is a wrapping span rather than the button (a disabled `<button>`
 * fires no pointer events, so Radix would never see the hover), the reason is
 * also the control's `title`, and it is announced through `aria-describedby`.
 * The one adaptation is that the span is `role="presentation"` and not
 * focusable — a `role="tablist"` may only hold tabs, and the reason is on the
 * button either way.
 *
 * **The strip is a real tab strip**, `DetailTabs`' arrangement: both tabs carry
 * `aria-controls`, the diary below is a `role="tabpanel"`, and it names the
 * tab that selected it. Two tabs with nothing marked as the thing they control
 * is markup that only looks like a tab strip.
 */
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { useClaimDetail } from "@/api/claims";
import { DiaryTab } from "@/features/diary/DiaryTab";
import { useSelectedClaimId } from "@/features/queue/useSelectedClaim";

/** The sentence the ⚡ Actions tab shows until Epic 6 builds the assistant. */
export const ACTIONS_SEAM_REASON = "Copilot arrives with the AI epic — Epic 6";

const ACTIONS_TAB_ID = "copilot-actions-reason";

/**
 * The panel both tabs control, named once — `DetailTabs`' arrangement.
 *
 * One panel rather than one per tab, because only the selected tab's content
 * is ever mounted: an `aria-controls` pointing at an element that does not
 * exist is worse than a shared id, and the shared id is what the case file's
 * six tabs already do.
 */
const PANEL_ID = "copilot-panel";
const DIARY_TAB_ID = "copilot-tab-diary";

export function CopilotPane() {
  const claimId = useSelectedClaimId();
  const detail = useClaimDetail(claimId);
  const workerName = detail.data?.header.workerName ?? null;
  // Read here for the same reason `workerName` is: the case file is already in
  // the cache under the detail pane's key, so the diary's greeting line costs
  // no extra request.
  const injuryType = detail.data?.header.injuryType ?? null;

  return (
    <div data-testid="copilot" className="flex h-full min-h-0 flex-col">
      <header className="flex-shrink-0 border-b border-border p-[10px_12px_9px]">
        <h2 className="flex items-center gap-[6px] font-display text-[13px] font-bold text-text">
          {/* Decorative: the pulse says "the panel is live", which the title
              beside it already says in words. */}
          <span aria-hidden className="size-[7px] shrink-0 rounded-full bg-ok" />
          AI Adjuster Copilot
        </h2>
        <p data-testid="copilot-context" className="mt-[2px] text-[10px] text-faint">
          {claimId === null
            ? "Select a case"
            : workerName === null
              ? claimId
              : `${claimId} — ${workerName}`}
        </p>
      </header>

      <div
        role="tablist"
        aria-label="Copilot sections"
        data-testid="copilot-tabs"
        className="flex flex-shrink-0 border-b border-border"
      >
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              {/* `role="presentation"` and no `tabIndex`: a `role="tablist"`
                  may only hold tabs, and Story 3.5's wrapping span is neither
                  — it is the tooltip's trigger, because a disabled `<button>`
                  fires no pointer events. Presentational here, and out of the
                  tab order, so the strip is not a stop in front of a control
                  nobody can use; the reason itself survives without a pointer
                  on the button's `title` and `aria-describedby`, which is what
                  that pattern's redundancy is for. */}
              <span
                role="presentation"
                data-testid="copilot-actions-seam"
                className="flex-1"
              >
                <button
                  type="button"
                  role="tab"
                  id="copilot-tab-actions"
                  data-testid="copilot-tab-actions"
                  aria-selected={false}
                  aria-controls={PANEL_ID}
                  disabled
                  // Duplicated as a native tooltip so the reason survives
                  // without a pointer — Story 3.5's note.
                  title={ACTIONS_SEAM_REASON}
                  aria-describedby={ACTIONS_TAB_ID}
                  className="w-full border-b-2 border-transparent py-[7px] text-[11px] font-semibold text-muted-text disabled:cursor-not-allowed disabled:opacity-50"
                >
                  ⚡ Actions
                </button>
              </span>
            </TooltipTrigger>
            <TooltipContent data-testid="copilot-actions-reason">
              {ACTIONS_SEAM_REASON}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <button
          type="button"
          role="tab"
          id={DIARY_TAB_ID}
          data-testid="copilot-tab-diary"
          aria-selected
          aria-controls={PANEL_ID}
          // The only tab there is. It is still a `<button>` with
          // `aria-selected` rather than a heading, because Epic 6 makes the
          // strip a real two-way switch and a tab that had to be re-typed then
          // would be a second change nobody asked for.
          className="flex-1 border-b-2 border-brand py-[7px] text-[11px] font-semibold text-brand"
        >
          📓 Diary
        </button>
      </div>

      <span id={ACTIONS_TAB_ID} className="sr-only">
        {ACTIONS_SEAM_REASON}
      </span>

      <div
        id={PANEL_ID}
        role="tabpanel"
        aria-labelledby={DIARY_TAB_ID}
        className="flex min-h-0 flex-1 flex-col"
      >
        <DiaryTab claimId={claimId} workerName={workerName} injuryType={injuryType} />
      </div>
    </div>
  );
}
