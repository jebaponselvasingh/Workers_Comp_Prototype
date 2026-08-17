/**
 * The detail sheet behind a schedule week or a line item — and, since Story
 * 3.4, the place a payment is approved.
 *
 * The prototype's `reviewScheduleWeek` and `reviewLineItem` modals, now
 * *with* their approve buttons: 3.3 shipped the sheet read-only because there
 * was no command behind the control, and said 3.4 would add the two together.
 * This is that.
 *
 * **The sheet resolves its row from the payload rather than holding a copy.**
 * `SheetTarget` is an *identity* — a week number or a line-item id — and the
 * row is looked up here on every render. That is the whole reason approving
 * works without any state of its own: the mutation installs a fresh payload,
 * the lookup finds the same row with its new status, and the chip, the note
 * and the button all follow. Holding the row (which is what this component did
 * in 3.3) would have left the sheet showing "Pending Approval" over a button
 * that had just succeeded.
 *
 * **Radix `Dialog`, for `DocumentViewerDialog`'s reasons** — focus-trapped,
 * `role="dialog"`, closable by ✕, backdrop and Escape. The prototype's own
 * modal closes on ✕ and backdrop but not Escape and traps no focus (UX-DR11).
 *
 * **Feedback is the updated state, not an alert** (UX-DR11, NFR-3). The
 * prototype closes the modal on approve; this keeps it open, because what the
 * handler needs to see is the *result* — the chip and the batch date — and a
 * modal that vanishes leaves them looking for confirmation in a table behind
 * it. A `role="status"` region announces the change for the same reason. A
 * refusal renders inline, in the sheet, at the control that caused it — never
 * a toast the handler has to catch and never a blocking dialog.
 */
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  conflictPaymentStatus,
  useApprovePayment,
  useClaimWriteInFlight,
  type BillLine,
  type ClaimFinancials,
  type ExpenseLine,
  type ScheduleWeek,
} from "@/api/claims";
import { formatCents } from "@/lib/money";

import { formatDate } from "../Cards";
import {
  BILL_CATEGORY_LABEL,
  EXPENSE_CATEGORY_LABEL,
  LINE_ITEM_STATUS_LABEL,
  SCHEDULE_STATUS_LABEL,
} from "../labels";

/**
 * Which row the sheet is open on — an **identity**, not the row itself.
 *
 * A discriminated union rather than three nullable props, for
 * `ClaimDetailResponse`'s reason: the alternative can describe a sheet open on
 * a week *and* a bill at once, which is a state no click can produce and every
 * reader has to rule out by hand.
 *
 * A week is addressed by its number and a line item by its id, which is the
 * two tables' own identities and exactly what the approval body sends.
 */
export type SheetTarget =
  | { kind: "week"; weekNo: number }
  | { kind: "bill"; id: number }
  | { kind: "expense"; id: number };

/** The resolved row, once the payload has been searched for the target. */
type Resolved =
  | { kind: "week"; week: ScheduleWeek }
  | { kind: "bill"; item: BillLine }
  | { kind: "expense"; item: ExpenseLine };

function resolve(financials: ClaimFinancials, target: SheetTarget): Resolved | null {
  if (target.kind === "week") {
    const week = financials.schedule.find((row) => row.weekNo === target.weekNo);
    return week ? { kind: "week", week } : null;
  }
  const list = target.kind === "bill" ? financials.bills.items : financials.expenses.items;
  const item = list.find((row) => row.id === target.id);
  if (!item) return null;
  return target.kind === "bill"
    ? { kind: "bill", item: item as BillLine }
    : { kind: "expense", item: item as ExpenseLine };
}

/**
 * What each status means for the reader, in a sentence.
 *
 * Every member is covered rather than the three the prototype happened to
 * branch on, so a status can never render a sheet that says nothing about it.
 * `payment_scheduled` now names the batch date, which is the sentence Story
 * 3.3 withheld because there was no batch to name.
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

/**
 * The approve control and the three states that replace it.
 *
 * The **server** decides which of the four renders: `approvable` is a field on
 * the row (AD-1), not a comparison this component makes against a status. That
 * is not pedantry — the approvable set differs between a week (three statuses)
 * and a line item (one), and a browser that reimplemented either would offer a
 * button the command refuses.
 */
