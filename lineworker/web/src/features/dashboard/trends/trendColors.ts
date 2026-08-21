/**
 * A cohort's colour — identity, never rank (Story 7.2 AC 2).
 *
 * `chartTheme.ts` owns every hue this console draws and is **closed to
 * addition**; this module owns nothing but the question "which of those hues
 * does this cohort get", and the answer has to hold across five charts and
 * across a refetch. Two rules, and the split is the whole of the file:
 *
 * **Severity band takes `RISK_FILL`, because that dimension already owns a
 * semantic palette.** A High cohort line is the same red as the High Risk KPI
 * card, the severity donut's High arc and a claim's risk gauge — four drawings
 * of one registered `risk` derivation, which is what AC 2 asks for in the words
 * "match the KPI cards' risk colouring exactly". Giving it a positional hue
 * instead would have been the shorter code and would have painted the worst
 * cohort in the portfolio steel blue.
 *
 * **Everything else takes `CATEGORICAL_FILLS[paletteSlot]`, and `paletteSlot`
 * is the server's ordinal.** `CATEGORICAL_FILLS`' own docstring records the flaw
 * this fixes: a hue there means *rank position*, so a series drawing its
 * neighbours' colours from their order on screen would repaint every line the
 * moment one cohort overtook another — and would paint the same cohort two
 * colours on two charts of the same page, since the five metrics rank
 * differently. The server assigns the slot by sorting cohort values on their
 * **wire key** (`services/worklist/trends.py`), which is a fact about the
 * vocabulary rather than about the data, so it does not move when the numbers
 * do. The browser only indexes.
 *
 * **Nothing is added to the palette and nothing wraps.** Sector is nine free-text
 * values across ten seeded employers, which fits under the ten-hue ceiling but is
 * close enough to it that the overflow fill has to be wired rather than assumed
 * unreachable — and the previous generation of this code in `DistributionBars`
 * records what wrapping looked like: an eleventh category silently drawn in the
 * first one's steel blue, two lines on one chart claiming one colour, with
 * nothing on screen to say why. `.at()` rather than a bounds comparison, because
 * a comparison is what `noDerivation.test.ts` refuses on sight and because
 * `undefined` is the honest answer for a slot the palette does not have.
 */
import type { TrendCohort } from "@/api/dashboard";
import type { RiskBand } from "@/api/claims";

import {
  CATEGORICAL_FILLS,
  PALETTE_OVERFLOW_FILL,
  RISK_FILL,
  SERIES_FILL,
  UNKNOWN_KEY_FILL,
} from "../charts/chartTheme";

/**
 * The colour one series is drawn in.
 *
 * `key` and `paletteSlot` are both `null` on an unsplit series — the response
 * publishes them that way so "is this a cohort?" is a field rather than an
 * inference — and that case takes `SERIES_FILL`, the single brand hue the injury
 * chart uses, for that chart's stated reason: one line carries no comparison, so
 * a categorical hue would invite a reader to look for a meaning that is not
 * there.
 */
export function cohortFill(
  dimension: TrendCohort,
  key: string | null,
  paletteSlot: number | null,
): string {
  if (key === null || paletteSlot === null) return SERIES_FILL;
  if (dimension === "severity_band") {
    // A `Record<RiskBand, …>` reached through a wire string, so an unknown band
    // gets the neutral rather than `undefined` — `DistributionDonut`'s rule.
    // The cast is the same narrow one `enumValue` makes in `drill/filters.ts`:
    // only the server can say whether a string is a member of its own enum.
    return RISK_FILL[key as RiskBand] ?? UNKNOWN_KEY_FILL;
  }
  return CATEGORICAL_FILLS.at(paletteSlot) ?? PALETTE_OVERFLOW_FILL;
}
