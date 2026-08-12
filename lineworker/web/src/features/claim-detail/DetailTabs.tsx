/**
 * The six-tab case-file bar (UX-DR5) and the five cross-story empty states
 * behind it.
 *
 * **Every tab exists from this story, and five of them say what they are
 * waiting for.** The alternative — shipping one tab now and adding the
 * others as their stories land — hides the shape of the case file from the
 * people reviewing it and makes each later story a layout change as well as
 * a feature. NFR-3 asks for an explicit empty state on every not-yet-built
 * surface, and "arrives with the financial engine" is a more useful thing
 * for a handler to read than a blank pane or a missing tab.
 *
 * Tab selection is **local UI state** (AD-9): it is not a server resource
 * and it is not shareable the way the selected claim is, so it lives in a
 * `useState` here rather than in the URL beside `?claim=`. It resets when
 * the selected claim changes — see `ClaimDetailPane`, which keys this
 * component by claim id; a handler who opened Documents on one claim and
 * then clicked another is looking at a different case file, and landing on
 * that file's Documents tab implies a continuity that is not there.
 *
 * **The Photos label carries a count, and it is the server's** (Story 2.6,
 * UX-DR5). The prototype renders `Photos (${c.photos.length})` from the same
 * global object its grid maps, which is consistent because there is only one
 * of it. Here the label and the grid are different components, so the number
 * comes from the payload's `photos.count` — computed in exactly one place, on
 * the server — rather than from the length of a list this component would
 * otherwise have to be handed for no other reason.
 *
 * It is also why the count is a **prop and not a read inside the panel**: the
 * panels are mounted only while selected, so a label sourced from the Photos
 * panel would render `Photos ()` until somebody clicked it.
 *
 * **Story 2.4 deleted the first seam entry, 2.5 the second and 2.6 the
 * third**, which is what the list shape was for: filling a tab removes a row
 * from `SEAMS` and adds a branch, and both are one line. Two remain — the two
 * that belong to other epics.
 */
import { useState } from "react";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "injury", label: "Injury Diagram" },
  { key: "bills", label: "Bills & Payments" },
  { key: "documents", label: "Documents & ID" },
  { key: "photos", label: "Photos" },
  { key: "insights", label: "AI Insights" },
] as const;

export type TabKey = (typeof TABS)[number]["key"];

/**
 * What each unbuilt tab says, and which story fills it.
 *
 * Written as data rather than as five JSX branches so the seam is a list a
 * reviewer can read against the epic — and so the story that fills one
 * deletes an entry here rather than hunting for a paragraph.
 */
type BuiltTab = "overview" | "injury" | "documents" | "photos";

const SEAMS: Record<Exclude<TabKey, BuiltTab>, { message: string; story: string }> = {
  bills: {
    message: "Financial detail arrives with the financial engine.",
    story: "Epic 3",
  },
  insights: {
    message: "AI insights arrive with the copilot.",
    story: "Epic 6",
  },
};

function SeamPanel({ tab }: { tab: Exclude<TabKey, BuiltTab> }) {
  const seam = SEAMS[tab];
  return (
    <div
      data-testid={`tab-empty-${tab}`}
      data-story={seam.story}
      className="rounded-lg border border-dashed border-border bg-surface p-6 text-center text-[11.5px] text-faint"
    >
      {seam.message}
    </div>
  );
}

/**
 * Arrow / Home / End across the tab strip — the other half of roving tabindex.
 *
 * Without this, `tabIndex={-1}` on the unselected tabs is not a keyboard
 * *pattern*, it is a keyboard *trap*: a handler tabs into the strip, lands on
 * the selected tab, and the remaining five are unreachable without a mouse
 * (WCAG 2.1.1). That is exactly the state this component shipped in, under a
 * comment promising the behaviour — found in the 2026-08-12 code review.
 *
 * Focus follows selection, which is the simple half of the WAI-ARIA tabs
 * pattern and the right one here: every panel is already rendered from data
 * the client holds, so activating on arrow costs nothing and saves a second
 * keystroke on every move.
 */
