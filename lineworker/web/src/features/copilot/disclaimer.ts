/**
 * The line under the composer — UX-DR8's disclaimer, in one place.
 *
 * A constant rather than a string in the JSX because two things read it: the
 * component that renders it, and the test that asserts it is on screen. A
 * sentence spelled twice is a sentence that stops matching the day somebody
 * improves one of them.
 *
 * **The wording is the prototype's own**, kept deliberately. The prototype's
 * copilot carried "AI-generated — verify before acting" beneath its input, and
 * the honest-degradation posture (AD-14) makes that *more* true rather than
 * less: this build's answers come from a real local model rather than from
 * canned text, so the sentence is now a live caution instead of a decoration on
 * pre-written prose.
 *
 * It says nothing about figures, and that is a decision. AD-2 means every money
 * amount and date in an answer was computed by a deterministic service and
 * quoted verbatim, so a disclaimer that told a reader to check the arithmetic
 * would be describing a risk this design removed — and would dilute the one it
 * has not, which is that the surrounding prose is a model's.
 */
export const COPILOT_DISCLAIMER = "AI-generated — verify before acting. Not legal advice.";
