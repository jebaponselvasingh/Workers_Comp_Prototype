/**
 * The one place an age band becomes a range of years (Story 7.3).
 *
 * `AgeBand`'s wire values are ordinal words — `youngest`, `younger`, `older`,
 * `oldest` — and they carry no numbers at all. That is the server's deliberate
 * design rather than an omission: a member spelled for a range would put the
 * cut-off in the Python tier as well as in `derivation_thresholds`, and moving
 * an edge in the document would leave the member's own name asserting the old
 * one, on every chip, legend and shared URL that had ever carried it
 * (`services/derivations/worker_age_band.py`).
 *
 * The consequence is that the browser has to say "35–44" and the browser is the
 * only place that string can be built — from the three edges
 * `GET /dashboard/segmentation/values` publishes, on the response that also
 * publishes the bands. So there is exactly one function, it takes the edges as
 * an argument, and nothing anywhere holds an age constant.
 *
 * **This is a rendering, not a derivation, and the distinction is worth being
 * precise about because it looks like one.** The three edges are *lower* bounds
 * of half-open bands; a reader expects closed ones, so the upper label of a band
 * is the day before the next band opens. That subtraction turns one interval
 * notation into another and decides nothing: it cannot put a claim in a
 * different band, because no payload in this console carries a worker's raw age
 * for it to band. `noDerivation.test.ts` records why the three edges are
 * deliberately absent from its field list, which is the same argument from the
 * guard's side.
 */
import type { AgeBand, SegmentationEdges } from "@/api/dashboard";

/**
 * The four bands in the order a picker draws them, youngest first.
 *
 * A scale's reading order rather than a legend's, matching the server's own
 * declaration order — an age segmentation is scanned the way a histogram is,
 * from the youngest cohort rightwards. `RISK_LABEL_BY_BAND` reads high-first for
 * the opposite reason: a donut's loudest slice is read first.
 *
 * Written out rather than read off the payload, which the values endpoint would
 * happily supply: this array is the *vocabulary*, and the payload publishes only
 * the members some claim is actually in. A picker that lost `oldest` because
 * nobody in the book was would be right; a label map that lost it would render
 * `oldest` raw on a chip restored from a shared URL.
 */
export const AGE_BAND_ORDER: readonly AgeBand[] = [
  "youngest",
  "younger",
  "older",
  "oldest",
];

/**
 * The four bands' labels, composed from the edges the server published.
 *
 * An en dash between the bounds because it is a range rather than a subtraction,
 * and "Under N" / "N and over" at the ends because those two bands are genuinely
 * open — writing "0–34" would assert a lower bound the document does not have
 * and "55–120" an upper one nobody chose.
 *
 * Returns a plain `Record<string, string>` keyed on the **wire** value, which is
 * the shape `chipLabel`'s `composed` argument takes and the shape a `<select>`
 * indexes by option value — so one map serves the picker and the chip, and the
 * two cannot disagree about what a band is called.
 */
export function ageBandLabels(edges: SegmentationEdges): Record<string, string> {
  const younger = edges.ageYoungerMin;
  const older = edges.ageOlderMin;
  const oldest = edges.ageOldestMin;
  return {
    youngest: `Under ${String(younger)}`,
    younger: `${String(younger)}–${String(older - 1)}`,
    older: `${String(older)}–${String(oldest - 1)}`,
    oldest: `${String(oldest)} and over`,
  };
}
