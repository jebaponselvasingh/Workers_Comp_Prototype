"""One-off extraction: prototype PATH_DOCS → path_required_forms.json (Story 2.5).

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only. Run
from the server/ directory:

    uv run python data/seed/extract_prototype_path_forms.py ../../docs/Workers_Comp_Prototype.html

A script of its own rather than a mode of `extract_prototype_seed.py`, for
`extract_prototype_glossary.py`'s reason: that one owns `seed_data.json`, and
rewriting a 100-claim portfolio as a side effect of a statutory-forms story is
how an unrelated diff gets into a review.

**Only `PATH_DOCS` is extracted, never `PATH_META`.** The two literals sit
next to each other in the prototype and mean different things. `PATH_DOCS` is
regulatory *data* — which forms a path requires, what each is for, when it is
due, where the blank lives — and belongs in a table. `PATH_META` is a label,
an icon, a hex colour and a banner sentence: display metadata, which the Enums
convention puts in the UI. Porting both would have put two hex colours in a
database column and given an operator a way to recolour a banner by editing
reference data.

Field mapping is the story's: `form` → `form_code`, `name` → `form_name`,
`desc` → `description`, `timing` → `timing`, `url` → `download_url`, plus
`path` (the object key, lowercased to the wire token) and `sort_order` = the
index *within its path*, because the prototype renders each path's forms in
array order and `ORDER BY form_code` would scatter them (`C-11` sorts before
`C-3`).

**The URLs are placeholders and this script does not pretend otherwise.** They
are NY WCB and FNSB links the prototype chose to make its demo concrete;
per-jurisdiction validation of statutory form references is NFR-4 and an
explicit Deferred decision. Nothing here resolves, fetches or checks them.
"""

import json
import re
import sys
from pathlib import Path

# One `A:[…]` / `B:[…]` / `C:[…]` block of the PATH_DOCS object. Structural
# rather than a wholesale "unquoted keys → quoted keys" rewrite: a rewrite
# firing on `, name:"` would also fire inside a description, and this way a
# fourth path added to the prototype fails the completeness check below rather
# than being silently dropped.
PATH_BLOCK = re.compile(r"([A-Z])\s*:\s*\[(.*?)\]\s*,?\s*(?=[A-Z]\s*:\s*\[|\}\s*;?\s*$)", re.S)

# One `{form:"…",name:"…",desc:"…",url:"…",timing:"…"}` literal.
#
# **Positional, and the keys are named only so a wrong one cannot be read as a
# right one** (code review, 2026-08-12). An earlier version of this comment
# claimed the five keys were "matched by name rather than by position so that
# a reordered literal is still read correctly", which is the opposite of what
# this pattern does: reorder the prototype's object and it matches zero
# entries, which trips the `len(entries) != starts` completeness check and
# aborts the extraction.
#
# That failure mode is the safe one and is deliberately kept — a silent
# reordering would swap a form's *timing* with its *description* on a
# statutory card, which is a wrong filing deadline shown with total confidence.
# But it has to be described accurately, because the comment as written invited
# a maintainer to reorder the source literal and expect it to work. Naming each
# key in the pattern is still doing real work: it means a literal whose fields
# have moved cannot be silently parsed with every value shifted by one.
ENTRY = re.compile(
    r'\{\s*form:\s*("(?:[^"\\]|\\.)*")\s*,'
    r'\s*name:\s*("(?:[^"\\]|\\.)*")\s*,'
    r'\s*desc:\s*("(?:[^"\\]|\\.)*")\s*,'
    r'\s*url:\s*("(?:[^"\\]|\\.)*")\s*,'
    r'\s*timing:\s*("(?:[^"\\]|\\.)*")\s*,?\s*\}'
)

# Counts entry *boundaries*, not braces: a `{` inside a description would
# inflate the expected total and let a real miss hide behind it. An entry
# starts with `{form:` and nothing else in this object does.
ENTRY_START = re.compile(r"\{\s*form\s*:")

FIELDS = ("form", "name", "desc", "url", "timing")


def _decode(literal: str, path: str, index: int, field: str) -> str:
    """Decode one captured JS string literal, naming the entry if it fails.

    `json.loads` on the still-quoted capture, so escapes decode exactly once
    and by the browser's rules — but JS accepts escapes JSON rejects (`\\'`,
    `\\x41`, `\\0`). A bare `JSONDecodeError` points at a column offset inside
    a slice nobody can see; this says which form and which field to look at.
    """
    try:
        value: str = json.loads(literal)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"PATH_DOCS[{path}][{index}] field {field!r} is not decodable as a JSON "
            f"string ({exc.msg}). JS allows escapes JSON does not (\\', \\x41, \\0); "
            f"the literal was: {literal}"
        ) from exc
    return value


def main(html_path: str) -> None:
    source = Path(html_path)
    # The common mistake is a relative path off by one directory, and "no such
    # file" without the path it tried is the least useful sentence a script
    # can end on.
    if not source.is_file():
        raise SystemExit(f"prototype not found: {source.resolve()}")

    html = source.read_text(encoding="utf-8")
    match = re.search(r"const PATH_DOCS\s*=\s*(\{.*?\n\}\;)", html, re.S)
    if not match:
        raise SystemExit("PATH_DOCS not found")
    literal = match.group(1)

    blocks = PATH_BLOCK.findall(literal)
    if not blocks:
        raise SystemExit("PATH_DOCS matched no path blocks — the object's shape changed")

    total_starts = len(ENTRY_START.findall(literal))
    rows: list[dict[str, object]] = []

    for path_key, block in blocks:
        entries = ENTRY.findall(block)
        starts = len(ENTRY_START.findall(block))
        # `raise`, not `assert`: an assertion vanishes under `python -O`, and
        # the thing it guards — a regex miss silently dropping a statutory
        # form from the seed — must not depend on how the interpreter was
        # invoked.
        if len(entries) != starts:
            raise SystemExit(
                f"parsed {len(entries)} of {starts} forms for path {path_key} — "
                "the entry shape changed"
            )
        for index, captured in enumerate(entries):
            values = {
                field: _decode(literal_, path_key, index, field)
                for field, literal_ in zip(FIELDS, captured, strict=True)
            }
            rows.append(
                {
                    # The wire token, not the prototype's display key: the
                    # column is a snake_case enum per the Enums convention.
                    "path": path_key.lower(),
                    "form_code": values["form"],
                    "form_name": values["name"],
                    "description": values["desc"],
                    "timing": values["timing"],
                    "download_url": values["url"],
                    "sort_order": index,
                }
            )

    if len(rows) != total_starts:
        raise SystemExit(
            f"extracted {len(rows)} of {total_starts} PATH_DOCS entries — "
            "a form fell outside every path block"
        )

    dest = Path(__file__).parent / "path_required_forms.json"
    # ensure_ascii=False, like the glossary and unlike seed_data.json: these
    # descriptions are *prose*, and "verbatim from the prototype" is only
    # reviewable if a human can read the em dashes instead of — escapes.
    dest.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest} ({len(rows)} required forms)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../../docs/Workers_Comp_Prototype.html")
