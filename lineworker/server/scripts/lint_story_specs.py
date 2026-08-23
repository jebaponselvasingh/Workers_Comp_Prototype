"""AD-15's sprint-status ↔ e2e-spec bijection, as a build step rather than a habit.

    uv run python -m scripts.lint_story_specs [sprint-status.yaml [e2e/stories]]

AD-15 says two things about the story gate, and only one of them has ever been
mechanical. The PR stage runs "the declared story spec(s) plus `@smoke`" and the
merge stage runs the full suite — that part is `ci.yaml`'s `e2e` job and it
works. The other part is the claim that makes the first one worth anything:
**every story that is not backlog has exactly one spec, and every spec belongs
to exactly one such story.** Until this file, that was prose in the spine and a
convention forty specs happened to keep.

## What a broken bijection looks like from inside CI

It looks like a green build. `ci.yaml`'s PR stage derives story keys from the
branch name and the PR labels, and it only counts a key `ls stories/"$k"-*
.spec.ts` can resolve — so a story whose spec was never written, or whose spec
was renamed, contributes **no** grep term. The job then runs the `@smoke` set,
passes, and reports that the PR's story was gated. Nothing anywhere says the
spec that was supposed to prove the story is missing. That is precisely the
"gate passing vacuously" AD-15 names, and it is invisible in a diff because the
absence of a file is not a line anybody reviews.

The other direction is quieter still. A spec file for a story key nobody
tracks — a renamed story, a split epic, a copy-paste that kept its neighbour's
name — runs on the merge stage forever, is attributed to a story that does not
exist, and shows up in no report.

## Anchored, in both directions, because `1-3` is a prefix of `1-30`

This is the trap AD-15 calls out by name, and it has two halves that are easy to
fix separately and get wrong together:

* the **tag check** — `grep "@story:1-3"` matches the string `@story:1-30`, so a
  spec tagged for the wrong story passes.

An earlier draft of this docstring claimed a second half — that a glob of
`1-3-*.spec.ts` matches `1-30-something.spec.ts`. **It does not**, and review
disproved it by running it: `1-3-*` requires a literal `-` as the fourth
character and `1-30-…` has a `0` there. The correction is recorded rather than
quietly deleted, because a module whose subject is documentation asserting
untrue things should not open with one. The filename half is compared by
equality anyway, for the reason below.

Neither the tag nor the filename is fixed by adding a `\\b` to a pattern and
hoping. The key is
*captured* out of the filename (`^(\\d+-\\d+)-`) and out of the tag
(`@story:(\\d+-\\d+)\\b`) and then compared for **equality**, so a prefix
relationship cannot be mistaken for a match by construction. `tests/
test_story_spec_lint.py` drives the `1-3`/`1-30` pair from both directions,
which is the only way to tell a genuinely anchored matcher from one that has
simply never met the case.

## Refusing an empty input, and why that is the first rule rather than the last

A lint over a set that vanished exits 0 and reads exactly like a clean tree in
every output anybody looks at — the argument `scripts/lint_log_phi.py::sources`
makes about a renamed package, arriving here twice over. This file reads two
paths that live **outside** `server/`: move `e2e/stories`, rename
`sprint-status.yaml`, or reorganise `_bmad-output/` and this becomes a loop over
nothing. So an empty key set and an empty spec set are each findings in their
own right, reported before anything else, rather than the happy path.

## Importable detectors, because the test is the other half

`story_keys`, `spec_files`, `story_tags` and `problems` take text and paths and
are imported by `tests/test_story_spec_lint.py`, which drives them over
hand-written fixtures in `tmp_path` *and* over the real repository. `main()` is
the thin CLI on top that the `server` CI job runs beside ruff, mypy and the PHI
lint. The two paths default from **this module's own location** and are
overridable by argv precisely so the fixtures are drivable — a lint that
resolved its inputs from the cwd would be a lint whose result depended on which
directory CI happened to be standing in.

## No YAML parser, deliberately

`tests/test_deploy_tls_posture.py` states the rule for this repository: a YAML
parser is a dependency this suite does not declare, and nothing here is worth
adding one for. `development_status:` is a flat block of `key: value` lines with
full-line comments, which is a shape a dozen lines of `re` read correctly and
which fails loudly rather than subtly when it changes — the block header stops
matching and the key set goes empty, which is the one failure this file is
guaranteed to report.
"""

