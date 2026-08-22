"""AC 1: the database's own auditing must not become the leak (AD-11).

pgaudit is the only component in this build that can write claim data somewhere
the Story 8.1 purge cascade cannot reach. `audit_event` holds value-level
history because AD-4 says it should, and the cascade redacts its diffs in
place; a line in `postgresql-Sat.log` is a file on a volume, and nothing this
system owns can go back and take a value out of it. So the posture is not "log
a bit less" — it is that the DDL and role classes are captured and every DML
class is off, stated explicitly rather than left at a default.

## These assert the running server, not the migration

`data/versions/20260822_0050_pgaudit.py` installs the extension, and
`tests/test_schema_seed.py` would notice if `alembic upgrade head` stopped
working. Neither says anything about *what pgaudit captures*, which is decided
by the `command:` flag list on the postgres service in all three compose
profiles and is therefore a property of the process rather than of the schema.
`tests/test_deploy_tls_posture.py` reads those files as text; this file asks the
server. Both are needed: the text check catches a profile that drifted, and this
catches a database somebody started without them.

That is also why the failure messages carry the `docker run` recipe. A
developer meeting this file for the first time meets it as "pgaudit is not
loaded", and the useful answer is the command that starts a database where it
is — not a link to a story.
"""

import ast
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.conftest import MIGRATION_DB_URL, requires_db

pytestmark = requires_db

#: The command that starts a database this file can pass against, printed in
#: every failure that means "your test database is the stock image".
#:
#: `tests/conftest.py` carries the same recipe in its module docstring, which is
#: where somebody looks *before* running the suite; this is where they look
#: after. Kept in both because a failure message that says "see the conftest" is
#: a failure message that costs a file open.
#:
#: **The list is the whole `command:` from `deploy/compose.yaml`, and an
#: abbreviated one is a bug rather than a tidier docstring.** It was abbreviated
#: once — preload, `pgaudit.log`, the rotation four and `log_min_error_statement`
#: — on the reasonable-looking theory that everything else is already at its
#: default. Two of them are not: PostgreSQL ships `pgaudit.log_catalog=on` and
#: `log_parameter_max_length=-1`, so a database started from the short recipe
#: fails `test_pgaudit_captures_ddl_and_role_classes_only` with `assert 'on' ==
#: 'off'` — a failure message that reads as a broken build and is really a
#: broken docstring. `test_the_documented_recipe_would_actually_pass_this_suite`
#: below is what stops it happening again.
RECIPE = (
    "Start a hardened test database:\n"
    "  docker build -t lineworker/postgres:pg18-pgaudit deploy/postgres\n"
    "  docker run -d --name lw-test-pg -e POSTGRES_USER=lineworker "
    "-e POSTGRES_PASSWORD=test -e POSTGRES_DB=lineworker -p 55432:5432 "
    "lineworker/postgres:pg18-pgaudit postgres "
    "-c shared_preload_libraries=pgaudit -c pgaudit.log=ddl,role "
    "-c pgaudit.log_catalog=off -c pgaudit.log_parameter=off "
    "-c pgaudit.log_relation=off -c pgaudit.log_statement_once=on "
    "-c log_statement=none -c log_duration=off "
    "-c log_min_duration_statement=-1 -c log_min_error_statement=panic "
    "-c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0 "
    "-c logging_collector=on -c log_destination=stderr -c log_directory=log "
    "-c log_filename=postgresql-%a.log -c log_rotation_age=1d "
    "-c log_rotation_size=0 -c log_truncate_on_rotation=on"
)

#: Every logging GUC that could turn the database log into a second copy of the
#: claim book, with the value that keeps it from doing so.
#:
#: All six are already at these values by default. They are pinned, and asserted,
#: because a default is a thing nobody decided: `log_statement=all` is one line
#: in a `command:` list away, reads in review as a debugging aid, and would put
#: every INSERT's literal values in a file on the encrypted volume for ever.
#: With them stated in the compose files and asserted here, weakening one is a
#: failing test rather than a Tuesday afternoon.
DML_SILENCING_GUCS: dict[str, str] = {
    "log_statement": "none",
    "log_duration": "off",
    "log_min_duration_statement": "-1",
    "log_min_error_statement": "panic",
    "log_parameter_max_length": "0",
    "log_parameter_max_length_on_error": "0",
}


