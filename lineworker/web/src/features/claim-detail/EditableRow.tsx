/**
 * A `Kv` row whose value is editable — the two components joined.
 *
 * `Kv` owns the label/value layout and `InlineEditField` owns the
 * interaction; this is the seam between them, written once so that the two
 * cards offering inline edits (the investigation injury card and the
 * treatment recovery window) cannot drift on which state maps to which prop.
 */
import type { EditableField } from "@/api/claims";

import { Kv } from "./Cards";
import { type EditOption, InlineEditField } from "./InlineEditField";
import type { InlineEdits } from "./useInlineEdits";

export function EditableRow({
  edits,
  field,
  label,
  value,
  options,
  width,
  onCommit,
}: {
  edits: InlineEdits;
  field: EditableField;
  label: string;
  value: string;
  options?: readonly EditOption[];
  width?: number;
  onCommit: (next: string) => void;
}) {
  return (
    <Kv label={label}>
      <InlineEditField
        field={field}
        label={label}
        value={value}
        options={options}
        width={width}
        // **Every field is disabled while any commit is in flight**, not just
        // the one being saved. `expectedVersion` is read from the cache and
        // an optimistic update does not advance it, so a second commit
        // launched before the first settles carries a version the first has
        // already consumed — the server 409s and the handler is told somebody
        // else changed the claim, about their own edit (code review).
        saving={edits.busy}
        feedback={edits.feedbackFor(field)}
        onCommit={onCommit}
      />
    </Kv>
  );
}
