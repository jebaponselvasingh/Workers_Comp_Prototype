/**
 * The analyst workspace's address bar, in one hook (Story 7.3, AC 3).
 *
 * Stories 7.1 and 7.2 both put their controls in component state and both wrote
 * down why: three sort parameters and three selector parameters would each have
 * tripled the surface area of a shareable link for a preference carrying no
 * data, and **this story owns what belongs in this workspace's query string**.
 * Deciding that twice — once for sorts, once for filters — is how two schemes end
 * up in one address bar, so the decision is made here for all of it:
 *
 * - **`filter[<camelKey>]` is reserved for segmentation**, spelled exactly as
 *   the drill list spells it (`drill/filters.ts`), so the workspace URL and the
 *   drill URL differ by a merge rather than by a translation.
 * - **Section controls are bare named parameters** under the API's own names —
 *   `grain`, `anchor`, `cohort`, `sort[injuryType]`, `sort[employer]`,
 *   `sort[handler]`. The `sort[…]` brackets are not an exception to "bare": they
 *   are what the route calls those parameters, and `filters.ts` records that the
 *   URL uses the API's own aliases precisely so no translation layer exists.
 *
 * **Every write preserves everything it did not touch.** A filter change must
 * not clear a grain and a grain change must not clear a filter, so each mutation
 * starts from the current `URLSearchParams` and edits one key — which also means
 * an unknown parameter somebody appended survives, exactly as the server ignores
 * it.
 *
 * **Nothing here validates a value.** A vocabulary check in the browser would be
 * a second copy of an enum that changes on the server, and the first copy to go
 * stale — `filters.ts`' standing ruling. What this hook does instead is
 * *degrade*: `clearRefused` takes the parameter names the server's 422 named and
 * removes exactly those, which is AC 5's "that dimension clears, the rest still
 * applies" as a function of the server's own answer rather than of a guess.
 *
 * **Nothing here computes anything.** It reads a `URLSearchParams` and writes
 * one. `noDerivation.test.ts` walks `features/dashboard` recursively and names
 * this file.
 */
import { useCallback, useMemo } from "react";

import { useSearchParams } from "react-router";

import {
  SEGMENTATION_KEYS,
  paramNameOf,
  type DrillFilters,
  type SegmentationKey,
} from "../drill/filters";

/**
 * The section controls the workspace's URL carries, under the API's own names.
 *
 * Declared as a closed union rather than left as `string` so a control cannot be
 * written to the URL under a name no route reads — which would be a preference
 * that survived a reload and changed nothing, the quietest possible bug in a
 * shareable link.
 */
export type ControlName =
  | "grain"
  | "anchor"
  | "cohort"
  | "sort[injuryType]"
  | "sort[employer]"
  | "sort[handler]";

export interface WorkspaceUrl {
  /** The active segmentation, as the URL spells it. */
  segmentation: DrillFilters;
  /** Set one dimension, or clear it with `null`. */
  setDimension: (key: SegmentationKey, value: string | null) => void;
  /** Clear every dimension, leaving the section controls where they are. */
  clearAll: () => void;
  /** One section control's value, or `null` when the URL does not carry it. */
  control: (name: ControlName) => string | null;
  setControl: (name: ControlName, value: string) => void;
  /**
   * Drop the dimensions the server refused, keeping everything else.
   *
   * Takes **query parameter names** (`filter[gender]`) rather than facet keys,
   * because that is what a 422's `errors[].loc` carries — the caller hands over
   * the server's own answer and this hook does not re-derive which dimension it
   * described.
   */
  clearRefused: (parameterNames: readonly string[]) => void;
}

/**
 * The segmentation dimensions present in a `URLSearchParams`, in chip order.
 *
 * `fromSearchParams`' rule restricted to the ten this control owns: `""` is
 * treated as absent — a blank value comes from a hand-edited URL or a link that
 * lost its value, and `filter[sector]=` is a dimension nobody chose which would
 * draw a chip with no label and send a parameter the server would refuse.
 *
 * The *drill list's* fourteen other facets are deliberately not read here: a
 * workspace URL carrying `filter[stage]=settled` is carrying a parameter this
 * control does not own, and reading it would put a chip on the bar that no
 * picker could clear.
 */
function segmentationFrom(params: URLSearchParams): DrillFilters {
  const filters: DrillFilters = {};
  for (const key of SEGMENTATION_KEYS) {
    const raw = params.get(paramNameOf(key));
    if (raw !== null && raw !== "") filters[key] = raw;
  }
  return filters;
}

export function useSegmentation(): WorkspaceUrl {
  const [searchParams, setSearchParams] = useSearchParams();

  const segmentation = useMemo(() => segmentationFrom(searchParams), [searchParams]);

  /**
   * Edit one key of the current query string and navigate to the result.
   *
   * A **replace** rather than a push, which is the opposite of
   * `DrillClaimsPage.moveTo`'s ruling and deliberately so: removing a chip
   * *there* is a navigation into a different list, while narrowing here is
   * adjusting the view the analyst is already on. A push per dimension would put
   * one Back press per picker interaction between the analyst and the page they
   * arrived from, and the workspace has six controls beside the ten pickers.
   */
  const edit = useCallback(
    (mutate: (next: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams);
      mutate(next);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const setDimension = useCallback(
    (key: SegmentationKey, value: string | null) => {
      edit((next) => {
        if (value === null || value === "") next.delete(paramNameOf(key));
        else next.set(paramNameOf(key), value);
      });
    },
    [edit],
  );

  const clearAll = useCallback(() => {
    // Only the ten, so a grain or a sort survives "Clear all" — the control says
    // "clear the filters" and clearing a period selection with them would be a
    // second thing happening on one click.
    edit((next) => {
      for (const key of SEGMENTATION_KEYS) next.delete(paramNameOf(key));
    });
  }, [edit]);

  const control = useCallback(
    (name: ControlName) => searchParams.get(name),
    [searchParams],
  );

  const setControl = useCallback(
    (name: ControlName, value: string) => {
      edit((next) => {
        next.set(name, value);
      });
    },
    [edit],
  );

  const clearRefused = useCallback(
    (parameterNames: readonly string[]) => {
      edit((next) => {
        for (const name of parameterNames) next.delete(name);
      });
    },
    [edit],
  );

  return { segmentation, setDimension, clearAll, control, setControl, clearRefused };
}

/**
 * One control's value from the URL, held to the vocabulary the caller offers.
 *
 * The counterpart to "nothing here validates a value", and the difference is
 * which side of the wire the vocabulary is on. A *dimension*'s vocabulary is the
 * server's, so a bad one is refused there with a 422 the bar degrades on. A
 * *control*'s vocabulary is a closed enum the SPA already holds as a literal
 * union — `GRAIN_ORDER`, `COHORT_ORDER`, the five rate sorts — and it is the
 * list a `<select>` renders from, so a URL carrying `?grain=fortnight` would
 * leave a `<select>` with a value none of its options has: React renders that as
 * *no selection at all*, which is a control the analyst cannot read.
 *
 * So an unrecognised control value falls back to the section's own default
 * rather than being sent. It is not a second copy of a server enum: the options
 * are passed in by the component that already renders them, and this function
 * holds no vocabulary of its own.
 */
export function pickOption<T extends string>(
  raw: string | null,
  options: readonly T[],
  fallback: T,
): T {
  return options.find((option) => option === raw) ?? fallback;
}
