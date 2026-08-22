"""The AD-11 app-log PHI ban, as a build step rather than as a convention.

    uv run python -m scripts.lint_log_phi

Before Story 8.2 the ban was a sentence in `logging_config.py`'s docstring and
six hand-written tests that ran a flow and searched stderr for the fixtures'
distinctive strings. Those tests are good and they stay — they are the only
thing that can prove a *value* did not reach a line — but they cover the flows
somebody remembered to cover, and a leak arrives in the flow nobody thought
about. So this reads every log call in the tree and holds each one to the
closed vocabulary in `logging_config.LOG_KEY_ALLOWLIST`.

## What it refuses, and why those two shapes

1. **A keyword that is not on the allowlist.** `log.info("note.created",
   body=note.text)` is the whole failure in one line, and it does not look like
   a mistake while you are writing it. The allowlist inverts the burden: a name
   nobody has argued for is refused, so adding one is a diff a reviewer sees
   rather than a leak nobody does.
2. **An interpolated or stringified value.** `log.info("x", claim_id=f"{claim
   .claim_id} ({claim.cause})")` passes an allowlisted *key* and carries a
   claim's narrative anyway. An interpolated value is the one shape where the
   key stops predicting the content, so the interpolation itself is the smell —
   and this codebase never needs one, because structlog renders values rather
   than messages.

   f-strings are not the only spelling, and the one that matters most is not an
   f-string at all. `error` is allowlisted because the house spelling is
   `error=type(exc).__name__` — a class name, which carries nothing — but
   `error=str(exc)` passes the same key and an exception *message* routinely
   quotes the value the statement was carrying: a `CHECK` violation on
   `diary_note` names the note. So `str(…)`, `repr(…)`, `"…".format(…)` and
   `"%s" % …` are refused under **any** keyword, allowlisted ones included, for
   the same reason the f-string is. Anything that turns an object into a string
   before structlog sees it has decided what the line says, and the allowlist
   only governs what the line is *called*.

`**` unpacks are refused for the same reason as (2) — the keys are invisible to
this file — with one registered exemption below.

## AST rather than grep, and `tests/test_purge_ownership.py` is why

Every module that argues about a rule names the thing the rule forbids while
doing so. This file is itself full of the strings `body=`, `prompt=` and
`diagnosis=`; `services/claims/edit.py` has a docstring reading "echoing what
they typed would put claim data in an error body"; `test_copilot_logging.py`
carries whole prose paragraphs about prompts. A regex would fire on all of
them, and a guard whose enforcement fires on the prose explaining the guard is
a guard somebody switches off. So calls are read from `ast.Call`, and only from
receivers this codebase actually names a logger.

## Importable detectors, because the tests are the other half

`log_calls` and `offending_keywords` take a parsed module and are imported by
`tests/test_log_phi_lint.py`, which runs them over the tree *and* over
hand-written smells and hand-written innocents. `main()` is the thin CLI on top
that the `server` CI job runs beside ruff and mypy.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from logging_config import _META_KEYS, LOG_KEY_ALLOWLIST

SERVER_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: Every keyword name a log call may pass, from `logging_config`'s two sets.
#:
#: **Two sets because they make two different claims, and the gate has to know
#: both.** `LOG_KEY_ALLOWLIST` is a list of permissions somebody argued for, and
#: `tests/test_log_phi_lint.py` holds it to the tree in both directions — an
#: entry nothing emits is removed. `_META_KEYS` is the shape of a rendered line:
#: `exc_info`, `stack_info`, `level`, `timestamp` and the rest are added or
#: consumed by processors rather than emitted as anybody's data, so they are not
#: permissions and must not be allowlist entries.
#:
#: A lint that read only the first would report `log.error("x", exc_info=True)`
#: as a leak and then print a remedy telling the author to allowlist `exc_info`
#: — which the "every allowlisted key is still used" test immediately fails on,
#: because nothing *passes* it as a keyword the AST can see. Two constants that
#: must agree with a gate consulting one is the failure `logging_config`'s
#: docstring says it avoided by giving each rule a single definition; this
#: union is what keeps that true on this side of the boundary.
PERMITTED_KEYS: Final[frozenset[str]] = LOG_KEY_ALLOWLIST | _META_KEYS

#: The trees a log call could hide in — `tests/test_purge_ownership.py::
#: SCAN_ROOTS`, restated rather than imported so that a guard and its neighbour
#: are two independent statements about the tree rather than one.
#:
#: `tests/` is deliberately absent. The suite logs on purpose (it configures the
#: pipeline and reads stderr back), and it is the independent oracle rather than
#: code under rule.
SCAN_ROOTS: Final[tuple[str, ...]] = ("api", "services", "rules", "agents", "data", "scripts")

#: Alembic revisions are excluded: a migration is a frozen historical record of
#: what the schema became, and rewriting one to satisfy a rule invented later is
#: how a migration stops describing what actually ran. None of them logs.
EXCLUDED: Final[tuple[str, ...]] = ("data/versions",)

#: The names this codebase gives a logger. `logging_config.py` says loggers are
#: obtained uniformly as a module-level `log = structlog.get_logger()`, and the
#: whole tree keeps to it; `logger` is admitted because it is the name anybody
#: would reach for next and a guard that missed it would be blind by spelling.
#:
#: Narrow on purpose, and the negative control names the cost: a `body.error(…)`
#: or a `parser.error(…)` is not a log call, and a receiver-blind detector would
#: report `argparse` in `scripts/purge_claim.py` as a PHI leak.
RECEIVERS: Final[frozenset[str]] = frozenset({"log", "logger"})

#: structlog's `BoundLogger` levels plus the stdlib ones it mirrors. `log` and
#: `msg` are structlog's aliases for `info`; `exception` is `error` with
#: `exc_info`.
LEVELS: Final[frozenset[str]] = frozenset(
    {
        "critical",
        "debug",
        "error",
        "exception",
        "fatal",
        "info",
        "log",
        "msg",
        "warn",
        "warning",
    }
)

#: The other way keywords reach a log line, and levels alone are blind to it.
#:
#: `log.bind(body=note.text).info("note.created")` emits exactly the line the
#: whole rule forbids, and the only keyword in it is on the `bind`: the `.info`
#: call carries none, and its receiver is a `Call` rather than a `Name`, so a
#: level-only detector reads the statement as clean twice over. Same for
#: `log = log.bind(...)` a few lines earlier, which is the spelling anybody
#: adding request context would actually reach for.
#:
#: The tree uses neither today. It is here because "the lint is the gate and the
#: processor is depth" is the stated architecture (`logging_config
#: ._block_unallowlisted`'s docstring), and a gate with a hole this shape means
#: the run-time backstop is the only thing between a bound value and the log —
#: which is the arrangement Design Note 5 says is not enough on its own.
BINDERS: Final[frozenset[str]] = frozenset({"bind"})

#: structlog's context-local binder, which takes keywords with no logger at all.
#:
#: `structlog.contextvars.bind_contextvars(body=…)` puts a value into every
#: subsequent line in the request, so it is a log call in every sense this file
#: cares about while naming no receiver `RECEIVERS` would recognise. Matched on
#: the final attribute name so the import style does not decide whether the rule
#: applies — `structlog.contextvars.bind_contextvars(…)`, `contextvars
#: .bind_contextvars(…)` and a bare `bind_contextvars(…)` are one call.
CONTEXT_BINDERS: Final[frozenset[str]] = frozenset({"bind_contextvars"})

#: Files permitted to pass `**kwargs` into a log call, with the reason.
#:
#: A `**` unpack hides its key names from this file completely, so it is refused
#: everywhere else: it is the one shape that can put an arbitrary mapping —
#: `**claim.__dict__` — into a log line while reviewing as a tidy refactor.
#:
#: An explicit map rather than a directory carve-out, `tests/
#: test_purge_ownership.py::PHI_DELETE_EXEMPTIONS`' convention, so that adding
#: an entry is a diff somebody has to agree with. `test_every_star_kwarg_
#: exemption_still_exists` keeps it from going stale, which matters more here
#: than for most allowlists: rename a module onto an exempt path and its log
#: calls stop being checked.
STAR_KWARG_EXEMPTIONS: Final[dict[str, str]] = {
    "services/audit/purge.py": (
        "`_log_run(event, run, **subject)` — the tree's only `**` unpack into a "
        "log call. Its docstring states what the mapping may hold and why: "
        "'The subject is a claim's business id or a user's surrogate id. Both "
        "are identifiers rather than content — AD-11 permits ids and event "
        "names'. The two call sites pass `claim_id=` and `user_id=` as literal "
        "keywords, both of which are on LOG_KEY_ALLOWLIST, so the vocabulary is "
        "closed in fact even though it is closed one frame further out than "
        "this file can see."
    ),
}

#: Builtins that turn an arbitrary object into a string. Refused as a log
#: keyword's value under *any* key, allowlisted or not — see `_interpolated`.
_STRINGIFIERS: Final[frozenset[str]] = frozenset({"str", "repr"})

#: What a failure tells the reader to do. Printed once, under the offenders, so
#: that the fix is in the same output as the problem rather than in a wiki.
REMEDY: Final[str] = (
    "AD-11: operational logs carry identifiers and event names only — never a "
    "claim field value, a prompt body or a model's answer.\n"
    "Log an id and let the value live in `audit_event`, which the Story 8.1 "
    "purge cascade can redact; a log line cannot be redacted by anything this "
    "system owns.\n"
    'A finding spelled `key=f"…"`, `key=str(…)`, `key=repr(…)`, '
    '`key="…".format(…)` or `key="%s" % …` is about the VALUE, and the key '
    "being allowlisted does not settle it: the house spelling for an exception "
    "is `error=type(exc).__name__`, because an exception's message quotes what "
    "the statement was carrying. Pass the object and let structlog render it, "
    "or pass an id.\n"
    "If the keyword really is an identifier, a count or a closed vocabulary, "
    "add it to `logging_config.LOG_KEY_ALLOWLIST` in the group it belongs to "
    "— that is a decision a reviewer sees. Keys the pipeline itself adds "
    "(`exc_info`, `stack_info`, …) are `logging_config._META_KEYS` and are "
    "already permitted; they do not belong on the allowlist."
)


def sources() -> list[Path]:
    """Every module this lint reads.

    **The six packages *and* `server/`'s own top-level modules.** `SCAN_ROOTS`
    is `tests/test_purge_ownership.py::SCAN_ROOTS` restated, and that guard has
    a reason to be package-shaped: it is about repositories and delete calls,
    which live in packages. This guard is about log calls, which live wherever
    somebody writes one — and `config.py` and `logging_config.py` are not under
    any of the six. Neither logs today, so the hole is latent rather than open;
    it is closed here because the next top-level module is exactly the sort of
    thing that gets one, and nothing would have said so.

    The vacuity guard is `tests/test_purge_ownership.py::_sources`' and it is
    not decoration: a renamed package would turn this whole lint into a loop
    over nothing that exits 0, which is indistinguishable from a clean tree in
    every output anybody looks at. It **raises** rather than asserting, which is
    the one place this file departs from that template: the template runs under
    pytest, where `-O` is unreachable, and this runs as `python -m
    scripts.lint_log_phi` in CI. Under `PYTHONOPTIMIZE` an `assert` is compiled
    out, and the lint would then exit 0 over an empty tree — the precise
    outcome the guard exists to make impossible.
    """
    found = [
        path
        for root in SCAN_ROOTS
        for path in (SERVER_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
        and not any(str(path.relative_to(SERVER_ROOT)).startswith(skip) for skip in EXCLUDED)
    ]
    found += sorted(SERVER_ROOT.glob("*.py"))
    if not found:
        raise RuntimeError(
            f"nothing scanned under {SCAN_ROOTS} or {SERVER_ROOT} — did a package move? "
            "An empty scan exits 0 and reads exactly like a clean tree, which is why "
            "this refuses instead."
        )
    return found


def _receiver(node: ast.expr) -> str | None:
    """The dotted name a call is made on, or `None` if it is not a plain name.

    `log` → `"log"`, `structlog.contextvars` → `"structlog.contextvars"`, and
    `None` for anything computed — including `log.bind(…)` in a chained
    `log.bind(…).info(…)`, whose receiver is a `Call`. That last one is not a
    gap: `ast.walk` visits the inner `bind` call in its own right, and the
    keywords are on the `bind`, so the chain is caught where its content is.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _receiver(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def log_calls(tree: ast.AST) -> Iterator[ast.Call]:
    """Every call in a module that can put keywords on a log line.

    Three shapes: `log.<level>(…)` / `logger.<level>(…)`, `log.bind(…)` on the
    same receivers, and `structlog.contextvars.bind_contextvars(…)`. The last
    two carry keywords into a line the level call then emits, so a detector that
    knew only levels would read `log.bind(body=note.text).info("x")` as clean.

    Matched on the receiver *name* rather than on its type, which is the same
    compromise `tests/test_purge_ownership.py::_is_phi_delete_call` makes and
    for the same reason: the type is not in the AST. The cost is a
    `log = SomethingElse()` that this file would read as a logger; the benefit
    is that `parser.error(…)` and `response.warning(…)` are not log calls and
    are not reported as leaks.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in CONTEXT_BINDERS:
            yield node
            continue
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr in CONTEXT_BINDERS:
            yield node
            continue
        if func.attr not in LEVELS and func.attr not in BINDERS:
            continue
        if _receiver(func.value) in RECEIVERS:
            yield node


def _interpolated(value: ast.expr) -> str | None:
    """How this value was turned into a string before structlog could see it.

    Returns the shape to report, or `None` for a value the caller simply passed.

    **All five shapes are one finding**, and reading them as one is the point:
    an f-string, `str(x)`, `repr(x)`, `"{}".format(x)` and `"%s" % x` all take
    an object whose contents nobody has argued about and hand the pipeline a
    string. The key stops predicting the content at that moment, which is why
    this is checked under allowlisted keys too — `error` is on the allowlist
    for `type(exc).__name__`, and `error=str(exc)` is the same key carrying an
    exception message that quotes whatever literal the failing statement held.

    Deliberately narrow in two ways. Only `str`/`repr` **called by name** are
    refused: a `Name` receiver is what the shadowing rules make reliable, and
    the alternative (any call whose result might be a string) would report
    `sorted(after)`, `sorted(set(failures))` and every other honest call in the
    tree. And only a **string literal** on the left of `%`: `duration_ms=
    elapsed % 1000` is arithmetic, and a guard that could not tell the two
    apart would be a guard people work around.
    """
    if isinstance(value, ast.JoinedStr):
        return 'f"…"'
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name) and func.id in _STRINGIFIERS:
            return f"{func.id}(…)"
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return '"…".format(…)'
    if (
        isinstance(value, ast.BinOp)
        and isinstance(value.op, ast.Mod)
        and isinstance(value.left, ast.Constant)
        and isinstance(value.left.value, str)
    ):
        return '"%s" % …'
    return None


def offending_keywords(tree: ast.AST, *, allow_star: bool = False) -> list[tuple[int, str]]:
    """`(line, keyword)` for everything a log call in this module may not pass.

    Three findings, reported as the keyword name so the message names the thing
    to change:

    * a keyword whose name is neither in `LOG_KEY_ALLOWLIST` nor in
      `logging_config._META_KEYS`;
    * a keyword whose value was stringified before it got here — an f-string,
      `str(…)`, `repr(…)`, `.format(…)` or `"…" % …` — reported as
      `<name>=<shape>` even when the *name* is allowlisted, because the
      interpolation is the finding (see `_interpolated`);
    * a `**` unpack, reported as `**`, unless `allow_star` says the file is in
      `STAR_KWARG_EXEMPTIONS`.

    **Why `_META_KEYS` is consulted here and not only by the processor.**
    `logging_config` defines two sets for two different claims: the allowlist is
    a list of permissions somebody argued for, and the meta keys are the shape
    of a rendered line — `exc_info`, `stack_info` and the rest are consumed by
    processors rather than emitted as data. A gate that knew only the first
    would report `log.error("x", exc_info=True)` as a leak and its own remedy
    would tell the author to allowlist `exc_info`, which
    `tests/test_log_phi_lint.py::test_every_allowlisted_key_is_still_used_by_
    some_log_call` then fails on: two constants that must agree, with the gate
    consulting one, is exactly the failure `logging_config`'s docstring claims
    to have avoided by having a single definition of each.

    The event name is a *positional* argument and is deliberately not checked.
    Two of them are computed — `agents/approval.py`'s `f"copilot.write_
    {outcome}"` and `services/audit/purge.py`'s `log.info(event, …)` — and both
    interpolate a closed set of outcome words rather than anything a claim
    carries. An event name is the subject of the line, not its content.
    """
    findings: list[tuple[int, str]] = []
    for call in log_calls(tree):
        for keyword in call.keywords:
            if keyword.arg is None:
                if not allow_star:
                    findings.append((keyword.value.lineno, "**"))
                continue
            shape = _interpolated(keyword.value)
            if shape is not None:
                findings.append((keyword.value.lineno, f"{keyword.arg}={shape}"))
                continue
            if keyword.arg not in PERMITTED_KEYS:
                findings.append((keyword.value.lineno, keyword.arg))
    return sorted(findings)


def offenders() -> list[str]:
    """Every finding in the tree, already rendered as `path:line: keyword`."""
    reported: list[str] = []
    for path in sources():
        relative = str(path.relative_to(SERVER_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allow_star = relative in STAR_KWARG_EXEMPTIONS
        reported.extend(
            f"{relative}:{line}: {keyword}"
            for line, keyword in offending_keywords(tree, allow_star=allow_star)
        )
    return sorted(reported)


def main() -> int:
    """Print the offenders and the remedy; 0 when the tree is clean."""
    found = offenders()
    if not found:
        return 0
    print("log calls carrying a keyword the AD-11 allowlist does not permit:", file=sys.stderr)
    for line in found:
        print(f"  {line}", file=sys.stderr)
    print(f"\n{REMEDY}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
