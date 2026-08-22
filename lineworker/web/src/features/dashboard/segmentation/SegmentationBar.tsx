/**
 * The analyst workspace's segmentation control (FR-AN-4, Story 7.3).
 *
 * One bar above every analyst section, so the fraud panel, the trend charts and
 * Story 7.4's financial decomposition all narrow together — which is what "every
 * KPI, chart and table in the workspace recomputes under the combined filter"
 * means as a component tree rather than as a sentence. It is mounted by
 * `DashboardShell` for the analyst's sections alone; see that file for why the
 * decision lives here rather than in the frame.
 *
 * **Ten pickers and a chip row, and the two halves answer different questions.**
 * A picker offers what the caller's book *can* be sliced by; the chips say what
 * it *is* sliced by, drawn from the server's own reading of the URL. That split
 * is why the options do not shrink as filters are applied — a picker narrowed by
 * its own filter could not be used to widen one, and every narrowing would be a
 * one-way door.
 *
 * **The chips are `FilterChips`, unforked.** The drill list's chip row and this
 * one are the same component over the same `AppliedFilter` model, so a chip on
 * the workspace and the chip it becomes after a click into the list are the same
 * element with the same aria label — which is the visible form of "one
 * vocabulary". The only thing this surface adds is the composed age-band label,
 * which arrives through `chipLabel`'s optional `composed` argument because it is
 * built per response from the edges the server published.
 *
 * **A failed options request degrades the pickers and keeps the chips.** The
 * two halves fail independently because they come from different places: the
 * options are the payload, so without it there are no pickers to draw; the chips
 * are the *URL*, which is still there and is still what the four sections beside
 * this bar are folding over. A bar that removed itself on a 404 would leave every
 * figure narrowed by a filter with no visible control to clear it.
 *
 * **Nothing here computes anything.** Every option, its order, every chip, both
 * counts and the three age edges arrive decided; a picker sets a URL parameter
 * and the sections refetch. The one arithmetic in this folder is
 * `ageBands.ageBandLabels`, which turns a half-open interval into a closed one
 * for display and is argued at length there. `noDerivation.test.ts` walks
 * `features/dashboard` recursively and names every file in this folder.
 */
import { useEffect, useState } from "react";

import { useSegmentationValues, type DimensionValues } from "@/api/dashboard";
import { isValidationError, problemExtension } from "@/api/errors";

import { FilterChips, type ChipLabels } from "../drill/FilterChips";
import {
  appliedFromFilters,
  FILTER_LABEL,
  paramNameOf,
  SEGMENTATION_KEYS,
  valueLabel,
  type SegmentationKey,
} from "../drill/filters";

import { ageBandLabels } from "./ageBands";
import { useSegmentation } from "./useSegmentation";

/** The option value a picker shows when no dimension is chosen. */
const ANY = "";

/**
 * The value the URL carries, guaranteed to be an option this `<select>` has.
 *
 * `useSegmentation`'s "a `<select>` must never carry a value none of its options
 * has" applied to the ten dimensions, which is where it was missing. React
 * renders such a select as **no selection at all**, so `?filter[sector]=
 * Nonexistent` — a hand-edited URL, or a link written before an employer's last
 * claim closed — drew a chip row saying the dimension was applied, every figure
 * beside it reading zero, and the picker for that dimension reading "Any". Three
 * controls on one screen telling two stories, and the one the analyst would act
 * on ("no filter here") was the false one.
 *
 * The out-of-book value is surfaced as a **transient option** rather than
 * silently corrected, and that is the half worth arguing. Falling back to "Any"
 * would be the browser deciding the filter is not applied — but it *is* applied:
 * the server read it, narrowed by it, and published the chip. The picker's job
 * is to say what the request said. Marked in the option's text so it does not
 * read as a value the book contains, and it disappears the moment the analyst
 * chooses anything else, because nothing but this URL ever puts it back.
 *
 * `pickOption` is not reused here for the reason its own docstring gives: a
 * control's vocabulary is a closed enum the SPA holds, and a dimension's is the
 * server's — so the fallback that is right there (the section's default) is
 * exactly wrong here.
 */
function optionsWithValue(
  dimension: DimensionValues,
  value: string,
): { value: string; label: string | null; missing: boolean }[] {
  const options = dimension.values.map((option) => ({ ...option, missing: false }));
  if (value === ANY || options.some((option) => option.value === value)) return options;
  return [{ value, label: null, missing: true }, ...options];
}

/**
 * One 422's field paths, as the server sends them.
 *
 * RFC 9457 extension member, read through `problemExtension` because the value
 * crossed the network: `errors[].loc` is `["query", "filter[gender]"]`, and the
 * *last* element is the parameter name. Narrowed defensively rather than cast,
 * for that helper's recorded reason — pretending a cast is a guarantee is how a
 * truncated body becomes a crash.
 */
