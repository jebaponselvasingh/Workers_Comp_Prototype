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

import type { SlaMetric } from "@/api/stats";
import { useSlaStrip } from "@/api/stats";

import {
  NO_VALUE,
  SLA_TILES,
  TONE_CLASS,
  formatTarget,
  formatValue,
  toneOf,
  type SlaTileSpec,
} from "./slaTiles";

/**
 * The tile vocabulary moved to `./slaTiles.ts` in Story 5.3, unchanged.
 *
 * The dashboard renders these same four metrics from the same `strip_of` a
 * second time, and the alternative to sharing the specs was a second copy of
 * four labels, four tooltips and two formatters — free to drift a word at a
 * time while both surfaces went on claiming to show one server value (AD-2).
 * The `data-testid` is the one thing that could not move: both groups are in
 * one DOM, so each composes its own prefix around the spec's `slug`.
 */
function Tile({
  spec,
  metric,
}: {
  spec: SlaTileSpec;
  metric: SlaMetric | undefined;
}) {
  const tone = toneOf(metric, spec);

  return (
    <Tooltip>
      <TooltipTrigger
        data-testid={`sla-${spec.slug}`}
        // A button because it must be focusable: a tooltip only a mouse
        // can open is not an explanation for everyone who needs one.
        type="button"
        className={`flex min-w-[58px] cursor-default flex-col items-center rounded px-2 py-[3px] leading-tight ${TONE_CLASS[tone]}`}
      >
        <span
          // Its own test id so assertions match the figure exactly: "2.7d"
          // contains "2", and a tile-level substring check would pass on
          // the wrong number.
          data-testid={`sla-${spec.slug}-value`}
          className="font-mono text-[14px] font-bold"
        >
          {metric?.value == null
            ? NO_VALUE
            : formatValue(metric.value, metric.decimals, spec.unit)}
        </span>
        <span className="text-[9px] tracking-[0.3px] opacity-80">
          {spec.label}
        </span>
        <span data-testid={`sla-${spec.slug}-target`} className="text-[8.5px]">
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

        {SLA_TILES.map((spec) =>
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
