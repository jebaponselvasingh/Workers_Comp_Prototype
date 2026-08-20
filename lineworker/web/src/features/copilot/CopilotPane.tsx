/**
 * The copilot panel shell — the right column of the three-pane workspace
 * (Story 4.1 built it, Story 6.3 filled it; UX-DR8).
 *
 * The prototype's `.copilot` header and tab strip (lines 504-509): a pulse dot,
 * the title, a claim-context sub-line, and two tabs. **Both tabs are live.**
 *
 * ⚡ Actions shipped `disabled` from Story 4.1 until now, behind three redundant
 * carriers of one sentence — a Radix tooltip, the button's `title`, and an
 * `sr-only` span it pointed `aria-describedby` at. All three are gone together,
 * along with the `role="presentation"` span that existed only to give a disabled
 * button something that could receive a hover. A seam is removed whole or it is
 * not removed: a leftover `title` naming an epic that has arrived is worse than
 * the original, because it tells a handler a working control does not work.
 *
 * It lives in `features/copilot/` rather than `features/diary/` because the
 * *shell* is the copilot's and the diary is a tenant of it — which is exactly
 * what this story exercised: Epic 6 filled the other tab without touching the
 * diary, and Stories 4.2 and 4.3 filled the diary without touching this file.
 *
 * **The shell reads the selected claim from the URL** (`useSelectedClaimId`),
 * not from a prop threaded through `WorkspaceShell` — the same source the queue
 * writes and the detail pane reads, which is what keeps the three panes from
 * owning each other. The case file it looks the worker's name up in is already
 * in the cache under the detail pane's key, so the sub-line and the scheduler's
 * read-only claim field cost no extra request.
 *
 * **Which tab is selected is local UI state** (AD-9), `DiaryTab`'s sub-tab rule:
 * it is not a server resource and it is not shareable the way `?claim=` is. It
 * is a `useState` here rather than a context because nothing else *owns* it —
 * the centre pane's deep links do have to reach it, and they do so by bumping
 * `DiaryNav`'s `diaryRequestSession`, which this component watches. A context
 * would have inverted that: the pane would no longer own its own tab, and the
 * two levels a deep link moves would be owned by one provider that draws
 * neither of them.
 *
 * **The strip is a real tab strip**, `DetailTabs`' arrangement: both tabs carry
 * `aria-controls`, the panel below is a `role="tabpanel"`, it names the tab that
 * selected it, and `KEYBOARD_TABS` now holds both — which is exactly what this
 * file's Story 4.1 docstring predicted it would ("Epic 6 adds `"actions"` here
 * and the pattern is already correct"). It was, and it did.
 */
import { useState } from "react";

import { useClaimDetail } from "@/api/claims";
import { tabKeyHandler } from "@/lib/tabKeys";
import { useDiaryNav } from "@/features/diary/DiaryNav";
import { DiaryTab } from "@/features/diary/DiaryTab";
import { useSelectedClaimId } from "@/features/queue/useSelectedClaim";

import { ActionsTab } from "./ActionsTab";

/**
 * The panel both tabs control, named once — `DetailTabs`' arrangement.
 *
 * One panel rather than one per tab, because only the selected tab's content is
 * ever mounted: an `aria-controls` pointing at an element that does not exist is
 * worse than a shared id, and the shared id is what the case file's six tabs
 * already do.
 */
const PANEL_ID = "copilot-panel";

/** Which of the two tabs is showing. Local UI state — see the module docstring. */
type CopilotTab = "actions" | "diary";

/** The tab a panel names as its label. */
const tabId = (tab: CopilotTab): string => `copilot-tab-${tab}`;

/**
 * The tabs a keyboard may reach — **both, since Story 6.3**.
 *
 * The strip ships the ARIA roles, so it owes the arrow keys and the roving
 * `tabIndex` that go with them (`DetailTabs` records that defect from an earlier
 * review; this strip and the diary's shipped with the same gap). While ⚡ Actions
 * was disabled this list held one entry and the handler was a well-formed no-op
 * — a `<button disabled>` is not focusable, so an arrow that "moved" to it would
 * have been a key that did nothing. Now there are two, and the pattern that was
 * already correct simply starts doing something.
 */
