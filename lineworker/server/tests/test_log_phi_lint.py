"""AC 2 as a property of the tree: **no log call carries PHI** (AD-11).

`tests/test_copilot_logging.py` and `tests/test_ai_insights.py` prove that the
flows they drive emit nothing they shouldn't. That is the strongest evidence
there is — it reads the bytes the process actually wrote — and it covers
exactly the flows somebody remembered to write a test for. A leak arrives in
the flow nobody thought about, which is why Story 8.2 adds the other kind of
check: `scripts/lint_log_phi.py` reads every log call in the tree and holds each
one to the closed vocabulary in `logging_config.LOG_KEY_ALLOWLIST`.

This file is that lint's own test, and it is written the way every static guard
in this suite is written (`tests/test_purge_ownership.py` is the template),
because a guard that has only ever read a clean tree cannot tell "nothing is
wrong" from "nothing is checked". Three mandatory controls:

* **would-notice** — the detector fires on each shape of leak somebody would
  plausibly write while solving a real problem;
* **leaves-innocent-alone** — and does not fire on the prose that explains the
  rule, on an allowlisted keyword, or on a `.error(…)` that is not a log call;
* **exemption staleness** — because an allowlist entry for a deleted file is
  both invisible and the shape a bypass takes.

The fourth test here is the one the other guards in this suite have no analogue
for, and it is the reason the allowlist is worth having at all: it holds the
list to the tree in the *other* direction. A name nothing emits is a permission
nobody is exercising, and the next reader takes it as precedent for a keyword
close to it that is not safe.
"""

import ast
from pathlib import Path

import pytest

from logging_config import LOG_KEY_ALLOWLIST
from scripts.lint_log_phi import (
    SERVER_ROOT,
    STAR_KWARG_EXEMPTIONS,
    log_calls,
    offenders,
    offending_keywords,
    sources,
)


def test_no_log_call_passes_a_keyword_off_the_allowlist() -> None:
    """The rule itself, over the whole scanned tree.

    The remedy is never "add the name to the allowlist so this passes". It is
    to log an id and leave the value in `audit_event`, which the Story 8.1
    cascade can redact — a database log line and an operator's scrollback
    cannot be redacted by anything this system owns, which is the whole reason
    AD-11 draws the line where it does. Widening the vocabulary is a decision
    about what operations legitimately needs to see, and it belongs in a diff a
    reviewer reads rather than in a fix for a red build.
    """
    found = offenders()
    assert found == [], (
        "these log calls carry a keyword the AD-11 allowlist does not permit: "
        f"{found}. Run `uv run python -m scripts.lint_log_phi` for the remedy."
    )


def test_the_log_guard_is_actually_reading_log_calls() -> None:
    """The other half: the lint is not passing because it found nothing to check.

    An assertion that no call in the tree does X is satisfied trivially by a
    tree the scan never reached — a renamed package, a changed receiver
    convention, an `EXCLUDED` prefix that grew. `sources()` carries its own
    vacuity guard; this one is about the detector rather than the file list, and
    it is the positive statement the negative one above needs.
    """
    scanned = sources()
    assert len(scanned) > 100, f"only {len(scanned)} modules scanned — did a package move?"
    calls = sum(
        len(list(log_calls(ast.parse(path.read_text(encoding="utf-8"))))) for path in scanned
    )
    assert calls > 50, (
        f"the lint found only {calls} log calls in the whole tree — the receiver or "
        "level vocabulary in scripts/lint_log_phi.py has drifted from how this "
        "codebase actually logs"
    )


