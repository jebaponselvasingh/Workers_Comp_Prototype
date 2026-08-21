/**
 * The one place a chart decides a colour (AD-10 at the visual layer, AC 2).
 *
 * **Why `Record`s of `var(--color-…)` strings rather than Tailwind classes.**
 * `RiskGauge.tsx` set the precedent and states the reason: these values end up
 * on SVG *attributes* (`fill`, `stroke`), and a Tailwind class cannot reach an
 * attribute Recharts writes itself. So the tokens are read out of the theme as
 * CSS variables and handed to `<Cell fill={…}>`. They are still the Story 1.1
 * tokens — `--color-error` here is the same declaration `bg-error` compiles
 * against — so a High severity slice and a High Risk KPI card are the same red
 * by construction rather than by two hexes that happen to match.
 *
 * **Keyed by the wire value, and exhaustively.** Every map below is a
 * `Record<Enum, string>` over a generated enum type, so a fifth stage or a
 * fourth recovery status fails the build here rather than rendering a
 * colourless slice nobody notices. The wire keys are snake_case; the display
 * labels live in `PortfolioCharts.tsx`, because a colour is a semantic and a
 * label is copy.
 *
 * **No palette invention.** The five semantic tokens plus the prototype's five
 * extension hues, and nothing else — see `CATEGORICAL_FILLS`.
 */
import type { FraudBand } from "@/api/dashboard";
import type { RiskBand, Stage } from "@/api/claims";
import type { ReturnStatus } from "@/api/claims";

/**
 * Settlement status → the prototype's donut colours (`renderSV`, line 1081).
 *
 * Settled is the ok green and Under Treatment the warn amber, which are the
 * same two tones the Settled & Closed and Under Treatment KPI cards carry
 * directly above this donut. Intake takes the steel informational token and
 * Investigation the error red, which is the prototype's fourth arc — read as
 * loudness rather than as failure, the sense `bills/statusTone.ts` records for
 * its own map.
 */
export const STAGE_FILL: Record<Stage, string> = {
  settled: "var(--color-ok)",
  treatment: "var(--color-warn)",
  intake: "var(--color-steel)",
  investigation: "var(--color-error)",
};

/**
 * Severity band → the KPI card's own tones.
 *
 * `RiskGauge`'s `ARC_COLOR` map, verbatim and deliberately so: the gauge on a
 * claim, the dot on a queue card and this slice are three drawings of one
 * `risk` derivation, and AC 2 asks for them to be the same red.
 */
export const RISK_FILL: Record<RiskBand, string> = {
  high: "var(--color-error)",
  med: "var(--color-warn)",
  low: "var(--color-ok)",
};

/**
 * Recovery status → the prototype's three recovery bars (`renderSV`, line 1094).
 *
 * Fully recovered is ok, still under treatment is warn, and returned-but-still-
 * in-therapy is the brand orange — the prototype's `var(--ac)`, which is
 * `--color-brand` in the Story 1.1 tokens (the orange is `brand`, not
 * `accent`; `--accent` in `index.css` is shadcn's soft surface and would render
 * a near-white bar).
 */
export const RECOVERY_FILL: Record<ReturnStatus, string> = {
  returned_and_fully_recovered: "var(--color-ok)",
  under_treatment: "var(--color-warn)",
  returned_and_under_therapy: "var(--color-brand)",
};

/**
 * Fraud band → the same three tones the severity donut uses (Story 7.1).
 *
 * `RISK_FILL`'s three values, over a different rule and a different column, and
 * **no new colour enters this file** — which is the constraint UX-DR12 and this
 * module's own docstring impose. A fraud analyst reading a high band beside a
 * high severity band is reading the same red for the same reason: "look here
 * first". That the two enums have different member *names* (`medium` against
 * `med`) is why this is a second map rather than a reuse of the first: a
 * `Record<FraudBand, string>` keyed on `med` would fail to compile, which is the
 * type system saying that these are two vocabularies over two columns.
 *
 * Keyed by the wire value and exhaustive over the generated enum, so a fourth
 * band fails the build here rather than rendering a colourless bar nobody
 * notices.
 */
export const FRAUD_BAND_FILL: Record<NonNullable<FraudBand>, string> = {
  high: "var(--color-error)",
  medium: "var(--color-warn)",
  low: "var(--color-ok)",
};

/**
 * The single-hue fill for the injury-type chart.
 *
 * One colour for every bar, matching the prototype, and that is a statement
 * rather than a shortcut: injury types carry no ordering and no severity of
 * their own, so ten hues would invite a reader to look for a meaning that is
 * not there. The employer and state charts take `CATEGORICAL_FILLS` instead,
 * for the reason stated there: ten same-coloured bars are hard to tell apart at
 * a glance, not because a hue identifies an employer.
 */
export const SERIES_FILL = "var(--color-brand)";

