/**
 * The top bar's SLA strip — four server-computed tiles, for every role
 * (UX-DR2, FR-SLA-1).
 *
 * This component styles and formats; it decides nothing. Value, target,
 * comparison direction, display precision and pass/warn verdict all arrive
 * from `services/worklist/sla` (AD-1), so there is no threshold, no
 * comparison operator and no rounding rule anywhere in this file — and no
 * fallback that could put a number on a tile the server said it could not
 * compute.
 *
 * That last point is the story. The prototype filled this strip with
 * 7.4d / 42d / 87% whenever a segment was empty and only recomputed it on
 * handler screens, so supervisors and analysts read invented figures with
 * nothing to mark them as invented. Here an uncomputable tile draws an em
 * dash in a muted style (NFR-3), and the same endpoint answers for all
 * three roles because the strip lives in the shared bar.
 *
 * Renders inside `TopBar`, which is why it returns a fragment rather than
 * a positioned container: the bar owns the layout, the strip owns the
 * tiles.
 */
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import type { SlaMetric, SlaStripData } from "@/api/stats";
import { useSlaStrip } from "@/api/stats";

type MetricKey = keyof SlaStripData;

interface TileSpec {
  key: MetricKey;
  /** `data-testid` stem — kebab-case, matching the wire key. */
  testId: string;
  label: string;
  unit: "d" | "%";
  /** Verbatim from the prototype's tile titles (UX notes). */
  tooltip: string;
  /**
   * Which token a *missed* target is drawn in. Warn everywhere except the
   * return-to-work rate, which the prototype colours as an error: a
   * missed RTW rate means injured people are still off work, and it is
   * the one tile whose miss is not merely a process delay.
   */
  missTone: "warn" | "error";
}

const TILES: readonly TileSpec[] = [
  {
    key: "pick",
    testId: "sla-pick",
    label: "Pick",
    unit: "d",
    tooltip: "Avg days from FROI to Handler Assignment — target <1 day",
    missTone: "warn",
  },
  {
    key: "approve",
    testId: "sla-approve",
    label: "Approve",
    unit: "d",
    tooltip: "Avg days from FROI to Claim Approval — target <5 days",
    missTone: "warn",
  },
  {
    key: "settle",
    testId: "sla-settle",
    label: "Settle",
    unit: "d",
    tooltip: "Avg days from FROI to Settlement — target <30 days",
    missTone: "warn",
  },
  {
    key: "rtwRate",
    testId: "sla-rtw-rate",
    label: "RTW Rate",
    unit: "%",
    tooltip: "Percentage of settled claims with successful RTW",
    missTone: "error",
  },
];

const TONE_CLASS = {
  pass: "bg-ok text-white",
  warn: "bg-warn text-white",
  error: "bg-error text-white",
  // Neither green nor red: "we have nothing to measure" is not a verdict,
  // and dressing it as one is exactly how the prototype's 87% happened.
  none: "bg-surface-2 text-faint",
} as const;

/**
 * The figure, at the precision the server rounded it to.
 *
 * `decimals` comes off the wire rather than out of `TILES` because the
 * server decided the verdict on the rounded value: formatting to a
 * precision of our own would eventually print a number it never computed
 * (a `61.5` warned against `<30d`, displayed as `62d`).
 */
function formatValue(value: number, decimals: number, unit: string): string {
  return `${value.toFixed(decimals)}${unit}`;
}

/**
 * The target line: the comparison the server made, written out.
 *
 * A met "below" target reads `✓ <1d`; a missed one flips the operator to
 * `⚠ >1d`, which is how the prototype states it — the annotation
 * describes where the caseload *is*, not only what was asked of it. With
 * no data there is no verdict to announce, so the target stands alone.
 */
function formatTarget(metric: SlaMetric, { unit }: TileSpec): string {
  const target = `${metric.target}${unit}`;
  if (metric.status === "no_data")
    return `${metric.direction === "below" ? "<" : ">"}${target}`;

  const met = metric.status === "pass";
  const below = metric.direction === "below";
  return `${met ? "✓" : "⚠"} ${met === below ? "<" : ">"}${target}`;
}

function Tile({
  spec,
  metric,
}: {
  spec: TileSpec;
  metric: SlaMetric | undefined;
}) {
  const tone =
    metric === undefined || metric.status === "no_data"
      ? "none"
      : metric.status === "pass"
        ? "pass"
        : spec.missTone;

  return (
    <Tooltip>
      <TooltipTrigger
        data-testid={spec.testId}
        // A button because it must be focusable: a tooltip only a mouse
        // can open is not an explanation for everyone who needs one.
        type="button"
        className={`flex min-w-[58px] cursor-default flex-col items-center rounded px-2 py-[3px] leading-tight ${TONE_CLASS[tone]}`}
      >
        <span
          // Its own test id so assertions match the figure exactly: "2.7d"
          // contains "2", and a tile-level substring check would pass on
          // the wrong number.
          data-testid={`${spec.testId}-value`}
          className="font-mono text-[14px] font-bold"
        >
          {metric?.value == null
            ? "—"
            : formatValue(metric.value, metric.decimals, spec.unit)}
        </span>
        <span className="text-[9px] tracking-[0.3px] opacity-80">
          {spec.label}
        </span>
        <span data-testid={`${spec.testId}-target`} className="text-[8.5px]">
          {metric ? formatTarget(metric, spec) : ""}
        </span>
      </TooltipTrigger>
      <TooltipContent>{spec.tooltip}</TooltipContent>
    </Tooltip>
  );
}

function TileSkeleton() {
  return (
    <div
      data-testid="sla-skeleton"
      aria-hidden
      className="h-[38px] min-w-[58px] animate-pulse rounded bg-surface-2"
    />
  );
}

export function SlaStrip() {
  const strip = useSlaStrip();

  return (
    <TooltipProvider delayDuration={0}>
      <div
        data-testid="sla-strip"
        className="flex items-center gap-[6px] border-r border-border px-[10px]"
      >
        <span className="text-[9px] tracking-[0.4px] text-faint uppercase [writing-mode:vertical-lr] rotate-180">
          SLA
        </span>

        {strip.isError && (
          <span
            role="status"
            data-testid="sla-error"
            title="The server could not compute your SLA figures. The tiles below are unknown, not zero."
            className="text-[11px] font-semibold text-error"
          >
            ⚠ SLA unavailable
          </span>
        )}

        {TILES.map((spec) =>
          strip.isPending ? (
            <TileSkeleton key={spec.key} />
          ) : (
            <Tile key={spec.key} spec={spec} metric={strip.data?.[spec.key]} />
          ),
        )}
      </div>
    </TooltipProvider>
  );
}