@pytest.mark.parametrize(
    "smell",
    [
        'log.info("note.created", body=note.text)',
        'log.info("copilot.run", prompt=composed_message)',
        'log.warning("claim.assessed", diagnosis=claim.icd_desc)',
        'log.info("diary.saved", notes=note_text)',
        'log.info("copilot.streamed", content=chunk.content)',
        'log.info("claim.edited", claim_id=f"{claim.claim_id} ({claim.cause})")',
        'log.error("copilot.run_failed", error=str(exc))',
        'log.error("copilot.run_failed", error=repr(exc))',
        'log.info("claim.edited", reason="patched {}".format(claim.icd_desc))',
        'log.info("claim.edited", reason="patched %s" % claim.icd_desc)',
        'log.bind(body=note.text).info("note.created")',
        "structlog.contextvars.bind_contextvars(prompt=composed_message)",
        'log.error("x", **claim.__dict__)',
        'logger.debug("x", answer=result.text)',
    ],
)
def test_the_log_guard_would_notice_a_leaking_call(smell: str) -> None:
    """The mandatory positive control, one shape per line.

    Every one of these is something somebody writes while solving a real
    problem: five of the field names Story 8.2's task list names by name, an
    f-string that carries a claim's narrative behind a perfectly respectable
    `claim_id=` key, a `**` unpack in a file with no exemption, and the same
    hazard under the other receiver spelling.

    The f-string case is the one that justifies the second detector existing at
    all. Its *key* is on the allowlist, so a key-only check would pass it — and
    interpolation is precisely where a key stops predicting its content.

    **`error=str(exc)` is the same finding and is the likeliest of the set to
    be written.** `error` is allowlisted *because* the house spelling is
    `type(exc).__name__`, and a class name carries nothing; an exception's
    message routinely quotes the value the failing statement held, so a
    constraint violation on `diary_note` puts a handler's note in the log under
    a key nobody would look at twice. `repr`, `.format` and `%` are the same
    move in the three other spellings Python offers, and all four are refused
    under allowlisted keys for the reason the f-string is.

    **The two `bind` lines are the hole levels alone leave.** In the first, the
    `.info(…)` carries no keywords at all and its receiver is a `Call` rather
    than a name, so a level-only detector reads the whole statement as clean
    while it emits `body=<the note>`; in the second there is no logger in the
    expression to recognise. Neither shape is in the tree today, which is why
    they are here: the lint is the gate, and a gate is judged on what it would
    catch rather than on what it has caught.
    """
    assert offending_keywords(ast.parse(smell)) != [], smell


@pytest.mark.parametrize(
    "innocent",
    [
        'log.info("audit.recorded", claim_id=claim.claim_id, fields=sorted(after))',
        'log.info("app.start")',
        'log.error("x", error=type(exc).__name__)',
        'log.warning("retention.checkpoints_swept", errors=sorted(set(failures)))',
        'log.error("x", error=type(exc).__name__, exc_info=True)',
        'log.info("x", duration_ms=elapsed % 1000)',
        'log.bind(claim_id=claim.claim_id).info("claim.opened")',
        'body.error("the body could not be parsed", detail=raw)',
        'parser.error(f"unknown claim {business_id}")',
        "raw = str(payload)",
        '"""Never log.info(\'x\', body=note.text) — AD-11 forbids it."""',
        'BANNED = ("body", "prompt", "diagnosis")',
        'log.info(f"copilot.write_{outcome}", tool=tool_name)',
    ],
)
def test_the_log_guard_leaves_innocent_calls_alone(innocent: str) -> None:
    """The negative control, and it is the half that decides whether this survives.

    `tests/test_layering.py`'s first version was a grep and it failed on the
    prose explaining the rule it enforced. The hazard is sharper here than
    anywhere else in the suite, because *this* rule's subject is words: the
    lint's own module docstring contains `body=`, `prompt=` and `diagnosis=`;
    `services/claims/edit.py` has a docstring about putting claim data "in an
    error body"; `test_copilot_logging.py` argues about prompts for forty
    lines. A guard that fired on any of them would be switched off within a
    week, which is why the detector reads `ast.Call` and not text.

    `parser.error(…)` and `body.error(…)` are here for the second reason the
    receiver set is narrow: `scripts/purge_claim.py` calls `parser.error` with
    an interpolated business id, and a receiver-blind detector would report
    argparse as a PHI leak in the one module the purge story added.

    `errors=sorted(set(failures))` and `fields=sorted(after)` are the shapes the
    widened value check has to stay clear of: a call whose result is a list of
    names this codebase chose is not a stringification, and a detector that
    refused every call would refuse most of the honest log lines in the tree. So
    only `str`/`repr` called by name, `.format` and a `%` whose left operand is
    a string literal are findings — which is also why `duration_ms=elapsed %
    1000` is here, as arithmetic that must not be read as a format string.

    `exc_info=True` is `logging_config._META_KEYS`, not `LOG_KEY_ALLOWLIST`, and
    the distinction is the point: meta keys are the shape of a rendered line
    rather than permissions anybody was granted. A lint that knew only the
    allowlist would report this and its own remedy would say "add `exc_info` to
    the allowlist" — which `test_every_allowlisted_key_is_still_used_by_some_
    log_call` then fails on, because nothing passes it as a keyword this file
    can see.

    `log.bind(claim_id=…)` is the counterweight to the two bind smells above:
    binding is a legitimate way to put request context on every subsequent
    line, and the rule for a bound keyword is the rule for any other keyword —
    the key decides, not the mechanism.

    `raw = str(payload)` is a bare assignment: the stringification check applies
    to log keywords, and a guard that reported every `str()` in the tree would
    be reporting the tree.

    The last line is the deliberate carve-out: an event **name** may be
    computed. `agents/approval.py` builds `f"copilot.write_{outcome}"` from a
    closed set of outcome words and `services/audit/purge.py` passes an event
    variable. An event name is the subject of a line, not its content.
    """
    assert offending_keywords(ast.parse(innocent)) == [], innocent


