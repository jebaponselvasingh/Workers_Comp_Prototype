"""The seed file against the prototype it claims to come from (Story 1.6, AC 1).

Every other glossary assertion — migration, `test_glossary.py`,
`seed_fixture.glossary_terms()`, the e2e oracle — reads
`data/seed/glossary_terms.json`. That is the seed-is-the-fixture doctrine
working as intended for *counts and text*, and it leaves exactly one thing
unasserted: whether the file is still the prototype's `GLOSS`. A truncated
regeneration, a hand-edited definition or a reordered pair would satisfy
every one of those tests, because they would all agree with the same wrong
file. AC 1's word is "verbatim", and until this file existed that half of it
was asserted only in prose.

Not DB-backed on purpose, so it runs in the plain `uv run pytest` job rather
than only where a Postgres happens to be available: it is a statement about
two files in the repository, and needs nothing else to be true.

The parse here is deliberately **not** `extract_prototype_glossary.ENTRY`.
Importing the extractor's regex would make this an assertion that the
extractor agrees with itself — it would pass for any entry the regex failed
to see, which is the failure mode worth catching. So the array literal is
walked by hand instead: a second implementation, as `seed_fixture`'s other
oracles are.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from tests import seed_fixture

PROTOTYPE = Path(__file__).resolve().parents[3] / "docs" / "Workers_Comp_Prototype.html"

requires_prototype = pytest.mark.skipif(
    not PROTOTYPE.exists(),
    reason=(
        f"prototype not found at {PROTOTYPE} — this test compares two files in the repo, "
        "so a partial checkout (or a package built from server/ alone) legitimately has "
        "nothing to compare and must not fail CI for it"
    ),
)


def _read_js_string(text: str, start: int) -> tuple[str, int]:
    """Read one double-quoted JS string literal beginning at `start`.

    Returns the decoded value and the index just past the closing quote.
    Escapes are decoded by `json.loads` on the still-quoted slice, which is
    the browser's own rule for every escape `GLOSS` actually uses.
    """
    assert text[start] == '"', f"expected a string literal at offset {start}"
    index = start + 1
    while text[index] != '"':
        index += 2 if text[index] == "\\" else 1
    value: str = json.loads(text[start : index + 1])
    return value, index + 1


def _parse_gloss(html: str) -> list[dict[str, str]]:
    """Walk `const GLOSS=[…]` by hand — see the module docstring for why."""
    marker = html.index("const GLOSS")
    cursor = html.index("[", marker) + 1

    entries: list[dict[str, str]] = []
    while True:
        char = html[cursor]
        if char == "]":
            return entries
        if char != "{":
            # Whitespace and the commas between entries. Anything else means
            # the array grew a shape this parser has not been taught, and
            # guessing at it is how a term goes missing quietly.
            assert char in " \t\r\n,", f"unexpected {char!r} between GLOSS entries at {cursor}"
            cursor += 1
            continue

        cursor += 1
        entry: dict[str, str] = {}
        while html[cursor] != "}":
            if html[cursor] in " \t\r\n,":
                cursor += 1
                continue
            colon = html.index(":", cursor)
            key = html[cursor:colon].strip()
            entry[key], cursor = _read_js_string(html, colon + 1)
        entries.append(entry)
        cursor += 1


@requires_prototype
def test_the_seed_file_is_the_prototypes_gloss_verbatim() -> None:
    """`glossary_terms.json` == `GLOSS`, entry for entry, field for field.

    Compared as whole lists rather than term by term so the failure output
    shows *which* entry drifted and how, and so an entry added, dropped or
    moved fails here — order is part of the contract (`sort_order` is the
    array index, and the prototype's order is deliberate).
    """
    parsed = _parse_gloss(PROTOTYPE.read_text(encoding="utf-8"))
    expected: list[dict[str, Any]] = [
        {
            # The story's field mapping, restated here rather than imported:
            # a → abbreviation, t → term, d → definition.
            "abbreviation": entry["a"],
            "term": entry["t"],
            "definition": entry["d"],
            "sort_order": index,
        }
        for index, entry in enumerate(parsed)
    ]

    assert seed_fixture.glossary_terms() == expected


@requires_prototype
def test_every_parsed_entry_has_exactly_the_three_contract_fields() -> None:
    """A fourth key in `GLOSS` is a contract change, not a field to ignore.

    The extractor would drop it silently (its regex matches three fields and
    stops); this says so out loud, in the one place that looks at the
    prototype rather than at the extractor's output.
    """
    parsed = _parse_gloss(PROTOTYPE.read_text(encoding="utf-8"))

    assert parsed, "GLOSS parsed as empty — the array's shape changed"
    for index, entry in enumerate(parsed):
        assert set(entry) == {"a", "t", "d"}, f"GLOSS entry {index} has fields {sorted(entry)}"
