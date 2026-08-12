"""`case_file_seed.json` against the prototype it claims to come from (Story 2.2, AC 4).

The seed-is-the-fixture doctrine leaves one thing unasserted, and Story 1.6
hit it first: migration 0011, `seed_fixture`, `test_case_file_seed.py` and the
e2e oracle all read `data/seed/case_file_seed.json`, so a truncated or
hand-edited regeneration would satisfy every one of them by making them all
agree with the same wrong file.

What needs an independent restatement here is different from 1.6's, though.
`ALL_CLAIMS` **is** valid JSON (unlike `GLOSS`, a JS object literal), so
parsing it with `json.loads` shares nothing with the extractor that is worth
doubting. What the extractor *decides* is the transformation — the snake_case
mapping, and above all the reading of five different display-date shapes into
a nullable `DATE`. Those are restated below from the prototype's own strings,
by hand, and compared row for row.

Not DB-backed on purpose (1.6's precedent): this is a statement about two
files in the repository and needs nothing else to be true. It skips with the
resolved path if the prototype is absent, so a partial checkout cannot turn
it into a CI failure.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests import seed_fixture

PROTOTYPE = Path(__file__).resolve().parents[3] / "docs" / "Workers_Comp_Prototype.html"

requires_prototype = pytest.mark.skipif(
    not PROTOTYPE.exists(),
    reason=(
        f"prototype not found at {PROTOTYPE} — this test compares two files in the repo, "
        "so a partial checkout legitimately has nothing to compare"
    ),
)


def _all_claims() -> list[dict[str, Any]]:
    html = PROTOTYPE.read_text(encoding="utf-8")
    match = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    assert match, "ALL_CLAIMS not found in the prototype"
    claims: list[dict[str, Any]] = json.loads(match.group(1))
    return claims


def _token(display: str) -> str:
    """snake_case, restated: lowercase, non-alphanumerics collapse to `_`."""
    return "_".join(part for part in re.split(r"[^a-z0-9]+", display.lower()) if part)


def _iso(raw: str, doi: date) -> str | None:
    """The prototype's display date as ISO, restated from its five shapes.

    Written as an explicit table of the shapes that appear in the data rather
    than as the extractor's branch order, so a shape the extractor mis-reads
    does not get mis-read the same way twice.
    """
    digits = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", raw)  # a document's MM/DD/YYYY
    if digits:
        month, day, year = (int(part) for part in digits.groups())
        return _roll(year, month, day)

    stale = re.fullmatch(r"24/(\d{2})/(\d{2})", raw)  # the intake event's 24/MM/DD
    if stale:
        month, day = (int(part) for part in stale.groups())
        assert (month, day) == (doi.month, doi.day), (
            f"{raw!r} is read as a stale year token plus the date of injury, "
            f"but the claim's DOI is {doi.isoformat()}"
        )
        return _roll(doi.year, month, day)

    short = re.fullmatch(r"(\d{2})/(\d{2})", raw)  # every other event's MM/DD
    if short:
        month, day = (int(part) for part in short.groups())
        return _roll(doi.year, month, day)

    assert raw in {"Closed", "Post-surgery"}, f"unhandled display date {raw!r}"
    return None


def _roll(year: int, month: int, day: int) -> str:
    """Story 1.2's house rule for the dataset's impossible calendar dates."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return (date(year, month, 1) + timedelta(days=day - 1)).isoformat()


@requires_prototype
def test_the_seeded_timeline_is_the_prototypes_timeline() -> None:
    expected = [
        {
            "claim_id": claim["claimId"],
            "event_date": _iso(event["date"], date.fromisoformat(claim["doi"])),
            "description": event["desc"],
            "tag": _token(event["tag"]),
        }
        for claim in _all_claims()
        for event in claim["timeline"]
    ]

    # Compared as a list, not a set: the order is the append order the
    # migration relies on, and a reordered file is exactly the drift a set
    # comparison would let through.
    assert seed_fixture.all_timeline_events() == expected


@requires_prototype
def test_the_seeded_documents_are_the_prototypes_documents() -> None:
    expected = [
        {
            "claim_id": claim["claimId"],
            "name": document["name"],
            "doc_type": _token(document["type"]),
            "filed_date": _iso(document["date"], date.fromisoformat(claim["doi"])),
        }
        for claim in _all_claims()
        for document in claim["documents"]
    ]

    assert seed_fixture.all_documents() == expected


@requires_prototype
def test_the_non_dates_are_the_only_rows_without_one() -> None:
    """The date ruling, asserted rather than described.

    62 settlement events and 101 documents carry a timing note (`Closed`,
    `Post-surgery`) where a date belongs; those and only those seed null. A
    regression that started dropping *parseable* dates would otherwise show
    up only as a blank column in a screenshot.
    """
    undated_events = sum(
        1 for claim in _all_claims() for event in claim["timeline"] if "/" not in event["date"]
    )
    undated_documents = sum(
        1
        for claim in _all_claims()
        for document in claim["documents"]
        if "/" not in document["date"]
    )

    assert undated_events == sum(
        1 for event in seed_fixture.all_timeline_events() if event["event_date"] is None
    )
    assert undated_documents == sum(
        1 for document in seed_fixture.all_documents() if document["filed_date"] is None
    )
    # Non-zero, so the assertions above cannot pass by both sides being empty.
    assert undated_events > 0 and undated_documents > 0
