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
 * **Story 2.4 deleted the first seam entry, 2.5 the second, 2.6 the third, 3.3
 * the fourth and 6.2 the last**, which is what the list shape was for: filling
 * a tab removed a row from `SEAMS` and added a branch, and both were one line.
 *
 * `SEAMS` and `SeamPanel` are gone with the last entry, which this docstring
 * anticipated: a `Record` over an empty key set is a type that admits nothing
 * and a component that renders for no tab, and leaving them would have been two
 * pieces of dead code advertising a mechanism with nothing left to do. The
 * *pattern* is still on record here and in `services/worklist/actions.py`'s
 * `SEAM_REASONS`, which still has one entry (Story 6.5's RTW letter) — so a
 * later story adding a seventh tab has a worked example rather than an empty
 * shell to fill.
 *
 * `BuiltTab` went too. It existed to name the complement of `SEAMS`, and with
 * every tab built its `Exclude` was over an empty set — a type that admits
 * nothing, used to key a record with nothing in it.
 *
 * **What `BuiltTab` did carry, and what replaced it**, is the exhaustiveness
 * check. `SeamPanel` was the chain's default branch, so a seventh `TABS` entry
 * used to land somewhere that said out loud it was a tab with nothing built;
 * the first version of this deletion ended the chain in a bare `else` returning
 * `insights`, which meant a seventh tab would render the AI Insights panel and
 * nothing anywhere would say so (review of Story 6.2, L5). `panelFor` below
 * ends in `assertNever`, so the omission is a **compile** error naming the
 * missing key rather than a wrong panel at runtime.
 */
import { useState } from "react";

import { tabKeyHandler } from "@/lib/tabKeys";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "injury", label: "Injury Diagram" },
  { key: "bills", label: "Bills & Payments" },
  { key: "documents", label: "Documents & ID" },
  { key: "photos", label: "Photos" },
  { key: "insights", label: "AI Insights" },
] as const;

export type TabKey = (typeof TABS)[number]["key"];

export function DetailTabs({
  activeTab,
  onTabChange,
  children,
  injury,
  bills,
  documents,
  photos,
  insights,
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
   * The Bills & Payments tab's content (Story 3.3).
   *
   * A prop and rendered only when selected, for `injury`'s reason twice over:
   * the panel owns which row's sheet is open, *and* it fetches its own
   * payload — a schedule, two line-item lists and their totals — so mounting
   * it behind an unselected tab would put a request on every case file a
   * handler opens in order to render nothing.
   */
  bills: React.ReactNode;
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
   * The AI Insights tab's content (Story 6.2) — the seam this file's last
   * `SEAMS` entry described, filled.
   *
   * A prop and rendered only when selected, for `bills`' reason: the panel
   * fetches its own payload — four cached narratives, far more than the
   * Overview needs — so mounting it behind an unselected tab would put a
   * request on every case file a handler opens in order to render nothing.
   */
  insights: React.ReactNode;
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
  // The shared strip helper — see `lib/tabKeys.ts`. It was this file's private
  // `useTabKeys` until the copilot and diary strips turned out to need the same
  // thing and to have shipped without it.
  const onKeyDown = tabKeyHandler({
    tabs: TABS.map((tab) => tab.key),
    active: activeTab,
    onSelect: onTabChange,
    testId: (tab) => `tab-${tab}`,
  });

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
            // reached with the arrow keys `tabKeyHandler` implements. A row of
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
        {panelFor(activeTab, {
          children,
          injury,
          bills,
          documents,
          photos,
          insights,
        })}
      </div>
    </>
  );
}

/**
 * The one place a tab key becomes a panel — exhaustive by construction.
 *
 * A `switch` over `TabKey` with `assertNever` at the end rather than the ternary
 * chain this replaced, because the chain's final branch was an `else`: a seventh
 * `TABS` entry would have fallen into it and rendered the AI Insights panel,
 * with nothing in the type system, the tests or the screen to say the tab had
 * been forgotten. Now the six `TABS` entries and the six panels are checked
 * against each other at compile time, which is what `SeamPanel`'s default branch
 * used to do informally before the last seam was filled.
 */
function panelFor(
  tab: TabKey,
  panels: {
    children: React.ReactNode;
    injury: React.ReactNode;
    bills: React.ReactNode;
    documents: React.ReactNode;
    photos: React.ReactNode;
    insights: React.ReactNode;
  },
): React.ReactNode {
  switch (tab) {
    case "overview":
      return panels.children;
    case "injury":
      return panels.injury;
    case "bills":
      return panels.bills;
    case "documents":
      return panels.documents;
    case "photos":
      return panels.photos;
    case "insights":
      return panels.insights;
    default:
      return assertNever(tab);
  }
}

/**
 * "This value cannot exist" — the compile-time half of an exhaustive switch.
 *
 * Local to this module rather than in `lib/`, because it is the only
 * exhaustiveness check in the SPA today and a shared helper with one caller is a
 * shared helper nobody finds. A second caller is the moment to move it.
 *
 * It throws as well as failing to type-check, which is not redundant: the type
 * argument is erased at runtime, so a value arriving from outside TypeScript's
 * knowledge — a persisted tab key from a future version, say — still fails
 * loudly instead of rendering whichever panel the chain happened to end in.
 */
function assertNever(value: never): never {
  throw new Error(`unhandled tab key: ${String(value)}`);
}

/** The tab bar's own state, so the pane can reset it per claim. */
export function useDetailTab(): [TabKey, (tab: TabKey) => void] {
  return useState<TabKey>("overview");
}
