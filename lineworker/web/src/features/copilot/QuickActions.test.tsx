/**
 * The seven quick-action buttons, as a component (Story 6.4, UX-DR8).
 *
 * Three properties, and each is a way the strip could look right and be wrong:
 *
 * 1. **All seven render, with their glyphs, in the prototype's order.** A
 *    `Record` over the key union means a missing key is a build error, so what
 *    is left to assert at runtime is that the map is actually *rendered* whole —
 *    a `.slice(0, 5)` added for layout would fail here and nowhere else.
 * 2. **The key crosses the boundary, not the label.** `onPick` receives the
 *    snake_case key the server routes on and the label the run's message
 *    carries; a component that handed up its own display string would put the
 *    emoji on the wire, which the Enums convention exists to prevent.
 * 3. **Disabled while a run is in flight**, which is the client half of the
 *    thread's single-flight rule.
 *
 * What is *not* here is whether the strip is mounted, hidden on a read-only
 * thread, or wired to a run — those are `ActionsTab`'s decisions and are
 * asserted there against a stubbed API, because they are about which body
 * reaches the wire rather than about this component's markup.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { QuickActions } from "./QuickActions";
import { QUICK_ACTIONS, QUICK_ACTION_KEYS } from "./quickActionMeta";

test("all seven actions render, with their glyphs, in the prototype's order", () => {
  render(<QuickActions onPick={vi.fn()} />);

  const buttons = screen.getAllByTestId("copilot-quick-action");
  expect(buttons).toHaveLength(7);
  expect(buttons.map((button) => button.getAttribute("data-quick-action"))).toEqual([
    "laborlaw",
    "similar",
    "rtw",
    "reserve",
    "fraud",
    "nextactions",
    "data_alignment",
  ]);

  for (const key of QUICK_ACTION_KEYS) {
    const meta = QUICK_ACTIONS[key];
    const button = buttons.find((candidate) => candidate.dataset.quickAction === key);
    expect(button).toBeDefined();
    expect(button).toHaveTextContent(meta.label);
    expect(button).toHaveTextContent(meta.icon);
    expect(button).toHaveTextContent(meta.hint);
  }
});

test("the glyph is decorative, so a reader hears seven labels and not seven symbols", () => {
  // Seven buttons each announced as "section sign, Labor law and state rules"
  // is noise: the label is the accessible name and the icon repeats nothing.
  render(<QuickActions onPick={vi.fn()} />);

  const laborLaw = screen
    .getAllByTestId("copilot-quick-action")
    .find((button) => button.dataset.quickAction === "laborlaw")!;
  const glyph = laborLaw.querySelector('[aria-hidden="true"]');
  expect(glyph).not.toBeNull();
  expect(glyph).toHaveTextContent(QUICK_ACTIONS.laborlaw.icon);
});

test("a click hands up the key the server routes on, and the label the run says", () => {
  // The Enums convention, at the one boundary it is about. `✓` and "Reserve
  // review" are this file's; `reserve` is the server's, and it is the only one
  // of the three that may cross the wire as a decision.
  const picked = vi.fn();
  render(<QuickActions onPick={picked} />);

  const reserve = screen
    .getAllByTestId("copilot-quick-action")
    .find((button) => button.dataset.quickAction === "reserve")!;
  return userEvent.click(reserve).then(() => {
    expect(picked).toHaveBeenCalledTimes(1);
    expect(picked).toHaveBeenCalledWith("reserve", "Reserve review");
  });
});

test("every button is disabled while a run is in flight", async () => {
  // The thread is single-flight and the server answers a second run 409. The
  // buttons grey out so a handler never meets that refusal for something they
  // could not have known — and, more to the point, so no second POST is made.
  const picked = vi.fn();
  render(<QuickActions onPick={picked} busy />);

  for (const button of screen.getAllByTestId("copilot-quick-action")) {
    expect(button).toBeDisabled();
  }
  await userEvent.click(screen.getAllByTestId("copilot-quick-action")[0]!);
  expect(picked).not.toHaveBeenCalled();
});