import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final

SERVER_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The repository root, three levels up from `server/scripts/`. Resolved from
#: `__file__` rather than from the cwd: this module is run as `python -m
#: scripts.lint_story_specs` from `server/`, imported by pytest from `server/`,
#: and read by a developer from anywhere, and only one of those three has a
#: predictable working directory.
REPO_ROOT: Final[Path] = SERVER_ROOT.parents[1]

#: The sprint tracker. Read-only, always: the spine's Project Structure Notes
#: say in as many words that CI never mutates sprint status, and this is the one
#: automation that has a reason to open the file at all.
SPRINT_STATUS: Final[Path] = (
    REPO_ROOT / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
)

#: The Playwright story specs — the other half of the bijection.
STORY_SPEC_DIR: Final[Path] = SERVER_ROOT.parent / "e2e" / "stories"

#: The one status that legitimately has no spec. A backlog story "only exists in
#: the epic file" (sprint-status.yaml's own status definitions), so requiring a
#: spec for it would make the lint fail on every story nobody has started —
#: which is a lint that gets deleted rather than a gate that holds.
BACKLOG: Final[str] = "backlog"

#: One `  key: value` line inside the `development_status:` block.
_ENTRY: Final[re.Pattern[str]] = re.compile(r"^\s+(?P<key>[A-Za-z0-9_.-]+):\s*(?P<value>\S+)\s*$")

#: A story entry's key, and the capture is the whole anchoring story.
#:
#: `epic-1` and `epic-1-retrospective` are tracked in the same block and are not
#: stories; they fail this pattern and are skipped. `8-4-complete-quality-
#: operations-gate` yields `8-4`, which is the key `ci.yaml` derives from a
#: branch name and the key a spec's `@story:` tag spells.
_STORY_ENTRY: Final[re.Pattern[str]] = re.compile(r"^(?P<key>\d+-\d+)-[a-z0-9]+[a-z0-9-]*$")

#: The keys in this block that are legitimately not stories. Everything else that
#: is not a story is reported rather than skipped — see `_entries`.
_NOT_A_STORY: Final[re.Pattern[str]] = re.compile(r"^epic-\d+(-retrospective)?$")

#: A spec filename's leading story key. Greedy `\d+` is what makes it anchored:
#: `1-30-x.spec.ts` captures `1-30`, never `1-3`, so the two stories cannot
#: resolve to one another's file however similar their numbers look.
_SPEC_NAME: Final[re.Pattern[str]] = re.compile(r"^(?P<key>\d+-\d+)-")

#: A `test.describe("…"` title — the only place AD-15 puts the tag, and now the
#: only place this file looks for it.
#:
#: Scanning the whole file was wrong in both directions, which review found and
#: `test_story_spec_lint.py` now pins. A spec whose *comment* mentions another
#: story ("the 7-3 spec covers the rest") failed the lint with no defect; and a
#: describe that *lost* its tag while a header comment still named the story
#: passed, which is the failure that matters — the PR stage greps
#: `@story:<key>\b`, selects nothing, and the job goes green having tested none
#: of it. `lint_log_phi`'s argument applies here too: a file that argues about a
#: rule names the thing the rule is about while doing so.
_DESCRIBE_TITLE: Final[re.Pattern[str]] = re.compile(r'test\.describe(?:\.\w+)*\(\s*"([^"]*)"')

#: The tag a describe block carries. `\b` bounds the key so `@story:1-30` is not
#: read as `@story:1-3` with a stray `0`; the captured key is then compared for
#: equality with the filename's, which is the check that actually holds.
_STORY_TAG: Final[re.Pattern[str]] = re.compile(r"@story:(\d+-\d+)\b")

#: What a failure tells the reader to do, printed once under the findings —
#: `scripts/lint_log_phi.py::REMEDY`'s convention, so the fix is in the same
#: output as the problem.
REMEDY: Final[str] = (
    "AD-15: every non-backlog story in sprint-status.yaml has exactly one "
    "e2e/stories/<story-key>.spec.ts named for the key exactly, and every spec "
    "belongs to exactly one such story.\n"
    "A missing spec is a story whose gate never ran: `ci.yaml`'s PR stage only "
    "counts a story key whose spec file exists, so a story with no spec silently "
    "contributes no grep term and the job passes having tested nothing about it.\n"
    "A spec with no story key runs on the merge stage forever, attributed to "
    "nothing.\n"
    "Fix it by writing the spec, or by amending sprint-status.yaml — never by "
    "deleting a spec. AD-15: specs are amended, never deleted."
)


