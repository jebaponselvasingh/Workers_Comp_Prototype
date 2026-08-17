/**
 * The "⏰ Upcoming actions required" card (Story 3.5, UX-DR5).
 *
 * The prototype's `actionsBlockHTML` (lines 1224–1254) for look and feel —
 * dense rows, an urgency chip on the left, a right-aligned control, and the
 * empty state "No outstanding actions — claim is on track." What it renders is
 * *not* the prototype's list: that one is document- and bill-driven with no
 * rules behind it, and this one is the eleven trigger rules the BRD names,
 * evaluated on the server.
 *
 * **This component decides nothing.** It does not sort, filter, count, compare
 * a score or work out which epics have shipped. The order is the server's, the
 * urgency is the server's, whether a control is enabled is the server's, the
 * sentence explaining a disabled one is the server's, and which completion a
 * row offers is the server's. What is decided here is what the enum convention
 * leaves to the UI: a chip colour, a button's wording, and which of two
 * controls a row draws. `noDerivation.test.ts` walks this directory and fails
 * the build over anything more.
 *
 * **Disabled-with-a-tooltip is new in this codebase, and deliberately so.**
 * The house pattern elsewhere is to *replace* an unavailable control with a
 * sentence (`ApproveControl`'s four branches); AC 3 asks for the other thing,
 * because the point of these four rows is that the work is real and the
 * *surface* is not here yet — a sentence in place of the button would read as
 * "there is nothing to do". Radix tooltips are used in exactly one other place
 * (`SlaStrip`), which hosts its own `TooltipProvider`; this card follows that
 * precedent rather than adding a provider at the app root for one card.
 *
 * **The tooltip is not the only way to read the reason.** A tooltip needs a
 * pointer or a focus, and a disabled `<button>` takes neither — so the trigger
 * is a wrapping span (Radix's own guidance for disabled triggers), the reason
 * is also the control's `title`, and it is announced by `aria-describedby` on
 * the row. NFR-3 asks for no dead clicks; a control whose explanation only a
 * mouse can reach is a dead click for everybody else.
 *
 * **Feedback is in place and non-blocking** (UX-DR11): the completion controls
 * update where the handler is already looking, a refusal renders inline at the
 * card, and the whole thing is announced politely through a `role="status"`
 * region. There is still no toast infrastructure in this SPA — Story 3.4
 * recorded that and this story does not invent one.
 */
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import type { ActionCommand, ActionTarget, ClaimAction, ClaimDetail } from "@/api/claims";
import {
  useApproveAssessment,
  useClaimActions,
  useClaimWriteInFlight,
  useMarkOshaLogged,
  useSetDocumentReview,
} from "@/api/claims";

import { CaseCard } from "../Cards";
import { ACTION_TARGET_LABEL, ACTION_URGENCY_LABEL } from "../labels";
import { ACTION_CHIP_CLASS, ACTION_URGENCY_TONE } from "./actionTone";

/** What each completion control says, before and during its request. */
const COMMAND_LABEL: Record<ActionCommand, { idle: string; busy: string }> = {
  approve_assessment: { idle: "✓ Approve Assessment", busy: "Approving…" },
  mark_document_reviewed: { idle: "Mark Reviewed", busy: "Saving…" },
  confirm_document: { idle: "✓ Confirm", busy: "Confirming…" },
  mark_osha_logged: { idle: "Mark Logged", busy: "Saving…" },
};

function UrgencyChip({ action }: { action: ClaimAction }) {
  return (
    <span
      data-testid="action-urgency"
      data-urgency={action.urgency}
      className={`${ACTION_CHIP_CLASS} ${ACTION_URGENCY_TONE[action.urgency]}`}
    >
      {ACTION_URGENCY_LABEL[action.urgency]}
    </span>
  );
}

const CONTROL_CLASS =
  "shrink-0 rounded border border-border bg-surface-2 px-[9px] py-[4px] text-[11px] font-semibold text-text hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50";

/**
 * Targets this card can actually send the reader somewhere.
 *
 * **The card is mounted inside the Overview tab**, which is what makes this a
 * shorter list than `navigate` in `ClaimDetailPane` handles: `overview` is
 * already where the reader is standing, and `approve` is not a place at all —
 * its affordance is the ✓ completion button beside it. Both rendered an
 * enabled button that called `navigate` and fell through to nothing: two
 * near-identical buttons on every `initial` claim, one of which worked. That
 * is the dead click NFR-3 forbids, found by the review pass rather than by a
 * test, and it is why this set is stated rather than inferred from `enabled`
 * (code review, 2026-08-17).
 *
 * A *disabled* seam target still renders — the sentence naming the arriving
 * epic is the point of it, and a control that says why it is refused is not a
 * dead click.
 */