const KEYBOARD_TABS = ["actions", "diary"] as const;

const TABS: readonly { key: CopilotTab; label: string }[] = [
  { key: "actions", label: "⚡ Actions" },
  { key: "diary", label: "📓 Diary" },
];

export function CopilotPane() {
  const claimId = useSelectedClaimId();
  const detail = useClaimDetail(claimId);
  const workerName = detail.data?.header.workerName ?? null;
  // Read here for the same reason `workerName` is: the case file is already in
  // the cache under the detail pane's key, so the diary's greeting line costs
  // no extra request.
  const injuryType = detail.data?.header.injuryType ?? null;

  // **📓 Diary stays the tab the panel opens on**, and that is a deliberate
  // non-change rather than an oversight. ⚡ Actions is first in the strip
  // (UX-DR8's order, and the prototype's), but which tab *opens* is a separate
  // question and Story 4.1 answered it: the diary is the handler's working
  // surface, and the copilot is something they turn to.
  //
  // AD-15 is the other half of the argument. Story 6.3 falsifies exactly one
  // Epic 4 assertion — that ⚡ Actions is disabled — and amends it. Making the
  // copilot the opening tab would have falsified two more specs that have
  // nothing to do with this story (`4-2-…` and `4-3-…` both open the workspace
  // and go straight to a diary sub-tab), and "amended into its opposite" is a
  // rule about assertions a story genuinely invalidates, not a licence to
  // rewrite the ones it merely inconveniences.
  const [tab, setTab] = useState<CopilotTab>("diary");

  // **The outer half of the centre pane's deep links**, and the thing that had
  // to be built the moment this strip gained a second live tab. Story 3.5's
  // "Schedule a meeting" action and Story 4.3's post-send jump both call into
  // `DiaryNav`, which moves a *sub*-tab — and while ⚡ Actions was disabled that
  // was the whole journey, because 📓 Diary was the only tab there was. Now a
  // deep link has to move two levels, and the outer one is this component's.
  //
  // Adjusted **during render** rather than in an effect: this is React's own
  // documented pattern for "a prop changed and some state has to follow it",
  // and it re-renders before the browser paints, so a handler never sees ⚡
  // Actions flash before the diary arrives. An effect would show that flash and
  // would put the scheduler behind it for a frame.
  const { diaryRequestSession } = useDiaryNav();
  const [seenDiaryRequest, setSeenDiaryRequest] = useState(diaryRequestSession);
  if (diaryRequestSession !== seenDiaryRequest) {
    setSeenDiaryRequest(diaryRequestSession);
    setTab("diary");
  }

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
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={tabId(key)}
            data-testid={tabId(key)}
            aria-selected={tab === key}
            aria-controls={PANEL_ID}
            // Roving `tabIndex`: only the selected tab is a stop, and the arrows
            // move between them — the WAI-ARIA tabs pattern, and the reason
            // `tabKeyHandler` exists at all.
            tabIndex={tab === key ? 0 : -1}
            onClick={() => setTab(key)}
            onKeyDown={tabKeyHandler({
              tabs: KEYBOARD_TABS,
              active: tab,
              onSelect: setTab,
              testId: (value) => tabId(value),
            })}
            className={
              tab === key
                ? "flex-1 border-b-2 border-brand py-[7px] text-[11px] font-semibold text-brand"
                : "flex-1 border-b-2 border-transparent py-[7px] text-[11px] font-semibold text-muted-text"
            }
          >
            {label}
          </button>
        ))}
      </div>

      <div
        id={PANEL_ID}
        role="tabpanel"
        aria-labelledby={tabId(tab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        {tab === "actions" ? (
          <ActionsTab claimId={claimId} />
        ) : (
          <DiaryTab claimId={claimId} workerName={workerName} injuryType={injuryType} />
        )}
      </div>
    </div>
  );
}
