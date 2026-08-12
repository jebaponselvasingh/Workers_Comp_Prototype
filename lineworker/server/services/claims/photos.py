"""The Photos tab, assembled server-side (Story 2.6, FR-DET-4).

Two things the prototype's `photosHTML` and `openPhoto` decide in the browser
are decided here (AD-1): **how many photos a claim has** — the number the tab
label carries — and **whether there is an image to show**.

## The count is published, not counted

`renderDet` writes ``Photos (${c.photos.length})`` into the tab bar while
`photosHTML` maps the same array into cards. That works because both read one
global object; here they are a tab strip and a grid in different components,
and a label that counted a list it does not hold would be a second answer to
"how many photos does this claim have". So `count` crosses the wire beside the
rows, and `photos_block` is the only thing that computes it — which is why
`test_the_count_is_the_number_of_cards_by_construction` can assert the two are
incapable of disagreeing rather than merely observed to agree.

## A card is addressed by id, not by index

`openPhoto(c, i)` takes an **array index**. That is a handle whose meaning
changes the moment anything is filed or removed, and the row it names is
whatever happens to be in that slot. Each card therefore carries its `photo.id`,
the same surrogate the row has in the database.

## Bytes

There are none: `blob_key` is null on all 293 seeded rows, because the
prototype's thumbnails are the 📷 emoji and it has no image files behind them.
Two fields say so honestly, and it takes two:

- `has_blob` — is there a file at all?
- `blob_url` — can this deployment hand the browser a direct link to it?

Collapsing them into one would break a volume-backed deployment, where every
photo has bytes and `BlobStore.url` answers `None` by design; a UI reading
`blobUrl === null` as "no photo" would then show the placeholder for the whole
grid. The store is optional and `None` in every deployment today — passing one
makes `blobUrl` real without changing a caller, which is the whole of what the
protocol boundary buys here (`document_content` takes it the same way, and for
the same stated reason: a default store wired into this signature would be a
storage decision taken by a viewer).

**Nothing here reads bytes.** A grid of thumbnails must not pull image data
through the API container; the block asks the store for a *link* and stops.

## No write path

There is no upload, capture, annotation or deletion anywhere in Epics 1–8, so
this module has no command and needs none of AD-4's machinery. `photo`'s grant
is `SELECT` (migration 0020) — read-only is the shape of the schema here, not a
discipline the components keep.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from data.models.core import Photo
from services.blobstore import BlobStore


@dataclass(frozen=True)
class PhotoCard:
    """One card of the incident-photo grid (AC 1, AC 2).

    `caption` and `source` are the prototype's `t` and `d` under their
    canonical names. The source line keeps its trailing `MM/DD` fragment inside
    the string — it is provenance ("Plant safety, 03/22"), not a date field, and
    splitting it would produce a date column that is wrong about a third of the
    time beside a source that had lost half its meaning.
    """

    id: int
    caption: str
    source: str
    #: Is there a file behind this row? False for all 293 seeded photos.
    has_blob: bool
    #: A direct link, when the deployment's store can mint one. `None` both for
    #: a photo with no bytes and for a store that does not presign — which is
    #: why `has_blob` is beside it rather than inferred from it.
    blob_url: str | None


@dataclass(frozen=True)
class PhotosBlock:
    """Everything the Photos tab draws, plus the number its label carries."""

    photos: tuple[PhotoCard, ...]
    #: The tab label's `(n)`. Computed here and nowhere else — see the module
    #: docstring on why this is not `photos.length` in the browser.
    count: int


def photos_block(
    photos: Sequence[Photo],
    *,
    store: BlobStore | None = None,
) -> PhotosBlock:
    """Assemble the Photos tab for one already-resolved claim's rows.

    Takes the rows rather than reading them: `claim_detail` has already fetched
    them under the caller's scope, and a second lookup here would be a second
    place the tab's contents could come from (`documents_block`'s rule, arrived
    at the same way — by a code review that found the same query running twice).

    Synchronous, unlike `documents_block`, because there is nothing left to ask
    anybody: no reference data joins in, no rule document decides anything, and
    `BlobStore.url` is a link-minting call rather than a fetch.
    """
    cards = tuple(
        PhotoCard(
            id=photo.id,
            caption=photo.caption,
            source=photo.source,
            has_blob=photo.blob_key is not None,
            # The `is not None` guard is load-bearing rather than defensive:
            # `VolumeBlobStore` refuses a key that is not a flat name — the
            # empty string included — so passing a null key through would turn
            # every seeded row into a `BlobStoreError` on the day a store is
            # wired in.
            blob_url=(
                store.url(photo.blob_key)
                if store is not None and photo.blob_key is not None
                else None
            ),
        )
        for photo in photos
    )
    return PhotosBlock(photos=cards, count=len(cards))