def _entries(
    text: str, unclassified: list[str] | None = None
) -> Iterator[tuple[re.Match[str], re.Match[str]]]:
    """Every story line in the `development_status:` block, matched twice.

    One scan behind both public readers, because two copies of this loop is two
    places for the block boundary to be read differently — and a reader that
    disagreed about where the block ends would silently return a subset, which
    is the empty-input failure in miniature.

    **`unclassified` collects the lines this scan could not read as either a
    story or a known non-story, and review is why it exists.** Skipping them was
    the lint's own version of the failure it exists to catch: `8-5-Complete-Gate`
    (an uppercase letter), `8-5-complete_gate` (an underscore), `8-5` (no slug)
    and `8-5-gate: 'ready for dev'` (a quoted value) each fail one of the two
    patterns, and every one of them was **dropped in silence** — so a tracked
    story with no spec produced `ok: N non-backlog story keys, each with exactly
    one spec`. The empty-key-set guard did not help, because it only fires when
    *every* key is unreadable. A lint whose entire subject is an absence must
    not have a class of absence it cannot see, so an unreadable entry is now a
    finding rather than a silence.
    """
    if unclassified is None:
        unclassified = []
    inside = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line[0].isspace():
            # A top-level key ends the block as surely as it starts it.
            inside = line.strip() == "development_status:"
            continue
        if not inside:
            continue
        entry = _ENTRY.match(line)
        if entry is None:
            unclassified.append(line.strip())
            continue
        story = _STORY_ENTRY.match(entry["key"])
        if story is not None:
            yield entry, story
        elif not _NOT_A_STORY.match(entry["key"]):
            unclassified.append(entry["key"])


def story_slugs(text: str) -> dict[str, str]:
    """Story key → the full `sprint-status.yaml` entry it came from.

    `8-4` → `8-4-complete-quality-operations-gate`. AD-15 does not say a spec
    starts with the story key; it says the spec **is** `e2e/stories/<story-key>
    .spec.ts` for the sprint-status story key, and gives the reason in the same
    clause — "renaming a story renames its spec in the same change". A lint that
    checks only the numeric prefix cannot see that rename: `8-4-complete-quality-
    operations-gate` becomes `8-4-ci-gate` in the tracker, the file keeps its old
    name for ever, and every human reading `e2e/stories/` is told the story is
    still called something it is not. The numeric key stays the unit everything
    else works in — it is what a branch name yields and what a tag spells — so
    this is a second map rather than a change to the first.
    """
    slugs: dict[str, str] = {}
    for entry, story in _entries(text):
        slugs[story["key"]] = entry["key"]
    return slugs


def unreadable_entries(text: str) -> list[str]:
    """Every `development_status:` line that is neither a story nor a known non-story.

    Exported so `problems` can report them and `tests/test_story_spec_lint.py`
    can drive each shape. See `_entries` for why silence here was the bug.
    """
    unclassified: list[str] = []
    for _ in _entries(text, unclassified):
        pass
    return unclassified


def story_keys(text: str) -> dict[str, str]:
    """Story key → status, read out of `sprint-status.yaml`'s `development_status:` block.

    Hand-parsed rather than loaded, for the reason the module docstring gives
    and `tests/test_deploy_tls_posture.py` states first: no YAML parser is a
    declared dependency of this project, and a flat `key: value` block under a
    known header does not need one.

    Two shapes are deliberately skipped rather than reported. `epic-1` and
    `epic-1-retrospective` are tracked in the same block and are not stories —
    they carry no spec by definition — and a full-line `# Epic 1: …` comment is
    the file's own section marker. Everything else that looks like a story entry
    is a story entry: the pattern is anchored end to end, so a key this file
    does not recognise is a key it says nothing about, which is why the empty
    result is itself a finding in `problems`.

    A duplicate numeric key **raises**. Two entries sharing `1-3` would silently
    collapse into one here and the story that lost is a story nothing checks —
    the same shape as the empty scan, and the same treatment. It raises rather
    than asserting because this runs as `python -m …` in CI, where
    `PYTHONOPTIMIZE` would compile an `assert` out (`lint_log_phi.sources`'
    argument, restated because it applies to every guard in a `scripts/` module).
    """
    keys: dict[str, str] = {}
    for entry, story in _entries(text):
        key = story["key"]
        if key in keys:
            raise RuntimeError(
                f"sprint-status.yaml tracks two stories under the key {key!r}; one of them "
                "can never resolve to a spec and nothing else would say so."
            )
        keys[key] = entry["value"]
    return keys


