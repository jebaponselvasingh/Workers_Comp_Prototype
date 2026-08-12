"""Story 2.5 — the `BlobStore` protocol, and one implementation of it.

Two different things are checked here, and the split is the point of the
protocol existing before any bytes do:

- **Conformance.** Whatever a deployment chooses (MinIO or a mounted volume —
  the decision is explicitly Deferred), it has to behave the same way at these
  four operations, including at the edges: a missing key raises rather than
  returning `b""`, a delete of an absent key succeeds, and `url` is allowed to
  answer `None`. Every test below is written against the *protocol type*, so
  the second implementation inherits the suite by being added to `STORES`.
- **Safety.** `VolumeBlobStore` resolves keys against a directory, which is a
  path traversal waiting to be handed a `blob_key` from outside this system
  (an ingest endpoint, an AD-13 agent tool). Reading arbitrary files out of the
  API container is the worst thing a document viewer could be made to do, so
  the refusal is asserted rather than assumed.

No database and no network: a directory in `tmp_path` is the whole fixture.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from services.blobstore import BlobNotFound, BlobStore, BlobStoreError, VolumeBlobStore

PDF = b"%PDF-1.7\nnot really a pdf\n"

#: Every implementation of the protocol, by name. A second entry here is all
#: it should take for MinIO to inherit this file.
STORES: dict[str, Callable[[Path], BlobStore]] = {
    "volume": lambda root: VolumeBlobStore(root),
}


@pytest.fixture(params=list(STORES))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[BlobStore]:
    yield STORES[request.param](tmp_path)


# --- conformance ---------------------------------------------------------


def test_what_was_put_is_what_comes_back(store: BlobStore) -> None:
    store.put("froi-wc-20017.pdf", PDF, content_type="application/pdf")

    assert store.get("froi-wc-20017.pdf") == PDF


def test_a_second_put_replaces_the_first(store: BlobStore) -> None:
    """`put` is documented as replacing, so a corrected filing overwrites
    rather than appending or failing."""
    store.put("k", b"first", content_type="application/pdf")
    store.put("k", b"second", content_type="application/pdf")

    assert store.get("k") == b"second"


def test_getting_an_absent_key_raises_rather_than_answering_empty(store: BlobStore) -> None:
    """The distinction a viewer depends on.

    A store that answered `b""` for a missing object would render a blank
    document rather than reporting that the file is not there — and `b""` is
    also a legitimate stored value, so the caller could not tell them apart.
    """
    with pytest.raises(BlobNotFound):
        store.get("never-written")


def test_deleting_is_idempotent(store: BlobStore) -> None:
    """Story 8.1's purge walks a claim's blobs and must be re-runnable.

    A purge that failed half-way through because one object was already gone
    is a purge nobody can trust to have finished.
    """
    store.put("k", PDF, content_type="application/pdf")
    store.delete("k")
    store.delete("k")

    with pytest.raises(BlobNotFound):
        store.get("k")


def test_url_is_either_a_link_or_an_honest_none(store: BlobStore) -> None:
    """The one operation the protocol lets an implementation decline.

    A caller written against a store that always presigns would break the day
    a deployment chose a volume — so `None` is part of the contract, not a
    stand-in for "not implemented yet".
    """
    store.put("k", PDF, content_type="application/pdf")

    answer = store.url("k")

    assert answer is None or isinstance(answer, str)


def test_the_implementations_satisfy_the_protocol_at_runtime(tmp_path: Path) -> None:
    """`@runtime_checkable` earns its keep: a store missing `delete` would
    otherwise fail for the first time inside Story 8.1's purge."""
    for build in STORES.values():
        assert isinstance(build(tmp_path), BlobStore)


# --- the volume implementation's own hazard ------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "../../../etc/passwd",
        "nested/key.pdf",
        "windows\\style.pdf",
        "..",
        ".",
        "",
    ],
)
def test_a_key_that_is_not_a_flat_name_is_refused(tmp_path: Path, key: str) -> None:
    """A `blob_key` is data, and one day it will come from outside.

    Today every `blob_key` in the database is null and the only writer would be
    an ingest path this story does not build — which is exactly when the check
    is cheap to add and impossible to remember later.
    """
    store = VolumeBlobStore(tmp_path)

    with pytest.raises(BlobStoreError):
        store.get(key)
    with pytest.raises(BlobStoreError):
        store.put(key, PDF, content_type="application/pdf")
    with pytest.raises(BlobStoreError):
        store.delete(key)
    # …including on the path that does not touch bytes, so a caller cannot
    # learn that a key is invalid only when it asks for the content.
    with pytest.raises(BlobStoreError):
        store.url(key)


def test_nothing_is_written_outside_the_root(tmp_path: Path) -> None:
    """The property the refusal above exists to guarantee, stated directly."""
    root = tmp_path / "blobs"
    outside = tmp_path / "escaped.pdf"
    store = VolumeBlobStore(root)

    with pytest.raises(BlobStoreError):
        store.put("../escaped.pdf", PDF, content_type="application/pdf")

    assert not outside.exists()
