/**
 * Arrow / Home / End across a tab strip — the other half of roving tabindex.
 *
 * **Extracted from `features/claim-detail/DetailTabs.tsx`, where it was written
 * once and then needed three times.** That file's own docstring records the
 * defect: shipping `role="tablist"` / `role="tab"` / `aria-selected` without a
 * key handler is not merely an omission, it is a keyboard *trap* — a handler
 * tabs into the strip, lands on the selected tab, and every other tab is
 * unreachable without a mouse (WCAG 2.1.1). It was caught in the 2026-08-12
 * review of the case file's six tabs, and Story 4.1 then shipped the copilot
 * strip and Story 4.2's sub-tab strip with exactly the same markup and exactly
 * the same gap. A helper is what stops the fourth strip repeating it.
 *
 * It lives in `lib/` rather than beside any one strip because three features
 * use it now (`claim-detail`, `copilot`, `diary`) and none of them owns it. It
 * is a plain function rather than a hook — it closes over its arguments and
 * calls nothing — so nothing here depends on React beyond the event's type.
 *
 * **Focus follows selection**, which is the simple half of the WAI-ARIA tabs
 * pattern and the right one for every strip in this console: each panel is
 * rendered from data the client already holds, so activating on arrow costs
 * nothing and saves a second keystroke on every move.
 */

/**
 * Build the `onKeyDown` a strip's tabs share.
 *
 * @param tabs the strip's keys **in the order they are rendered**, and only the
 *   ones a keyboard may reach: a disabled tab is not a stop, so a strip with a
 *   seam in it (the copilot's ⚡ Actions) passes the enabled subset and the
 *   arrows simply skip it.
 * @param active which of them is selected.
 * @param onSelect what selecting one does.
 * @param testId the `data-testid` of a tab's button, used to move the caret
 *   onto the newly selected one — without that the focus is left on a tab that
 *   has just dropped out of the tab order.
 */
export function tabKeyHandler<T extends string>({
  tabs,
  active,
  onSelect,
  testId,
}: {
  tabs: readonly T[];
  active: T;
  onSelect: (tab: T) => void;
  testId: (tab: T) => string;
}): (event: React.KeyboardEvent<HTMLElement>) => void {
  return (event) => {
    const index = tabs.indexOf(active);
    const last = tabs.length - 1;
    if (index === -1 || last === -1) return;

    const next =
      event.key === "ArrowRight"
        ? tabs[index === last ? 0 : index + 1]
        : event.key === "ArrowLeft"
          ? tabs[index === 0 ? last : index - 1]
          : event.key === "Home"
            ? tabs[0]
            : event.key === "End"
              ? tabs[last]
              : undefined;

    if (next === undefined) return;
    // Left/Right own the strip once focus is inside it; without this the arrow
    // also scrolls the pane behind the tabs.
    event.preventDefault();
    onSelect(next);
    // Selection moved, so the newly selected tab is the one with `tabIndex={0}`
    // — put the caret on it, or focus is left on a tab that is no longer in the
    // tab order.
    event.currentTarget.parentElement
      ?.querySelector<HTMLElement>(`[data-testid="${testId(next)}"]`)
      ?.focus();
  };
}