interface ValidationDetail {
  loc?: unknown;
}

/**
 * Which segmentation parameters the server refused, from its own answer.
 *
 * The whole of AC 5's degradation, and the reason it is read off the problem
 * document rather than guessed: the browser holds no vocabulary for these enums
 * — deliberately, `filters.ts`' standing ruling — so the only thing that knows
 * `filter[ageGroup]=25_34` is not a band is the server, and it says so by naming
 * the parameter. A client that had tried to work it out would need a second copy
 * of four enums, and would clear the wrong dimension the day one of them grew a
 * member.
 *
 * Returns only names this control owns, so a refusal about some other parameter
 * cannot silently delete a section's grain.
 */
function refusedParameters(error: unknown): string[] {
  const details = problemExtension<ValidationDetail[]>(error, "errors");
  if (!Array.isArray(details)) return [];
  const owned = new Set(SEGMENTATION_KEYS.map((key) => paramNameOf(key)));
  const refused = new Set<string>();
  for (const detail of details) {
    if (!Array.isArray(detail.loc)) continue;
    for (const part of detail.loc) {
      if (typeof part === "string" && owned.has(part)) refused.add(part);
    }
  }
  return [...refused];
}

/** The dimension a `filter[…]` parameter name belongs to, for a message. */
function labelOf(parameterName: string): string {
  const key = SEGMENTATION_KEYS.find((candidate) => paramNameOf(candidate) === parameterName);
  return key === undefined ? parameterName : FILTER_LABEL[key];
}

/**
 * The facet key a published dimension names, or `undefined`.
 *
 * The payload spells its dimensions the way the wire does (`severityBand`) and
 * this file spells them the way the URL builder does (`filter[severityBand]`),
 * which is one string with brackets around it — resolved through `paramNameOf`
 * rather than by concatenating, so the one place that owns the bracket spelling
 * stays the one place.
 */
function keyOf(wireKey: string): SegmentationKey | undefined {
  return SEGMENTATION_KEYS.find((candidate) => paramNameOf(candidate) === `filter[${wireKey}]`);
}

/**
 * One dimension's picker.
 *
 * A native `<select>` rather than the vendored combobox, and it is the same
 * choice `TrendsPage.Selector` and `FraudRateTables` made for their controls: a
 * `<select>` is keyboard-operable, screen-reader-announced and type-ahead
 * searchable with no code, and ten of them on one bar is exactly the case where
 * ten popovers would be ten focus traps. `components/ui/select.tsx` is vendored
 * and stays available for a control that needs a custom option row; a list of
 * plain strings is not that.
 *
 * The empty option is "Any", which is the *absence* of a narrowing rather than a
 * value: choosing it deletes the parameter, so the URL never carries
 * `filter[sector]=` — a blank the server would refuse and a chip nobody could
 * label.
 */
