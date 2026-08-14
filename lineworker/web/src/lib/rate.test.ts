/**
 * Basis points ↔ percentage — the one conversion the SPA is allowed to do.
 *
 * The whole reason a comp rate crosses the wire as an integer is that 66.67 is
 * not a float anybody can round-trip, so these are the tests that would fail
 * first if somebody replaced the arithmetic with `Number(x) / 100` and a
 * `toFixed(2)`.
 *
 * `formatBasisPoints` is the client half of a contract the server states in
 * `services/financials/rationale.py`'s `format_comp_rate`; the e2e spec is
 * where the two are compared against one claim.
 */
import { expect, test } from "vitest";

import { basisPointsToPercent, formatBasisPoints, parseBasisPoints } from "./rate";

test("basis points format with two decimals, always", () => {
  expect(formatBasisPoints(6667)).toBe("66.67");
  // The case a `toFixed`-free implementation gets wrong: a trailing zero is
  // part of a percentage and dropping it reads as a different rate.
  expect(formatBasisPoints(6670)).toBe("66.70");
  expect(formatBasisPoints(10_000)).toBe("100.00");
  expect(formatBasisPoints(15_000)).toBe("150.00");
  expect(formatBasisPoints(0)).toBe("0.00");
  expect(formatBasisPoints(5)).toBe("0.05");
});

test("a percentage parses back to the exact basis points it came from", () => {
  for (const basisPoints of [0, 5, 6667, 6670, 7025, 10_000, 15_000]) {
    expect(parseBasisPoints(formatBasisPoints(basisPoints))).toBe(basisPoints);
  }
});

test("a whole or one-decimal percentage is accepted", () => {
  expect(parseBasisPoints("70")).toBe(7000);
  expect(parseBasisPoints("70.5")).toBe(7050);
  expect(parseBasisPoints(" 66.67 ")).toBe(6667);
});

test("anything that is not a percentage is refused rather than coerced", () => {
  // The prototype's `updateCompRate` discards these by re-rendering, so a
  // handler watches their entry snap back with no explanation.
  for (const text of ["", "  ", "abc", "70.2.5", "-5", "70%", "1e2", "+70"]) {
    expect(parseBasisPoints(text), text).toBeNull();
  }
});

test("a third decimal place is refused, not rounded away", () => {
  // Storing 70.25 for a typed 70.255 would be the console inventing a number
  // the handler did not type — `normalise_severity`'s argument about clamping.
  expect(parseBasisPoints("70.255")).toBeNull();
});

test("the numeric form is what an input's min and max attributes want", () => {
  expect(basisPointsToPercent(0)).toBe(0);
  expect(basisPointsToPercent(15_000)).toBe(150);
});
