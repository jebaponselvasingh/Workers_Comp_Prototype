/**
 * The body-silhouette injury diagram (UX-DR6) — the prototype's inline SVG,
 * ported as a typed component.
 *
 * **Data-only, by construction.** The single prop is the markers array the
 * server sent, each one already carrying the severity band it is drawn in
 * (`services/claims/detail.py` bands every marker through the one registered
 * `risk` derivation). The prototype re-derives a colour per marker inside
 * `injHTML` from the raw score, which is a second banding rule living in a
 * drawing function; here there is nothing to derive, and `noDerivation.test.ts`
 * walks this directory to keep it that way.
 *
 * **The two dictionaries below are drawing, not rule.** `HOTSPOTS` is where a
 * region sits on a 130×310 figure and `RING` is how big the halo around it
 * is — geometry, ported verbatim from the prototype so the diagram a
 * stakeholder reviewed is the diagram that ships. The band → token map is the
 * same kind of thing the risk gauge's `ARC_COLOR` is: one value per band,
 * keyed so that a fourth band fails the build here rather than rendering a
 * colourless marker.
 *
 * [Source: docs/Workers_Comp_Prototype.html — hotspots 1463, markers
 * 1466-1475, silhouette 1476-1494]
 */
import type { InjuryMarker, RiskBand } from "@/api/claims";

interface Hotspot {
  cx: number;
  cy: number;
  r: number;
}

/**
 * Where each of the eleven regions sits on the figure, and how big its
 * marker is. The prototype's `HS`, verbatim.
 *
 * `ears` shares the head's hotspot — the figure has no ear geometry, and the
 * prototype points both at the same circle rather than inventing one. Kept
 * as a duplicate entry rather than an alias so the dictionary reads as
 * "every key has a position" and a reviewer comparing it against
 * `editOptions.bodyParts` can count eleven.
 */
const HOTSPOTS: Record<string, Hotspot> = {
  head: { cx: 65, cy: 28, r: 18 },
  ears: { cx: 65, cy: 28, r: 18 },
  shoulder_right: { cx: 98, cy: 72, r: 12 },
  shoulder_left: { cx: 32, cy: 72, r: 12 },
  forearm_right: { cx: 110, cy: 115, r: 10 },
  hand_right: { cx: 116, cy: 148, r: 9 },
  hand_left: { cx: 14, cy: 148, r: 9 },
  torso: { cx: 65, cy: 100, r: 16 },
  lumbar: { cx: 65, cy: 158, r: 13 },
  tibia_left: { cx: 47, cy: 240, r: 11 },
  tibia_right: { cx: 83, cy: 240, r: 11 },
};

/**
 * Where an unrecognised key is drawn — the prototype's `HS[m.bodyKey]||HS["torso"]`.
 *
 * Unreachable through the UI: the select is built from the server's
 * vocabulary and the command refuses anything outside it. It is kept because
 * the alternative when a key does go missing is an `undefined.cx`, which
 * renders nothing at all and reads as "this claim has no injury" — the one
 * thing a body map must never say by accident.
 */
const FALLBACK_KEY = "torso";

/** Band → the token a marker is filled with. The gauge's `ARC_COLOR` set. */
const MARKER_COLOR: Record<RiskBand, string> = {
  high: "var(--color-error)",
  med: "var(--color-warn)",
  low: "var(--color-ok)",
};

/** The prototype's three concentric radii and their opacities. */
const HALO_OUTER = 10;
const HALO_INNER = 5;
const PULSE_OFFSET = 4;
const PULSE_GROWTH = 14;
const CROSSHAIR = 6;

/**
 * The silhouette itself: an ellipse for the head, rounded rects for the
 * torso and limbs. Neutral greys with a hairline stroke, ported from the
 * prototype (UX-DR12 keeps its palette canonical) — deliberately *not*
 * design tokens, because these are the figure's own material and tinting
 * them with a semantic colour would make an uninjured leg look like a state.
 */
