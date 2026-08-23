"""The copilot stream's vocabulary, spelled on both sides and asserted nowhere until now.

NFR-7 asks for "one integration test that drives the real SSE + assistant-ui
stream protocol end to end", and Story 8.4's audit found both halves of it
already built. `tests/test_copilot_threads.py` drives the actual endpoint and
asserts `text/event-stream`, the frame sequence, exactly one `done` and exactly
one `error`; `web/src/api/copilot.test.ts` drives the client parser through
truncation, cancellation and a final frame with no trailing newline. Neither is
missing, and writing a third genuinely cross-process test would be re-doing
Story 6.5's scope.

**What is missing is the join.** The event names are literals in
`api/routers/copilot.py` and re-spelled as `SERVER_EVENTS` in
`web/src/api/copilot.ts`, whose own comment says it out loud — "a string spelled
in two places is a string that stops matching the day one of them changes" — and
nothing checked. Rename `updates` on the server and both suites stay green: the
server test asserts the sequence it was told to expect, the client test parses
frames a fixture wrote, and the failure appears in a browser as a copilot that
streams nothing and reports no error. So this file closes the actual hole, which
is a set comparison, rather than the hole NFR-7's sentence describes.

## Three seams, not one

1. **The frame vocabulary.** Every name the server can put in an `event:` line
   against every name the client knows.
2. **The terminal set.** The server decides terminality in `_terminal_for`; the
   client decides it again in `TERMINAL_EVENTS`, and `copilot.ts` explains at
   length that it must read terminality from the *server's* name because an
   interrupt is delivered to the runtime under a different one. Two independent
   spellings of "which frames end a run" — and a run whose terminal frame the
   client does not recognise is reported to the handler as `TRUNCATED_STREAM`
   on top of a perfectly good answer.
3. **`MESSAGES_FRAME_PREFIX`.** A third spelling of `messages`, on the server,
   in bytes, used by `_stream` to decide whether the run said anything — which
   is what makes `done` mean "your question was answered". Rename the frame and
   this constant stops matching, every run reports as having answered nothing,
   and every one of them terminates in `error`.

## Read, not imported

`api/routers/copilot.py` has no exported constant naming its frames — they are
literals at four `_sse(…)` call sites and in `_terminal_for`'s returns — so
there is nothing to import, and adding one would be production code written to
make a test convenient. The names are read from the module's **AST** instead,
for `scripts/lint_log_phi.py`'s reason applied to its sharpest case: this file's
subject is words, `copilot.py` argues about `done`, `error` and `interrupt` for
paragraphs at a time, and a grep would find the prose. The TypeScript side has
no AST available here and is matched textually with an anchored, whole-block
pattern that **fails loudly when it matches nothing** — because two empty sets
are equal, and a comparison of two empty sets is the vacuity AD-15 names.
"""

import ast
import re
from pathlib import Path
from typing import Final

SERVER_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
ROUTER: Final[Path] = SERVER_ROOT / "api" / "routers" / "copilot.py"
CLIENT: Final[Path] = SERVER_ROOT.parent / "web" / "src" / "api" / "copilot.ts"

#: The server helper that writes an `event:` line. Every frame the API can emit
#: goes through it — its own docstring says it is "the only place this wire
#: format is spelled" — so its first argument is the server's vocabulary.
_SSE = "_sse"

#: The server function that decides which single frame ends a run. Its returns
#: are the other half of the vocabulary: `yield _sse(*terminal)` passes them
#: through a starred argument, which no reader of the call site can see.
_TERMINAL_FN = "_terminal_for"

#: `SERVER_EVENTS` in `web/src/api/copilot.ts`, matched as a whole block.
#:
#: Anchored at both ends (`^const …` / `^} as const;$`) and constrained line by
#: line, rather than a loose search for quoted words: `copilot.ts` is a large
#: module full of string literals, and a pattern that scooped up any `"done"` in
#: it would compare the wire vocabulary against whatever else that file says.
_CLIENT_EVENTS: Final[re.Pattern[str]] = re.compile(
    r"^const SERVER_EVENTS = \{\n(?P<body>(?:  \w+: \"[a-z_]+\",\n)+)\} as const;$", re.MULTILINE
)

#: `TERMINAL_EVENTS`, the client's independent statement of which frames end a run.
_CLIENT_TERMINAL: Final[re.Pattern[str]] = re.compile(
    r"^const TERMINAL_EVENTS[^=\n]*= new Set\(\[\n"
    r"(?P<body>(?:  SERVER_EVENTS\.\w+,\n)+)\]\);$",
    re.MULTILINE,
)