def spec_files(directory: Path) -> dict[str, list[Path]]:
    """Story key → every spec file claiming it.

    **A list rather than a single path, and that is not defensive.** A
    `dict[str, Path]` cannot express two files claiming `8-4`: the second
    overwrites the first, the bijection reports itself intact, and one of the
    two specs is attributed to nothing. That is the vacuity shape this whole
    file exists to refuse, so it is expressible here and reported by `problems`.

    A file whose name does not start with a story key is filed under its own
    stem — `helpers.spec.ts` → `helpers` — which no story key can equal, so it
    lands in the orphan-spec branch and is named there. Everything in this
    directory is a story spec by construction (`playwright.config.ts`'s
    `stories` project has no other members), so a file that is not one is a
    finding rather than a file to skip.
    """
    found: dict[str, list[Path]] = {}
    # `rglob`, not `glob`: `playwright.config.ts`'s `stories` project matches
    # `/stories\/.*\.spec\.ts/`, and `.*` crosses a `/`. A spec one directory
    # down runs on the merge stage for ever and would be invisible to a
    # non-recursive scan — the orphan-spec half of the bijection, quietly.
    for path in sorted(directory.rglob("*.spec.ts")):
        match = _SPEC_NAME.match(path.name)
        key = match["key"] if match else path.name.removesuffix(".spec.ts")
        found.setdefault(key, []).append(path)
    return found


def story_tags(text: str) -> set[str]:
    """Every story key the `@story:` tags in one spec file name.

    **A set, where the spec sketched `story_tag(text) -> str | None`.** The
    difference is `6-6-honest-degradation.spec.ts`, which has two describe
    blocks and tags both. A detector that returned the first tag would read a
    file whose second describe says `@story:7-1` as belonging entirely to `6-6`
    — a spec running under another story's name, which is one of the two
    failures this lint exists to catch, masked by the one place it is most
    likely to happen. `problems` asserts the set equals `{key}`, so a file with
    one right tag and one wrong tag fails and the message names both.

    Bounded by `\\b` and *captured*: the key that comes back is compared for
    equality against the filename's, so `@story:1-30` can never satisfy a spec
    named for `1-3` no matter how the patterns are later edited.
    """
    return {key for title in _DESCRIBE_TITLE.findall(text) for key in _STORY_TAG.findall(title)}


