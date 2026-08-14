/**
 * Status chip colours for the Bills & Payments tab (Story 3.3).
 *
 * The prototype's `.wkstat` and `.billstat` classes, on Epic 1's tokens. The
 * enum convention leaves labels *and* colours to the UI; what the server sends
 * is a token, and these two maps are where a token becomes something to look
 * at.
 *
 * **Colour here means "does this need me?", not "is this good?".** That is why
 * `paid` is muted rather than green and `due_this_week` is the loud one: a
 * handler scanning a twenty-row schedule is looking for the week that needs an
 * action this week, and a column of green ticks for every elapsed week would
 * bury it. The prototype makes the same call.
 *
 * `pending_approval` and `under_review` are warnings rather than errors —
 * something is waiting on somebody, which is normal — while nothing on this
 * tab is an error tone at all. A bill nobody has submitted is a gap in the
 * file, not a failure of the console, and `pending_submission` is muted for
 * that reason: it is the state the *provider* is in.
 */
import type { LineItemStatus, ScheduleWeekStatus } from "@/api/claims";

/** Border + background + text, as one utility string per state. */
export const SCHEDULE_STATUS_TONE: Record<ScheduleWeekStatus, string> = {
  due_this_week: "border-warn/40 bg-warn-soft text-warn",
  pending_approval: "border-warn/30 bg-warn-soft text-warn",
  upcoming: "border-border bg-surface-2 text-muted-text",
  payment_scheduled: "border-steel/40 bg-surface-2 text-steel",
  paid: "border-border bg-surface-2 text-faint",
};

export const LINE_ITEM_STATUS_TONE: Record<LineItemStatus, string> = {
  under_review: "border-warn/40 bg-warn-soft text-warn",
  pending_submission: "border-border bg-surface-2 text-muted-text",
  payment_scheduled: "border-steel/40 bg-surface-2 text-steel",
  paid: "border-border bg-surface-2 text-faint",
};

/** The shared chip shape — dense, uppercase-free, one line. */
export const CHIP_CLASS =
  "inline-block shrink-0 rounded-full border px-[7px] py-[1px] text-[10px] font-semibold";
