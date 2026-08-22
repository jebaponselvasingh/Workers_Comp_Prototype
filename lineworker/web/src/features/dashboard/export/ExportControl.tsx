/**
 * The one export affordance, mounted beside every exportable surface in the
 * analyst workspace (FR-AN-6, Story 7.5, NFR-3).
 *
 * **Two buttons, not a menu.** `SegmentationBar.tsx:157-165` already ruled that
 * the vendored `select`/popover primitives are for a control that needs a custom
 * option row, and that "a list of plain strings is not that". Two formats are not
 * that either: a dropdown would add a popover, a focus trap and a second click to
 * a choice between two words, and it would hide half the capability behind a
 * gesture. Two buttons say what they do and are one tab stop each.
 *
 * **Successes toast, refusals stay here.** `toast.tsx:28-34` says so in as many
 * words, and this control is where the rule earns its keep: a completion
 * notification is something a reader may miss — the file is already in their
 * downloads folder — while a refusal is something they must not, because the
 * next move (narrow the filter, or ask for the capability) is theirs. So the
 * failure renders in a `role="alert"` under the buttons, stays until the next
 * attempt, and describes what the server said.
 *
 * **Only the clicked format reports busy — and both may be busy at once.**
 * NFR-3's "only the affected control" taken literally: `useExport` is one
 * mutation, so a naive `isPending` would disable *both* buttons on either click
 * and — because each surface mounts its own instance — would leave the rest of
 * the workspace untouched but the sibling format inert for no reason. The
 * formats that are in flight are remembered as a **set**, which is the shape the
 * feature actually has: the sibling button is deliberately left enabled, so
 * "click CSV, then XLSX while it is still going" is not an edge case, it is the
 * documented behaviour. A single `pending: ExportFormat | null` could not
 * represent it — whichever request settled first cleared the other's busy state,
 * so the second file arrived under a button that had already stopped saying it
 * was working.
 *
 * **Each click owns its own completion.** `mutateAsync` in a `try`/`catch`/
 * `finally` rather than `mutate`'s `onSuccess`/`onError` options, and the
 * difference is not stylistic. TanStack Query's `MutationObserver.mutate`
 * removes the observer from the previous mutation and overwrites
 * `mutateOptions`, so a second click *silently detaches the first click's
 * callbacks*: a CSV refusal that arrived after an XLSX click rendered no
 * `role="alert"` at all, which is AC 4 ("the caller receives problem+json that
 * names the reason") failing without a symptom. `mutateAsync` returns the
 * promise of the mutation that call started, so a click's success and a click's
 * failure both come back to the click that made them, whatever happened
 * meanwhile.
 *
 * **It holds no rows and folds nothing.** The request carries a filter spec and
 * the server produces the file under the caller's re-resolved scope (AD-1,
 * AD-7). This component's entire contribution is a click and a label; the pull
 * it exists in spite of is that the rows are already in the query cache one
 * component away, and a CSV assembled from them is a `.map().join()` —
 * `features/queue/noDerivation.test.ts` scans this folder for exactly that.
 */
import { useState } from "react";

import {
  useExport,
  type ExportFormat,
  type ExportParams,
  type ExportPath,
} from "@/api/dashboard";
import { ApiError } from "@/api/errors";
import { useToast } from "@/components/ui/toast";

/** The two formats, in the order they are drawn — CSV first, as the default shape. */
const FORMATS: readonly { format: ExportFormat; label: string; busyLabel: string }[] = [
  { format: "csv", label: "CSV", busyLabel: "CSV…" },
  { format: "xlsx", label: "XLSX", busyLabel: "XLSX…" },
];

/**
 * What a reader is told when the server refuses, when it says nothing useful.
 *
 * A fallback rather than the only message: the two refusals this control can
 * actually meet — over the row cap, and the role gate — both arrive as problem
 * documents whose `detail` names the reason and, for the cap, the number to
 * narrow past. Replacing that with a house sentence would be throwing away the
 * only part of the answer the reader can act on.
 */
const UNEXPLAINED_FAILURE = "The export could not be produced. Try again in a moment.";

