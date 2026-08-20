"""The dependency direction, and the AD-3 exception, as grep properties.

Two rules that have been true since Story 1.1 and enforced by review until now.
Story 6.3 is the moment machine-checking them becomes worth it, for two
different reasons.

**`services/` must never import `agents/`.** `agents/__init__.py` states the
direction — composition root → `agents/` → `services/` → `data/` — and
`services/rag/insights.py` is built around it: it takes an injected
`InsightGenerator` rather than calling a model, which is the entire reason chat
code stays out of the package that owns `ai_insight`. Until this story that
arrangement had **one** consumer, so the rule was easy to keep by accident. This
story adds the second (`agents/graph.py` and `agents/registry.py` both reach
down into `services/`), and a rule with two consumers is a rule somebody will
eventually satisfy by importing upward "just this once" — at which point the
import graph has a cycle and the claim that inference originates in exactly one
package stops being checkable.

**No application code may read a checkpoint table.** AD-3's registered exception
buys a vendored migration on the understanding that the saver is the only thing
that touches `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and
`checkpoint_migrations`. Vendoring the DDL is worthless if a history endpoint
then runs `SELECT … FROM checkpoints` anyway, and that is a tempting shortcut
precisely because the tables are right there and the saver's API is not obvious.

Both are **AST** checks rather than text greps, and the first version of this
module was the grep. It failed immediately and instructively: every module that
argues *why* it does not read a checkpoint table names one in its docstring, and
`api/app.py` says "`saver.setup()` is never called" in the very place a grep
would read as a call. A rule whose enforcement fires on the prose explaining the
rule is a rule somebody deletes. So imports are read from `ast.Import`/
`ast.ImportFrom`, calls from `ast.Call`, and SQL from **string literals only** —
which is where SQL actually lives and where a docstring's sentence does not.

`tests/` is excluded from both, for the reason every structural guard in this
suite excludes it: the tests are the independent oracle and are allowed to name
what they are asserting about — `test_copilot_persistence.py` deliberately
selects from `checkpoints` to prove the saver wrote there, and this very module
contains every string it scans for.
"""

import ast
import re
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: The four tables the LangGraph saver owns.
CHECKPOINT_TABLE_NAMES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)

#: A checkpoint table named as a SQL object — the shortcut this guard forbids.
#:
#: Matched against **string literals only**, and against a SQL keyword before
#: the name rather than against the name alone. Both narrowings are load-bearing:
#: a docstring saying "the saver owns `checkpoints`" is correct and must not
#: fire, while `sa.text("SELECT count(*) FROM checkpoints")` must.
_SQL_AGAINST_CHECKPOINTS = re.compile(
    r"\b(?:from|into|update|join|table)\s+(?:" + "|".join(CHECKPOINT_TABLE_NAMES) + r")\b",
    re.IGNORECASE,
)

#: Files allowed to name a checkpoint table in SQL, each with the reason.
#:
#: An allowlist rather than a directory carve-out, so adding a file to it is a
#: diff a reviewer sees and has to agree with.
CHECKPOINT_SQL_EXEMPTIONS: dict[str, str] = {
    # The vendored DDL itself — it is what creates them (AD-3's exception).
    "data/versions/20260820_0043_copilot_threads.py": "the vendored migration",
}


def _python_sources() -> list[Path]:
    return [
        path
        for path in SERVER_ROOT.rglob("*.py")
        if ".venv" not in path.parts and "tests" not in path.parts
    ]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def imports_agents(tree: ast.Module) -> bool:
    """Whether a module imports the `agents` package, in either spelling.

    Read off the AST, so the sentence "services/ may never import agents/" in a
    docstring is what it is — an explanation — rather than a violation.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "agents":
            return True
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "agents" for alias in node.names
        ):
            return True
    return False


def calls_setup(tree: ast.Module) -> bool:
    """Whether a module *calls* a `.setup()` method.

    An `ast.Call` whose function is an attribute named `setup`. `api/app.py`
    contains the string `saver.setup()` in the paragraph explaining that it never
    calls it, which is exactly the sentence a text grep cannot tell from the
    thing it forbids.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setup"
        for node in ast.walk(tree)
    )


