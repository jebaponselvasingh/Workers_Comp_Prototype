/**
 * The read-only document viewer (Story 2.5, AC 4, UX-DR10) — the prototype's
 * `openDoc` + `showModal`.
 *
 * **Read-only structurally, not by discipline.** There is no input, no select
 * and no submit anywhere in this component, and no mutation hook is imported.
 * A viewer that merely *looked* read-only would be one prop away from being a
 * second write path for columns the four AD-4 commands already own.
 *
 * **The rows are the server's, and so is the dispatch.** Which fields a FROI
 * sheet shows — and that it shows eleven where a wage statement shows three —
 * is decided in `services/claims/documents.py` (AD-1). This component renders
 * `sheet.rows` in order and never composes a field from the claim object it
 * happens to be rendered beside; `sheetVariant` is used only to label the
 * sheet, never to decide what is in it.
 *
 * **Money is still cents on the wire.** A row carries either `text` or
 * `cents`, and `formatCents` is applied here — the one place in the app that
 * turns cents into a string. A server that had sent `"$1,432.00"` would have
 * been the single exception to a convention that holds everywhere else.
 *
 * **No native dialog (NFR-3, UX-DR11).** Radix's `Dialog` is a focus-trapped
 * `role="dialog"`, closable by ✕, by backdrop click and by Escape — three ways
 * out, where `window.alert` has one and blocks the whole tab. The prototype's
 * own modal closes on ✕ and backdrop but not Escape and traps no focus.
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDocumentSheet, type DocumentSheet } from "@/api/claims";
import { formatCents } from "@/lib/money";

/**
 * What an absent value reads as — the same em dash `formatDate` renders for a
 * null date, arrived at independently rather than by borrowing a date
 * formatter for rows that are not dates (see the row renderer below).
 */
const EMPTY = "—";

/**
 * What each sheet variant is called, for the dialog's own subtitle.
 *
 * **Keyed by the generated union, so a new server variant fails `tsc` here.**
 * Story 6.5 added `letter` and widened this to `Record<string, string>` with a
 * `?? summary` fallback, which type-checks against anything and therefore
 * checks nothing: the fourth variant would have shipped silently labelled
 * "Document summary sheet", which is a subtitle that contradicts the sheet
 * underneath it. The index signature is the *only* thing that made the fallback
 * necessary, so both are gone. A missing key is now a compile error naming this
 * constant, which is exactly the reminder the next variant needs.
 */
const VARIANT_LABEL: Record<DocumentSheet["sheetVariant"], string> = {
  froi: "First report of injury — full detail",
  summary: "Document summary sheet",
  // Story 6.5's third variant: a document that carries its own words. The
  // server dispatches on `bodyText is not None` rather than on the document
  // type, so a `rtw`-typed seeded row with no body keeps the summary sheet —
  // see `services/claims/documents.LETTER_VARIANT`.
  letter: "Generated letter",
} as const;

/**
 * The subtitle while there is no sheet to name (code review, 2026-08-12).
 *
 * The header renders outside the pending/error branch below, so a subtitle
 * that read "Loading…" whenever `sheet.data` was absent said it over the *error*
 * message too: a dialog stating "⚠ This document could not be loaded" beneath
 * a line promising a fetch still in flight. Two contradictory claims, and the
 * one a handler is likelier to act on is the wrong one — they wait instead of
 * retrying. Reachable on a 500, and on the 404 a document deleted between the
 * list render and the click produces.
 */
const STATUS_LABEL = {
  pending: "Loading…",
  error: "Could not be loaded",
} as const;