function DimensionPicker({
  dimension,
  dimensionKey,
  value,
  composed,
  onChange,
}: {
  dimension: DimensionValues;
  dimensionKey: SegmentationKey;
  value: string;
  /**
   * Labels this response decided, for the one dimension whose copy is not a
   * constant — handed straight to `valueLabel`, which is the same resolution the
   * chip beside this picker goes through.
   */
  composed: ChipLabels | undefined;
  onChange: (next: string | null) => void;
}) {
  const key = dimensionKey;
  return (
    <div className="flex flex-col gap-[3px]">
      <label
        htmlFor={`segmentation-${dimension.key}`}
        className="font-display text-[9.5px] font-bold tracking-[0.3px] text-faint uppercase"
      >
        {FILTER_LABEL[key]}
      </label>
      <select
        id={`segmentation-${dimension.key}`}
        data-testid={`segmentation-picker-${dimension.key}`}
        value={value}
        // Disabled on the *book's* options rather than on the rendered list, so
        // a picker holding nothing but an out-of-book value stays operable —
        // it is the one control that can clear that value without the address
        // bar, which is the whole reason the value is rendered at all.
        disabled={dimension.values.length === 0}
        onChange={(event) => {
          onChange(event.target.value === ANY ? null : event.target.value);
        }}
        className="max-w-[160px] rounded border border-border bg-surface px-[6px] py-[2px] text-[11px] text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none disabled:opacity-50"
      >
        <option value={ANY}>Any</option>
        {optionsWithValue(dimension, value).map((option) => (
          <option key={option.value} value={option.value}>
            {/* The same resolution the chip goes through — the server's name
                where it sent one (the employer id), this response's composed
                label next (the age band), this build's copy for the enums, and
                the stored value itself for free text. A picker offering "high"
                beside a chip reading "High" would be two vocabularies for one
                value on one screen. */}
            {valueLabel(key, option.value, option.label, composed)}
            {/* …and the transient option says what it is. Without the suffix it
                would read as a value some claim carries, which is the one thing
                it is known not to be. */}
            {option.missing ? " (no claims)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

export function SegmentationBar() {
  const { segmentation, setDimension, clearAll, clearRefused } = useSegmentation();
  const values = useSegmentationValues(segmentation);

  /**
   * Which dimensions the server refused, and therefore which were cleared (AC 5).
   *
   * **State rather than a value derived from the error**, and the difference is
   * the whole behaviour: clearing the refused parameter makes the *next* request
   * succeed, so an error-derived message would vanish on the render that proves
   * the fix worked — leaving an analyst whose pasted link silently lost a filter
   * with nothing on screen to say so. The message outlives the error that caused
   * it, and is dismissed by the analyst touching a picker, which is the moment it
   * stops being news.
   */
  const [refused, setRefused] = useState<readonly string[]>([]);
  const rejected = isValidationError(values.error) ? refusedParameters(values.error) : [];
  // **Adjusted during render, which is React's own pattern for state that has to
  // outlive the value it came from** ("You Might Not Need an Effect"): React
  // re-renders immediately without committing the first pass, so the message is
  // on screen in the same paint as the refusal. An effect would set it *after*
  // the paint that showed nothing, and a ref cannot be read during render at all.
  if (rejected.length !== 0 && rejected.map(labelOf).join(",") !== refused.join(",")) {
    setRefused(rejected.map(labelOf));
  }
  /**
   * Clearing the refused dimension is a **navigation**, so it happens in an effect.
   *
   * The fix for a URL the server will not accept is a different URL, and writing
   * one during render is a side effect React is entitled to run twice.
   */
  useEffect(() => {
    if (rejected.length !== 0) clearRefused(rejected);
    // `rejected` is rebuilt per render, so the join is what makes this effect
    // depend on the *content* rather than on the array's identity — without it a
    // stable refusal would re-run the navigation on every render.
  }, [rejected.join(","), clearRefused]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * The one facet whose label is neither the server's nor a constant.
   *
   * `ageGroup`'s members are ordinal words carrying no numbers at all, so the
   * range a reader wants is built from the three edges *this response* published
   * — which is why the map is per-response and travels as an argument to both
   * the pickers and the chips rather than living beside `FILTER_LABEL`.
   */
  const composed: ChipLabels | undefined =
    values.data === undefined ? undefined : { ageGroup: ageBandLabels(values.data) };

  /**
   * The failure that costs the analyst the pickers, and the one that does not.
   *
   * A 422 is *not* this state: it names a dimension, `clearRefused` drops it,
   * and the request that follows succeeds — so the bar keeps rendering and the
   * notice above says what was lost. Anything else (a 404, a 500, a dropped
   * connection) leaves the options unknown for as long as the analyst stays.
   */
  const optionsFailed = values.isError && !isValidationError(values.error);
  /**
   * Whether a narrowing is actually in force, read from the **URL**.
   *
   * Not from `values.data.appliedFilters`, which is the right source everywhere
   * else on this bar and is exactly the wrong one here: the case this decides is
   * the one where there is no payload. The URL is what the sections beside this
   * bar are reading, so it is what the sentence beside them must describe.
   */
  const filtered = Object.keys(segmentation).length !== 0;

  return (
    <section
      aria-label="Segmentation"
      data-testid="segmentation-bar"
      aria-busy={values.isPending}
      className="mb-[10px] rounded-lg border border-border bg-surface p-3"
    >
      {refused.length !== 0 && (
        // Inline and non-blocking (NFR-3, UX-DR11), naming the dimension rather
        // than the parameter: an analyst who pasted a stale link needs to know
        // which of their filters did not survive, and the rest of it still
        // applies — which is what the request that follows this render proves.
        <p
          role="alert"
          data-testid="segmentation-refused"
          className="mb-[8px] rounded-md border border-border bg-warn-soft px-3 py-2 text-[11.5px] font-semibold text-warn"
        >
          ⚠ {refused.join(", ")} is not a filter these claims can be narrowed by. It has been
          cleared; the rest of the filter still applies.
        </p>
      )}

      {optionsFailed && (
        // **The message, and then the chips underneath it** — never instead of
        // them. The first version of this branch returned this paragraph *in
        // place of* the fragment below, which deleted the chip row, "Clear all"
        // and all ten pickers at once. That would be survivable if the failure
        // also unfiltered the sections, and it does not: `FraudPage` and
        // `TrendsPage` call `useSegmentation()` themselves and read the filter
        // straight from the URL, so all four aggregates stay folded over the
        // intersection while the only control that could widen them is gone.
        // The way out was the address bar. The story's rule is the opposite —
        // "the chips stay clearable, or the analyst is stranded".
        //
        // And the sentence is **conditional on there being a filter**, because
        // the old one asserted "the figures below are unfiltered" — false
        // exactly when the URL carries a dimension, which is the only case where
        // it matters. An analyst would have read a narrowed subset as the book.
        <p
          role="alert"
          data-testid="segmentation-error"
          className="mb-[8px] rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          {filtered
            ? "⚠ The segmentation options could not be loaded. The filter below is still applied to every figure — clear a chip to widen the view."
            : "⚠ The segmentation options could not be loaded. No filter is applied, so the figures below are the whole portfolio."}
        </p>
      )}

      {optionsFailed ? (
        // Degraded, not absent. The *pickers* are what failed — their options
        // are the payload that did not arrive — so they are the half that goes;
        // the chips are drawn from the URL through `appliedFromFilters`, which
        // is `DrillClaimsPage`'s own answer to the same problem and for the same
        // reason: with no echo to render, the URL is what was asked, and a chip
        // with a ✕ is the only exit that is not the address bar. `display` is
        // null throughout, so an employer chip reads "#3" rather than a name —
        // honest, since nothing here can resolve one.
        filtered && (
          <FilterChips
            applied={appliedFromFilters(segmentation)}
            onRemove={(key) => {
              const dimension = keyOf(key);
              if (dimension === undefined) return;
              setRefused([]);
              setDimension(dimension, null);
            }}
            onClearAll={() => {
              setRefused([]);
              clearAll();
            }}
          />
        )
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-[10px]">
            {(values.data?.dimensions ?? []).flatMap((dimension) => {
              const key = keyOf(dimension.key);
              // A dimension the server published that this build does not know
              // is dropped rather than drawn nameless: the vocabulary is the
              // server's, and a picker labelled with a raw wire key would be a
              // control nobody can read.
              if (key === undefined) return [];
              return [
                <DimensionPicker
                  key={dimension.key}
                  dimension={dimension}
                  dimensionKey={key}
                  value={segmentation[key] ?? ANY}
                  composed={composed}
                  onChange={(next) => {
                    // Touching a picker dismisses the refusal notice: the
                    // analyst has read it and is now composing a filter of their
                    // own, and a notice about a link they arrived through would
                    // outstay its usefulness from here on.
                    setRefused([]);
                    setDimension(key, next);
                  }}
                />,
              ];
            })}

            {/* The two figures a zero-result state is read off, stated rather
                than implied — and decided on a **total** rather than on a row
                count, which is `DistributionDonut`'s rule: zero-filled
                vocabularies produce rows for nothing, so counting what is on
                screen would call a full page empty. */}
            {values.data !== undefined && (
              <p data-testid="segmentation-count" className="ml-auto text-[10px] text-faint">
                {values.data.claimsMatching} of {values.data.claimsInScope} claims match
              </p>
            )}
          </div>

          {values.data !== undefined && values.data.appliedFilters.length !== 0 && (
            <div className="mt-[8px]">
              <FilterChips
                applied={values.data.appliedFilters}
                composed={composed}
                onRemove={(key) => {
                  const dimension = keyOf(key);
                  if (dimension === undefined) return;
                  setRefused([]);
                  setDimension(dimension, null);
                }}
                onClearAll={() => {
                  setRefused([]);
                  clearAll();
                }}
              />
            </div>
          )}

          {values.data !== undefined && values.data.claimsMatching === 0 && (
            // The zero-result state on the control itself, beside the chips that
            // caused it — a reader stranded on an empty workspace needs the way
            // out in the same place as the explanation.
            //
            // **Two sentences, because there are two facts.** With no filter
            // applied there is nothing to clear, and telling an analyst whose
            // whole book is empty to "clear one to widen the view" sends her
            // looking for a filter she never set — the emptiest possible screen
            // plus an instruction that cannot be followed. Decided on the
            // *applied* list rather than on `claimsInScope`, so the two figures
            // stay what they are: a scoped analyst with a genuinely empty book
            // and an analyst who narrowed it to nothing both read `0`, and only
            // the chip row can tell them apart.
            <p data-testid="segmentation-empty" className="text-[11.5px] text-faint">
              {values.data.appliedFilters.length === 0
                ? "No claims in this portfolio."
                : "No claims match these filters. Clear one to widen the view."}
            </p>
          )}
        </>
      )}

      {/* One polite sentence when the answer lands, `FraudPage`'s rule: an
          analyst using a screen reader gets a sentence rather than ten pickers
          silently changing their options. `sr-only` because the bar is the
          visual announcement. */}
      <p role="status" aria-live="polite" className="sr-only">
        {values.isPending
          ? "Loading segmentation options."
          : values.data === undefined
            ? ""
            : `${String(values.data.claimsMatching)} of ${String(values.data.claimsInScope)} claims match the current filters.`}
      </p>
    </section>
  );
}