def test_the_star_kwarg_exemption_is_what_makes_the_one_forwarder_pass() -> None:
    """The exemption does something, and only that.

    Two claims in one test because they are two halves of one property: the
    exempted file's `**` unpack really is refused by the default rule (so the
    entry is load-bearing rather than decorative), and it really is admitted
    under the exemption (so the map is wired to the detector).

    Without the first half a reviewer cannot tell an exemption that matters
    from one somebody added defensively; without the second, `STAR_KWARG_
    EXEMPTIONS` could be an unread constant while the lint quietly passed every
    `**` in the tree.
    """
    unpack = ast.parse("log.info(event, rows_deleted=run.rows_deleted, **subject)")
    assert offending_keywords(unpack) == [(1, "**")]
    assert offending_keywords(unpack, allow_star=True) == []


def test_every_star_kwarg_exemption_still_exists() -> None:
    """An allowlist entry for a deleted file is an allowlist entry nobody notices.

    `tests/test_purge_ownership.py::test_every_phi_delete_exemption_still_
    exists`' argument, and it applies with the same force: rename a module onto
    one of these paths and its `**` unpacks stop being checked, silently, on a
    rule whose whole subject is that claim data does not reach a log line.
    """
    for relative in STAR_KWARG_EXEMPTIONS:
        assert (SERVER_ROOT / relative).is_file(), (
            f"{relative} is exempted from the log-lint's `**` rule but does not exist"
        )


def test_every_star_kwarg_exemption_still_forwards_something() -> None:
    """…and the entry is not stale in the other direction either.

    A file that no longer unpacks anything into a log call does not need this
    exemption, and an exemption it does not need is a hole nobody is watching:
    the next `**` added to that module inherits an argument written about a
    different call. Checked rather than trusted, because `services/audit/purge
    .py` is a large module that changes for reasons unrelated to this rule.
    """
    for relative in STAR_KWARG_EXEMPTIONS:
        tree = ast.parse((SERVER_ROOT / relative).read_text(encoding="utf-8"))
        assert any(keyword.arg is None for call in log_calls(tree) for keyword in call.keywords), (
            f"{relative} no longer unpacks `**` into a log call; drop its exemption"
        )