const NAVIGABLE_FROM_OVERVIEW: ReadonlySet<ActionTarget> = new Set<ActionTarget>([
  "bills",
  "documents",
  // Story 4.1. Not a tab — it opens the *right* pane's Diary → Meetings and
  // its scheduler, through the context `WorkspaceShell` provides. It belongs
  // in this set for the same reason the two tabs do: the control goes
  // somewhere, so rendering it is not a dead click.
  "meetings",
]);

/**
 * The row's "go to" control — a real navigation, or a visibly refused one.
 *
 * The disabled branch wraps the button in the tooltip trigger rather than
 * using the button as the trigger: a disabled `<button>` fires no pointer
 * events, so Radix would never see the hover and the tooltip would never open.
 */
function GoToControl({
  action,
  onNavigate,
}: {
  action: ClaimAction;
  onNavigate: (target: ActionTarget) => void;
}) {
  const label = ACTION_TARGET_LABEL[action.target];

  if (action.enabled) {
    return (
      <button
        type="button"
        data-testid="action-goto"
        data-target={action.target}
        onClick={() => onNavigate(action.target)}
        className={CONTROL_CLASS}
      >
        {label} →
      </button>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span data-testid="action-goto-seam" tabIndex={0}>
          <button
            type="button"
            data-testid="action-goto"
            data-target={action.target}
            disabled
            // Duplicated as a native tooltip so the reason survives without a
            // pointer — see the module docstring.
            title={action.disabledReason ?? undefined}
            className={CONTROL_CLASS}
          >
            {label}
          </button>
        </span>
      </TooltipTrigger>
      <TooltipContent data-testid="action-goto-reason">{action.disabledReason}</TooltipContent>
    </Tooltip>
  );
}

/** One row: chip, sentence, completion control (if any), go-to control. */
function ActionRow({
  action,
  claimVersion,
  busy,
  pending,
  onNavigate,
  onComplete,
}: {
  action: ClaimAction;
  claimVersion: number;
  /** Any write on this claim is in flight — what *disables* the control. */
  busy: boolean;
  /** **This row's own** command is in flight — what *relabels* it. */
  pending: boolean;
  onNavigate: (target: ActionTarget) => void;
  onComplete: (action: ClaimAction, expectedVersion: number) => void;
}) {
  const command = action.command;
  // The **document's** version when the command acts on a document, the
  // claim's otherwise. Both are published on payloads this component is
  // already holding, so neither is constructed here.
  const expectedVersion = action.documentVersion ?? claimVersion;
  const reasonId = `${action.id}-reason`;

  return (
    <li
      data-testid="action-row"
      data-action={action.id}
      aria-describedby={action.enabled ? undefined : reasonId}
      className="flex items-center justify-between gap-3 border-b border-hairline py-[7px] last:border-b-0"
    >
      <div className="flex min-w-0 items-center gap-2">
        <UrgencyChip action={action} />
        <span data-testid="action-label" className="min-w-0 text-[11.5px] text-text">
          {action.label}
        </span>
      </div>

      <div className="flex shrink-0 items-center gap-[6px]">
        {command !== null && (
          <button
            type="button"
            data-testid="action-command"
            data-command={command}
            onClick={() => onComplete(action, expectedVersion)}
            // Disabled while *any* command against this claim is in flight,
            // not only this one — `useClaimWriteInFlight`'s whole reason: two
            // overlapping writes send a version the first has consumed, and the
            // second is refused with a message about somebody else.
            //
            // **Disabled by `busy`, relabelled by `pending`**, and conflating
            // the two was a real defect (code review, 2026-08-17): editing the
            // recovery window in the Treatment card put "Approving…",
            // "Saving…" and "Confirming…" on three checklist buttons for
            // commands nobody had started. `LineItemDialog`'s approve control
            // already separates them.
            disabled={busy}
            className={`${CONTROL_CLASS} border-ok/40 bg-ok-soft text-ok hover:bg-ok-soft/70`}
          >
            {pending ? COMMAND_LABEL[command].busy : COMMAND_LABEL[command].idle}
          </button>
        )}
        {(!action.enabled || NAVIGABLE_FROM_OVERVIEW.has(action.target)) && (
          <GoToControl action={action} onNavigate={onNavigate} />
        )}
      </div>

      {!action.enabled && (
        <span id={reasonId} className="sr-only">
          {action.disabledReason}
        </span>
      )}
    </li>
  );
}

export function ActionsCard({
  claim,
  onNavigate,
}: {
  /** The whole case file: the two claim-level commands send its `version`. */
  claim: ClaimDetail;
  onNavigate: (target: ActionTarget) => void;
}) {
  const claimId = claim.claimId;
  const actions = useClaimActions(claimId);
  const busy = useClaimWriteInFlight(claimId);

  const approve = useApproveAssessment(claimId);
  const review = useSetDocumentReview(claimId);
  const osha = useMarkOshaLogged(claimId);

  // The first refusal any of the three is holding. One message rather than
  // three regions, because only one command can be in flight at a time (the
  // controls are disabled while `busy`), so a second error can only be the
  // previous one that has not been superseded yet.
  const failure = approve.error ?? review.error ?? osha.error;

  /**
   * Is *this row's own* command the one in flight?
   *
   * Matched on the document id as well as the step, because a claim can carry
   * two document rows offering the same step and only one of them was clicked.
   */
  function pendingFor(action: ClaimAction): boolean {
    switch (action.command) {
      case "approve_assessment":
        return approve.isPending;
      case "mark_osha_logged":
        return osha.isPending;
      case "mark_document_reviewed":
      case "confirm_document":
        return review.isPending && review.variables?.documentId === action.documentId;
      case null:
        return false;
    }
  }

  function complete(action: ClaimAction, expectedVersion: number): void {
    // **Clear what the other two are holding before starting a new command.**
    // TanStack keeps `error` and `isSuccess` until that same mutation runs
    // again, so without this a refused approval stays on screen underneath a
    // later OSHA entry that succeeded, describing a failure that no longer
    // applies — and the live region below accumulates every sentence of the
    // session. The same stale-refusal shape as `LineItemDialog`'s, one
    // component over (code review, 2026-08-17).
    approve.reset();
    review.reset();
    osha.reset();

    switch (action.command) {
      case "approve_assessment":
        approve.mutate({ expectedVersion });
        return;
      case "mark_osha_logged":
        osha.mutate({ expectedVersion });
        return;
      case "mark_document_reviewed":
      case "confirm_document":
        // `documentId` is non-null whenever the server sent a document step —
        // the generator sets the pair together. The guard is here because the
        // wire type admits null independently and a silent no-op would be a
        // button that does nothing.
        if (action.documentId !== null) {
          review.mutate({
            documentId: action.documentId,
            step: action.command,
            expectedVersion,
          });
        }
        return;
      case null:
        return;
    }
  }

  return (
    <div className="mb-[10px]">
      <CaseCard title="⏰ Upcoming actions required" testId="actions-card">
        {actions.isPending ? (
          <p data-testid="actions-loading" className="text-[11.5px] text-faint">
            Loading actions…
          </p>
        ) : actions.isError ? (
          <p role="alert" data-testid="actions-error" className="text-[11.5px] text-error">
            ⚠ The action checklist could not be loaded. Try again in a moment.
          </p>
        ) : actions.data.items.length === 0 ? (
          /* The prototype's own sentence. A card that rendered an empty list
             would read as "we have not checked", which is a different fact. */
          <p data-testid="actions-empty" className="text-[11.5px] text-faint">
            No outstanding actions — claim is on track.
          </p>
        ) : (
          <TooltipProvider delayDuration={0}>
            <ul className="flex flex-col">
              {actions.data.items.map((action) => (
                <ActionRow
                  key={action.id}
                  action={action}
                  claimVersion={claim.version}
                  busy={busy}
                  pending={pendingFor(action)}
                  onNavigate={onNavigate}
                  onComplete={complete}
                />
              ))}
            </ul>
          </TooltipProvider>
        )}

        {/* Polite, in place, and never a dialog (UX-DR11). The region is
            always mounted so a screen reader is subscribed to it before
            anything is written into it — a `role="status"` element that appears
            *with* its message is frequently not announced at all. */}
        {/* **One sentence, not three concatenated.** `isSuccess` is sticky
            until its own mutation runs again, so three adjacent ternaries
            announced "Claim assessment approved.OSHA 300 entry recorded." —
            no separator, and the first sentence re-announced long after the
            fact. `complete` resets the other two before every command, so at
            most one of these is true and the reader hears what they just did
            (code review, 2026-08-17). */}
        <p role="status" data-testid="actions-status" className="sr-only">
          {approve.isSuccess
            ? "Claim assessment approved."
            : review.isSuccess
              ? "Document review recorded."
              : osha.isSuccess
                ? "OSHA 300 entry recorded."
                : ""}
        </p>

        {failure !== null && (
          <p
            role="alert"
            data-testid="actions-failure"
            className="mt-2 text-[11px] font-semibold text-error"
          >
            {failure.message}
          </p>
        )}
      </CaseCard>
    </div>
  );
}
