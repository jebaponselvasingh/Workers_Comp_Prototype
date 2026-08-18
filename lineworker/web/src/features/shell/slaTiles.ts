/**
 * The SLA tile's vocabulary, shared by the two surfaces that draw it (AD-2, AC 3).
 *
 * **This is a move, not a rewrite.** Every declaration below was in
 * `SlaStrip.tsx` and is unchanged: the four tile specs in the prototype's
 * left-to-right order, the four tone classes, the value formatter and the
 * target sentence. Story 5.3's dashboard renders the same four metrics from the
 * same `strip_of` a second time, lower on the page, and the alternative to
 * lifting this was a second copy — four labels, four tooltips, four tone
 * classes and two formatters that would then be free to drift a word at a time
 * while both surfaces went on claiming to show one server value.
 *
 * The one thing that could not be lifted verbatim is the `data-testid`. The two
 * tile groups are on screen **at the same time**, so they need to be separately
 * addressable — a spec asserting "the dashboard tiles equal the top-bar strip"
 * cannot be written if both answer to `sla-pick`. So the spec carries a `slug`
 * and each surface composes its own prefix: `sla-pick` in the top bar,
 * `chart-sla-pick` on the dashboard. Nothing else differs.
 *
 * Deliberately *not* moved: the tooltip wrapper and the skeleton. Those are
 * presentation choices each surface makes for itself — the top bar's tiles are
 * focusable tooltip triggers in a dense strip, and the dashboard's are cards in
 * a chart grid whose explanation has room to be visible.
 */
import type { SlaMetric, SlaStripData } from "@/api/stats";

export type SlaMetricKey = keyof SlaStripData;

export interface SlaTileSpec {
  key: SlaMetricKey;
  /**
   * The `data-testid` stem, without a surface prefix.
   *
   * Kebab-case, matching the wire key. The top bar renders `sla-${slug}` and
   * the dashboard `chart-sla-${slug}`, which is what lets one DOM hold both
   * tile groups and a test tell them apart.
   */
  slug: string;
  label: string;
  unit: "d" | "%";
  /** Verbatim from the prototype's tile titles (UX notes). */
  tooltip: string;
  /**
   * Which token a *missed* target is drawn in. Warn everywhere except the
   * return-to-work rate, which the prototype colours as an error: a missed RTW
   * rate means injured people are still off work, and it is the one tile whose
   * miss is not merely a process delay.
   */
  missTone: "warn" | "error";
}

export const SLA_TILES: readonly SlaTileSpec[] = [
  {
    key: "pick",
    slug: "pick",
    label: "Pick",
    unit: "d",
    tooltip: "Avg days from FROI to Handler Assignment — target <1 day",
    missTone: "warn",
  },
  {
    key: "approve",
    slug: "approve",
    label: "Approve",
    unit: "d",
    tooltip: "Avg days from FROI to Claim Approval — target <5 days",
    missTone: "warn",
  },
  {
    key: "settle",
    slug: "settle",
    label: "Settle",
    unit: "d",
    tooltip: "Avg days from FROI to Settlement — target <30 days",
    missTone: "warn",
  },
  {
    key: "rtwRate",
    slug: "rtw-rate",
    label: "RTW Rate",
    unit: "%",
    tooltip: "Percentage of settled claims with successful RTW",
    missTone: "error",
  },
];

export const TONE_CLASS = {
  pass: "bg-ok text-white",
  warn: "bg-warn text-white",
  error: "bg-error text-white",
  // Neither green nor red: "we have nothing to measure" is not a verdict,
  // and dressing it as one is exactly how the prototype's 87% happened.
  none: "bg-surface-2 text-faint",
} as const;

export type SlaTone = keyof typeof TONE_CLASS;

/**
 * Which tone a tile is drawn in — the server's verdict, mapped to a class.
 *
 * Extracted alongside the map because the two are one decision and were one
 * expression: reading `status` without `missTone` gives a green/red tile for a
 * missed RTW rate, and reading `missTone` without `status` colours a passing
 * one. There is no threshold and no comparison here; `status` arrived decided.
 */
export function toneOf(metric: SlaMetric | undefined, spec: SlaTileSpec): SlaTone {
  if (metric === undefined || metric.status === "no_data") return "none";
  return metric.status === "pass" ? "pass" : spec.missTone;
}

/**
 * The figure, at the precision the server rounded it to.
 *
 * `decimals` comes off the wire rather than out of `SLA_TILES` because the
 * server decided the verdict on the rounded value: formatting to a precision of
 * our own would eventually print a number it never computed (a `61.5` warned
 * against `<30d`, displayed as `62d`).
 */
export function formatValue(value: number, decimals: number, unit: string): string {
  return `${value.toFixed(decimals)}${unit}`;
}

/**
 * The target line: the comparison the server made, written out.
 *
 * A met "below" target reads `✓ <1d`; a missed one flips the operator to
 * `⚠ >1d`, which is how the prototype states it — the annotation describes
 * where the caseload *is*, not only what was asked of it. With no data there is
 * no verdict to announce, so the target stands alone.
 */
export function formatTarget(metric: SlaMetric, { unit }: SlaTileSpec): string {
  const target = `${metric.target}${unit}`;
  if (metric.status === "no_data")
    return `${metric.direction === "below" ? "<" : ">"}${target}`;

  const met = metric.status === "pass";
  const below = metric.direction === "below";
  return `${met ? "✓" : "⚠"} ${met === below ? "<" : ">"}${target}`;
}

/** The em dash a tile draws when the server had nothing to average. */
export const NO_VALUE = "—";
