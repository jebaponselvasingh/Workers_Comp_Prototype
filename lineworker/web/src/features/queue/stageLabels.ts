/**
 * The UI's names for the four stages — icons, labels, one copy.
 *
 * They were defined twice, identically, in `StageGroup.tsx` (which draws the
 * section header) and `ClaimCard.tsx` (which draws the stage pill). Two
 * copies of a display mapping is how a queue ends up with a section headed
 * "Investigation" full of cards pilled "Investigating".
 *
 * A module of its own rather than an export from either component, because
 * `react-refresh/only-export-components` is right about the reason: a file
 * that exports both a component and a constant loses fast refresh, and the
 * lint rule was already flagging both files for it.
 *
 * **Labels are the UI's and the wire's values are not.** The server sends
 * `intake | investigation | treatment | settled` (the enum convention:
 * snake_case on the wire, display text owned here). This file is the whole
 * of the translation — there is no rule in it, which is why the derivation
 * guard is happy to see `STAGE_LABEL[stage]` in a component.
 */
import type { Stage } from "@/api/claims";

/** The prototype's `STAGE_GROUPS` icons (line 1133). */
export const STAGE_ICON: Record<Stage, string> = {
  intake: "📥",
  investigation: "🔍",
  treatment: "🩺",
  settled: "✅",
};

export const STAGE_LABEL: Record<Stage, string> = {
  intake: "Intake",
  investigation: "Investigation",
  treatment: "Treatment",
  settled: "Settled",
};
