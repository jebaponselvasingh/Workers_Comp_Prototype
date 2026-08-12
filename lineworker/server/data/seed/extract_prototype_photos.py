"""One-off extraction: prototype per-claim `photos` arrays → photos.json.

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only.
Run from the server/ directory:

    uv run python data/seed/extract_prototype_photos.py ../../docs/Workers_Comp_Prototype.html

A separate extractor from `extract_case_file_seed.py` rather than a mode of
it, on Story 2.5's reasoning: `case_file_seed.json` is the input migration
0011 already ran with, and a 2.6 story that rewrote it would be editing the
recorded history of a shipped migration to add a table it does not create.

**Nothing is normalized, and that is the whole of the job.** Unlike the
timeline and document extraction, there is no date to parse and no display
string to snake_case: `t` and `d` are free text a human wrote, and they seed
two `TEXT` columns under their canonical descriptive names.

- ``t`` → ``caption``  — "Machine guard / interlock mechanism detail"
- ``d`` → ``source``   — "Plant safety, 03/22"

`source` keeps its trailing `MM/DD` fragment *inside the string*, deliberately.
It is a provenance line ("who photographed this, and roughly when"), not a
date field: the day is not always the incident's, the year is nowhere in it,
and splitting it would produce a `DATE` column that is wrong about a third of
the time next to a source column that had lost half its meaning. The prototype
prints the sentence; so does the console.

**Row order is the prototype's array order, per claim, in claim order** — and
migration 0021 inserts them in that order, so the identity column carries it.
That is load-bearing rather than tidy: `photo` has no other total order (two
photos of one claim routinely share a source, and 54 captions repeat across
the book), so the grid's display order *is* its insertion order. `document`
and `timeline_event` are seeded on exactly this contract; `path_required_form`
needed an explicit `sort_order` only because three independent reference lists
share one table.

**No `blob_key` is emitted.** The prototype has no image files — its thumbnails
are the 📷 emoji — so every row's key is null and the UI's placeholder is the
designed state rather than a degradation. Inventing a key here would make the
API answer `hasBlob: true` for content no store can serve.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

#: The whole book carries photos. Asserted rather than reported, because an
#: extraction that silently produced fewer would migrate cleanly and leave
#: handlers looking at an evidence grid that had quietly lost its evidence.
EXPECTED_CLAIMS = 100
EXPECTED_PHOTOS = 293


def main(html_path: str) -> None:
    source = Path(html_path)
    if not source.exists():
        raise SystemExit(f"prototype not found at {source.resolve()}")
    html = source.read_text(encoding="utf-8")
    match = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    if not match:
        raise SystemExit("ALL_CLAIMS not found")
    raw: list[dict[str, Any]] = json.loads(match.group(1))

    photos: list[dict[str, Any]] = []
    for claim in raw:
        claim_id = claim["claimId"]
        entries = claim.get("photos") or []
        if not entries:
            raise SystemExit(
                f"{claim_id} has no photos — every claim in this dataset carries "
                "between two and four, so an empty array means the read is wrong, "
                "not that the claim has no evidence"
            )
        for index, photo in enumerate(entries):
            if set(photo) != {"t", "d"}:
                raise SystemExit(
                    f"{claim_id} photos[{index}] has fields {sorted(photo)}, expected "
                    "['d', 't'] — the prototype's photo shape has changed and the "
                    "mapping to caption/source is no longer known to be right"
                )
            photos.append({"claim_id": claim_id, "caption": photo["t"], "source": photo["d"]})

    if len(raw) != EXPECTED_CLAIMS or len(photos) != EXPECTED_PHOTOS:
        raise SystemExit(
            f"read {len(photos)} photos across {len(raw)} claims, expected "
            f"{EXPECTED_PHOTOS} across {EXPECTED_CLAIMS} — re-read the dataset "
            "before seeding a portfolio that has changed size"
        )

    dest = Path(__file__).parent / "photos.json"
    dest.write_text(json.dumps(photos, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest} ({len(photos)} photos across {len(raw)} claims)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../../docs/Workers_Comp_Prototype.html")
