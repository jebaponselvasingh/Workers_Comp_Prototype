/**
 * One inline-editable value in a `Kv` row — the prototype's `.editfield`,
 * with a server behind it (Story 2.3).
 *
 * Written as a shared component from the first use because it is the shape
 * every later editable surface copies: Story 2.4's severity score and body
 * diagram, Epic 3's comp-rate override, Epic 4's diary fields. What it owns
 * is the *interaction*; what it deliberately does not own is the mutation,
 * the version, or what any of it means — those belong to the card, which
 * belongs to the feature.
 *
 * **Focus, and why the draft exists.** A controlled input bound straight to
 * the payload would be rewritten under the cursor every time the case file
 * refetched — which, with a mutation invalidating the detail key, is while
 * the handler is still typing in it. So the field holds a `draft` from first
 * keystroke to commit, and shows the server's value the rest of the time.
 * That is also what makes "no full-pane re-render steals focus mid-edit"
 * true rather than hoped for: `renderDet()` redrawing the whole pane is
 * precisely the prototype behaviour React and targeted invalidation replace.
 *
 * **Commit on blur or Enter; Escape abandons.** The prototype commits on the
 * DOM's `change` event, which for a text input means exactly this. Selects
 * commit on choice, as they do there.
 *
 * **Errors render here, inline, never as a dialog** (NFR-3, UX-DR11). Two
 * kinds, and the difference is visible: a validation refusal keeps what the
 * handler typed so they can fix it, while a conflict shows the value that
 * won and says so.
 */
import * as React from "react";

import type { EditableField } from "@/api/claims";

export interface EditOption {
  value: string;
  label: string;
}

export interface FieldFeedback {
  /** `invalid` keeps the typed value; `conflict` shows the server's. */
  kind: "invalid" | "conflict" | "failed";
  message: string;
  /** What the handler typed, for the `invalid` case. */
  attempted?: string;
}

/**
 * The fields this component can be pointed at.
 *
 * Story 2.3's seven patch fields plus `severityScore`, which goes through a
 * route of its own (`PATCH /claims/{id}/severity`) rather than the patch —
 * see `services/claims/edit.py`. Spelled as a union rather than widened to
 * `string` so the element ids and test ids stay an enumerable set: every
 * `edit-*` selector in the vitest and Playwright suites names a member of
 * this type, and a typo would otherwise be a silently missing assertion.
 */
export type EditableFieldName = EditableField | "severityScore" | "compRate";

/** Bounds for a numeric field, passed to the input and enforced server-side. */
export interface NumericBounds {
  min: number;
  max: number;
  /**
   * The value granularity, when the field is not a whole number.
   *
   * Added for Story 3.1's comp rate, whose values have two decimal places
   * (66.67% of AWW). With the default step of 1 the browser marks every such
   * rate a `stepMismatch` and one click of the spinner's up arrow snaps to
   * 67, discarding the decimals the handler typed.
   *
   * **It has to be the storage granularity, not a convenient increment**
   * (code review, 2026-08-14). `stepUp()` moves to the nearest multiple of
   * `step` above the step base (`min`), so any step the stored value is not a
   * multiple of rounds the value away on the first click — a step of 0.5
   * fixes the whole-percent case and reintroduces the same bug one decimal
   * down. The comp rate passes 0.01, which is one basis point: exactly the
   * unit the column stores and `parseBasisPoints` accepts. The severity score
   * passes nothing and keeps integer steps, which is what it wants.
   */
  step?: number;
}

export function InlineEditField({
  field,
  label,
  value,
  options,
  numeric,
  saving = false,
  feedback,
  onCommit,
  width,
}: {
  field: EditableFieldName;
  label: string;
  /** The server's current value — the source of truth between edits. */
  value: string;
  /** Present → a select over a server-supplied vocabulary; absent → text. */
  options?: readonly EditOption[];
  /**
   * Present → a number input with these bounds (Story 2.4's severity score).
   *
   * The bounds are the browser's *first* refusal, not the only one: a number
   * input with `min`/`max` still submits an out-of-range value, and the
   * command answers 422 for it. What they buy is the spinner stopping at the
   * ends and a native hint before the round trip.
   */
  numeric?: NumericBounds;
  saving?: boolean;
  feedback?: FieldFeedback;
  onCommit: (next: string) => void;
  width?: number;
}) {
  const messageId = `edit-${field}-message`;
  // A refused value is shown back so the handler can correct it rather than
  // retype it; a conflict shows the server's, which is the whole point of
  // the 409 carrying fresh state.
  const shown = feedback?.kind === "invalid" ? (feedback.attempted ?? value) : value;

  const common = {
    id: `edit-${field}`,
    name: field,
    "data-testid": `edit-${field}`,
    "aria-label": label,
    "aria-invalid": feedback?.kind === "invalid" ? true : undefined,
    "aria-describedby": feedback ? messageId : undefined,
    disabled: saving,
    className: [
      "w-full rounded-[3px] border bg-surface px-[5px] py-[2px] text-right",
      "text-[11.5px] text-text outline-none",
      "focus:border-brand focus:ring-1 focus:ring-brand",
      "disabled:opacity-60",
      feedback?.kind === "invalid" ? "border-error" : "border-border",
    ].join(" "),
    style: width ? { maxWidth: `${width}px` } : undefined,
  };

  return (
    <div className="flex min-w-0 flex-col items-end gap-[2px]">
      {options ? (
        <select
          {...common}
          value={shown}
          onChange={(event) => onCommit(event.target.value)}
        >
          {/* A value the vocabulary does not contain would otherwise make the
              select silently show its first option — and committing that is
              an edit nobody made. Rendered as a disabled option instead, so
              the mismatch is visible rather than corrected by the browser. */}
          {options.some((option) => option.value === shown) ? null : (
            <option value={shown} disabled>
              {shown}
            </option>
          )}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <TextEditor
          {...common}
          type={numeric ? "number" : "text"}
          min={numeric?.min}
          max={numeric?.max}
          step={numeric?.step}
          value={shown}
          onCommit={onCommit}
        />
      )}
      {feedback && (
        <p
          id={messageId}
          data-testid={`edit-${field}-${feedback.kind}`}
          role={feedback.kind === "conflict" ? "status" : "alert"}
          className={`text-right text-[10px] ${
            feedback.kind === "conflict" ? "text-warn" : "text-error"
          }`}
        >
          {feedback.message}
        </p>
      )}
    </div>
  );
}

/**
 * The text half, split out because only it needs local draft state.
 *
 * `key`ed on the server value by its caller is deliberately *not* done: that
 * would remount the input — and drop focus — every time an edit committed.
 * Instead the draft is cleared on commit and on abandon, and the value falls
 * back to the prop.
 */
function TextEditor({
  value,
  onCommit,
  ...rest
}: {
  value: string;
  onCommit: (next: string) => void;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const [draft, setDraft] = React.useState<string | null>(null);

  const commit = (): void => {
    const next = draft;
    setDraft(null);
    // Nothing typed, or typed back to what was already there: no request.
    // The server would answer 200 to a no-op patch anyway (it writes
    // nothing), but a round trip per focus change is a round trip per focus
    // change.
    if (next !== null && next !== value) onCommit(next);
  };

  return (
    <input
      {...rest}
      value={draft ?? value}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          event.currentTarget.blur();
        } else if (event.key === "Escape") {
          event.preventDefault();
          setDraft(null);
        }
      }}
    />
  );
}