def _docstrings(tree: ast.AST) -> set[int]:
    """Every docstring node in a module, by identity.

    Excluded from the SQL scan below, and this is the second half of the lesson
    the module docstring records. A docstring **is** a string literal in the
    AST, so filtering to string literals was not enough on its own: three
    modules explain at length that they read checkpoints *through the saver*,
    and every one of them names the tables while doing it.

    Identified positionally — the first statement of a module, class or function
    when it is a bare string — because that is what a docstring is. A string
    used as a block comment elsewhere in a body is not one, and is deliberately
    still scanned: a SQL statement parked in a loose string is exactly the
    smuggling route this guard should close.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found


def sql_against_checkpoints(tree: ast.Module) -> bool:
    """Whether any non-docstring string literal addresses a checkpoint table in SQL."""
    skip = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and _SQL_AGAINST_CHECKPOINTS.search(node.value)
        for node in ast.walk(tree)
    )


def test_no_module_under_services_imports_agents() -> None:
    """The dependency direction, machine-checked at its second consumer.

    A violation is not a style problem: `services/rag` owns `ai_insight` and
    `agents/insights.py` writes through it, so an import the other way is a
    cycle — and the first symptom is an `ImportError` at process start whose
    cause is three files away from the line that caused it.
    """
    offenders = [
        str(path.relative_to(SERVER_ROOT))
        for path in _python_sources()
        if path.is_relative_to(SERVER_ROOT / "services") and imports_agents(_tree(path))
    ]
    assert offenders == [], (
        "these modules under services/ import agents/, which inverts the dependency "
        f"direction the whole server is arranged around: {offenders}"
    )


def test_no_module_under_data_or_rules_imports_agents() -> None:
    """The same rule, one and two layers further down.

    `services/` is where the temptation actually is, but the direction is
    `agents/` → `services/` → `data/` throughout, and a repository or a rules
    module importing an agent would be a worse version of the same cycle.
    """
    offenders = [
        str(path.relative_to(SERVER_ROOT))
        for path in _python_sources()
        if (path.is_relative_to(SERVER_ROOT / "data") or path.is_relative_to(SERVER_ROOT / "rules"))
        and imports_agents(_tree(path))
    ]
    assert offenders == []


def test_the_import_guard_would_notice_an_upward_import() -> None:
    """A guard that only ever reads a clean tree cannot tell "nothing is wrong"
    from "nothing is checked" (`test_scoped_repository.py`'s rule)."""
    for smell in (
        "from agents import refresh_claim_insights",
        "import agents",
        "def f():\n    from agents.registry import REGISTRY",
        "from agents.graph import build_graph",
    ):
        assert imports_agents(ast.parse(smell))


def test_the_import_guard_leaves_innocent_modules_alone() -> None:
    """The other half: the rule is about the package, not about the word."""
    for innocent in (
        '"""agents/ may import services/, never the other way round."""',
        "from services.agents_helper import thing",
        "agents = build_agents()",
    ):
        assert not imports_agents(ast.parse(innocent))


def test_no_application_module_queries_a_checkpoint_table() -> None:
    """AD-3's exception, enforced rather than trusted.

    The saver owns these tables. `agents/threads.py::thread_state` reads a
    transcript back **through the graph**, which asks its checkpointer; the
    history route asks `thread_state`. Nothing anywhere writes SQL against them,
    and this is what keeps that true when the next story wants "just a count".

    The one exemption is the migration that creates them, which has to name them
    to do its job.
    """
    offenders = []
    for path in _python_sources():
        relative = str(path.relative_to(SERVER_ROOT))
        if relative in CHECKPOINT_SQL_EXEMPTIONS:
            continue
        if sql_against_checkpoints(_tree(path)):
            offenders.append(relative)
    assert offenders == [], (
        "these modules query a LangGraph checkpoint table; AD-3's exception vendors that "
        "schema on the understanding that only the saver reads it, and a query here "
        f"would make the vendored migration pointless: {offenders}"
    )


def test_the_sql_guard_would_notice_a_query_and_not_a_docstring() -> None:
    """Both directions, because this guard's first version failed on the prose.

    Every module that explains why it does not read a checkpoint table names one
    while doing so, and a grep read those sentences as violations. The check is
    what distinguishes "SELECT … FROM checkpoints" from "the saver owns
    `checkpoints`".
    """
    assert sql_against_checkpoints(
        ast.parse('sa.text("SELECT count(*) FROM checkpoints WHERE thread_id = :t")')
    )
    assert sql_against_checkpoints(ast.parse('sa.text("DELETE FROM checkpoint_writes")'))
    assert not sql_against_checkpoints(
        ast.parse('"""Nothing here selects from checkpoints; the saver is asked instead."""')
    )
    assert not sql_against_checkpoints(ast.parse('CHECKPOINT_TABLES = ("checkpoints",)'))


def test_every_checkpoint_exemption_still_exists() -> None:
    """An allowlist entry for a deleted file is an allowlist entry nobody notices.

    It is also the shape a bypass takes: rename a module to a path that happens
    to be exempt and the guard stops covering it.
    """
    for relative in CHECKPOINT_SQL_EXEMPTIONS:
        assert (SERVER_ROOT / relative).is_file(), (
            f"{relative} is exempted from the checkpoint-table guard but does not exist"
        )


def test_saver_setup_is_never_called_outside_a_migration() -> None:
    """ "Never call `saver.setup()` in prod" — the header's promise, checked.

    `setup()` issues DDL. The application role has no schema rights, Alembic owns
    the schema, and a `setup()` on boot would be a second owner of tables whose
    whole point is that they have one. The scratch script that generated
    migration 0043's SQL called it exactly once, on a throwaway database, and is
    not in the tree.

    `api/app.py` is where the call *would* go and says so in prose; reading calls
    off the AST is what lets the paragraph and the prohibition coexist.
    """
    offenders = [
        str(path.relative_to(SERVER_ROOT)) for path in _python_sources() if calls_setup(_tree(path))
    ]
    assert offenders == []


def test_the_setup_guard_would_notice_a_call() -> None:
    """…and is not passing because `.setup()` is spelled some other way."""
    assert calls_setup(ast.parse("await saver.setup()"))
    assert calls_setup(ast.parse("asyncio.run(checkpointer.setup())"))
    assert not calls_setup(ast.parse('"""saver.setup() is never called in prod."""'))
