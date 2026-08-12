"""One-off extraction: prototype `timeline`/`documents` arrays → case_file_seed.json.

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only.
Run from the server/ directory:

    uv run python data/seed/extract_case_file_seed.py ../../docs/Workers_Comp_Prototype.html

A separate extractor from Story 1.2's rather than a mode of it, for Story
1.6's reason: an unrelated 100-claim `seed_data.json` must not be rewritten
by a case-file story. `seed_data.json` already carries these two arrays
verbatim under `"deferred"`; this script re-reads them from the prototype
because that is the contract file, and normalizes them into rows a migration
can insert without knowing anything about the prototype's display quirks.

Two normalizations happen here and nowhere else.

**Snake_case tokens.** `tag` ("Intake", "FROI") and `type` ("MEDAUTH") are
display strings in the prototype; the conventions put snake_case on the wire
and display labels in the UI, so both are mapped mechanically by the same
`snake_token` rule Story 1.2 used for the status enums.

**Dates — the awkward half.** The prototype's timeline and document dates are
*display* strings written in three shapes and two non-dates, and the columns
they seed are real `DATE`s:

- ``MM/DD/YYYY`` — every dated document. Unambiguous.
- ``MM/DD`` — every timeline event after the first. The year is the claim's
  DOI year: no event in the dataset carries a month/day earlier than its
  claim's DOI, so there is no year-rollover ambiguity to resolve (asserted
  below rather than assumed).
- ``24/MM/DD`` — the first ("Intake") timeline event of all 100 claims. The
  leading component is the literal string ``24`` on every one of them, and
  ``MM/DD`` equals the claim's DOI exactly, so it is a stale ``YY`` token
  left behind when the source dataset was shifted to 2026 — not a date this
  format can be read as. The month/day are taken and the year comes from the
  DOI like every other event; both facts are asserted, so a data change that
  breaks the reading aborts the extraction instead of mis-dating a hundred
  claims.
- ``Closed`` (62 settlement events, 62 settlement documents) and
  ``Post-surgery`` (39 documents) — **not dates at all**, and the prototype
  prints them where a date belongs ("reached final settlement on *Closed*").
  They seed `null`. The alternative — a parallel text column so a UI can
  print the token — reproduces the prototype's habit of showing a non-date in
  a date field, which NFR-3 is the argument against. The raw strings survive
  in `seed_data.json`'s `"deferred"` block, so a later story that wants the
  "filed post-surgery" wording back has not lost anything.

Impossible calendar dates follow Story 1.2's house rule: rolled forward
(`2026-02-29` → `2026-03-01`, one claim, three rows).
"""

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# The stale year token the intake event is prefixed with on every claim. Its
# value is asserted, not accepted: if a future dataset writes a real year
# here, the assertion below is what stops us reading it as a month.
INTAKE_YEAR_PREFIX = "24"


def snake_token(display: str) -> str:
    """Story 1.2's mechanical display-string → snake_case mapping."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", display.lower())).strip("_")


def _calendar(year: int, month: int, day: int, raw: str) -> str:
    """A real date, rolling an impossible one forward (Story 1.2's rule)."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        rolled = date(year, month, 1) + timedelta(days=day - 1)
        print(f"  date quirk: {raw} -> {rolled.isoformat()}", file=sys.stderr)
        return rolled.isoformat()


def parse_display_date(raw: str, doi: date, *, where: str) -> str | None:
    """One of the prototype's display dates as ISO, or `None` if it is not one.

    `doi` supplies the year for the year-less forms. `where` names the row in
    any refusal, because a hundred claims' worth of rows fail identically
    otherwise.
    """
    parts = raw.split("/")

    if len(parts) == 3 and len(parts[2]) == 4:  # MM/DD/YYYY
        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
        return _calendar(year, month, day, raw)

    if len(parts) == 3 and len(parts[2]) == 2:  # the intake event's 24/MM/DD
        if parts[0] != INTAKE_YEAR_PREFIX:
            raise SystemExit(
                f"{where}: {raw!r} has a three-part date whose first component is "
                f"{parts[0]!r}, not the stale {INTAKE_YEAR_PREFIX!r} token this "
                "extractor knows how to drop — read the format before seeding it"
            )
        month, day = int(parts[1]), int(parts[2])
        if (month, day) != (doi.month, doi.day):
            raise SystemExit(
                f"{where}: intake event dated {raw!r} does not match the claim's "
                f"date of injury {doi.isoformat()} — the assumption that the "
                "leading component is a stale year no longer holds"
            )
        return _calendar(doi.year, month, day, raw)

    if len(parts) == 2:  # MM/DD
        month, day = int(parts[0]), int(parts[1])
        if (month, day) < (doi.month, doi.day):
            raise SystemExit(
                f"{where}: {raw!r} falls before the claim's date of injury "
                f"{doi.isoformat()} in the same year — this dataset has no such "
                "row, so the year-rollover rule was never chosen; choose one"
            )
        return _calendar(doi.year, month, day, raw)

    # "Closed", "Post-surgery" — a timing note in a date field. See module docstring.
    return None


def main(html_path: str) -> None:
    source = Path(html_path)
    if not source.exists():
        raise SystemExit(f"prototype not found at {source.resolve()}")
    html = source.read_text(encoding="utf-8")
    match = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    if not match:
        raise SystemExit("ALL_CLAIMS not found")
    raw: list[dict[str, Any]] = json.loads(match.group(1))

    timeline_events: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []

    # Rows are emitted in the prototype's array order, per claim, in the
    # prototype's claim order — and the migration inserts them in that order.
    # That is load-bearing rather than tidy: the timeline is an append-only
    # log with no total order in its own columns (dates repeat, and 62 of
    # them are null), so its display order is its append order, which is its
    # identity-column order. The prototype's treatment overview shows the
    # *last six* events, so an order that drifted would silently show a
    # different six.
    for claim in raw:
        claim_id = claim["claimId"]
        doi = date.fromisoformat(claim["doi"])

        for index, event in enumerate(claim.get("timeline") or []):
            timeline_events.append(
                {
                    "claim_id": claim_id,
                    "event_date": parse_display_date(
                        event["date"], doi, where=f"{claim_id} timeline[{index}]"
                    ),
                    "description": event["desc"],
                    "tag": snake_token(event["tag"]),
                }
            )

        for index, document in enumerate(claim.get("documents") or []):
            documents.append(
                {
                    "claim_id": claim_id,
                    "name": document["name"],
                    "doc_type": snake_token(document["type"]),
                    "filed_date": parse_display_date(
                        document["date"], doi, where=f"{claim_id} documents[{index}]"
                    ),
                }
            )

    out = {"timeline_events": timeline_events, "documents": documents}
    dest = Path(__file__).parent / "case_file_seed.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {dest} ({len(timeline_events)} timeline events, "
        f"{len(documents)} documents, {len(raw)} claims)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../../docs/Workers_Comp_Prototype.html")
