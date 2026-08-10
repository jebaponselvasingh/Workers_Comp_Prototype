"""One-off extraction: prototype GLOSS → glossary_terms.json (Story 1.6).

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only.
Run from the server/ directory:

    uv run python data/seed/extract_prototype_glossary.py ../../docs/Workers_Comp_Prototype.html

Two levels up, not one: `docs/` sits at the repository root, beside
`lineworker/`, so from `lineworker/server/` the prototype is `../../docs/`.
(`extract_prototype_seed.py` documents `../docs/` — that is a pre-existing
bug in its docstring, deferred rather than fixed here so a glossary story
does not touch the 100-claim extractor.)

A separate script rather than a mode of extract_prototype_seed.py: that one
owns seed_data.json, and rewriting a 100-claim portfolio as a side effect of
a glossary story is how an unrelated diff gets into a review. Same AD-12
posture — a migration-time loader, not a runtime importer.

Field mapping is the story's: `a` → abbreviation, `t` → term, `d` →
definition, plus `sort_order` = the array index, because the prototype's
order is deliberate (FNOL first, then the acronym cluster, then the
manufacturing-specific hazards) and `ORDER BY term` would scatter it.

Nothing here normalizes the text. Two quirks are contract, not defects:
"OSHA 300" and "Arc Flash" have multi-word abbreviations, and
"Apportionment" has an abbreviation identical to its term.
"""

import json
import re
import sys
from pathlib import Path

# Matches one `{a:"…",t:"…",d:"…"}` literal. Deliberately structural rather
# than a "unquoted keys → quoted keys" rewrite of the whole array: a rewrite
# that fires on `, t:"` would also fire inside a definition, and this way a
# GLOSS entry that ever grows a fourth field fails the count check below
# instead of being silently dropped.
ENTRY = re.compile(
    r'\{\s*a:\s*("(?:[^"\\]|\\.)*")\s*,'
    r'\s*t:\s*("(?:[^"\\]|\\.)*")\s*,'
    r'\s*d:\s*("(?:[^"\\]|\\.)*")\s*\}'
)

# Counts entry *boundaries*, not braces. The previous check compared against
# `array.count("{")`, which also counts any `{` inside a definition — so a
# definition mentioning a brace inflated the expected total and a real miss
# could hide behind it. An entry starts with `{a:` and nothing else in this
# array does.
ENTRY_START = re.compile(r"\{\s*a\s*:")


def _decode(literal: str, index: int, field: str) -> str:
    """Decode one captured JS string literal, naming the entry if it fails.

    `json.loads` on the still-quoted capture, so escapes decode exactly once
    and by the same rules as the browser's — but JS accepts escapes JSON
    rejects (`\\'`, `\\x41`, `\\0`). If a future prototype edit uses one, the
    bare `JSONDecodeError` points at a column offset inside a slice nobody
    can see; this says which entry and which field to go and look at.
    """
    try:
        value: str = json.loads(literal)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"GLOSS entry {index} field {field!r} is not decodable as a JSON string "
            f"({exc.msg}). JS allows escapes JSON does not (\\', \\x41, \\0); "
            f"the literal was: {literal}"
        ) from exc
    return value


def main(html_path: str) -> None:
    source = Path(html_path)
    # Same friendly failure as the "GLOSS not found" branch below. The path
    # is resolved into the message because the common mistake is a relative
    # path off by one directory, and "no such file" without the path it
    # tried is the least useful sentence a script can end on.
    if not source.is_file():
        raise SystemExit(f"prototype not found: {source.resolve()}")

    html = source.read_text(encoding="utf-8")
    match = re.search(r"const GLOSS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    if not match:
        raise SystemExit("GLOSS not found")
    array = match.group(1)

    entries = ENTRY.findall(array)
    starts = len(ENTRY_START.findall(array))
    # `raise`, not `assert`: an assertion vanishes under `python -O`, and the
    # thing it guards against — a regex miss silently dropping a term from
    # the seed — is exactly what must not depend on how the interpreter was
    # invoked.
    if len(entries) != starts:
        raise SystemExit(
            f"parsed {len(entries)} of {starts} GLOSS entries — the array's shape changed"
        )

    terms = [
        {
            "abbreviation": _decode(abbreviation, index, "a"),
            "term": _decode(term, index, "t"),
            "definition": _decode(definition, index, "d"),
            "sort_order": index,
        }
        for index, (abbreviation, term, definition) in enumerate(entries)
    ]

    dest = Path(__file__).parent / "glossary_terms.json"
    # ensure_ascii=False, unlike seed_data.json: this file is *prose*, and
    # "verbatim from the prototype" is only reviewable if a human can read
    # the em dashes and apostrophes instead of \u2014 escapes.
    dest.write_text(json.dumps(terms, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest} ({len(terms)} glossary terms)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../../docs/Workers_Comp_Prototype.html")
