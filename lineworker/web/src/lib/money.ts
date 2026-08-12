/**
 * Money formatting — the only place cents become a string.
 *
 * The convention is integer cents end to end, formatted only in the UI, and
 * every wire field carries a `Cents` suffix so the unit is un-missable at
 * the call site. This is the one function allowed to divide by a hundred.
 *
 * In `lib/` rather than in a feature folder because Epic 3's bills and
 * payment schedules, Epic 5's KPI cards and Epic 7's decomposition all show
 * the same amounts, and two formatters would eventually disagree about a
 * rounding or a currency symbol on two screens showing one number.
 *
 * **Whole dollars, like the prototype's `$m`.** Claim reserves and payouts
 * run to five and six figures; cents in that context are noise, and the
 * prototype's own money helper drops them. A figure that needs the cents
 * (a single bill line, Epic 3) should get its own formatter and say so
 * rather than quietly changing this one.
 */
const DOLLARS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const CENTS_PER_DOLLAR = 100;

export function formatCents(cents: number): string {
  return DOLLARS.format(cents / CENTS_PER_DOLLAR);
}
