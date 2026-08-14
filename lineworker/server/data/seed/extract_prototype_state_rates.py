"""One-off extraction: prototype STATE_WC_RATES → state_rates.json (Story 3.1).

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only. Run
from the server/ directory:

    uv run python data/seed/extract_prototype_state_rates.py ../../docs/Workers_Comp_Prototype.html

A script of its own rather than a mode of `extract_prototype_seed.py`, for
`extract_prototype_path_forms.py`'s reason: that one owns `seed_data.json`,
and rewriting a 100-claim portfolio as a side effect of a benefit-calculation
story is how an unrelated diff gets into a review.

**Dollars in, cents out.** The prototype writes its statutory weekly bounds as
whole dollars (`OH:{max:1147,min:287}`) because its `$m` helper formats
dollars. Every money column in this schema is integer cents, so each bound is
multiplied by 100 here — once, in the extractor, rather than in the migration
or at read time, so the seed file itself is already in the unit the column
holds. The `_cents` suffix on the output field names is what makes that
un-missable at the next call site.

**No effective date is extracted, because the prototype has none.** When a
schedule took effect is a fact about the jurisdiction's benefit board, not
something the prototype's object literal says, so migration 0023 supplies one
and documents the choice. Inventing a date here would put a number in a data
file that nothing in the source supports.

**These figures are illustrative, and this script does not pretend otherwise.**
The prototype's own card carries a disclaimer ("verify against the current WC
board benefit schedule"); NFR-4 and an explicit Deferred architecture decision
put per-jurisdiction validation of statutory rates before go-live. Nothing here
resolves, fetches or checks them.
"""

import json
import re
import sys
from pathlib import Path

# One `XX:{max:…,min:…,name:"…"}` entry of the STATE_WC_RATES object.
#
# Positional in the same sense `extract_prototype_path_forms.py`'s `ENTRY` is:
# the three keys are named in the pattern, so a literal whose fields have moved
# cannot be silently parsed with `min` read as `max` — it matches zero entries
# instead and trips the completeness check below. A swapped pair here would
# clamp every weekly benefit in the state to a bound that is the wrong way
# round, which is a wrong statutory figure shown with total confidence.
ENTRY = re.compile(
    r"([A-Z]{2})\s*:\s*\{"
    r"\s*max\s*:\s*(\d+)\s*,"
    r"\s*min\s*:\s*(\d+)\s*,"
    r'\s*name\s*:\s*("(?:[^"\\]|\\.)*")\s*\}'
)

# Counts entry *boundaries*, not braces, so a real miss cannot hide behind an
# inflated expected total. An entry starts with a two-letter key and a brace,
# and nothing else in this object does.
ENTRY_START = re.compile(r"\b[A-Z]{2}\s*:\s*\{")

CENTS_PER_DOLLAR = 100


def main(html_path: str) -> None:
    source = Path(html_path)
    # The common mistake is a relative path off by one directory, and "no such
    # file" without the path it tried is the least useful sentence a script can
    # end on.
    if not source.is_file():
        raise SystemExit(f"prototype not found: {source.resolve()}")

    html = source.read_text(encoding="utf-8")
    match = re.search(r"const STATE_WC_RATES\s*=\s*(\{.*?\n\}\;)", html, re.S)
    if not match:
        raise SystemExit("STATE_WC_RATES not found")
    literal = match.group(1)

    entries = ENTRY.findall(literal)
    starts = len(ENTRY_START.findall(literal))
    # `raise`, not `assert`: an assertion vanishes under `python -O`, and the
    # thing it guards — a regex miss silently dropping a jurisdiction from the
    # seed — must not depend on how the interpreter was invoked.
    if len(entries) != starts:
        raise SystemExit(f"parsed {len(entries)} of {starts} state rates — the entry shape changed")
    if not entries:
        raise SystemExit("STATE_WC_RATES matched no entries — the object's shape changed")

    rows: list[dict[str, object]] = []
    for code, max_dollars, min_dollars, name_literal in entries:
        weekly_min_cents = int(min_dollars) * CENTS_PER_DOLLAR
        weekly_max_cents = int(max_dollars) * CENTS_PER_DOLLAR
        # An inverted pair is not a tuning choice: the clamp is
        # `max(min, min(max, weekly))`, so with the bounds the wrong way round
        # *every* claim in the state is pinned to the lower of the two and the
        # card still renders a confident figure. Refused here, and again in the
        # migration, because a data file is edited by hand more often than a
        # regex is.
        if weekly_min_cents > weekly_max_cents:
            raise SystemExit(
                f"{code}: weekly min {min_dollars} exceeds max {max_dollars} — "
                "the prototype literal has its bounds reversed"
            )
        rows.append(
            {
                "state_code": code,
                "state_name": json.loads(name_literal),
                "weekly_min_cents": weekly_min_cents,
                "weekly_max_cents": weekly_max_cents,
            }
        )

    codes = [str(row["state_code"]) for row in rows]
    if len(set(codes)) != len(codes):
        raise SystemExit(f"duplicate state codes in STATE_WC_RATES: {sorted(codes)}")

    dest = Path(__file__).parent / "state_rates.json"
    # ensure_ascii=False, like the glossary and the statutory forms: the state
    # names are prose, and "verbatim from the prototype" is only reviewable if
    # a human can read them.
    dest.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest} ({len(rows)} jurisdictions)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../../docs/Workers_Comp_Prototype.html")
