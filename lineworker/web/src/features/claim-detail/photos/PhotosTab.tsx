/**
 * The Photos tab (Story 2.6, UX-DR5) — the prototype's `photosHTML`.
 *
 * One card, the grid of photographs, and the read-only viewer a card opens.
 * Story 2.2 shipped this tab as an explicit empty state naming this story; that
 * seam is gone from `DetailTabs` and this is what replaced it — the last of
 * Epic 2's seams, leaving only Bills & Payments (Epic 3) and AI Insights
 * (Epic 6).
 *
 * **No loading or error state of its own (NFR-3, AD-9).** The photos ride the
 * shared `claimDetail` query, so there is one request, one skeleton and one
 * error message for the whole case file — `ClaimDetailPane` renders all three.
 * A skeleton here would imply a fetch that does not happen, which is a worse
 * kind of dishonesty than a missing one: it would tell a handler the console
 * was waiting on their evidence when it was waiting on nothing.
 *
 * **Which photo is open is local UI state** (AD-9), like the tab selection
 * above it: it is not a server resource and it is not shareable the way
 * `?claim=` is — a link to a modal over a claim somebody else cannot see is a
 * link to a 404. It resets with the tab, because `DetailTabs` renders this
 * panel only while its tab is selected.
 *
 * **The empty state is a branch of its own.** Every seeded claim carries two to
 * four photos, so it is unreachable against the dev seed — which is exactly why
 * it is written and tested rather than left to a `.map` over an empty array.
 * A card that rendered an empty grid would read as "nothing was photographed"
 * only to somebody who already knew the grid was the whole story; the sentence
 * says it.
 */
import { useState } from "react";

import type { ClaimDetail } from "@/api/claims";

import { PhotoCard } from "./PhotoCard";
import { PhotoViewerDialog } from "./PhotoViewerDialog";

export function PhotosTab({ claim }: { claim: ClaimDetail }) {
  const [openPhotoId, setOpenPhotoId] = useState<number | null>(null);
  const { photos } = claim.photos;

  // Looked up by id rather than held as an object, so the dialog cannot go on
  // rendering a photo the payload no longer carries after a refetch.
  const openPhoto = photos.find((photo) => photo.id === openPhotoId) ?? null;

  return (
    <div data-testid="photos-tab" className="flex min-w-0 flex-col">
      <section className="rounded-lg border border-border bg-surface p-3">
        <h3 className="font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
          Incident &amp; site photos
        </h3>

        {photos.length === 0 ? (
          <p data-testid="photos-empty" className="mt-[10px] text-[11.5px] text-faint">
            No photos on file.
          </p>
        ) : (
          <div className="mt-[9px] grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-[7px]">
            {photos.map((photo) => (
              <PhotoCard key={photo.id} photo={photo} onOpen={setOpenPhotoId} />
            ))}
          </div>
        )}
      </section>

      <PhotoViewerDialog
        claimId={claim.claimId}
        photo={openPhoto}
        onClose={() => setOpenPhotoId(null)}
      />
    </div>
  );
}
