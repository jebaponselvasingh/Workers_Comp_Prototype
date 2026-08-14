/**
 * The read-only detail sheet behind a schedule week or a line item (3.3).
 *
 * The prototype's `reviewScheduleWeek` and `reviewLineItem` modals, **minus
 * their approve buttons**. Story 3.3 ships no status-mutating command, so a
 * sheet with an "✓ Approve Payment" control would be a button that either does
 * nothing or writes through a path this story has not built; 3.4 adds the
 * command and the button together. The story's Dev Notes say so explicitly.
 *
 * **Radix `Dialog`, for `DocumentViewerDialog`'s reasons** — focus-trapped,
 * `role="dialog"`, closable by ✕, backdrop and Escape. The prototype's own
 * modal closes on ✕ and backdrop but not Escape and traps no focus (UX-DR11).
 *
 * **Nothing here is fetched.** Every value is already in the Bills payload the
 * tab holds, so the sheet is a projection of cached data rather than a second
 * request — unlike the document viewer, which fetches because a document's
 * sheet is assembled server-side from rows the case file does not carry.
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { BillLine, ExpenseLine, ScheduleWeek } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { formatDate } from "../Cards";
import {
  BILL_CATEGORY_LABEL,
  EXPENSE_CATEGORY_LABEL,
  LINE_ITEM_STATUS_LABEL,
  SCHEDULE_STATUS_LABEL,
} from "../labels";

/**
 * What the sheet is open on. A discriminated union rather than three nullable
 * props, for `ClaimDetailResponse`'s reason: the alternative can describe a
 * sheet open on a week *and* a bill at once, which is a state no click can
 * produce and every reader has to rule out by hand.
 */
export type SheetTarget =
  | { kind: "week"; week: ScheduleWeek }
  | { kind: "bill"; item: BillLine }
  | { kind: "expense"; item: ExpenseLine };

/**
 * What each status means for the reader, in a sentence.
 *
 * The prototype's modal ends with one of these lines; here they cover every
 * member rather than the three the prototype happened to branch on, so a
 * status can never render a sheet that says nothing about it.
 *
 * `payment_scheduled` names no batch date — the prototype's "next batch:
 * Tuesday" line belongs to Story 3.4, which is the story that makes a batch
 * exist. Promising a disbursement date the system does not schedule would be
 * worse than saying only what is known.
 */
const WEEK_STATUS_NOTE: Record<ScheduleWeek["status"], string> = {
  pending_approval: "This week is projected and has not been approved for payment.",
  due_this_week: "This week's indemnity payment falls due within the current period.",
  upcoming: "This week is scheduled and has not yet fallen due.",
  payment_scheduled: "Approved and queued for disbursement in the next payment batch.",
  paid: "This week's indemnity payment has been disbursed.",
};

const LINE_STATUS_NOTE: Record<BillLine["status"], string> = {
  pending_submission: "The provider has not yet submitted this item for payment.",
  under_review: "Submitted and awaiting review before it can be approved for payment.",
  payment_scheduled: "Approved and queued for disbursement in the next payment batch.",
  paid: "This item has been paid.",
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      data-testid="line-sheet-row"
      data-label={label}
      className="flex items-baseline justify-between gap-3 border-b border-hairline py-[5px] last:border-b-0"
    >
      <dt className="shrink-0 text-[11px] text-muted-text">{label}</dt>
      <dd className="min-w-0 text-right font-mono text-[11px] text-text">{children}</dd>
    </div>
  );
}

export function LineItemDialog({
  claimId,
  target,
  onClose,
}: {
  claimId: string;
  /** `null` while the sheet is closed. */
  target: SheetTarget | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent data-testid="line-item-sheet" className="max-h-[85vh] overflow-y-auto">
        {target === null ? null : (
          <>
            <DialogHeader>
              <DialogTitle data-testid="line-sheet-title" className="text-[13px]">
                {target.kind === "week"
                  ? `Week ${target.week.weekNo} — indemnity payment`
                  : target.item.label}
              </DialogTitle>
              <DialogDescription data-testid="line-sheet-subtitle" className="text-[11px]">
                {target.kind === "week"
                  ? "Scheduled indemnity instalment"
                  : target.kind === "bill"
                    ? "Medical bill"
                    : "Claim expense"}
              </DialogDescription>
            </DialogHeader>

            <dl data-testid="line-sheet" data-kind={target.kind}>
              <Row label="Claim ID">{claimId}</Row>
              {target.kind === "week" ? (
                <>
                  <Row label="Week">Wk {target.week.weekNo}</Row>
                  <Row label="Period">
                    {formatDate(target.week.periodStart)} – {formatDate(target.week.periodEnd)}
                  </Row>
                  <Row label="Amount">{formatCents(target.week.amountCents)}</Row>
                  <Row label="Status">{SCHEDULE_STATUS_LABEL[target.week.status]}</Row>
                </>
              ) : (
                <>
                  <Row label="Category">
                    {target.kind === "bill"
                      ? BILL_CATEGORY_LABEL[target.item.category]
                      : EXPENSE_CATEGORY_LABEL[target.item.category]}
                  </Row>
                  <Row label="Amount">{formatCents(target.item.amountCents)}</Row>
                  <Row label="Status">{LINE_ITEM_STATUS_LABEL[target.item.status]}</Row>
                </>
              )}
            </dl>

            <p data-testid="line-sheet-note" className="mt-2 text-[11.5px] text-muted-text">
              {target.kind === "week"
                ? WEEK_STATUS_NOTE[target.week.status]
                : LINE_STATUS_NOTE[target.item.status]}
            </p>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