def problems(
    keys: Mapping[str, str],
    specs: Mapping[str, list[Path]],
    slugs: Mapping[str, str],
    unreadable: Sequence[str] = (),
) -> list[str]:
    """Every way this pair of inputs breaks the bijection, already worded for `::error::`.

    Four findings, in the order a reader can act on them:

    1. **an empty input on either side** — reported first and on its own terms,
       because everything below it would then pass by having nothing to check;
    2. a non-backlog story key with no spec, or with more than one;
    3. a spec file whose key is not a story, or is a `backlog` story;
    4. a spec whose `@story:` tags do not agree with its own filename;
    5. a spec whose filename is not the story's full sprint-status key.

    (4) is the one that looks redundant and is not. (2) and (3) are satisfied by
    a file *named* correctly, and the cheapest way to start a new spec is to
    copy the neighbouring one — which brings its describe title, its tag and its
    story attribution along. The filename says `8-4`, the tag says `8-3`, the PR
    stage greps for `@story:8-4\\b`, matches nothing, and runs the smoke set.
    """
    found: list[str] = []

    for entry in unreadable:
        found.append(
            f"sprint-status.yaml line {entry!r} is neither a story key nor an epic/"
            "retrospective row, so nothing checks whether it has a spec. A story this "
            "scan cannot read is a story the gate cannot see — fix the entry, or teach "
            "this lint the shape."
        )

    if not keys:
        found.append(
            "refusing to pass on an empty key set: no story entries parsed out of "
            "sprint-status.yaml's `development_status:` block. A lint over nothing "
            "exits 0 and reads exactly like a clean tree."
        )
    if not specs:
        found.append(
            "refusing to pass on an empty spec set: no *.spec.ts under the story "
            "directory. Either the e2e suite moved or this lint is pointed at the "
            "wrong path — both exit 0 without this."
        )
    if not keys or not specs:
        # The per-key comparisons below would report every key as an orphan (or
        # nothing at all), burying the one finding that explains the run.
        return found

    for key in sorted(k for k, status in keys.items() if status != BACKLOG):
        paths = specs.get(key, [])
        if not paths:
            found.append(
                f"story {key} is {keys[key]!r} in sprint-status.yaml and has no spec — "
                f"expected e2e/stories/{slugs.get(key, key)}.spec.ts"
            )
        elif len(paths) > 1:
            names = ", ".join(path.name for path in paths)
            found.append(
                f"story {key} is claimed by {len(paths)} spec files ({names}); the PR "
                "stage greps one tag and cannot say which of them is the story's gate"
            )

    for key in sorted(specs):
        for path in specs[key]:
            if key not in keys:
                found.append(
                    f"{path.name} names story key {key!r}, which sprint-status.yaml does "
                    "not track — a spec attributed to no story runs on the merge stage "
                    "forever and appears in no report"
                )
            elif keys[key] == BACKLOG:
                found.append(
                    f"{path.name} names story key {key!r}, which is {BACKLOG!r} — a "
                    "backlog story has no spec by definition, so either the status is "
                    "stale or the spec is somebody else's"
                )

    for key in sorted(specs):
        for path in specs[key]:
            expected = slugs.get(key)
            if expected is not None and path.name != f"{expected}.spec.ts":
                found.append(
                    f"{path.name} is the gate for story {key}, whose sprint-status key is "
                    f"{expected!r} — AD-15 names the spec for the key exactly "
                    f"({expected}.spec.ts), so that renaming a story renames its spec in "
                    "the same change instead of leaving the old name in e2e/stories/ for "
                    "ever"
                )

    for key in sorted(specs):
        for path in specs[key]:
            try:
                tags = story_tags(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                # `main`'s docstring promises a finding rather than a traceback:
                # this runs in a CI step whose failure line is read at a glance.
                found.append(f"{path.name} could not be read: {type(exc).__name__}")
                continue
            if not tags:
                found.append(
                    f"{path.name} carries no @story: tag, so the PR stage can never "
                    "select it and the merge stage cannot attribute it"
                )
            elif tags != {key}:
                named = ", ".join(sorted(tags))
                found.append(
                    f"{path.name} is named for story {key} and its describe blocks say "
                    f"@story:{named} — the filename and the tag are one claim, and a "
                    "copied spec is how they come apart"
                )

    return found


def main(argv: Sequence[str] = ()) -> int:
    """Print the findings and the remedy; 0 when the bijection holds.

    Two optional positional arguments — the sprint-status file and the story
    directory — defaulting to the paths this module resolves from its own
    location. The arguments exist for `tests/test_story_spec_lint.py`, which
    drives fixtures in `tmp_path`; CI passes none and gets the repository.

    A missing input is reported rather than raised. This runs in a CI step whose
    failure line is read at a glance, and "no such file" as a traceback reads as
    a crashing script rather than as a gate that could not find what it guards.
    """
    args = list(argv)
    status_path = Path(args[0]) if args else SPRINT_STATUS
    spec_dir = Path(args[1]) if len(args) > 1 else STORY_SPEC_DIR

    missing = [
        f"{path} does not exist — {what} is this lint's input"
        for path, exists, what in (
            (status_path, status_path.is_file(), "the story tracker"),
            (spec_dir, spec_dir.is_dir(), "the story-spec directory"),
        )
        if not exists
    ]
    if missing:
        found, gated = missing, 0
    else:
        status_text = status_path.read_text(encoding="utf-8")
        keys = story_keys(status_text)
        found = problems(
            keys,
            spec_files(spec_dir),
            story_slugs(status_text),
            unreadable_entries(status_text),
        )
        gated = sum(1 for status in keys.values() if status != BACKLOG)

    if not found:
        print(f"ok: {gated} non-backlog story keys, each with exactly one spec, each tagged")
        return 0

    for problem in found:
        print(f"::error::{problem}")
    print(f"\n{REMEDY}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