_MEMBER: Final[re.Pattern[str]] = re.compile(r'^ {2}(?P<key>\w+): "(?P<value>[a-z_]+)",$', re.M)
_REFERENCE: Final[re.Pattern[str]] = re.compile(r"^ {2}SERVER_EVENTS\.(?P<key>\w+),$", re.M)


def _router_tree() -> ast.Module:
    return ast.parse(ROUTER.read_text(encoding="utf-8"))


def server_frames() -> set[str]:
    """Every `event:` name `api/routers/copilot.py` can put on the wire.

    Two sources, and both are needed because the second is invisible at its call
    site. The `_sse("messages", …)` / `_sse("updates", …)` literals are the
    content frames; the terminal frame reaches `_sse` as `*terminal`, a tuple
    `_terminal_for` built, so a detector that read only the call sites would
    conclude this API emits two frame types and never ends a run.
    """
    return _content_frames() | _terminal_frames()


def _content_frames() -> set[str]:
    """String literals passed as `_sse`'s first argument."""
    found: set[str] = set()
    for node in ast.walk(_router_tree()):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == _SSE):
            continue
        if not node.args:
            continue
        first = node.args[0]
        # `_sse(*terminal)` — a starred argument, deliberately skipped: its names
        # are `_terminal_frames`' business and guessing at them here would be a
        # second, weaker copy of that function.
        if isinstance(first, ast.Starred):
            continue
        # Anything else non-literal is a frame this scan cannot see, and a frame
        # it cannot see drops silently out of the server set — at which point the
        # comparison passes for a vocabulary that no longer matches. Review's
        # point: the failure mode of an extractor is under-collection, so
        # under-collection has to be the loud case.
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            f"a {_SSE}() call site names its frame with a non-literal "
            f"({type(first).__name__}); this contract test reads frame names "
            "statically and cannot follow it. Spell the frame as a string literal, "
            "or teach this extractor the new shape."
        )
        found.add(first.value)
    return found


def _terminal_frames() -> set[str]:
    """The first element of every tuple `_terminal_for` returns."""
    found: set[str] = set()
    for node in ast.walk(_router_tree()):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != _TERMINAL_FN:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return):
                continue
            if not isinstance(inner.value, ast.Tuple) or not inner.value.elts:
                continue
            first = inner.value.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                f"{_TERMINAL_FN} returns a frame name that is not a string literal "
                f"({type(first).__name__}); the same under-collection hazard as "
                f"{_SSE} above."
            )
            found.add(first.value)
    return found


def _client_text() -> str:
    text = CLIENT.read_text(encoding="utf-8")
    assert text.strip(), f"{CLIENT} is empty — this test reads the web tree, it does not own it"
    return text


def client_frames() -> dict[str, str]:
    """`SERVER_EVENTS`, as the SPA spells it: TypeScript handle → wire name.

    Asserted to have matched. A textual matcher that finds nothing returns an
    empty set, an empty set equals an empty set, and this whole module then
    passes while the two halves of the protocol drift apart — which is the
    failure the story's own vacuity rule names and applies to its additions
    first. So the failure here is loud and says which file to look at.
    """
    block = _CLIENT_EVENTS.search(_client_text())
    assert block is not None, (
        f"the SERVER_EVENTS block in {CLIENT} no longer matches the shape this test "
        "reads. Fix the pattern rather than deleting the test: with no match there is "
        "nothing to compare the server's vocabulary against, and this file would pass "
        "by checking nothing."
    )
    return {m["key"]: m["value"] for m in _MEMBER.finditer(block["body"])}


def client_terminal_frames() -> set[str]:
    """`TERMINAL_EVENTS`, resolved through `SERVER_EVENTS` to wire names."""
    block = _CLIENT_TERMINAL.search(_client_text())
    assert block is not None, (
        f"the TERMINAL_EVENTS block in {CLIENT} no longer matches the shape this test "
        "reads — see client_frames() for why an unmatched pattern is a failure."
    )
    events = client_frames()
    return {events[m["key"]] for m in _REFERENCE.finditer(block["body"])}