/**
 * The prototype's ten categorical hues, in its order (`renderSV`, lines 1086
 * and 1089 — one array, used for both the employer and the state chart).
 *
 * The first five **are** the Story 1.1 tokens, written as literals here and
 * only here: steel `#1D6A96`, brand `#E8560A`, ok `#1D7A45`, warn `#9A6E06`,
 * error `#C73E2D`. They are hexes rather than `var(--color-…)` because this
 * array is an ordered sequence in which positions 6-10 have no token — mixing
 * the two forms would make position 5 look semantic and position 6 arbitrary,
 * when in fact the whole array is positional.
 *
 * **Positional, and that is the truth about what a hue here means: nothing.**
 * A bar takes the colour of its *rank*, exactly as the prototype's `empC[i]`
 * does, so Toyota is orange when it comes second in Jennifer Park's book and
 * steel blue when it comes second-from-top in David Bline's — and the state
 * chart re-uses the same ten hues in the same ten positions for a completely
 * different dimension. What the palette buys is that ten adjacent bars are
 * distinguishable from one another *within one chart*; what it does not buy,
 * and must not be described as buying, is an entity a reader can track between
 * charts or between personas. Colour-as-identity would need a stable key
 * (`employerId` is on the wire for exactly that kind of purpose) and a map that
 * outlives one render, which is a different feature with a different cost, and
 * this story does not have it. The behaviour is the prototype's and is kept;
 * only the claim about it is corrected.
 *
 * The last five are the prototype's extension hues, which exist because ten
 * employers need ten distinguishable colours and the design system has five.
 * **Nothing may be added to this list.** A palette that grows per chart is how
 * one surface ends up with a hue no other surface has; a series longer than ten
 * is handled by `PALETTE_OVERFLOW_FILL` below rather than by another hex
 * appended here.
 */
export const CATEGORICAL_FILLS: readonly string[] = [
  "#1D6A96",
  "#E8560A",
  "#1D7A45",
  "#9A6E06",
  "#C73E2D",
  "#7B5EA7",
  "#2E8B94",
  "#8B6914",
  "#1D4F8A",
  "#6B2D8B",
];

/**
 * The hue a bar past the tenth is drawn in.
 *
 * The employer series is **uncapped** by `_by_employer` — the employers in a
 * caller's book are bounded by the assignment, not by a limit — so "there are
 * only ever ten" is an observation about today's seed and not a property of the
 * response. The previous `fills[index % fills.length]` wrapped silently: an
 * eleventh employer would have been drawn in the first one's steel blue, two
 * bars in one chart claiming one colour, with nothing on screen to say why.
 *
 * One neutral for the whole tail is the honest answer. It is a Story 1.1 token
 * rather than an eleventh hue, so it reads as "outside the palette" instead of
 * as another category, and it keeps "no palette invention" true. It is
 * deliberately *not* a repeat of any of the ten.
 */
export const PALETTE_OVERFLOW_FILL = "var(--color-muted-text)";

/**
 * The fill a `Record`-keyed palette answers with for a key it does not hold.
 *
 * A wire key absent from a theme map is a contract change the browser cannot
 * fix, but drawing a *transparent* bar or slice for it is the worst available
 * rendering: the value silently disappears from a chart that still totals it.
 * The neutral says "there is a category here and this build has no colour for
 * it", which is the same thing the label fallbacks in `PortfolioCharts.tsx` and
 * `DistributionDonut.tsx` say in words.
 */
export const UNKNOWN_KEY_FILL = "var(--color-muted-text)";

/**
 * The fixed body heights every chart surface reserves, in pixels.
 *
 * **Fixed, in all four states, and that is NFR-3's whole requirement.** A
 * surface that sized itself to its content would be one height while loading,
 * another once the data landed, a third when the scope turned out to be empty
 * and a fourth under an inline alert — so a dashboard resolving three requests
 * would reflow twice under the reader's cursor. Reserving the height up front
 * means the skeleton, the chart, the empty line and the alert all occupy the
 * same box.
 *
 * Two numbers rather than one because the two forms need different room: a
 * donut is square-ish beside its legend, and a bar chart's height is a function
 * of how many bars it holds — ten states need more vertical space than three
 * recovery statuses, and sizing the tallest chart's box for all of them would
 * leave the recovery card mostly empty.
 *
 * **What these constants do not do is equalise a row.** An earlier version of
 * this comment said the two donuts and the SLA group "sit on one row together",
 * implying `donut` was the height of row one. It is not: row one is
 * `lg:grid-cols-4` and its fourth cell is the recovery *bar* chart at `bars`,
 * and CSS grid stretches every cell in a row to the tallest — so the row is 232
 * tall and the three shorter cards are stretched to match it whatever these
 * numbers say. The height each constant buys is the *body*'s, within one
 * surface, across the four states — which is all NFR-3 asks for and the only
 * thing a fixed box can give.
 */
export const CHART_HEIGHT = {
  /** The two donuts and the SLA tile group. */
  donut: 172,
  /** The four horizontal bar charts. */
  bars: 232,
} as const;
