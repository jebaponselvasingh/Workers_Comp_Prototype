/**
 * The shared surface every chart on this dashboard is drawn inside (NFR-3, AC 4).
 *
 * Seven surfaces, one state machine. `HandlerBenchmarkTable` writes the loading
 * / empty / error triple out inline because it is one section; repeating that
 * seven times would be seven chances for one of them to render an alert beside
 * stale bars, or to draw a headless chart where the scope simply has nothing in
 * it. The vocabulary is that component's, verbatim: a `<section>` with
 * `aria-labelledby` and its own `aria-busy`, an `animate-pulse` skeleton while
 * the request is in flight, a `role="alert"` paragraph **replacing** the
 * content on failure, and a `text-faint` line for an empty scope.
 *
 * **The body is a fixed height in all four states**, which is the whole of
 * NFR-3 on this page. A surface that sized itself to its content would be one
 * height while loading, another once the data landed, a third for an empty
 * scope and a fourth under an alert — so a dashboard resolving three
 * independent requests would reflow twice under the reader's cursor, and the
 * chart she was about to look at would move. The height is reserved up front
 * and every state occupies it; see `CHART_HEIGHT`.
 *
 * **Errors never open a dialog and never sit beside data.** The alert replaces
 * the chart rather than appearing above it, for `HandlerBenchmarkTable`'s
 * reason: an empty chart under a warning reads as "this scope has nothing in
 * it", which is a different and much quieter lie than a stated failure.
 */
import type { ReactNode } from "react";

/** How many placeholder rows a bar-chart skeleton holds open. */
const SKELETON_BARS = 5;

/**
 * The reserved caption row: one line of the footnote's own type scale.
 *
 * A height rather than a `min-h`, so a surface that never truncates and one that
 * does are the same height, and neither changes when its data arrives.
 */
const FOOTNOTE_ROW = "mt-1 h-[13px] text-[9.5px] leading-[13px]";

export function ChartFrame({
  testId,
  title,
  height,
  isLoading,
  isError,
  isEmpty,
  errorMessage,
  emptyMessage,
  footnote,
  children,
}: {
  /** `data-testid` stem; the heading gets `${testId}-heading`. */
  testId: string;
  title: string;
  /** The reserved body height in pixels — one of `CHART_HEIGHT`'s two. */
  height: number;
  isLoading: boolean;
  isError: boolean;
  /** The server answered, and the scope contains nothing to draw. */
  isEmpty: boolean;
  errorMessage: string;
  emptyMessage: string;
  /**
   * The truncation caption, or anything else that belongs under the chart.
   *
   * Written only in the data state — there is nothing to say about a skeleton,
   * an empty scope or a failure — but its **row is reserved in every state**,
   * which is not the same thing. Rendering it conditionally with no reserved
   * space made a ranked card one line taller the moment its response landed,
   * and `/dashboard/charts` is an independent query that resolves after the KPI
   * cards: the second chart row grew under the reader's cursor. That is the
   * reflow the fixed-height body exists to prevent (NFR-3), reintroduced one
   * element below it. A caption-height box, always present, costs one line of
   * whitespace on the five surfaces that never truncate and keeps the section
   * the same height from skeleton to data.
   */
  footnote?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      data-testid={testId}
      aria-labelledby={`${testId}-heading`}
      // Its own busy state as well as the page's: this surface can be loading
      // while the KPI cards above it have landed, and a screen reader told the
      // whole dashboard was busy would be describing a screen nobody sees.
      aria-busy={isLoading}
      className="flex flex-col rounded-lg border border-border bg-surface p-3"
    >
      <h3
        id={`${testId}-heading`}
        className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
      >
        {title}
      </h3>

      {/* The reserved box. Every branch below draws inside this one element, so
          the four states cannot disagree about how tall the surface is. */}
      <div
        data-testid={`${testId}-body`}
        style={{ height: `${String(height)}px` }}
        className="relative"
      >
        {isLoading ? (
          <div
            data-testid={`${testId}-skeleton`}
            aria-hidden
            className="flex h-full flex-col justify-center gap-[10px]"
          >
            {Array.from({ length: SKELETON_BARS }, (_, index) => (
              <span
                key={index}
                className="block h-[14px] animate-pulse rounded bg-surface-2"
              />
            ))}
          </div>
        ) : isError ? (
          <p
            role="alert"
            data-testid={`${testId}-error`}
            className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
          >
            {errorMessage}
          </p>
        ) : isEmpty ? (
          <p
            data-testid={`${testId}-empty`}
            className="flex h-full items-center text-[11.5px] text-faint"
          >
            {emptyMessage}
          </p>
        ) : (
          children
        )}
      </div>

      {/* Reserved in every state — see `footnote`. `FOOTNOTE_ROW` is the line
          height the caption occupies, so the box does not change size when the
          sentence appears. */}
      <div className={FOOTNOTE_ROW}>{!isLoading && !isError && !isEmpty && footnote}</div>
    </section>
  );
}