def test_both_halves_of_the_protocol_are_actually_being_read() -> None:
    """The vacuity guard, first, because everything below is a set comparison.

    Rename `_sse`, move `copilot.ts`, or reshape either literal and the parsers
    here go quiet — and two empty sets are equal. Story 8.3's review found this
    exact shape in a posture test of this suite (`after.find("\\n  ")` matching
    at index 0), which is why the counts are floors named in this story's own
    spec rather than something a reader is trusted to notice.
    """
    assert len(_content_frames()) >= 2, (
        f"only {_content_frames()} read out of {ROUTER} — the `_sse` call sites moved"
    )
    assert len(_terminal_frames()) >= 3, (
        f"only {_terminal_frames()} read out of {_TERMINAL_FN} — the terminal branches moved"
    )
    assert len(client_frames()) >= 5, f"only {client_frames()} read out of {CLIENT}"


def test_the_server_and_the_client_spell_the_same_frame_vocabulary() -> None:
    """The seam. One set, spelled in two languages, in two repositories' worth of tree.

    Neither existing suite catches a rename. `tests/test_copilot_threads.py`
    asserts the sequence the server produces against names written in the same
    story; `web/src/api/copilot.test.ts` parses frames a fixture in that file
    wrote. Both stay green while the browser receives frames it silently drops —
    a copilot that streams nothing and reports no error, because an unknown
    event name is not an error to either half.
    """
    server = server_frames()
    client = set(client_frames().values())
    assert server == client, (
        f"the copilot stream vocabulary has drifted. api/routers/copilot.py emits "
        f"{sorted(server)}; web/src/api/copilot.ts knows {sorted(client)}. "
        f"Server-only: {sorted(server - client)}. Client-only: {sorted(client - server)}. "
        "A frame the client does not know is dropped without an error."
    )


def test_the_two_sides_agree_about_which_frames_end_a_run() -> None:
    """Terminality is decided twice, independently, and the disagreement is silent.

    `_terminal_for` picks exactly one of `done`/`interrupt`/`error`;
    `TERMINAL_EVENTS` decides again on the client, and `copilot.ts` explains why
    it must read the *server's* name rather than the translated one — an
    interrupt reaches the runtime as an `updates` frame. If the client's set
    ever lost a member, a run ending in that frame would look to the SPA like a
    stream that simply stopped, and it would synthesise `TRUNCATED_STREAM` on
    top of a completed answer. The handler sees a failure notice under a correct
    reply, and nothing in either suite fails.
    """
    server = _terminal_frames()
    client = client_terminal_frames()
    assert server == client, (
        f"_terminal_for can end a run with {sorted(server)}; copilot.ts treats "
        f"{sorted(client)} as terminal. A terminal frame the client does not "
        "recognise is reported to the handler as a truncated stream."
    )


def test_the_content_frame_prefix_still_names_a_frame_the_server_emits() -> None:
    """The third spelling, and the one whose failure is furthest from its cause.

    `MESSAGES_FRAME_PREFIX = b"event: messages"` is how `_stream` decides whether
    a run said anything, and `_terminal_for` turns that answer into `done` versus
    `error`. Rename the content frame and this constant matches nothing: every
    run reports as having answered nothing, every run ends in `error`, and the
    only clue is a `copilot.run_answered_nothing` log line on a system where the
    model is working perfectly.

    Read from the AST rather than imported, like the rest of this file — the
    module pulls in the whole agent stack at import time, and this test has no
    business needing it.
    """
    # `ast.AnnAssign` as well as `ast.Assign`: this repository's prevailing style
    # for a module constant is `NAME: Final[bytes] = b"…"`, and reading only the
    # bare form would fail claiming the constant had been deleted the day somebody
    # annotated it to match every neighbour.
    assignments: dict[str, bytes] = {}
    for node in ast.walk(_router_tree()):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, bytes):
            for target in targets:
                assignments[target.id] = value.value
    prefix = assignments.get("MESSAGES_FRAME_PREFIX")
    assert prefix is not None, (
        "MESSAGES_FRAME_PREFIX is no longer a module-level bytes literal in "
        f"{ROUTER} — this test cannot read it, and an unread guard is no guard."
    )
    assert prefix.startswith(b"event: "), prefix
    named = prefix.removeprefix(b"event: ").decode()
    assert named in server_frames(), (
        f"MESSAGES_FRAME_PREFIX names frame {named!r}, which this API no longer "
        f"emits (it emits {sorted(server_frames())}). `_stream` would then decide "
        "that no run ever answers, and every run would terminate in `error`."
    )