export function DocumentViewerDialog({
  claimId,
  documentId,
  onClose,
}: {
  claimId: string;
  /** `null` while the viewer is closed — which is also what disables the query. */
  documentId: number | null;
  onClose: () => void;
}) {
  const sheet = useDocumentSheet(claimId, documentId);

  return (
    <Dialog
      open={documentId !== null}
      onOpenChange={(open) => !open && onClose()}
    >
      <DialogContent
        data-testid="document-viewer"
        className="max-h-[85vh] overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle
            data-testid="document-viewer-title"
            className="text-[13px]"
          >
            {sheet.data?.name ?? "Document"}
          </DialogTitle>
          <DialogDescription
            data-testid="document-viewer-subtitle"
            className="text-[11px]"
          >
            {sheet.data
              ? VARIANT_LABEL[sheet.data.sheetVariant]
              : sheet.isError
                ? STATUS_LABEL.error
                : STATUS_LABEL.pending}
          </DialogDescription>
        </DialogHeader>

        {sheet.isPending ? (
          <div
            data-testid="document-viewer-skeleton"
            aria-hidden
            className="flex flex-col gap-2"
          >
            <span className="block h-4 w-2/3 animate-pulse rounded bg-surface-2" />
            <span className="block h-4 w-1/2 animate-pulse rounded bg-surface-2" />
            <span className="block h-4 w-3/4 animate-pulse rounded bg-surface-2" />
          </div>
        ) : sheet.isError ? (
          <p
            role="alert"
            data-testid="document-viewer-error"
            className="text-[11.5px] text-error"
          >
            ⚠ This document could not be loaded. Try again in a moment.
          </p>
        ) : (
          <>
            <dl
              data-testid="document-sheet"
              data-variant={sheet.data.sheetVariant}
            >
              {sheet.data.rows.map((row) => (
                <div
                  key={row.label}
                  data-testid="document-sheet-row"
                  data-label={row.label}
                  className="flex items-baseline justify-between gap-3 border-b border-hairline py-[5px] last:border-b-0"
                >
                  <dt className="shrink-0 text-[11px] text-muted-text">
                    {row.label}
                  </dt>
                  <dd className="min-w-0 text-right font-mono text-[11px] text-text">
                    {/* `cents` and `text` are mutually exclusive by
                        construction on the server (`SheetRow.of_*`), so the
                        order of these two branches is not a precedence
                        decision — the second is simply where every non-money
                        row lands.

                        **`formatDate` is deliberately not used here** (code
                        review, 2026-08-12). Most of these rows are not dates
                        — `Claim ID`, `Employee`, `Injury Type`, `Severity`,
                        `Status` — and routing them through a date formatter
                        works only because `formatDate` is currently the
                        identity function plus a null case. The moment it does
                        what its name promises and locale-formats an ISO
                        string, the whole FROI sheet renders `Invalid Date`,
                        and the developer making that change would be looking
                        at the timeline and the header, not here. The em dash
                        is what these two have in common, so that is what is
                        shared. */}
                    {row.cents !== null && row.cents !== undefined
                      ? formatCents(row.cents)
                      : (row.text ?? EMPTY)}
                  </dd>
                </div>
              ))}
            </dl>

            {/* **The letter's body, as pre-wrapped plain text** (Story 6.5).
                Never `dangerouslySetInnerHTML` and never markdown: this began
                as model output that a handler edited and then filed, so what a
                reader sees has to be literally what was stored — AD-16's
                output discipline, and the same reason `Transcript.tsx` renders
                assistant prose through a parser that cannot produce an element
                from a `<script>`.

                Rendered above the signature lines and below the labelled rows,
                which is where a letter sits on a filing: the rows say what the
                document *is*, the body is the document. */}
            {sheet.data.bodyText ? (
              <div
                data-testid="document-sheet-body"
                className="mt-3 border-t border-border pt-3 text-[11.5px] whitespace-pre-wrap text-text"
              >
                {sheet.data.bodyText}
              </div>
            ) : null}

            <div
              data-testid="document-sheet-signatures"
              className="mt-3 grid grid-cols-2 gap-4 border-t border-border pt-3"
            >
              {sheet.data.signatures.map((line) => (
                <span
                  key={line}
                  className="border-t border-faint pt-1 text-[10px] text-faint"
                >
                  {line}
                </span>
              ))}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
