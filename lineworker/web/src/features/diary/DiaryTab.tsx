/**
 * The 📓 Diary tab and its three sub-tabs (Story 4.1, AC 1, UX-DR9).
 *
 * 📓 Notes · 📅 Meetings · ✉ Emails. Story 4.1 built Meetings, 4.2 built Notes
 * and 4.3 built Emails, so **there is no placeholder left in this pane** — the
 * `SEAMS` record this file carried, its `Exclude<>` type and the else-branch
 * that rendered it are deleted rather than emptied, which is what a seam table
 * is for: the story that fills the last entry removes the mechanism. The only
 * disabled affordance left in the copilot panel is the ⚡ Actions tab (Epic 6),
 * and that one belongs to `CopilotPane`.
 *
 * Sub-tab selection is **local UI state** (AD-9): it is not a server resource
 * and it is not shareable the way `?claim=` is. It lives in `DiaryNav`'s
 * context rather than in a `useState` here for one reason — the centre pane's
 * `meetings` deep link has to be able to switch it, and a state this component
 * owned would need an effect to hear about that. See `DiaryNav` on why the
 * context holds the state rather than an intent.
 *
 * **The email composer is mounted here rather than inside ✉ Emails**, which is
 * the one structural decision Story 4.3 made in this file. `DiaryTab` mounts
 * only the selected sub-tab, so a dialog owned by `EmailsSubTab` could not be
 * opened by the ✉ on a meeting card without dragging the pane to a sub-tab the
 * handler did not ask for. Hanging it off the panel instead keeps the
 * prototype's behaviour — its composer is a page-level modal, and the jump to
 * Emails happens *after* a send, when there is something new there to see.
 *
 * That is also why the **toast** is raised here: the send's confirmation and the
 * sub-tab switch are one consequence of one event, and splitting them across two
 * components would need the second to hear about the first.
 */
import { useToast } from "@/components/ui/toast";
import { tabKeyHandler } from "@/lib/tabKeys";

import type { DiarySubTab } from "./DiaryNav";
import { useDiaryNav } from "./DiaryNav";
import { EmailComposerDialog } from "./EmailComposerDialog";
import { EmailsSubTab } from "./EmailsSubTab";
import { PARTICIPANT_TAG_LABEL } from "./labels";
import { MeetingsSubTab } from "./MeetingsSubTab";
import { NotesSubTab } from "./NotesSubTab";

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

export function DiaryTab({
  claimId,
  workerName,
  injuryType,
}: {
  claimId: string | null;
  workerName: string | null;
  /** The selected claim's injury type, for the greeting's active-claim line. */
  injuryType: string | null;
}) {
  const { subTab, selectSubTab, composer, closeComposer, requestEmails } = useDiaryNav();
  const { push } = useToast();
  // **The keyboard half of the roles this strip declares.** It shipped with
  // `role="tablist"`, `role="tab"`, `aria-selected` and `aria-controls` and no
  // key handler and no roving `tabIndex` — which `DetailTabs` records as a
  // defect caught in an earlier review, in a docstring this file's strip was
  // written next to. Same helper, so there is one answer rather than three.
  const onKeyDown = tabKeyHandler({
    tabs: SUB_TABS.map((tab) => tab.key),
    active: subTab,
    onSelect: selectSubTab,
    testId: (tab) => `diary-subtab-${tab}`,
  });

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
            // Only the selected tab is in the tab order; the other two are
            // reached with the arrow keys — `DetailTabs`' arrangement, and the
            // half of it that has to travel with the roles.
            tabIndex={subTab === tab.key ? 0 : -1}
            onClick={() => selectSubTab(tab.key)}
            onKeyDown={onKeyDown}
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
        {subTab === "notes" ? (
          <NotesSubTab claimId={claimId} workerName={workerName} injuryType={injuryType} />
        ) : subTab === "meetings" ? (
          <MeetingsSubTab claimId={claimId} workerName={workerName} />
        ) : (
          <EmailsSubTab />
        )}
      </div>

      {/* Keyed on the session, so every open is a fresh mount and the draft is
          re-seeded — an empty letter with Employee ticked, or the meeting the
          ✉ named. Without it a handler who cancelled halfway through a
          settlement notice would re-open onto their own half-edited body,
          which is the prototype's behaviour (it clears only the subject and the
          body, and only after a *send*) and not one worth porting. */}
      <EmailComposerDialog
        key={composer.session}
        open={composer.open}
        claimId={claimId}
        workerName={workerName}
        prefill={composer.prefill}
        onClose={closeComposer}
        onSent={(recipients) => {
          // **The only toast in the flow** (UX-DR11). Refusals stay inline at
          // the control that caused them; this is the prototype's post-send
          // `alert()` in its proper form.
          push({
            tone: "ok",
            message: `✓ Email logged to: ${recipients
              .map((role) => PARTICIPANT_TAG_LABEL[role])
              .join(", ")}`,
          });
          // …and the pane switches to ✉ Emails, where the send is now first —
          // the prototype's own behaviour, and what makes the log immediately
          // visible rather than something to go and find.
          requestEmails();
        }}
      />
    </div>
  );
}
