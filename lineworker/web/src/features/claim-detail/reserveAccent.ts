/**
 * The reserve verdict's colours — one map, two surfaces (Story 3.3).
 *
 * Written by Story 3.2 inside `overview/TreatmentOverview.tsx`, and moved here
 * when Story 3.3's Bills financial summary became the second card to render
 * the same verdict. The server sends a token and the UI owns the colour, per
 * the enum convention — but "the UI owns it" has to mean *one* place, or the
 * treatment card and the Bills tab end up disagreeing about whether a light
 * reserve is red or amber, which reads as two different judgements of one
 * claim rather than one judgement shown twice.
 *
 * **Note which way round the two warnings go, because it reads backwards at
 * first glance: light is the error.** A light reserve is one that will not
 * cover the exposure — money the carrier has not put aside — while a heavy one
 * is merely capital tied up, which is a warning rather than a problem. The
 * prototype makes the same call (`var(--er)` for light, `var(--wn)` for heavy)
 * and it is the only sensible one; it is written down here because it is
 * exactly the kind of pairing a later refactor "corrects".
 *
 * `closed_final` is muted: a settled claim's verdict is a statement, not a
 * status to act on, and colouring it green would read as a pass mark on a
 * comparison nobody made. `indeterminate` is muted for the stronger version of
 * the same reason — it is the *absence* of a comparison, and any of the three
 * status colours would be the console implying it had reached a conclusion.
 */
import type { ReserveVerdict } from "@/api/claims";

export const VERDICT_ACCENT: Record<ReserveVerdict, { text: string; box: string }> = {
  light: { text: "text-error", box: "border-error/30 bg-error-soft" },
  adequate: { text: "text-ok", box: "border-ok/30 bg-ok-soft" },
  heavy: { text: "text-warn", box: "border-warn/30 bg-warn-soft" },
  closed_final: { text: "text-faint", box: "border-border bg-surface-2" },
  indeterminate: { text: "text-muted-text", box: "border-border bg-surface-2" },
};
