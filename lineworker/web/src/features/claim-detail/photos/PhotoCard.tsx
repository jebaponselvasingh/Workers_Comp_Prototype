/**
 * One card of the incident-photo grid (Story 2.6, AC 1) — the prototype's
 * `.phcard`.
 *
 * Thumbnail on top, caption bold, source muted: the prototype's shape, ported
 * rather than re-designed.
 *
 * **A card is a `<button>`, not a div with a click handler.** The prototype
 * attaches `data-pi` to a `div` and delegates, which makes its photo viewer
 * unopenable by keyboard and invisible to a screen reader — evidence nobody can
 * reach without a mouse. Story 2.5's document rows fixed the same defect the
 * same way; a `<button>` gets focus, Enter and Space for free, and its
 * accessible name is the photo's own caption.
 *
 * **Three thumbnail states, not two.** The server publishes `hasBlob` and
 * `blobUrl` as separate facts, and collapsing them here would throw away the
 * distinction it drew:
 *
 * - `absent`  — no file at all. All 293 seeded photos, because the prototype
 *   has no images behind its rows; the 📷 treatment is the designed state.
 * - `unlinked` — there **is** a file, and this deployment cannot hand the
 *   browser a direct link to it (`BlobStore.url` answers null by design on a
 *   mounted volume). Saying "no image on file" here would be false.
 * - the image itself, when there is a link.
 *
 * The 📷 glyph lives here rather than on the wire, per the Enums convention:
 * what an absent image looks like is a rendering decision, exactly as the risk
 * band's colours are.
 */
import type { PhotoCardData } from "@/api/claims";

/** What the thumbnail region says when there is no image to show. */
const PLACEHOLDER = {
  absent: { glyph: "📷", note: "No image file" },
  unlinked: { glyph: "🖼", note: "On file — not viewable here" },
} as const;

export function PhotoCard({
  photo,
  onOpen,
}: {
  photo: PhotoCardData;
  onOpen: (photoId: number) => void;
}) {
  const state = photo.blobUrl ? "image" : photo.hasBlob ? "unlinked" : "absent";

  return (
    <button
      type="button"
      data-testid="photo-card"
      data-photo-id={photo.id}
      onClick={() => onOpen(photo.id)}
      className="flex min-w-0 flex-col overflow-hidden rounded border border-border text-left hover:border-steel focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <span
        data-testid="photo-thumb"
        data-state={state}
        className="flex h-[82px] items-center justify-center bg-surface-2 text-[20px] text-faint"
      >
        {state === "image" ? (
          <img
            src={photo.blobUrl!}
            alt={photo.caption}
            className="h-full w-full object-cover"
          />
        ) : (
          // `aria-hidden` on the glyph and the note read out instead: an emoji
          // announced as "camera" tells a screen-reader user nothing about why
          // there is no picture.
          <span className="flex flex-col items-center gap-[2px]">
            <span aria-hidden>{PLACEHOLDER[state].glyph}</span>
            <span className="text-[8.5px] tracking-[0.2px]">{PLACEHOLDER[state].note}</span>
          </span>
        )}
      </span>

      <span className="block bg-surface px-[7px] py-[5px]">
        <span className="block text-[10px] leading-[1.3] font-semibold text-text">
          {photo.caption}
        </span>
        <span className="block text-[9px] text-faint">{photo.source}</span>
      </span>
    </button>
  );
}
