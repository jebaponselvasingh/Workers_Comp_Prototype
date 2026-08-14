"""One-off extraction: the prototype's bill and expense builders → JSON.

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only.
Run from the server/ directory:

    uv run python data/seed/extract_prototype_line_items.py \
        ../../docs/Workers_Comp_Prototype.html

**This extractor computes rather than reads, and it is the first one that
does.** Every other extractor lifts values the prototype wrote down — photo
captions, glossary definitions, state rate tables. `buildBills` (line 766) and
`buildExpenses` (line 784) write nothing down: they *derive* a claim's line
items at render time from a hash of its claim id, so the only way to obtain
the prototype's figures is to run the prototype's arithmetic. The story
sanctions this in as many words — "amounts and statuses precomputed
deterministically (hash-variance is fine to reproduce; it's seed data, not
runtime logic)".

That distinction is the whole reason this is an extractor and not a service.
The variance exists to make a demo look plausible, and it belongs in the seed
file with the rest of the dataset's invented content. Nothing at runtime
recomputes it; `bill` and `expense` rows are ordinary data from the moment
they land, and Story 3.4's approval moves their statuses like any other row's.

## The two builders, ported

Both walk a fixed list of candidate line items, appending the ones a claim
qualifies for, and both derive an amount and a status from `hashStr` of the
claim id (the expense builder salts it with `"::exp"`). `idx` counts *emitted*
items rather than candidates, so which bits a given line item consumes depends
on which earlier ones were skipped — ported exactly, because it is what makes
two claims with the same injuries differ.

- **Amount**: `Math.round(base * (0.8 + ((h >> idx*3) & 7) / 10))` for bills
  and `0.75 + …` for expenses, in whole dollars, multiplied into cents once
  here. `round_half_up` rather than Python's `round`, which is banker's:
  `Math.round(850 * 1.1)` is 935 in both, but a value landing exactly on .5
  would go to the even dollar in Python and up in JavaScript.
- **Status**: `pool[(h >> idx*2) % pool.length]`, with one deliberate
  divergence, below.

## The one divergence: JavaScript's signed shift over an unsigned hash

`hashStr` ends `>>> 0`, which is the author stating that the hash is a
**32-bit unsigned** value. `>>` then reads it back as *signed*: for the 56 of
200 claim-id hashes here that exceed 2^31, `h >> k` is negative, and
JavaScript's `%` keeps the sign of its dividend — so `pool[-1]` is
`undefined`. The prototype renders that through
``billStatusLabel(s){return BILL_STATUS_LABEL[s] || s || "Pending"}``, which
turns it into the string "Pending": a status that appears in no vocabulary the
prototype declares, on **26 of the 860 line items it draws**.

This extractor uses the **unsigned** shift throughout, so the index is always
inside the pool. It is a port of the intent rather than of the accident: the
hash is unsigned by the prototype's own construction, "Pending" is not one of
the states the design names, and a `status` column cannot hold it. Amounts are
bit-identical either way — `& 7` reads bits *k..k+2*, which the two shifts
agree on — so the divergence is confined to which of two declared statuses a
line item carries, and only on the rows where the prototype had no answer.

Checked rather than asserted: the 860 rows this emits were diffed against a
JavaScript run of the prototype's own two builders, and **every amount
matches**, with the status differing on exactly the 26 rows where the
prototype had produced `undefined`. `tests/test_financial_tables_migration.py`
pins both halves — `test_the_seeded_amounts_are_the_prototypes_own_arithmetic`
re-derives the amounts from the HTML with a second, independently written
evaluation of the expression, and
`test_every_seeded_status_and_category_is_in_its_enum` holds that no seeded
status falls outside `LineItemStatus`.

## Two prototype conditions that read as filters and are not

- **Pharmacy** is gated on ``c.disability==="Temporary"||c.disability===
  "Permanent"`` — and `Disability` has exactly those two members, so the
  condition is always true and every claim gets a pharmacy bill. Ported as
  written rather than simplified to an unconditional append, so the line
  matches the prototype's and a third disability class would change both.
- **Prosthetic** is gated on `surgeryRequired` and its *amount* on
  `disability === "Permanent"` ($2,600 against $900). Those are two different
  columns doing two different jobs, which is easy to misread as one.

## Settled claims are seeded fully paid

Every status pool on a settled claim is `["Paid"]`, which is the prototype's
own branch and not a simplification: a closed claim with an item still under
review would be a contradiction the Bills tab would render without comment.

**It does not, however, feed Story 2.2's settled payout breakdown**, which the
readiness review gave as the reason for creating `expense` in this story. That
card reads `claim.paid_expense`, and every seeded settled claim already carries
one. The seeded rows and the paid columns are also *different numbers* — the
prototype invented them independently and never reconciled them — which is
recorded in `deferred-work.md` rather than papered over here.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

#: The whole book gets line items. Asserted rather than reported, because an
#: extraction that silently produced fewer would migrate cleanly and leave a
#: handler reading an empty Bills tab as "this claim cost nothing".
EXPECTED_CLAIMS = 100

#: Every claim qualifies for initial treatment, imaging, a follow-up visit and
#: pharmacy, so no claim can have fewer than four bills or fewer than two
#: expenses (mileage and misc are unconditional too).
MIN_BILLS_PER_CLAIM = 4
MIN_EXPENSES_PER_CLAIM = 2

#: `/PT|physical|therapy/i` — the prototype's test for whether a treatment
#: plan includes physiotherapy.
_THERAPY = re.compile(r"PT|physical|therapy", re.I)

_UINT32 = 0xFFFFFFFF


def hash_str(value: str) -> int:
    """The prototype's `hashStr` (line 646), in Python.

    ``h=(h*31+charCode)>>>0`` — a 32-bit unsigned rolling hash. The mask is
    what `>>> 0` does, and applying it every iteration rather than once at the
    end is what keeps the multiply from carrying bits JavaScript would have
    dropped.
    """
    h = 0
    for char in value:
        h = (h * 31 + ord(char)) & _UINT32
    return h


def round_half_up(value: float) -> int:
    """`Math.round` — half away from zero, for the positive values here.

    Python's built-in `round` is banker's rounding, so a multiplier landing a
    dollar figure exactly on .5 would go to the even dollar here and up in the
    prototype. Every amount below is positive, so `floor(x + 0.5)` is the
    whole of `Math.round`'s contract.
    """
    return int(value + 0.5)


class _Builder:
    """One claim's line items, in the prototype's `addItem` shape.

    A tiny class rather than a closure because `idx` is genuinely stateful and
    the prototype's variance depends on it advancing only for *emitted* items
    — which is easy to get wrong with a list comprehension and impossible to
    get wrong with a counter that only `add` touches.
    """

    def __init__(self, seed: str, base_multiplier: float) -> None:
        self.hash = hash_str(seed)
        self.base_multiplier = base_multiplier
        self.idx = 0
        self.items: list[dict[str, Any]] = []

    def add(self, category: str, label: str, base_dollars: int, pool: tuple[str, ...]) -> None:
        multiplier = self.base_multiplier + ((self.hash >> (self.idx * 3)) & 7) / 10
        dollars = round_half_up(base_dollars * multiplier)
        # Unsigned shift — the divergence the module docstring argues for.
        status = pool[(self.hash >> (self.idx * 2)) % len(pool)]
        self.items.append(
            {
                "category": category,
                "label": label,
                "amount_cents": dollars * 100,
                "status": status,
            }
        )
        self.idx += 1


#: `"Paid"` / `"UnderReview"` / `"PendingSubmission"` → the snake_case tokens
#: `LineItemStatus` declares. The prototype's third label is the one this
#: console refuses to copy: it renders `PendingSubmission` as "Payment
#: Scheduled", conflating an unfiled bill with an approved one.
_STATUS = {
    "Paid": "paid",
    "UnderReview": "under_review",
    "PendingSubmission": "pending_submission",
}


def _bills(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """`buildBills` (line 766), ported line for line."""
    settled = claim.get("stage") == "settled"
    builder = _Builder(claim["claimId"], 0.8)

    builder.add("initial_treatment", "Emergency / Initial Treatment", 850, ("Paid",))
    if claim.get("surgeryRequired"):
        builder.add(
            "surgery_facility",
            "Surgical Procedure & Facility Fee",
            18500,
            ("Paid",) if settled else ("UnderReview", "Paid"),
        )
    builder.add(
        "imaging",
        "Diagnostic Imaging (MRI/CT/X-Ray)",
        1450,
        ("Paid",) if settled else ("Paid", "UnderReview"),
    )
    if any(_THERAPY.search(step) for step in claim.get("treatmentPlan") or ()):
        builder.add(
            "physical_therapy",
            "Physical Therapy Session Bundle",
            2200,
            ("Paid",) if settled else ("Paid", "PendingSubmission"),
        )
    builder.add(
        "follow_up",
        "Follow-Up Physician Visit",
        320,
        ("Paid",) if settled else ("Paid", "UnderReview"),
    )
    # Always true against this dataset — `Disability` has exactly these two
    # members. Kept as the prototype wrote it; see the module docstring.
    if claim.get("disability") in ("Temporary", "Permanent"):
        builder.add("pharmacy", "Prescription / Pharmacy", 180, ("Paid",))

    return builder.items


def _expenses(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """`buildExpenses` (line 784), ported line for line."""
    settled = claim.get("stage") == "settled"
    permanent = claim.get("disability") == "Permanent"
    builder = _Builder(claim["claimId"] + "::exp", 0.75)

    builder.add(
        "mileage_travel",
        "Mileage & Travel Reimbursement",
        95,
        ("Paid",) if settled else ("Paid", "UnderReview"),
    )
    if claim.get("stage") != "intake":
        builder.add(
            "dme",
            "Durable Medical Equipment",
            340,
            ("Paid",) if settled else ("UnderReview", "Paid"),
        )
    if claim.get("surgeryRequired"):
        # Two columns, two jobs: `surgeryRequired` decides whether there is a
        # device at all, `disability` decides how expensive it is.
        builder.add(
            "prosthetic_assistive",
            "Prosthetic / Assistive Device",
            2600 if permanent else 900,
            ("Paid",) if settled else ("PendingSubmission", "UnderReview"),
        )
    if permanent:
        builder.add(
            "home_workstation_mod",
            "Home / Workstation Modification Assessment",
            1750,
            ("Paid",) if settled else ("PendingSubmission", "UnderReview"),
        )
    builder.add(
        "misc",
        "Miscellaneous Claim-Related Expense",
        85,
        ("Paid",) if settled else ("Paid", "UnderReview"),
    )

    return builder.items


def _emit(claim_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "claim_id": claim_id,
            "category": item["category"],
            "label": item["label"],
            "amount_cents": item["amount_cents"],
            "status": _STATUS[item["status"]],
        }
        for item in items
    ]


def main(html_path: str) -> None:
    source = Path(html_path)
    if not source.exists():
        raise SystemExit(f"prototype not found at {source.resolve()}")
    html = source.read_text(encoding="utf-8")

    match = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    if not match:
        raise SystemExit("ALL_CLAIMS not found")
    claims: list[dict[str, Any]] = json.loads(match.group(1))

    if len(claims) != EXPECTED_CLAIMS:
        raise SystemExit(f"expected {EXPECTED_CLAIMS} claims, read {len(claims)}")

    bills: list[dict[str, Any]] = []
    expenses: list[dict[str, Any]] = []
    for claim in claims:
        claim_bills = _bills(claim)
        claim_expenses = _expenses(claim)
        if len(claim_bills) < MIN_BILLS_PER_CLAIM:
            raise SystemExit(
                f"{claim['claimId']} produced {len(claim_bills)} bills; "
                f"four are unconditional, so the builder has drifted"
            )
        if len(claim_expenses) < MIN_EXPENSES_PER_CLAIM:
            raise SystemExit(
                f"{claim['claimId']} produced {len(claim_expenses)} expenses; "
                f"two are unconditional, so the builder has drifted"
            )
        bills.extend(_emit(claim["claimId"], claim_bills))
        expenses.extend(_emit(claim["claimId"], claim_expenses))

    out = Path(__file__).parent
    (out / "bills.json").write_text(
        json.dumps(bills, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "expenses.json").write_text(
        json.dumps(expenses, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(  # noqa: T201 — a one-off script; the counts are the whole output
        f"wrote {len(bills)} bills and {len(expenses)} expenses across {EXPECTED_CLAIMS} claims"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
