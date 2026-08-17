/**
 * The 📓 Diary tab and its three sub-tabs (Story 4.1, AC 1, UX-DR9).
 *
 * 📓 Notes · 📅 Meetings · ✉ Emails, of which this story builds the middle
 * one. **The other two ship as explicit placeholders naming their stories**
 * rather than as missing tabs — `DetailTabs`' rule, and for its reasons: a
 * sub-tab bar that grew a third entry in Story 4.3 would hide the shape of the
 * pane from everyone reviewing it now, and NFR-3 asks for an explicit empty
 * state on every not-yet-built surface. "Diary notes arrive in Story 4.2" is a
 * more useful thing for a handler to read than a blank pane.
 *
 * Sub-tab selection is **local UI state** (AD-9): it is not a server resource
 * and it is not shareable the way `?claim=` is. It lives in `DiaryNav`'s
 * context rather than in a `useState` here for one reason — the centre pane's
 * `meetings` deep link has to be able to switch it, and a state this component
 * owned would need an effect to hear about that. See `DiaryNav` on why the
 * context holds the state rather than an intent.
 */
import type { DiarySubTab } from "./DiaryNav";
import { useDiaryNav } from "./DiaryNav";
import { MeetingsSubTab } from "./MeetingsSubTab";

/**
 * The panel the three sub-tabs control, named once — `DetailTabs`' shape.
 *
 * One shared id rather than three, because only the selected sub-tab's content
 * is mounted; an `aria-controls` pointing at an unmounted panel is worse than a
 * shared one.
 */
const PANEL_ID = "diary-panel";

/** The tab a panel names as its label, given which sub-tab is selected. */
const tabId = (tab: DiarySubTab): string => `diary-tab-${tab}`;

const SUB_TABS: readonly { key: DiarySubTab; label: string }[] = [
  { key: "notes", label: "📓 Notes" },
  { key: "meetings", label: "📅 Meetings" },
  { key: "emails", label: "✉ Emails" },
];

/**
 * What each unbuilt sub-tab says, and which story fills it.
 *
 * Data rather than JSX branches so the seam is a list a reviewer can read
 * against the epic — and so the story that fills one deletes an entry here.
 * `DetailTabs.SEAMS` is the same shape one pane over.
 */
const SEAMS: Record<Exclude<DiarySubTab, "meetings">, { message: string; story: string }> = {
  notes: {
    message: "Dated diary notes arrive with the notes tab.",
    story: "Story 4.2",
  },
  emails: {
    message: "The stakeholder email composer arrives with templated emails.",
    story: "Story 4.3",
  },
};

export function DiaryTab({
  claimId,
  workerName,
}: {
  claimId: string | null;
  workerName: string | null;
}) {
  const { subTab, selectSubTab } = useDiaryNav();

  return (
    <div data-testid="diary-tab" className="flex min-h-0 flex-1 flex-col">
      <div
        role="tablist"
        aria-label="Diary sections"
        data-testid="diary-subtabs"
        className="flex flex-shrink-0 border-b border-border"
      >
        {SUB_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={tabId(tab.key)}
            data-testid={`diary-subtab-${tab.key}`}
            aria-selected={subTab === tab.key}
            aria-controls={PANEL_ID}
            onClick={() => selectSubTab(tab.key)}
            className={`-mb-px flex-1 border-b-2 py-[6px] text-[11px] font-semibold ${
              subTab === tab.key
                ? "border-brand text-brand"
                : "border-transparent text-muted-text hover:text-text"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* `DetailTabs`' arrangement one pane over: the tabs name what they
          control and the content is the thing controlled. Only the selected
          sub-tab is mounted, which is why the panel is one element that
          re-labels itself rather than three. */}
      <div
        id={PANEL_ID}
        role="tabpanel"
        aria-labelledby={tabId(subTab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        {subTab === "meetings" ? (
          <MeetingsSubTab claimId={claimId} workerName={workerName} />
        ) : (
          <div className="flex-1 overflow-y-auto p-[8px_10px]">
            <p
              data-testid={`diary-empty-${subTab}`}
              data-story={SEAMS[subTab].story}
              className="rounded border border-dashed border-border bg-surface-2 p-[12px] text-center text-[11px] text-faint"
            >
              {SEAMS[subTab].message} — {SEAMS[subTab].story}.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
