"""Story 2.5 AC 1 — `path_required_form`, from the prototype to the database.

Three separable claims, and the file is organised along them:

1. The **vocabulary** is single-sourced. Migration 0017 writes its three path
   keys out literally (a migration is a historical record and must not read a
   live constant), so something has to compare that literal with `ClaimPath` —
   order included, because the migration's tuple decides the enum's label order
   in PostgreSQL. 0014 established this pattern for the eleven body regions and
   `test_injury_validation.py` is the model for the loader below.
2. The **rows** are the prototype's. Asserted against
   `docs/Workers_Comp_Prototype.html` itself rather than against
   `path_required_forms.json`, so the extractor is under test too: a comparison
   with its own output would pass however wrong it was.
3. The **grants** are what 0017 says: SELECT to the app role and nothing else.
   Reference data whose only writer is a migration.

Only the third needs a database, so the first two run everywhere.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from data.models.enums import ClaimPath
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = SERVER_ROOT / "data" / "seed" / "path_required_forms.json"
PROTOTYPE = SERVER_ROOT.parents[1] / "docs" / "Workers_Comp_Prototype.html"

_MIGRATION = SERVER_ROOT / "data" / "versions" / "20260812_0017_path_required_form.py"
_spec = importlib.util.spec_from_file_location("_m0017", _MIGRATION)
assert _spec and _spec.loader
_m0017 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0017)

MIGRATION_PATH_KEYS: tuple[str, ...] = _m0017.PATH_KEYS

#: `{form:"C-3",name:"…",desc:"…",url:"…",timing:"…"}` — the prototype's own
#: entries, read rather than restated so a form code it spells differently
#: fails here rather than being silently accepted from the extractor's output.
_PROTOTYPE_FORM = re.compile(
    r'\{\s*form:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*name:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*desc:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*url:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*timing:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*\}'
)

#: How many forms each path has in the prototype. Written down so a regex that
#: silently matched fewer fails the count rather than passing over a subset —
#: the failure the extractor's own `ENTRY_START` check exists for, restated by
#: an independent reader.
EXPECTED_PER_PATH = {"a": 2, "b": 4, "c": 3}


def seeded_forms() -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return forms


def prototype_form_codes() -> dict[str, list[str]]:
    """`{path: [form codes]}`, parsed from the prototype's `PATH_DOCS`.

    A second, independently written reading of the same literal the extractor
    parses — deliberately simpler (it captures the blocks by splitting on the
    path keys rather than by a lookahead), so the two are unlikely to be wrong
    in the same way.
    """
    html = PROTOTYPE.read_text(encoding="utf-8")
    literal = re.search(r"const PATH_DOCS\s*=\s*(\{.*?\n\}\;)", html, re.S)
    assert literal, "PATH_DOCS not found in the prototype"

    codes: dict[str, list[str]] = {}
    current: str | None = None
    for line in literal.group(1).splitlines():
        header = re.match(r"\s*([A-Z]):\s*\[", line)
        if header:
            current = header.group(1).lower()
            codes[current] = []
            continue
        entry = _PROTOTYPE_FORM.search(line)
        if entry and current:
            codes[current].append(json.loads(f'"{entry.group(1)}"'))
    return codes


# --- 1. the vocabulary ---------------------------------------------------


def test_the_enum_and_the_migration_name_the_same_three_paths() -> None:
    """`ClaimPath` and 0017's literal, compared — order included.

    Order matters concretely: the migration's tuple is what PostgreSQL uses to
    sort the native type, so a reordering that nobody compared would change
    `ORDER BY path` under every later query without changing any code.
    """
    assert tuple(member.value for member in ClaimPath) == MIGRATION_PATH_KEYS


def test_the_seed_file_names_only_paths_the_enum_knows() -> None:
    """A row whose path is not a member is a row nothing can ever select."""
    assert {form["path"] for form in seeded_forms()} == {member.value for member in ClaimPath}


# --- 2. the rows ---------------------------------------------------------


def test_the_seeded_forms_are_the_prototypes_forms_in_its_order() -> None:
    """The extraction, checked against the design contract itself.

    Both the *set* per path and the *order* within it: the card renders one
    path's forms in `sort_order`, and the prototype's array order is what that
    number carries. Comparing sets alone would let a re-ordered Path B ship.
    """
    expected = prototype_form_codes()
    assert {path: len(codes) for path, codes in expected.items()} == EXPECTED_PER_PATH

    seeded: dict[str, list[str]] = {}
    for form in sorted(seeded_forms(), key=lambda row: (row["path"], row["sort_order"])):
        seeded.setdefault(form["path"], []).append(form["form_code"])

    assert seeded == expected


def test_every_seeded_form_carries_all_five_of_its_fields() -> None:
    """A blank timing or description renders an empty row on a statutory card.

    Worth asserting separately from the codes above: the extractor could match
    every entry and still drop a field, and the migration's own shape check
    only compares *key names* on the first row.
    """
    for form in seeded_forms():
        for field in ("form_code", "form_name", "description", "timing", "download_url"):
            assert form[field].strip(), f"{form['form_code']} has an empty {field}"


def test_sort_order_numbers_each_path_from_zero_without_gaps() -> None:
    """What `(path, sort_order)` unique buys, checked as a property of the data.

    The constraint refuses a *duplicate*; nothing in the database refuses a
    gap, and a gap would mean the extractor dropped a form from the middle of
    a path — which is exactly the failure that would otherwise look like a
    shorter, perfectly well-ordered list.
    """
    per_path: dict[str, list[int]] = {}
    for form in seeded_forms():
        per_path.setdefault(form["path"], []).append(form["sort_order"])

    for path, orders in per_path.items():
        assert sorted(orders) == list(range(len(orders))), f"path {path} has a gap or a duplicate"


def test_no_path_display_metadata_reached_the_seed_file() -> None:
    """`PATH_META` is UI-owned — asserted, not merely documented.

    The prototype keeps the labels, icons and hex colours in an object beside
    `PATH_DOCS`, and the tempting mistake is to bring them along "while we are
    here". A hex colour in a database column is a banner an operator can
    recolour by editing reference data, and a label there is one that cannot be
    reworded without a migration.
    """
    text = SEED_PATH.read_text(encoding="utf-8")

    assert not re.search(r"#[0-9A-Fa-f]{6}", text), "a hex colour reached the statutory form seed"
    for label in ("Path A", "Path B", "Path C"):
        assert label not in text


# --- 3. the database -----------------------------------------------------


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


@requires_db
def test_the_native_enum_holds_exactly_the_three_paths(engine: Any) -> None:
    """A fourth label in the database would be a path no code can render."""
    with engine.connect() as conn:
        labels = conn.execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'claim_path' ORDER BY e.enumsortorder"
            )
        ).scalars()

        assert list(labels) == [member.value for member in ClaimPath]


@requires_db
def test_the_seeded_rows_are_the_seed_files_rows(engine: Any) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT path::text, form_code, form_name, description, timing, "
                "download_url, sort_order FROM path_required_form ORDER BY path, sort_order"
            )
        ).mappings()
        stored = [dict(row) for row in rows]

    expected = sorted(seeded_forms(), key=lambda row: (row["path"], row["sort_order"]))
    assert stored == expected


@requires_db
def test_two_forms_cannot_claim_the_same_position_in_one_path(engine: Any) -> None:
    """The card renders in `sort_order`, so a duplicate is an ambiguous list.

    A re-seed that doubled a path's forms fails at the migration rather than
    on screen.
    """
    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO path_required_form "
                    "(path, form_code, form_name, description, timing, download_url, sort_order) "
                    "VALUES ('b', 'C-3', 'x', 'x', 'x', 'x', 0)"
                )
            )
        conn.rollback()


@requires_db
def test_the_app_role_can_read_the_forms_and_cannot_write_them(engine: Any) -> None:
    """AD-4's grant statement for reference data, asserted rather than trusted.

    `glossary_term` sets the precedent: an app role that could rewrite the
    statutory form list would be a capability nobody asked for, behind an
    endpoint that cannot use it.
    """
    with engine.connect() as conn:
        granted = conn.execute(
            sa.text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'path_required_form' AND grantee = 'lineworker_app'"
            )
        ).scalars()

        assert set(granted) == {"SELECT"}
