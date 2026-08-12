"""The one binary-storage boundary (Story 2.5, Conventions — Binary storage).

Every file byte in this system rides this protocol. The architecture's
convention is that the *backing store* is swappable without touching a caller,
and the MinIO-versus-mounted-volume decision is explicitly Deferred — so what
Story 2.5 lands is the boundary, not the decision.

**Why the protocol arrives before a single real file does.** The `document`
table (Story 2.2) has 563 seeded rows and `blob_key` is null on every one of
them: the prototype has no files behind its document rows, so there are no
bytes to store yet. The temptation is therefore to skip this module and read
the claim columns directly in the viewer — and that is exactly how a codebase
ends up with `open(path)` in three services and no way to move to object
storage without touching all three. The boundary is what is binding.

## What a caller may assume

Four operations, and nothing else: `put`, `get`, `delete`, `url`. In
particular there is no `list`, no `exists` and no `stat`. Each of those would
be a capability the domain does not need and an implementation would have to
emulate — and `exists` in particular invites the check-then-read race that
`get` raising `BlobNotFound` closes by construction.

**`url` may return `None`, and callers must handle it.** A store that can mint
a time-limited direct link (S3/MinIO presigned) returns one; a store that
cannot (a mounted volume behind this API) returns `None` and the caller streams
the bytes itself. Making it optional in the *protocol* is what stops the
decision leaking: a caller written against a store that always presigns would
break on the day the deployment chose a volume.

## What Story 2.5 actually uses

Nothing, on the happy path — and that is the honest statement rather than an
admission. `GET /claims/{id}/documents/{id}/content` answers a **structured
sheet assembled server-side from claim and document columns** (AD-1), because
that is what the prototype's viewer renders and there are no bytes to serve.
The `blob_key` resolution is wired through `document_content` so that the day a
real PDF is attached, the route already knows where to ask.

`VolumeBlobStore` exists so the protocol has a conformance test with something
real behind it, and because a mounted volume is one of the two candidates in
the deferred decision. It is not wired into the app's dependency graph; the
first story that ingests a document chooses and configures one.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable


class BlobNotFound(LookupError):
    """No object is stored under that key.

    A `LookupError`, like `ClaimNotVisible`, because the caller's response is
    the same shape: this is an absence to be reported, not a failure to be
    retried.
    """


class BlobStoreError(RuntimeError):
    """The store is reachable but could not complete the operation.

    Distinct from `BlobNotFound` because the operator response differs: a
    missing key is a data question, a store error is an infrastructure one.
    """


@runtime_checkable
class BlobStore(Protocol):
    """Binary storage, as the rest of the system is allowed to see it."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store `data` under `key`, replacing anything already there.

        `content_type` is required rather than optional: it is what a browser
        needs to render a PDF instead of downloading it, and a store that
        guessed from the key's suffix would guess differently per backend.
        """
        ...

    def get(self, key: str) -> bytes:
        """The bytes stored under `key`, or raise `BlobNotFound`."""
        ...

    def delete(self, key: str) -> None:
        """Remove `key`.

        **Idempotent**: deleting an absent key is not an error. Story 8.1's
        PHI purge walks a claim's blobs and must not fail half-way through
        because one was already gone — a purge that cannot be re-run is a
        purge that cannot be trusted.
        """
        ...

    def url(self, key: str) -> str | None:
        """A direct link to `key`, if this store can mint one.

        `None` means "ask me for the bytes instead" — see the module
        docstring on why this is optional in the protocol rather than in one
        implementation.
        """
        ...


class VolumeBlobStore:
    """A `BlobStore` over a mounted directory.

    One of the two candidates in the deferred storage decision, and the one
    that lets the protocol have a conformance test with real bytes behind it.

    **Keys are checked, not trusted.** A key is a flat name in the store's
    namespace; anything containing a path separator or a parent reference is
    refused rather than resolved. Without that, a `blob_key` that ever came
    from outside this system — an ingest endpoint, an AD-13 agent tool — would
    be a path traversal, and the traversal would be *reading arbitrary files
    from the API container*, which is the worst possible thing for a document
    viewer to be able to do.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        if not key or "/" in key or "\\" in key or key in {".", ".."}:
            raise BlobStoreError("a blob key is a flat name; it may not contain a path separator")
        return self._root / key

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        # `content_type` is accepted and not stored: a directory has nowhere to
        # put it. That is a real limitation of this backend rather than an
        # oversight, and it is one of the things the deferred decision weighs —
        # a caller that needs the type on the way out stores it beside the
        # `blob_key`, on the row it belongs to.
        del content_type
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise BlobStoreError(f"could not write blob {key!r}: {exc}") from exc

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobNotFound(key) from exc
        except OSError as exc:
            raise BlobStoreError(f"could not read blob {key!r}: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise BlobStoreError(f"could not delete blob {key!r}: {exc}") from exc

    def url(self, key: str) -> str | None:
        """`None` — a mounted directory has no addressable URL.

        The key is validated anyway, so that a caller cannot learn that an
        invalid key is invalid only on the path that fetches bytes.
        """
        self._path(key)
        return None


__all__ = ["BlobNotFound", "BlobStore", "BlobStoreError", "VolumeBlobStore"]