function useTabKeys(activeTab: TabKey, onTabChange: (tab: TabKey) => void) {
  return (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const index = TABS.findIndex((tab) => tab.key === activeTab);
    const last = TABS.length - 1;

    const next =
      event.key === "ArrowRight"
        ? TABS[index === last ? 0 : index + 1]
        : event.key === "ArrowLeft"
          ? TABS[index === 0 ? last : index - 1]
          : event.key === "Home"
            ? TABS[0]
            : event.key === "End"
              ? TABS[last]
              : undefined;

    if (!next) return;
    // Left/Right own the strip once focus is inside it; without this the
    // arrow also scrolls the pane behind the tabs.
    event.preventDefault();
    onTabChange(next.key);
    // Selection moved, so the newly selected tab is the one with
    // `tabIndex={0}` — put the caret on it or focus is left on a tab that
    // has just dropped out of the tab order.
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`[data-testid="tab-${next.key}"]`)
      ?.focus();
  };
}

export function DetailTabs({
  activeTab,
  onTabChange,
  children,
  injury,
  documents,
  photos,
  photoCount,
}: {
  activeTab: TabKey;
  onTabChange: (tab: TabKey) => void;
  /** The Overview tab's content. */
  children: React.ReactNode;
  /**
   * The Injury Diagram tab's content (Story 2.4).
   *
   * A prop rather than a second child, and rendered only when its tab is
   * selected: the diagram's popover holds three mutations and a form's
   * worth of local state, and mounting it behind an unselected tab would
   * keep that state alive across a tab the handler is not looking at.
   */
  injury: React.ReactNode;
  /**
   * The Documents & ID tab's content (Story 2.5).
   *
   * A prop and rendered only when selected, for `injury`'s reason: the panel
   * owns which document the viewer has open, and mounting it behind an
   * unselected tab would keep a modal's state alive across a tab the handler
   * is not looking at.
   */
  documents: React.ReactNode;
  /**
   * The Photos tab's content (Story 2.6) — a prop for `documents`' reason: the
   * panel owns which photo the viewer has open.
   */
  photos: React.ReactNode;
  /**
   * The number in the Photos label, from the case file's `photos.count`.
   *
   * A number rather than the list, so this component cannot be tempted to
   * count: "how many photos does this claim have" is answered once, on the
   * server, and the label is a place it is displayed rather than a second
   * place it is decided.
   */
  photoCount: number;
}) {
  const onKeyDown = useTabKeys(activeTab, onTabChange);

  return (
    <>
      <div
        role="tablist"
        aria-label="Case file sections"
        data-testid="detail-tabs"
        className="mb-[10px] flex flex-wrap gap-1 border-b border-border"
      >
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={`tab-${tab.key}`}
            data-testid={`tab-${tab.key}`}
            aria-selected={activeTab === tab.key}
            aria-controls="tab-panel"
            // Only the selected tab is in the tab order; the rest are
            // reached with the arrow keys `useTabKeys` implements. A row of
            // six tab stops in front of the case file is the
            // accessible-looking version of the same markup, and it is worse
            // to use — but it is strictly better than this pattern with the
            // handler missing, which is how this shipped.
            tabIndex={activeTab === tab.key ? 0 : -1}
            onClick={() => onTabChange(tab.key)}
            onKeyDown={onKeyDown}
            className={`-mb-px border-b-2 px-[10px] py-[6px] text-[11.5px] ${
              activeTab === tab.key
                ? "border-brand font-bold text-text"
                : "border-transparent text-muted-text hover:text-text"
            }`}
          >
            {tab.key === "photos" ? `${tab.label} (${photoCount})` : tab.label}
          </button>
        ))}
      </div>

      <div id="tab-panel" role="tabpanel" aria-labelledby={`tab-${activeTab}`}>
        {activeTab === "overview" ? (
          children
        ) : activeTab === "injury" ? (
          injury
        ) : activeTab === "documents" ? (
          documents
        ) : activeTab === "photos" ? (
          photos
        ) : (
          <SeamPanel tab={activeTab} />
        )}
      </div>
    </>
  );
}

/** The tab bar's own state, so the pane can reset it per claim. */
export function useDetailTab(): [TabKey, (tab: TabKey) => void] {
  return useState<TabKey>("overview");
}
