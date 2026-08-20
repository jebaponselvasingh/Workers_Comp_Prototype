/**
 * The seven quick-action buttons (Story 6.4, UX-DR8) — the prototype's `qaList`.
 *
 * A strip between the seeded greeting and the transcript, which is where the
 * prototype puts it: the greeting says what the claim is, the buttons say what
 * can be asked about it, and the answers appear underneath. Clicking one sends
 * an ordinary run on the current thread with a `quickAction` key on the body, so
 * it rides the SSE path Story 6.3 built and adds no transport.
 *
 * ## Raw `<button>`, not the shadcn `Button`
 *
 * `EmailComposerDialog`'s template chips are the precedent (`:539-557`). These
 * are two-line, left-aligned, icon-plus-label rows in a 320px column — a shape
 * `Button` has no size for, and reaching for `size="sm"` and then overriding
 * padding, alignment, height and text wrapping is how a component library stops
 * being one. Token classes throughout (`border-border`, `bg-surface`,
 * `text-text`, `text-faint`), so a palette change moves the strip with
 * everything else.
 *
 * ## Disabled while a run is in flight, and that is the client's half of AD-6
 *
 * A thread is single-flight: a second run while one is streaming is refused 409
 * by the server, deliberately and correctly. The button strip disables on
 * `busy || running` so the handler never *sees* that refusal for something they
 * could not have known — the 409 is the guarantee, and a greyed-out button is
 * the courtesy. (`Composer` takes the same prop for the same reason.)
 *
 * **Per-key degradation is Story 6.6's**, not this component's. When the model
 * server is unreachable the actions that declare `requires_llm: false` still
 * answer, and disabling exactly the affected six is that story's job — which is
 * why `disabled` here has one source and not two.
 *
 * The strip is **absent** on a superseded thread rather than disabled, which is
 * `Composer`'s rule and its reason: a greyed-out control invites a handler to
 * work out why, where an absent one under "this conversation is read-only" says
 * it outright.
 */
import { QUICK_ACTIONS, QUICK_ACTION_KEYS, type QuickActionKey } from "./quickActionMeta";

export function QuickActions({
  onPick,
  busy,
}: {
  onPick: (key: QuickActionKey, label: string) => void;
  /** Whether a run is in flight — the client half of the single-flight rule. */
  busy?: boolean;
}) {
  return (
    <div
      data-testid="copilot-quick-actions"
      // A group rather than a toolbar: these are seven independent buttons in
      // reading order, not a set with roving focus, so the browser's own Tab
      // order is the right one and a `role="toolbar"` would take it away.
      role="group"
      aria-label="Quick actions for this claim"
      className="flex flex-shrink-0 flex-col gap-[3px] border-b border-border px-2 py-2"
    >
      {QUICK_ACTION_KEYS.map((key) => {
        const meta = QUICK_ACTIONS[key];
        return (
          <button
            key={key}
            type="button"
            data-testid="copilot-quick-action"
            data-quick-action={key}
            disabled={busy}
            onClick={() => onPick(key, meta.label)}
            className="flex w-full items-start gap-2 rounded border border-border bg-surface px-2 py-1.5 text-left hover:bg-steel-soft disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span
              // Decorative: the label beside it is the accessible name, and a
              // screen reader announcing "section sign" before every one of
              // seven buttons is noise rather than information.
              aria-hidden="true"
              className="w-[14px] flex-shrink-0 text-center text-[12px] leading-[15px] text-steel"
            >
              {meta.icon}
            </span>
            <span className="min-w-0">
              <span className="block text-[11px] leading-[15px] font-semibold text-text">
                {meta.label}
              </span>
              <span className="block text-[10px] leading-[13px] text-faint">{meta.hint}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
