/**
 * Comp-rate formatting — the only place basis points become a percentage.
 *
 * `lib/money.ts`'s twin, and it exists for the same reason. The convention on
 * the money side is integer cents end to end, formatted only in the UI; the
 * rate side is integer **basis points** end to end — the column stores them,
 * the `benefit_params` rule document declares them, the PATCH body carries
 * them — and this is the one function allowed to divide by a hundred.
 *
 * **Why the rate is not simply a percentage on the wire.** 66.67 has no exact
 * binary representation, so a float rate would make two questions unreliable
 * at once: what a claim's weekly benefit is (an IEEE-754 rounding away from
 * the server's answer) and whether a claim is overridden (a comparison two
 * values can fail by 1e-14). Basis points are the smallest unit the input can
 * produce, so nothing is lost by staying integral.
 *
 * In `lib/` rather than in the claim-detail feature because Story 3.3's
 * payment schedule shows the same rate beside the weeks it generates, and two
 * formatters would eventually disagree about a trailing zero on two screens
 * showing one number.
 */
const BASIS_POINTS_PER_PERCENT = 100;

/**
 * `6667` → `"66.67"`. Always two decimals, so `6670` reads `"66.70"`.
 *
 * Integer arithmetic rather than `bp / 100`, which is the whole point of the
 * unit: the string is exact by construction rather than by `toFixed` rounding
 * a float back to the value it started as. This is the server's
 * `format_comp_rate` — `services/financials/rationale.py` — restated on the
 * client, and the e2e spec compares the two against one claim.
 */
export function formatBasisPoints(basisPoints: number): string {
  const whole = Math.trunc(basisPoints / BASIS_POINTS_PER_PERCENT);
  const hundredths = Math.abs(basisPoints % BASIS_POINTS_PER_PERCENT);
  return `${whole}.${String(hundredths).padStart(2, "0")}`;
}

/**
 * `6667` → `66.67`, as a number, for an input's `min`/`max` attributes.
 *
 * Separate from `formatBasisPoints` because those attributes are numeric and
 * a string would be coerced by the DOM anyway — and because doing it here
 * rather than in the card is what keeps `benefit.compRateMaxBp / 100` out of a
 * React component, which `noDerivation.test.ts` refuses on sight and rightly:
 * a unit conversion written at a call site is one keystroke from a threshold.
 */
export function basisPointsToPercent(basisPoints: number): number {
  return basisPoints / BASIS_POINTS_PER_PERCENT;
}

/**
 * `"70.25"` → `7025`, or `null` for anything that is not a percentage.
 *
 * The inverse, and the one the input commits through. `null` rather than `NaN`
 * or a clamp: an unparseable entry is a refusal the field renders inline
 * (NFR-3), and the prototype's own `updateCompRate` discards it silently by
 * re-rendering — so a handler who typed `70.2.5` watches the value snap back
 * with no explanation.
 *
 * A third decimal is refused rather than rounded away. `70.255` is either a
 * typo or a rate this system cannot represent, and silently storing `70.25`
 * would be the server inventing a number the handler did not type — the same
 * argument `normalise_severity` makes about clamping.
 */
export function parseBasisPoints(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return null;
  const [whole, fraction = ""] = trimmed.split(".");
  return (
    Number(whole) * BASIS_POINTS_PER_PERCENT + Number(fraction.padEnd(2, "0"))
  );
}
