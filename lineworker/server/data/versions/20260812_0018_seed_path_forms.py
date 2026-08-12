"""Seed the statutory forms from the prototype (Story 2.5, AC 1).

Loads `data/seed/path_required_forms.json` — nine forms extracted from the
prototype's `PATH_DOCS` object by `data/seed/extract_prototype_path_forms.py`
(AD-12). Two for Path A, four for Path B, three for Path C. The text is copied
verbatim: it is *content*, not code, so nothing here trims, re-cases or
re-words a statutory description.

**Only `PATH_DOCS` came across.** The prototype's `PATH_META` sits beside it
and holds a label, an icon, a hex colour and a banner sentence per path — UI
metadata, which the Enums convention keeps in the browser. Seeding it here
would have put two hex colours in a database column and given an operator a
way to recolour a banner by editing reference data.

**The URLs are placeholders and this migration says so where it matters.**
NFR-4 and an explicit Deferred architecture decision put per-jurisdiction
validation of statutory form references before go-live; these are the NY WCB
and FNSB links the prototype chose. Nothing here fetches or checks them, and
the UI does not label them provisional — that would be a product claim this
story has no basis for.

Revision ID: 0018_seed_path_forms
Revises: 0017_path_required_form
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0018_seed_path_forms"
down_revision: str | None = "0017_path_required_form"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "path_required_forms.json"

COLUMNS = {
    "path",
    "form_code",
    "form_name",
    "description",
    "timing",
    "download_url",
    "sort_order",
}

#: How many forms each path must arrive with. Spelled out rather than counted
#: from the file, for 0009's reason about globbing: a seed file that lost a
#: form to a regex change would otherwise migrate cleanly and leave a handler
#: on a fatality claim looking at two of the three death-benefit filings, with
#: nothing anywhere saying one was missing.
#: [Source: docs/Workers_Comp_Prototype.html lines 1542-1558]
EXPECTED_PER_PATH = {"a": 2, "b": 4, "c": 3}


def upgrade() -> None:
    forms = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    # A shape check before the insert, because the failure without one is
    # unreadable: a drifted extractor output reaches the database as a NOT NULL
    # violation or a CompileError about a bind parameter, and leaves the chain
    # halted with the table created and empty — which the API then serves as a
    # successful, formless required-forms card (0007's argument).
    if not forms:
        raise ValueError(f"{SEED_PATH} is empty — re-run data/seed/extract_prototype_path_forms.py")
    if set(forms[0]) != COLUMNS:
        raise ValueError(
            f"{SEED_PATH} rows have fields {sorted(forms[0])}, expected {sorted(COLUMNS)} — "
            "the extractor and this migration have drifted apart"
        )

    counts: dict[str, int] = {}
    for form in forms:
        counts[form["path"]] = counts.get(form["path"], 0) + 1
    if counts != EXPECTED_PER_PATH:
        raise ValueError(
            f"{SEED_PATH} holds {counts} forms per path, expected {EXPECTED_PER_PATH} — "
            "re-run data/seed/extract_prototype_path_forms.py against the prototype"
        )

    bind = op.get_bind()
    meta = sa.MetaData()
    path_required_form = sa.Table("path_required_form", meta, autoload_with=bind)

    # Rows carry the file's own `sort_order` rather than an insertion counter:
    # display order is a property of the data file a reviewer can read, not of
    # the order this loop happens to run in (0007's rule).
    bind.execute(path_required_form.insert(), forms)


def downgrade() -> None:
    op.execute("DELETE FROM path_required_form")
