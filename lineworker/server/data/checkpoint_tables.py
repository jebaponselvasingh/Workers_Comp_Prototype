"""Which database objects belong to the LangGraph saver — AD-3's exception, listed.

One tuple and two Alembic hooks, in a module of their own because
`data/env.py` cannot be imported: Alembic's environment script runs migrations
as a side effect of import, so anything that needs to *read* this list — the
test that keeps it honest against migration 0043 — has to find it somewhere
else. `env.py` imports from here and stays the place the hooks are installed.

## Why the exclusion exists at all

`data/env.py` sets `target_metadata = Base.metadata` with no filter, which is
right for every table this project owns: a table in the database and not in the
metadata **is** drift, and `alembic check` is in the CI gate to say so.

Migration 0043 creates four tables this project deliberately does not own.
`checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and
`checkpoint_migrations` belong to `langgraph-checkpoint-postgres`; their DDL is
vendored and frozen, they have no ORM model by design, and no application code
reads or writes them. To autogenerate they therefore look exactly like drift —
so without this exclusion the very next `alembic check` proposes `drop_table`
for all four and fails, and somebody running `alembic revision --autogenerate`
gets those drops written into a real migration.

## Why the names are written twice

Migration 0043 declares its own `CHECKPOINT_TABLES` and `CHECKPOINT_INDEXES`
rather than importing this module, under the rule every migration in this tree
keeps about enums: a migration is a frozen historical record of what the
database was asked to become, and one that read a live list would silently
change what it did the day somebody edited it.
`tests/test_copilot_migration.py` asserts the two agree today, which is exactly
how `INSIGHT_KINDS` is kept honest against `InsightKind` in 0042.

## The exclusion is by name and nothing else

It does not skip "tables with no ORM model" as a class, which would silently
swallow a real omission the day somebody forgot to export a model from
`data/models/__init__.py`.
"""

from collections.abc import MutableMapping
from typing import Literal

from sqlalchemy.schema import SchemaItem

#: The object kinds Alembic's `include_name` hook is called with. Spelled out
#: because the hook's own signature is typed as this `Literal` and a plain `str`
#: parameter does not satisfy it — a real check rather than a formality, since a
#: typo in one of these strings is an exclusion that silently never fires.
NameType = Literal[
    "schema",
    "table",
    "column",
    "index",
    "unique_constraint",
    "foreign_key_constraint",
    "check_constraint",
]
ParentNames = MutableMapping[
    Literal["schema_name", "table_name", "schema_qualified_table_name"], "str | None"
]

#: Every database object migration 0043 vendors from
#: `langgraph-checkpoint-postgres` — the complete set of things in this database
#: that autogenerate must have no opinion about.
CHECKPOINT_OBJECTS: frozenset[str] = frozenset(
    {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    }
)


def include_name(
    name: str | None,
    type_: NameType,
    parent_names: ParentNames,
) -> bool:
    """Keep the saver's objects out of the *reflection* pass.

    `include_object` alone is not enough. Autogenerate reflects the database
    first and compares afterwards, and an object filtered only at comparison
    time has still been reflected — which for these four means SQLAlchemy
    building `Table` objects for a schema nothing in this process describes.
    Filtering by name is the cheaper and earlier of the two hooks, and it is the
    one Alembic documents for exactly this case.
    """
    del parent_names
    return not (type_ in {"table", "index"} and name in CHECKPOINT_OBJECTS)


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """…and out of the *comparison* pass, for anything reflection still sees.

    Both hooks, deliberately. `include_name` is not consulted for every object
    type in every Alembic version, and the failure mode of missing one is a
    proposed `drop_table` against a live conversation store — the kind of thing
    that must be impossible rather than usually prevented. The two together cost
    one function and make the exclusion total.
    """
    del object_, reflected, compare_to
    return not (type_ in {"table", "index"} and name in CHECKPOINT_OBJECTS)


__all__ = [
    "CHECKPOINT_OBJECTS",
    "NameType",
    "ParentNames",
    "include_name",
    "include_object",
]
