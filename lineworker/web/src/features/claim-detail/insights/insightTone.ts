/**
 * The tones the AI Insights cards are drawn in (Story 6.2, UX-DR12).
 *
 * `actionTone.ts`'s twin, one tab over, and it keeps that file's rule about
 * what colour means: **"does this need me?", not "is this good?"** — with one
 * deliberate exception, which is the whole reason this file exists rather than
 * the cards each reaching for a class string.
 *
 * The exception is the fraud card's low-risk variant. AC 3 asks for a low-risk
 * *confirmation* rather than an empty red-flag list, and a confirmation is the
 * one thing in this console that genuinely is "good news": the score sits below
 * both thresholds and nothing needs referring. So it is drawn in the ok tokens
 * — the same pair `VERDICT_ACCENT` gives an adequate reserve — and the red-flag
 * variant in the error tokens. Nothing here decides *which* variant a claim
 * gets; that is `outcome` on the payload, and `outcome` was decided by two
 * registered derivations on the server.
 *
 * The light palette is canonical (Story 1.1's tokens). The epics' "dark console
 * aesthetic" wording is a documented discrepancy ruled at story-creation time,
 * and no dark theme is invented here.
 */

/** Border + background + text, as one utility string per fraud outcome. */
export const FRAUD_OUTCOME_TONE: Record<"red_flags" | "low_risk", string> = {
  red_flags: "border-error/30 bg-error-soft text-error",
  low_risk: "border-ok/30 bg-ok-soft text-ok",
};

/** What the fraud card's own heading chip says for each outcome. */
export const FRAUD_OUTCOME_LABEL: Record<"red_flags" | "low_risk", string> = {
  red_flags: "Review indicated",
  low_risk: "Low risk",
};

/**
 * The shared bullet marker. A span rather than a `<ul>` marker so the list
 * keeps the console's 11.5px density without fighting the browser's default
 * indentation, which is what every other list on the case file does.
 *
 * Read by `InsightShell`, which is what makes it shared. It was exported and
 * then hardcoded again at the one call site, which is the state where a
 * constant is worse than no constant: two spellings of one decision, and the
 * next reader has no way to tell which one is authoritative (review of Story
 * 6.2, L2).
 */
export const BULLET_CLASS = "mt-[1px] shrink-0 text-faint";