@pytest.fixture(scope="module")
def server() -> Iterator[sa.Engine]:
    """A connection to the test database, on no particular schema.

    Deliberately **not** `seeded_db_url`: every assertion in this file is about
    the postmaster's configuration and its log file, and rebuilding a hundred
    seeded claims to ask `SHOW pgaudit.log` would tie a configuration test to a
    migration run that has nothing to do with it. The DDL probe below creates
    and drops its own table.
    """
    engine = sa.create_engine(
        str(MIGRATION_DB_URL).replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


def setting(engine: sa.Engine, name: str) -> str:
    """One GUC as `SHOW` renders it — units included, which is the point.

    `current_setting` rather than `pg_settings.setting`, because the two
    disagree in exactly the place this file cares about: `log_rotation_age`
    reads `1440` from the catalog (minutes, the internal unit) and `1d` from
    `SHOW`. The compose files say `1d`, so `SHOW` is the spelling that can be
    compared against them without a unit conversion nobody would maintain.
    """
    with engine.connect() as conn:
        return str(conn.execute(sa.text(f"SELECT current_setting('{name}')")).scalar_one())


def _gucs_this_module_asserts_on() -> set[str]:
    """Every GUC name this file reads, taken from this file's own source.

    A hand-written list would be a third statement of the same thing and the one
    that goes stale: somebody adds `setting(server, "log_connections")` to an
    assertion, forgets the list, and the recipe is short again with nothing
    reporting it. So the names are read back out of the AST — every
    `setting(…, "<literal>")` call — and unioned with `DML_SILENCING_GUCS`'
    keys, which reach `setting()` through a comprehension and are therefore
    invisible to that scan.

    `ast` rather than a regex for `tests/test_log_phi_lint.py`'s reason: this
    module's prose names GUCs while arguing about them, and a guard that fires
    on the paragraph explaining the guard is a guard somebody deletes.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    names = set(DML_SILENCING_GUCS)
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "setting":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                names.add(argument.value)
    return names


def test_the_documented_recipe_would_actually_pass_this_suite() -> None:
    """The recipe in a failure message has to be a fix, not a lead.

    Every assertion in this file prints `RECIPE` when it fails, on the argument
    that a developer meeting "pgaudit is not loaded" wants the command that
    starts a database where it is. That argument only holds while the command
    starts a database this file passes against — and it stopped holding once,
    invisibly, because the recipe listed the flags somebody thought were
    non-default and PostgreSQL's defaults disagreed: `pgaudit.log_catalog` ships
    `on` and `log_parameter_max_length` ships `-1`, so the short recipe produced
    `assert 'on' == 'off'` and a developer chasing a build break instead of a
    docstring.

    So every GUC this module asserts on must be named in the recipe, with the
    value the assertion expects where this file knows it. A flag whose default
    happens to be right is still listed: a default is a thing nobody decided,
    and the point of the `command:` list in all three compose profiles is that
    the posture is stated rather than inherited.

    Needs no database — it compares two constants — but lives under this
    module's `requires_db` mark rather than being lifted out, because the thing
    it is about is this module's own failure messages.
    """
    missing = []
    for name in sorted(_gucs_this_module_asserts_on()):
        expected = DML_SILENCING_GUCS.get(name)
        wanted = f"-c {name}=" if expected is None else f"-c {name}={expected}"
        if wanted not in RECIPE:
            missing.append(name)
    assert missing == [], (
        f"RECIPE does not start postgres with {sorted(missing)}, and this file asserts "
        "on every one of them. A database started from the recipe would fail the suite "
        "the recipe is printed by, which turns a documentation slip into an hour spent "
        "reading a real assertion as though it were a real defect."
    )


def test_the_server_is_started_with_pgaudit_preloaded(server: sa.Engine) -> None:
    """The premise for everything else in this file.

    pgaudit hooks the executor and the utility processor, which it can only do
    from `shared_preload_libraries` — so a server without this captures nothing
    at all, and `CREATE EXTENSION pgaudit` refuses outright. Migration 0050's
    docstring says that refusal is by design; this is the assertion that the
    other half of the arrangement is actually in place, and it is first because
    every failure below would otherwise be a confusing restatement of it.
    """
    loaded = setting(server, "shared_preload_libraries")
    assert "pgaudit" in loaded.split(","), (
        f"shared_preload_libraries is {loaded!r} and does not name pgaudit, so nothing "
        f"in AD-11's database-audit posture is in force.\n{RECIPE}"
    )


def test_pgaudit_captures_ddl_and_role_classes_only(server: sa.Engine) -> None:
    """`ddl, role` and nothing else — AD-11's division of labour with AD-4.

    `read`, `write` and `function` are the three that would log statement text,
    and `all` is all four. Any of them would make the database log a second,
    unredactable copy of the claim book: the Story 8.1 cascade rewrites
    `audit_event`'s diffs in place and deletes rows from nineteen tables, and it
    has no reach into a file the postmaster wrote.

    Asserted as an exact set rather than as "read is absent", so a class added
    later fails here and has to be argued for. `log_parameter` is in the same
    test because it is the same leak from the other direction — a bound
    parameter is a claim field with the statement stripped off.
    """
    classes = tuple(part.strip() for part in setting(server, "pgaudit.log").split(","))
    assert classes == ("ddl", "role"), (
        f"pgaudit.log is {classes!r}. Value-level history is audit_event's (AD-4), and "
        "the Story 8.1 purge cascade can redact that; a DB log line it cannot."
    )
    assert setting(server, "pgaudit.log_parameter") == "off"
    assert setting(server, "pgaudit.log_relation") == "off"
    assert setting(server, "pgaudit.log_catalog") == "off"

    wrong = {
        name: actual
        for name, expected in DML_SILENCING_GUCS.items()
        if (actual := setting(server, name)) != expected
    }
    assert wrong == {}, (
        f"these logging settings are not at their pinned values: {wrong} "
        f"(expected {DML_SILENCING_GUCS}). Every one of them is a route from a "
        f"statement's literal values into the database log.\n{RECIPE}"
    )


def test_the_db_log_rotates_within_a_bounded_window(server: sa.Engine) -> None:
    """AC 1's second half: the log destination is bounded and inside PGDATA.

    Two properties in one test because they are one arrangement.

    **Bounded, and the bound is a time bound** (Design Note 2). `postgresql-%a
    .log` + `log_truncate_on_rotation=on` + `log_rotation_age=1d` +
    `log_rotation_size=0` is Postgres's own recipe for seven files, one per
    weekday, each truncated when its day comes round. `log_rotation_size` is
    asserted *at zero* rather than at a byte cap, which looks backwards until
    you work out what a byte cap does here: it rotates within a day, back onto
    the same `%a` filename, silently destroying that morning's lines.

    **Inside PGDATA**, because `log_directory` is relative — so the file
    resolves under the data directory and therefore inside the volume the
    deployment view encrypts at rest. An absolute path or a bind mount would
    put the DDL record somewhere nobody had decided to encrypt, which is the
    failure this assertion exists to catch, and it is checked against
    `pg_current_logfile()`'s answer rather than against the setting, because the
    setting is the intent and the resolved path is the fact.
    """
    assert setting(server, "logging_collector") == "on", (
        "logging_collector is off, so the postmaster's log goes to container stdout "
        f"and never lands on the encrypted volume at all.\n{RECIPE}"
    )
    assert setting(server, "log_filename") == "postgresql-%a.log"
    assert setting(server, "log_rotation_age") == "1d"
    assert setting(server, "log_rotation_size") == "0"
    assert setting(server, "log_truncate_on_rotation") == "on"

    with server.connect() as conn:
        current = conn.execute(sa.text("SELECT pg_current_logfile()")).scalar_one()
        data_directory = conn.execute(sa.text("SHOW data_directory")).scalar_one()
    assert current, "pg_current_logfile() is empty — the collector is not writing a file"
    assert not str(current).startswith("/"), (
        f"the DB log resolves to the absolute path {current!r}, which is outside PGDATA "
        "and therefore outside the volume the deployment encrypts (AD-11)."
    )
    assert str(current).startswith("log/"), current
    assert str(data_directory).startswith("/var/lib/postgresql"), data_directory


def test_a_ddl_statement_is_logged_and_a_dml_literal_is_not(server: sa.Engine) -> None:
    """The posture as behaviour rather than as configuration — the whole story in one test.

    Every assertion above reads a setting, and a setting is a description. This
    runs the two statements the description is about and reads the file the
    postmaster actually wrote: a `CREATE TABLE` must appear as an `AUDIT` line
    of class `DDL`, and an `INSERT` carrying a literal nothing else in this
    build would ever produce must appear nowhere at all.

    **The literal is the test.** A search for a value that was never distinctive
    would pass against a log full of claim data — `tests/test_copilot_logging
    .py`'s rule — so it is long, specific and stamped with the clock, and the
    DDL assertion is its positive control: if the `AUDIT` line is missing then
    pgaudit wrote nothing and the absence below means nothing either.

    **The poll is because the collector is asynchronous.** `logging_collector`
    hands lines to a separate process, so a read immediately after the commit
    genuinely races it. Bounded at five seconds: longer than the collector needs
    on any machine, and short enough that a broken posture fails rather than
    hangs.

    **And the line polled for is the one written *last*.** This is the whole
    reason there is a `DROP TABLE` in the middle of a test about a `CREATE` and
    an `INSERT`. Polling until the `CREATE TABLE` line appears and then
    asserting the INSERT's literal is absent from that same snapshot applies the
    asynchrony argument to the positive control and forgets it for the negative
    one: the INSERT ran *after* the CREATE, so a leaked line need not have been
    flushed yet, and the absence assertion could pass against a database that
    was leaking and merely slow. The DDL that follows the DML is the marker —
    once it is in the file, everything the session issued before it has been
    written, and "not there" means not there.
    """
    stamp = f"ZZLINEWORKER82{int(time.time() * 1000)}ZZ"
    table = f"pgaudit_probe_{int(time.time() * 1000)}"
    with server.connect() as conn:
        conn.execute(sa.text(f"CREATE TABLE {table} (note text)"))
        try:
            conn.execute(sa.text(f"INSERT INTO {table} (note) VALUES ('{stamp}')"))
        finally:
            conn.execute(sa.text(f"DROP TABLE {table}"))

    deadline = time.monotonic() + 5.0
    log_text = ""
    while time.monotonic() < deadline:
        with server.connect() as conn:
            log_text = str(
                conn.execute(sa.text("SELECT pg_read_file(pg_current_logfile())")).scalar_one()
            )
        # The trailing DROP, not the leading CREATE: see the docstring. It is
        # the last statement the session issued, so its arrival is what makes
        # the absence of the INSERT's literal below a fact rather than a race.
        if f"DROP TABLE {table}" in log_text:
            break
        time.sleep(0.25)

    assert f"DROP TABLE {table}" in log_text, (
        f"no pgaudit AUDIT line for `DROP TABLE {table}` reached the log within 5s, so "
        "the collector has not caught up with the statements this test issued and "
        f"nothing below can be concluded from a value's absence.\n{RECIPE}"
    )

    ddl_lines = [
        line
        for line in log_text.splitlines()
        if "AUDIT:" in line and f"CREATE TABLE {table}" in line
    ]
    assert ddl_lines, (
        f"no pgaudit AUDIT line for `CREATE TABLE {table}` reached the log file within "
        f"5s — DDL capture is the half of pgaudit.log this posture keeps.\n{RECIPE}"
    )
    assert ",DDL," in ddl_lines[0], ddl_lines[0]

    assert stamp not in log_text, (
        "an INSERT's literal value reached the database log. That is a copy of claim "
        "data the Story 8.1 purge cascade cannot redact, which is the single thing "
        "this story's pgaudit posture exists to prevent."
    )
    assert f"INSERT INTO {table}" not in log_text, (
        "the DML statement text reached the database log — pgaudit.log names a write "
        "class, or one of the DML_SILENCING_GUCS has been weakened."
    )
