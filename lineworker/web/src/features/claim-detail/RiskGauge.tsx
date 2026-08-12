/**
 * The semicircular claim-risk gauge (UX-DR4) — the prototype's inline SVG,
 * ported as a typed component.
 *
 * Two arcs over a 100×56 viewBox: a track in the border token, and a
 * risk-coloured arc whose `stroke-dasharray` fills 115, 75 or 35 of the
 * path's 132 units. Those three lengths are the prototype's and they are
 * *drawing* rather than rule — the band they are chosen by is the `risk`
 * derivation the server sent, which is the same one the queue card's dot
 * reads (AD-10). The gauge cannot disagree with the card beside it because
 * neither of them decides anything.
 *
 * `stroke` is set through a CSS variable rather than a Tailwind class so the
 * one value that changes per band lives in a single `Record` beside the
 * dasharray it is paired with. Both are keyed by the band, so a fourth band
 * would fail the build here rather than render a colourless arc.
 */
import type { RiskBand } from "@/api/claims";

import { RISK_DESCRIPTION, RISK_LABEL } from "./labels";

/** The arc's filled length, out of the path's 132 units. The prototype's. */
const ARC_LENGTH: Record<RiskBand, number> = {
  high: 115,
  med: 75,
  low: 35,
};

/** The token each band is drawn in — `--er` / `--wn` / `--ok` from 1.1. */
const ARC_COLOR: Record<RiskBand, string> = {
  high: "var(--color-error)",
  med: "var(--color-warn)",
  low: "var(--color-ok)",
};

const ARC_PATH = "M8,50 A42,42 0 0,1 92,50";
const ARC_TOTAL = 132;

export function RiskGauge({ risk }: { risk: RiskBand }) {
  return (
    <div className="flex shrink-0 flex-col items-center">
      <svg
        width="68"
        height="40"
        viewBox="0 0 100 56"
        role="img"
        data-testid="risk-gauge"
        data-risk={risk}
        aria-label={`Claim risk: ${RISK_DESCRIPTION[risk]}`}
      >
        <path
          d={ARC_PATH}
          fill="none"
          stroke="var(--color-border)"
          strokeWidth="9"
          strokeLinecap="round"
        />
        <path
          data-testid="risk-gauge-arc"
          d={ARC_PATH}
          fill="none"
          stroke={ARC_COLOR[risk]}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={`${ARC_LENGTH[risk]} ${ARC_TOTAL}`}
        />
        <text
          x="50"
          y="46"
          textAnchor="middle"
          fontFamily="JetBrains Mono"
          fontSize="13"
          fontWeight="700"
          fill={ARC_COLOR[risk]}
          // The band is already in the SVG's accessible name; repeating it
          // as text would have a screen reader read the gauge twice.
          aria-hidden
        >
          {RISK_LABEL[risk]}
        </text>
      </svg>
      <span className="text-[9.5px] tracking-[0.3px] text-faint uppercase">Claim risk</span>
    </div>
  );
}
