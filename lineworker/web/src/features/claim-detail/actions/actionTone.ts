/**
 * Urgency chip colours for the action checklist (Story 3.5).
 *
 * `bills/statusTone.ts`'s twin, over a different vocabulary and with the same
 * rule about what colour means here: **"does this need me now?", not "is this
 * good?"**. High is the error tone because a statutory or lifecycle clock is
 * running; medium is a warning because somebody is waiting on this handler; low
 * is muted, because a routine review row that shouted would bury the two above
 * it on a six-row card.
 *
 * The prototype's `acti-tag` has two tones (`high`, `med`) and this is that
 * scheme with the third added — see `ActionUrgency` on why the padding rows
 * needed one.
 *
 * The enum convention leaves labels *and* colours to the UI. What the server
 * sends is a token; this is where a token becomes something to look at, and
 * nothing here decides which token a row gets.
 */
import type { ActionUrgency } from "@/api/claims";

/** Border + background + text, as one utility string per urgency. */
export const ACTION_URGENCY_TONE: Record<ActionUrgency, string> = {
  high: "border-error/40 bg-error-soft text-error",
  medium: "border-warn/40 bg-warn-soft text-warn",
  low: "border-border bg-surface-2 text-muted-text",
};

/** The shared chip shape — `statusTone.ts`'s `CHIP_CLASS`, one card over. */
export const ACTION_CHIP_CLASS =
  "inline-block shrink-0 rounded-full border px-[7px] py-[1px] text-[10px] font-semibold";