function Silhouette() {
  const fill = "#D8DFE6";
  const limb = "#D0D8E0";
  const extremity = "#C8D1D9";
  const stroke = "#B0BAC4";

  return (
    <g stroke={stroke} strokeWidth="1">
      <ellipse cx="65" cy="28" rx="18" ry="21" fill={fill} />
      <rect x="60" y="46" width="10" height="10" rx="2" fill={fill} />
      <rect x="36" y="55" width="58" height="90" rx="8" fill={fill} />
      <rect x="18" y="57" width="18" height="52" rx="7" fill={limb} />
      <rect x="94" y="57" width="18" height="52" rx="7" fill={limb} />
      <rect x="14" y="108" width="15" height="48" rx="6" fill={limb} />
      <rect x="101" y="108" width="15" height="48" rx="6" fill={limb} />
      <ellipse cx="21" cy="162" rx="8" ry="10" fill={extremity} />
      <ellipse cx="109" cy="162" rx="8" ry="10" fill={extremity} />
      <rect x="34" y="144" width="62" height="36" rx="10" fill={fill} />
      <rect x="34" y="176" width="26" height="60" rx="8" fill={limb} />
      <rect x="70" y="176" width="26" height="60" rx="8" fill={limb} />
      <rect x="36" y="234" width="22" height="55" rx="7" fill={limb} />
      <rect x="72" y="234" width="22" height="55" rx="7" fill={limb} />
      <ellipse cx="47" cy="294" rx="13" ry="7" fill={extremity} />
      <ellipse cx="83" cy="294" rx="13" ry="7" fill={extremity} />
    </g>
  );
}

/**
 * One marker: two translucent haloes, a solid disc with a white ring, a
 * crosshair — and, on the primary, an expanding ring that fades.
 *
 * The pulse is SMIL (`<animate>`), which is what the prototype uses and what
 * React passes through unchanged as lowercase attributes. A CSS keyframe
 * would have been equally acceptable per the story's Dev Notes; SMIL keeps
 * the animation inside the element it animates, which matters here because
 * the marker's radius varies per region and a CSS rule would need a custom
 * property per hotspot to say the same thing.
 */
function Marker({ marker }: { marker: InjuryMarker }) {
  const spot = HOTSPOTS[marker.bodyKey] ?? HOTSPOTS[FALLBACK_KEY];
  const color = MARKER_COLOR[marker.band];

  return (
    <g
      data-testid="injury-marker"
      data-body-key={marker.bodyKey}
      data-band={marker.band}
      data-primary={marker.primary}
      // The whole diagram is one image with one accessible name (below), so
      // the individual markers are hidden from assistive technology — the
      // injuries are listed as text in the popover's summary and in the
      // severity card, where they can actually be read.
      aria-hidden
    >
      <circle cx={spot.cx} cy={spot.cy} r={spot.r + HALO_OUTER} fill={color} opacity="0.12" />
      <circle cx={spot.cx} cy={spot.cy} r={spot.r + HALO_INNER} fill={color} opacity="0.22" />
      <circle
        cx={spot.cx}
        cy={spot.cy}
        r={spot.r}
        fill={color}
        opacity="0.88"
        stroke="white"
        strokeWidth="1.5"
      />
      {marker.primary && (
        <circle
          data-testid="injury-marker-pulse"
          cx={spot.cx}
          cy={spot.cy}
          r={spot.r + PULSE_OFFSET}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          opacity="0.7"
        >
          <animate
            attributeName="r"
            from={spot.r}
            to={spot.r + PULSE_GROWTH}
            dur="1.8s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            from="0.7"
            to="0"
            dur="1.8s"
            repeatCount="indefinite"
          />
        </circle>
      )}
      <line
        x1={spot.cx - CROSSHAIR}
        y1={spot.cy}
        x2={spot.cx + CROSSHAIR}
        y2={spot.cy}
        stroke="white"
        strokeWidth="1.5"
      />
      <line
        x1={spot.cx}
        y1={spot.cy - CROSSHAIR}
        x2={spot.cx}
        y2={spot.cy + CROSSHAIR}
        stroke="white"
        strokeWidth="1.5"
      />
    </g>
  );
}

export function BodyMap({ markers }: { markers: InjuryMarker[] }) {
  return (
    <svg
      viewBox="0 0 130 310"
      width="120"
      height="285"
      role="img"
      data-testid="body-map"
      // Named by how many injuries it shows rather than by where they are:
      // the regions are read out as text beside it, and a name that listed
      // eleven coordinates would be read before every one of them.
      aria-label={`Injury locations — ${markers.length} marked`}
    >
      <Silhouette />
      {/* Markers after the figure so they draw on top of it; the primary is
          first in the array (the server orders it so) and secondaries layer
          over it where they share a region, which is the prototype's order
          and the one that keeps an added injury visible. */}
      {markers.map((marker, index) => (
        <Marker key={marker.id ?? `primary-${index}`} marker={marker} />
      ))}
    </svg>
  );
}