export function ExportControl({
  testId,
  label,
  path,
  params,
}: {
  /** `data-testid` stem; the group gets `${testId}-export`, the buttons `-export-csv`/`-export-xlsx`. */
  testId: string;
  /**
   * What this control exports, in the reader's words — "the flagged claim list",
   * "the fraud score distribution".
   *
   * It is the group's accessible name and the toast's subject, which is why it
   * is a phrase rather than a title: several of these are on screen at once, and
   * "Exported" heard four times says nothing about which four.
   */
  label: string;
  /** Which of the six export routes this surface's file comes from. */
  path: ExportPath;
  /**
   * The surface's current request — the same query its read hook sends.
   *
   * Passed down from the page rather than read here, `FraudDistributionCard`'s
   * composition rule: the page owns the URL and the sections receive what it
   * decided, so a control cannot come to disagree with the chart beside it about
   * which filter is being exported.
   */
  params: ExportParams;
}) {
  const exporter = useExport();
  const { push } = useToast();
  // Two pieces of local state and no server state, which is AD-9 on this
  // surface: an export produces a file rather than something to cache.
  //
  // A `Set` rather than a single format, because two of these can be in flight
  // at once by design — see the file docstring. The updates go through the
  // functional form for the ordinary reason two independent async settlements
  // require it: `setPending(next(pending))` would read a `pending` captured when
  // the click happened, so the second request to settle would restore the first
  // one's busy state.
  const [pending, setPending] = useState<ReadonlySet<ExportFormat>>(new Set());
  const [failure, setFailure] = useState<string | null>(null);

  function busy(format: ExportFormat, is: boolean) {
    setPending((current) => {
      const next = new Set(current);
      if (is) next.add(format);
      else next.delete(format);
      return next;
    });
  }

  async function run(format: ExportFormat) {
    busy(format, true);
    // Cleared on *attempt* rather than on success, so a second click does not
    // leave the previous refusal standing under a request that is in flight —
    // which would read as a failure that had just happened again.
    setFailure(null);
    try {
      await exporter.mutateAsync({ path, params, format });
      // A toast, because the download has already happened somewhere the
      // reader may not be looking — `toast.tsx`'s rule, and the honest split.
      push({ tone: "ok", message: `Exported ${label}.` });
    } catch (error: unknown) {
      // The server's own sentence where there is one: an over-cap refusal
      // names the cap and the row count, which is the whole of what the
      // reader needs to narrow the filter.
      setFailure(error instanceof ApiError ? error.problem.detail : UNEXPLAINED_FAILURE);
    } finally {
      // `finally` rather than a line in each branch: the one thing that must be
      // true of every exit from this function is that the button stops saying it
      // is working, and a third outcome added later must not be able to skip it.
      busy(format, false);
    }
  }

  return (
    <div
      // A group rather than a `<fieldset>`: there is no form here and no legend,
      // and a screen reader announcing "group, Export the flagged claim list"
      // before two buttons is exactly the amount of context this needs.
      role="group"
      aria-label={`Export ${label}`}
      data-testid={`${testId}-export`}
      className="flex flex-col items-end gap-1"
    >
      <div className="flex items-center gap-1">
        <span className="font-display text-[9.5px] font-bold tracking-[0.3px] text-faint uppercase">
          Export
        </span>
        {FORMATS.map(({ format, label: formatLabel, busyLabel }) => (
          <button
            key={format}
            type="button"
            data-testid={`${testId}-export-${format}`}
            // **This format only.** The sibling stays enabled while this one is
            // in flight, which is the observable form of "only the affected
            // control reports busy" (NFR-3).
            disabled={pending.has(format)}
            aria-busy={pending.has(format)}
            onClick={() => {
              // The promise is deliberately not awaited and deliberately not
              // `catch`ed here: `run` handles both outcomes itself and returns
              // nothing a click handler could do anything with.
              void run(format);
            }}
            className="rounded border border-border bg-surface px-[6px] py-px text-[10px] font-semibold text-muted-text hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none disabled:opacity-50"
          >
            {/* The label swap, `RefreshInsightsButton`'s idiom: a disabled button
                with unchanged text reads as broken, and a spinner beside two
                three-letter labels is more chrome than signal. */}
            {pending.has(format) ? busyLabel : formatLabel}
          </button>
        ))}
      </div>
      {failure !== null && (
        <p
          role="alert"
          data-testid={`${testId}-export-error`}
          className="max-w-[280px] text-right text-[10.5px] font-semibold text-error"
        >
          ⚠ {failure}
        </p>
      )}
    </div>
  );
}
