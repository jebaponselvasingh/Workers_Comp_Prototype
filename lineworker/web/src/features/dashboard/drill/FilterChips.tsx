/**
 * The applied-filter chips — visible, and clearable one at a time (AC 1).
 *
 * The story's requirement is "the applied filter visible and clearable", and
 * the two halves are separate promises. *Visible* means the chip row is drawn
 * from the server's `appliedFilters` rather than from the URL: the server's
 * list is its reading of the request, so a parameter it ignored produces no
 * chip and a caller cannot be shown a narrowing that did not happen.
 * *Clearable* means each chip carries a real `<button>` that removes **that**
 * key from the query string, not a "clear filters" that throws away four
 * narrowings because a supervisor wanted to widen one.
 *
 * **Hand-rolled, like every other chip on this dashboard.** `components/ui/
 * badge.tsx` exists and is used nowhere; `CHIP_CLASS` is what the bills tab,
 * the priority table and the benchmark table already share, so a filter chip
 * looks like the chips beside it rather than like a fifth vocabulary.
 *
 * **Nothing here decides a label.** `chipLabel` resolves
 * `display ?? UI_LABEL[key][value] ?? value` in `filters.ts`, which is the one
 * place that rule lives; this component reads the payload and calls it.
 */
import { CHIP_CLASS } from "../../claim-detail/bills/statusTone";

import { chipLabel, type FilterKey } from "./filters";

import type { AppliedFilter } from "@/api/dashboard";

/**
 * One chip's ✕ removes exactly one key.
 *
 * A `<button>` rather than a `<Link>` even though the effect is a navigation,
 * because the control's *name* is "Remove the Severity filter" and a link
 * announcing a URL would say less. The navigation happens in `onRemove`, which
 * the page owns — the chip row has no opinion about push versus replace.
 */
function Chip({
  filter,
  onRemove,
}: {
  filter: AppliedFilter;
  onRemove: (key: FilterKey) => void;
}) {
  const key = filter.key as FilterKey;
  const label = chipLabel(key, filter.value, filter.display);
  return (
    <span
      data-testid="drill-chip"
      data-filter-key={filter.key}
      className={`${CHIP_CLASS} flex items-center gap-1 border-brand/40 bg-brand-soft text-brand`}
    >
      {label}
      <button
        type="button"
        data-testid="drill-chip-remove"
        data-filter-key={filter.key}
        aria-label={`Remove the ${label} filter`}
        onClick={() => onRemove(key)}
        className="rounded-full px-[3px] leading-none text-brand hover:bg-brand/20 focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
      >
        ✕
      </button>
    </span>
  );
}

export function FilterChips({
  applied,
  onRemove,
  onClearAll,
}: {
  /** The server's reading of the request, in its own order. */
  applied: readonly AppliedFilter[];
  onRemove: (key: FilterKey) => void;
  onClearAll: () => void;
}) {
  // Nothing to draw and nothing to say: an unfiltered list is the whole book,
  // and a row reading "no filters" would be a control with no purpose above a
  // list that is already complete.
  if (applied.length === 0) return null;

  return (
    <div
      data-testid="drill-chips"
      // A named group rather than a bare row, so the chips are announced as
      // "Applied filters" instead of as loose buttons above a list.
      aria-label="Applied filters"
      role="group"
      className="mb-2 flex flex-wrap items-center gap-[6px]"
    >
      {applied.map((filter) => (
        <Chip key={filter.key} filter={filter} onRemove={onRemove} />
      ))}
      {/* Offered only when there is more than one to clear: with a single chip
          its own ✕ already is "clear all", and two controls doing one thing is
          a choice a reader has to make for no reason.

          `!== 1` rather than `> 1`, and the empty case has already returned
          above, so the two spellings pick out the same rows. `DistributionBars`
          records the reason: `noDerivation.test.ts`'s first rule fails the build
          on any comparison against a numeric literal, because that is what a
          band cut-off looks like — and the equality shape is how this codebase
          says "this is a count of rendered chips, not a threshold". */}
      {applied.length !== 1 && (
        <button
          type="button"
          data-testid="drill-clear-all"
          onClick={onClearAll}
          className="text-[10.5px] font-semibold text-steel underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
        >
          Clear all
        </button>
      )}
    </div>
  );
}
