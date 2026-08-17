/**
 * The **viewer's own clock** — the three things about "now" that are honestly
 * the browser's to decide (Story 4.2, AD-1).
 *
 * Almost nothing in this console is computed client-side: statuses, counts,
 * orderings and bands all arrive decided, and `features/queue/noDerivation.test.ts`
 * fails the build over a comparison written in a component. This module is the
 * documented exception, and the line it draws is worth stating precisely.
 *
 * **What is here is presentation of the reader's local time, not business
 * data.** A server has never been told the handler's timezone, so it cannot
 * know whether it is morning where they are sitting, what day their calendar
 * shows, or how their locale spells a long date. Those three are the browser's
 * by construction. What is *not* here — and must never move here — is anything
 * about a meeting, a note or a claim: whether a meeting is upcoming, how many
 * are ahead, which ones fall today. Those are the server's, and the local day
 * this module resolves is *sent to it* as a parameter rather than used to
 * filter anything locally.
 *
 * **It lives in `lib/` rather than in `features/diary/` for that reason.**
 * `noDerivation.test.ts` scans `features/diary`, and `greetingFor`'s `< 12` /
 * `< 17` would trip its numeric-threshold rule — correctly, if the file were a
 * feature component. Putting the viewer's clock in `lib/`, beside `money.ts`
 * and `rate.ts`, keeps the guard blunt over the surfaces it is for instead of
 * training the guard to allow comparisons in the one directory where a real
 * derivation would hide.
 *
 * Every function takes `now` rather than calling `new Date()` inside, so the
 * boundaries are testable without freezing a global clock — the same reason
 * every calendar-dependent derivation on the server takes an `as_of`.
 */

/** The 24-hour bucket boundaries the prototype's greeting uses. */
const AFTERNOON_FROM = 12;
const EVENING_FROM = 17;

/**
 * "Good morning" before noon, "Good afternoon" before 17:00, else "Good
 * evening" — the prototype's three strings and its two cut-offs, verbatim.
 * [Source: docs/Workers_Comp_Prototype.html line 2047]
 *
 * **Local hours, not UTC.** `getHours()` rather than `getUTCHours()`: the
 * greeting is about where the reader is, and a handler in Seattle greeted good
 * evening over breakfast is the whole failure mode.
 */
export function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour < AFTERNOON_FROM) return "Good morning";
  if (hour < EVENING_FROM) return "Good afternoon";
  return "Good evening";
}

/**
 * Today as an ISO calendar date, `YYYY-MM-DD` — the **viewer's** today.
 *
 * Built from the local calendar parts rather than `toISOString().slice(0, 10)`,
 * which is UTC: for anyone far enough east or west that is a different day.
 * Story 4.1's review caught exactly this in the scheduler's default date, and
 * this function is where that fix now lives so the two definitions cannot
 * drift — the scheduler pre-fills from it, and the Notes sub-tab sends it to
 * the server as `day`.
 *
 * That second use is the important one: the string this returns is the only
 * "today" the server is ever told, so a UTC slip here would show a handler
 * yesterday's meetings for the first hours of their working day.
 */
export function todayIso(now: Date): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * "Monday, August 17" — the date the greeting card prints after "Today is".
 *
 * The prototype's exact `toLocaleDateString` options (weekday long, month
 * long, numeric day). Locale-aware by design: it is a sentence a person reads,
 * not a value anything compares.
 * [Source: docs/Workers_Comp_Prototype.html line 2048]
 */
export function formatLongDate(now: Date): string {
  return now.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

/**
 * `2026-08-17T09:30:00Z` → `8/17/2026 · 9:30 AM` — a note's header.
 *
 * The prototype writes `{n.date} · {n.time}` from two fields it stored
 * separately; the server stores one instant (see `DiaryNote`'s docstring on
 * why), so the split happens here, in the reader's own locale and timezone.
 *
 * Formatting only. Nothing about *when* the note falls relative to anything is
 * decided here — a note has no lifecycle and there is no status to derive.
 */
export function formatNotedAt(isoInstant: string): string {
  const when = new Date(isoInstant);
  if (Number.isNaN(when.getTime())) return isoInstant;
  return `${when.toLocaleDateString("en-US")} · ${when.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  })}`;
}
