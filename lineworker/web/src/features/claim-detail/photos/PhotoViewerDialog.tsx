/**
 * The read-only photo viewer (Story 2.6, AC 2, UX-DR10) — the prototype's
 * `openPhoto` + `showModal`.
 *
 * **Read-only structurally, not by discipline.** There is no input, no select
 * and no submit anywhere in this component, and no mutation hook is imported —
 * because there is no command to import: `photo`'s grant is `SELECT` (migration
 * 0020) and nothing in Epics 1–8 uploads, annotates or deletes one. An
 * affordance here would be an affordance for something the API cannot do.
 *
 * **It fetches nothing.** Story 2.5's document viewer is a query of its own,
 * because a sheet is a dozen rows of the claim's data assembled per document
 * and folding every sheet into the case file would multiply the console's
 * most-fetched payload. A photo's viewer shows three fields the grid is already
 * rendering behind the dialog — so it takes the card it was opened from, and a
 * round trip for them would be a round trip for data the client holds.
 *
 * **The photo it shows is the one it was opened by, not the one at an index.**
 * `openPhoto(c, i)` reads `c.photos[i]`; this takes the row's own id and looks
 * it up, so a grid that changed under it would render nothing rather than
 * render the wrong photograph under the right caption.
 *
 * **No native dialog (NFR-3, UX-DR11).** Radix's `Dialog` is a focus-trapped
 * `role="dialog"`, closable by ✕, by backdrop click and by Escape — three ways
 * out, where `window.alert` has one and blocks the whole tab. The prototype's
 * own modal closes on ✕ and backdrop but not Escape and traps no focus.
 */
import type { PhotoCardData } from "@/api/claims";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function PhotoViewerDialog({
  claimId,
  photo,
  onClose,
}: {
  claimId: string;
  /** `null` while the viewer is closed. */
  photo: PhotoCardData | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={photo !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent data-testid="photo-viewer" className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle data-testid="photo-viewer-title" className="text-[13px]">
            {photo?.caption ?? "Photo"}
          </DialogTitle>
          <DialogDescription className="text-[11px]">
            Incident & site photo — read only
          </DialogDescription>
        </DialogHeader>

        {photo && (
          <>
            <div
              data-testid="photo-viewer-image"
              data-state={photo.blobUrl ? "image" : photo.hasBlob ? "unlinked" : "absent"}
              className="flex h-[220px] items-center justify-center rounded border border-border bg-surface-2 text-[34px] text-faint"
            >
              {photo.blobUrl ? (
                <img
                  src={photo.blobUrl}
                  alt={photo.caption}
                  className="h-full w-full object-contain"
                />
              ) : (
                <span className="flex flex-col items-center gap-1">
                  <span aria-hidden>{photo.hasBlob ? "🖼" : "📷"}</span>
                  <span className="text-[10.5px] tracking-[0.2px]">
                    {photo.hasBlob
                      ? "Image on file — not viewable in this deployment"
                      : "No image file attached to this record"}
                  </span>
                </span>
              )}
            </div>

            {/* The three rows `openPhoto` prints, in its order. `Claim` is the
                business id — the same one the case header shows — because a
                photograph on its own says nothing about which file it belongs
                to, which is exactly what a viewer opened over a claim needs to
                make explicit. */}
            <dl data-testid="photo-sheet" className="mt-1">
              {[
                { label: "Caption", value: photo.caption },
                { label: "Source", value: photo.source },
                { label: "Claim", value: claimId },
              ].map((row) => (
                <div
                  key={row.label}
                  data-testid="photo-sheet-row"
                  data-label={row.label}
                  className="flex items-baseline justify-between gap-3 border-b border-hairline py-[5px] last:border-b-0"
                >
                  <dt className="shrink-0 text-[11px] text-muted-text">{row.label}</dt>
                  <dd className="min-w-0 text-right text-[11px] text-text">{row.value}</dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
