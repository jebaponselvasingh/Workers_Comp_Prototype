/**
 * The viewer's clock — the greeting's two boundaries, and the UTC trap.
 *
 * Every case here is a *local* wall clock built with `new Date(y, m, d, h, …)`,
 * which is the whole point: the functions under test are the console's one
 * sanctioned piece of client-side reasoning, and the failure they exist to
 * prevent is reaching for a UTC method by habit.
 */
import { describe, expect, test } from "vitest";

import { formatLongDate, formatNotedAt, greetingFor, todayIso } from "./clock";

describe("greetingFor", () => {
  test.each([
    [0, "Good morning"],
    [11, "Good morning"],
    // The two boundaries the story names explicitly — 11:59/12:00 and
    // 16:59/17:00 — because an off-by-one here is invisible for 22 hours a day.
    [12, "Good afternoon"],
    [16, "Good afternoon"],
    [17, "Good evening"],
    [23, "Good evening"],
  ])("at %i:00 local it reads %s", (hour, expected) => {
    expect(greetingFor(new Date(2026, 7, 17, hour, 0))).toBe(expected);
  });

  test("11:59 is still morning and 12:00 is not", () => {
    expect(greetingFor(new Date(2026, 7, 17, 11, 59))).toBe("Good morning");
    expect(greetingFor(new Date(2026, 7, 17, 12, 0))).toBe("Good afternoon");
  });

  test("16:59 is still afternoon and 17:00 is not", () => {
    expect(greetingFor(new Date(2026, 7, 17, 16, 59))).toBe("Good afternoon");
    expect(greetingFor(new Date(2026, 7, 17, 17, 0))).toBe("Good evening");
  });
});

describe("todayIso", () => {
  test("is the local calendar day, zero-padded", () => {
    expect(todayIso(new Date(2026, 0, 5, 13, 0))).toBe("2026-01-05");
  });

  test("is the *local* day even when UTC has already turned over", () => {
    // 2026-08-17 23:30 local. `toISOString()` would answer the 18th for any
    // reader west of Greenwich and the 17th for anyone east — the exact bug
    // Story 4.1's review found in the scheduler's default date. Comparing
    // against the local parts rather than against a fixed string keeps this
    // test true in every timezone a developer runs it in.
    const late = new Date(2026, 7, 17, 23, 30);
    expect(todayIso(late)).toBe("2026-08-17");

    const early = new Date(2026, 7, 17, 0, 15);
    expect(todayIso(early)).toBe("2026-08-17");
    // Both ends of the same local day agree, which is the property the server
    // depends on when this string arrives as `day`.
    expect(todayIso(early)).toBe(todayIso(late));
  });
});

describe("formatLongDate", () => {
  test("is the prototype's weekday/month/day sentence", () => {
    expect(formatLongDate(new Date(2026, 7, 17, 9, 0))).toBe("Monday, August 17");
  });
});

describe("formatNotedAt", () => {
  test("splits one stored instant into the prototype's date · time header", () => {
    const formatted = formatNotedAt(new Date(2026, 7, 17, 14, 5).toISOString());
    expect(formatted).toContain("8/17/2026");
    expect(formatted).toContain("·");
    expect(formatted).toContain("2:05");
  });

  test("renders an unparseable value verbatim rather than 'Invalid Date'", () => {
    // NFR-3: show the absence, never a placeholder that looks like data. The
    // wire type is a string, and a truncated one must not become the words
    // "Invalid Date" on a card.
    expect(formatNotedAt("not-a-timestamp")).toBe("not-a-timestamp");
  });
});