def _keywords_in_use() -> set[str]:
    """Every keyword name the tree actually passes to a log call.

    Two sources, because one of them is invisible to the AST at the call site.
    The literal keywords are read straight off the log calls. The second source
    is the `**` forwarders `STAR_KWARG_EXEMPTIONS` admits: `services/audit/
    purge.py::_log_run(event, run, **subject)` reaches the pipeline with
    whatever its *callers* named, and `user_id` is passed at exactly one such
    call site and nowhere else in the build.

    A forwarder is recognised structurally rather than by name — a function
    whose body unpacks its own `**` parameter into a log call — and its call
    sites are then read for their keywords. The alternative was to accept every
    keyword in an exempted file as evidence of use, which would have made this
    test blind inside the one module where a dead permission is hardest to
    spot.
    """
    used: set[str] = set()
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in log_calls(tree):
            used.update(keyword.arg for keyword in call.keywords if keyword.arg)
        forwarders = _log_forwarders(tree)
        if not forwarders:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forwarders
            ):
                used.update(keyword.arg for keyword in node.keywords if keyword.arg)
    return used


def _log_forwarders(tree: ast.AST) -> set[str]:
    """Names of functions that unpack their own `**` parameter into a log call."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        kwargs = node.args.kwarg
        if kwargs is None:
            continue
        for call in log_calls(node):
            for keyword in call.keywords:
                if (
                    keyword.arg is None
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == kwargs.arg
                ):
                    found.add(node.name)
    return found


def test_every_allowlisted_key_is_still_used_by_some_log_call() -> None:
    """A dead entry is a permission nobody is exercising.

    The allowlist is a list of things the build is *entitled* to log, and an
    entitlement that nothing uses is worse than an absent one: it survives
    review because removing it looks like a change and keeping it looks like
    none, and the next person adding a keyword reads it as precedent for the
    neighbouring name that is not safe. `payment_amount` is not on this list;
    the day `rows_paid` stops being emitted, the shortest path to a green build
    for whoever needs the next money-shaped field is the entry sitting there
    unused.

    So the list is held to the tree in both directions — the test above says
    every emitted keyword is allowlisted, and this one says every allowlisted
    keyword is emitted. Together they make `LOG_KEY_ALLOWLIST` a description of
    what this build logs rather than a wish about it.
    """
    dead = sorted(LOG_KEY_ALLOWLIST - _keywords_in_use())
    assert dead == [], (
        f"these names are on LOG_KEY_ALLOWLIST and nothing logs them: {dead}. "
        "Remove them — an unused permission is precedent for the next keyword "
        "somebody wants, and this list is the argument that stops it."
    )


def test_the_lint_is_wired_into_the_same_scan_roots_as_the_rest_of_the_suite() -> None:
    """The lint and the delete guard must not disagree about where code lives.

    `tests/test_purge_ownership.py` restates its scan roots rather than sharing
    them, deliberately — two guards sharing one detector would be two tests
    agreeing with one implementation. Restated is not the same as allowed to
    drift, though: a package that one of them scans and the other does not is a
    tree where PHI deletion is checked and PHI logging is not, which nothing
    would report. So the two lists are compared here, in one direction, by the
    file that is not either implementation.
    """
    from scripts.lint_log_phi import EXCLUDED, SCAN_ROOTS
    from tests import test_purge_ownership as ownership

    assert set(SCAN_ROOTS) == set(ownership.SCAN_ROOTS)
    assert set(EXCLUDED) == set(ownership.EXCLUDED)


def test_the_lint_module_is_where_ci_expects_it() -> None:
    """`uv run python -m scripts.lint_log_phi`, as the `server` job spells it.

    A module path in a workflow file is not type-checked, not imported by
    anything and not exercised by any other test — so it is the one part of
    this story a rename would break silently, in CI, in a job whose failure
    reads as "the lint found something".
    """
    assert (Path(SERVER_ROOT) / "scripts" / "lint_log_phi.py").is_file()