function ApproveControl({
  approvable,
  status,
  nextBatchDate,
  pending,
  disabled,
  error,
  onApprove,
}: {
  approvable: boolean;
  status: string;
  nextBatchDate: string;
  pending: boolean;
  disabled: boolean;
  error: string | null;
  onApprove: () => void;
}) {
  return (
    <div className="mt-3">
      {approvable ? (
        <button
          type="button"
          data-testid="approve-payment"
          onClick={onApprove}
          disabled={disabled}
          className="w-full rounded border border-ok/40 bg-ok-soft px-3 py-[7px] text-[11.5px] font-bold text-ok hover:bg-ok-soft/70 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Approving…" : "✓ Approve Payment"}
        </button>
      ) : status === "payment_scheduled" ? (
        <p data-testid="approve-scheduled" className="text-[11.5px] font-bold text-steel">
          ✓ Scheduled for next payment batch — {formatDate(nextBatchDate)}
        </p>
      ) : status === "paid" ? (
        <p data-testid="approve-paid" className="text-[11.5px] font-bold text-ok">
          ✓ Payment already made
        </p>
      ) : (
        /* A line item the provider has not filed. The prototype falls through
           to "payment already made" here, which is the opposite of true — so
           this is the one branch that is not a port. */
        <p data-testid="approve-unavailable" className="text-[11.5px] text-faint">
          Nothing to approve yet — this item has not been submitted for payment.
        </p>
      )}

      {error !== null && (
        <p
          role="alert"
          data-testid="approve-error"
          className="mt-2 text-[11px] font-semibold text-error"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export function LineItemDialog({
  claimId,
  financials,
  target,
  onClose,
}: {
  claimId: string;
  financials: ClaimFinancials;
  /** `null` while the sheet is closed. */
  target: SheetTarget | null;
  onClose: () => void;
}) {
  const approve = useApprovePayment(claimId);
  const busy = useClaimWriteInFlight(claimId);
  // **A refusal is stored with the row it was raised about, and read back by
  // comparison** rather than cleared by an effect. A refusal belongs to one
  // row: without the key, "already paid" would follow the handler onto the
  // next week they open and read as that week being refused too. Deriving it
  // during render is the same guarantee with no effect to fire late — the
  // sheet cannot paint one row's error over another's, even for a frame.
  const [refusal, setRefusal] = useState<{ key: string; message: string } | null>(null);

  const targetKey =
    target === null
      ? null
      : target.kind === "week"
        ? `week:${target.weekNo}`
        : `${target.kind}:${target.id}`;
  const error = refusal !== null && refusal.key === targetKey ? refusal.message : null;

  const resolved = target === null ? null : resolve(financials, target);

  function onApprove(kind: SheetTarget["kind"], targetId: number, expectedVersion: number) {
    const key = `${kind === "week" ? "week" : kind}:${targetId}`;
    setRefusal(null);
    approve.mutate(
      { kind, targetId, expectedVersion },
      {
        onError: (failure) => {
          // The row's fresh status names the *reason*, which is the difference
          // between "somebody beat you to it" and "this went out in a batch".
          // The generic sentence covers everything else (a 403, a network
          // failure) without pretending to know which.
          const status = conflictPaymentStatus(failure);
          setRefusal({
            key,
            message:
              status === "paid"
                ? "This payment was disbursed in a batch while you were looking at it. The figures have been refreshed."
                : status === "payment_scheduled"
                  ? "This payment was already approved by someone else. The figures have been refreshed."
                  : failure instanceof Error && failure.message
                    ? failure.message
                    : "This payment could not be approved. Try again in a moment.",
          });
        },
      },
    );
  }

  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (open) return;
        // **The refusal dies with the sheet that raised it.** The key above
        // stops it following the handler to a *different* row; it does not
        // stop it greeting them on the way back into the *same* one, where a
        // week now correctly chipped `paid` would still be sitting under
        // "this was disbursed in a batch" nobody had just attempted. Cleared
        // here rather than on open because every close — the button, Esc, the
        // overlay — comes through this callback, so there is one place to miss.
        setRefusal(null);
        onClose();
      }}
    >
      <DialogContent data-testid="line-item-sheet" className="max-h-[85vh] overflow-y-auto">
        {resolved === null ? null : (
          <>
            <DialogHeader>
              <DialogTitle data-testid="line-sheet-title" className="text-[13px]">
                {resolved.kind === "week"
                  ? `Week ${resolved.week.weekNo} — indemnity payment`
                  : resolved.item.label}
              </DialogTitle>
              <DialogDescription data-testid="line-sheet-subtitle" className="text-[11px]">
                {resolved.kind === "week"
                  ? "Scheduled indemnity instalment"
                  : resolved.kind === "bill"
                    ? "Medical bill"
                    : "Claim expense"}
              </DialogDescription>
            </DialogHeader>

            <dl data-testid="line-sheet" data-kind={resolved.kind}>
              <Row label="Claim ID">{claimId}</Row>
              {resolved.kind === "week" ? (
                <>
                  <Row label="Week">Wk {resolved.week.weekNo}</Row>
                  <Row label="Period">
                    {formatDate(resolved.week.periodStart)} –{" "}
                    {formatDate(resolved.week.periodEnd)}
                  </Row>
                  <Row label="Amount">{formatCents(resolved.week.amountCents)}</Row>
                  <Row label="Status">{SCHEDULE_STATUS_LABEL[resolved.week.status]}</Row>
                </>
              ) : (
                <>
                  <Row label="Category">
                    {resolved.kind === "bill"
                      ? BILL_CATEGORY_LABEL[resolved.item.category]
                      : EXPENSE_CATEGORY_LABEL[resolved.item.category]}
                  </Row>
                  <Row label="Amount">{formatCents(resolved.item.amountCents)}</Row>
                  <Row label="Status">{LINE_ITEM_STATUS_LABEL[resolved.item.status]}</Row>
                </>
              )}
            </dl>

            {/* `role="status"` rather than a toast: the sentence *is* the
                confirmation, it is already where the handler is looking, and
                an assistive technology announces it politely without moving
                focus (UX-DR11's "no native dialogs, non-blocking feedback"). */}
            <p
              role="status"
              data-testid="line-sheet-note"
              className="mt-2 text-[11.5px] text-muted-text"
            >
              {resolved.kind === "week"
                ? WEEK_STATUS_NOTE[resolved.week.status]
                : LINE_STATUS_NOTE[resolved.item.status]}
            </p>

            <ApproveControl
              approvable={
                resolved.kind === "week" ? resolved.week.approvable : resolved.item.approvable
              }
              status={resolved.kind === "week" ? resolved.week.status : resolved.item.status}
              nextBatchDate={financials.summary.nextBatchDate}
              pending={approve.isPending}
              // Disabled while *any* command against this claim is in flight,
              // not just this one — `useClaimWriteInFlight`'s whole reason: two
              // overlapping writes send versions the first has consumed, and
              // the second is refused with a message about somebody else.
              disabled={busy}
              error={error}
              onApprove={() =>
                resolved.kind === "week"
                  ? onApprove("week", resolved.week.weekNo, resolved.week.version)
                  : onApprove(resolved.kind, resolved.item.id, resolved.item.version)
              }
            />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
