"""AD-15's bijection, held to the tree — and the bijection lint, held to fixtures.

`scripts/lint_story_specs.py` is a guard whose whole subject is an absence: a
story with no spec, a spec with no story. A guard that has only ever read a
correct tree cannot tell "nothing is wrong" from "nothing is checked", which is
`tests/test_log_phi_lint.py`'s standing argument and the reason this file is
shaped the same way — hand-written fixtures for every failure the lint claims to
catch, hand-written innocents for the cases it must leave alone, and one test
over the **real** repository so the tree's own bijection is a test rather than a
CI-only claim.

Two of the fixtures are load-bearing beyond their own row.

**The `1-3` / `1-30` pair, driven from both directions.** AD-15 names it, and it
is the one bug in a matcher like this that a correct-looking tree will never
surface: a grep for `@story:1-3` matches `@story:1-30`. (An earlier version of
this paragraph also claimed a glob of `1-3-*.spec.ts` matches `1-30-…`; review
ran it and it does not — `1-3-*` needs a literal `-` where `1-30-` has a `0`.)
Today's sprint has no story past `8-4`, so
the repository cannot exercise it and never will until an epic reaches ten
stories — by which time the lint is old, trusted and wrong. Both directions are
asserted because fixing one is easy and fixing one *feels* like fixing both.

**The empty inputs.** This lint reads two paths outside `server/`, so a move of
`e2e/stories` or a reorganisation of `_bmad-output/` turns it into a loop over
nothing that exits 0. `tests/test_log_phi_lint.py` makes this argument about a
renamed package; here it applies twice over, to two directories neither of which
this module owns.
"""

from pathlib import Path

import pytest

from scripts.lint_story_specs import (
    SPRINT_STATUS,
    STORY_SPEC_DIR,
    main,
    problems,
    spec_files,
    story_keys,
    story_slugs,
    story_tags,
    unreadable_entries,
)

# --- fixture builders ------------------------------------------------------
#
# Real files on disk rather than in-memory doubles, because two of the four
# detectors are about the *filesystem* — a filename's leading key and the number
# of files claiming it — and a double would be this test agreeing with itself
# about how a directory listing works.


def write_status(tmp_path: Path, entries: dict[str, str], *, header: bool = True) -> Path:
    """A minimal `sprint-status.yaml`: the preamble, then `development_status:`.

    The preamble is not decoration. The real file carries top-level scalars and
    a wall of `#` commentary before the block, plus `epic-N` and
    `epic-N-retrospective` entries inside it, and a parser that only ever saw a
    bare block would be a parser that has not met the file it reads.
    """
    lines = [
        "# generated: 2026-08-09",
        "project: Workers_Comp_Prototype",
        "story_location: _bmad-output/implementation-artifacts",
        "",
    ]
    if header:
        lines.append("development_status:")
        lines.append("  # Epic 1: a section marker, which is not an entry")
        lines.append("  epic-1: done")
        lines.extend(f"  {key}: {status}" for key, status in entries.items())
        lines.append("  epic-1-retrospective: optional")
    path = tmp_path / "sprint-status.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_spec(directory: Path, name: str, tag: str) -> Path:
    """One spec file, in the shape every real one has: an import and a describe."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        'import { expect, test } from "../fixtures/test";\n\n'
        f'test.describe("@story:{tag} @epic:{tag.split("-")[0]} a story", () => {{\n'
        '  test("@smoke it works", async ({ page }) => {\n'
        '    await page.goto("/");\n'
        "  });\n"
        "});\n",
        encoding="utf-8",
    )
    return path


def healthy(tmp_path: Path) -> tuple[Path, Path]:
    """A tracker and a spec directory that agree, for the tests that perturb one thing."""
    status = write_status(tmp_path, {"1-1-skeleton": "done", "8-4-gate": "ready-for-dev"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")
    write_spec(specs, "8-4-gate.spec.ts", "8-4")
    return status, specs


def run(status: Path, specs: Path) -> list[str]:
    """Exactly the call `main` makes, including the unreadable-entry list.

    It takes all four arguments deliberately: a helper that dropped the fourth
    would have every test in this file passing while the finding it feeds went
    unreported in CI — the shape of the bug that argument exists to close.
    """
    text = status.read_text(encoding="utf-8")
    return problems(
        story_keys(text), spec_files(specs), story_slugs(text), unreadable_entries(text)
    )


# --- the parser ------------------------------------------------------------


def test_the_status_block_yields_story_keys_and_nothing_else(tmp_path: Path) -> None:
    """`epic-1` and `epic-1-retrospective` are tracked here and are not stories.

    They carry no spec by definition, so a parser that returned them would
    report two orphan story keys on a repository that is perfectly correct —
    and the fix for *that* is a per-key exception list, which is how a guard
    starts accumulating the carve-outs that eventually hide a real finding.
    """
    status = write_status(tmp_path, {"1-1-skeleton": "done", "2-6-photos": "backlog"})
    assert story_keys(status.read_text(encoding="utf-8")) == {"1-1": "done", "2-6": "backlog"}


def test_only_the_development_status_block_is_read(tmp_path: Path) -> None:
    """A `2-2-…` line outside the block is somebody's prose, not a story entry.

    The file's own preamble is forty lines of status definitions and workflow
    notes; a reader that scanned the whole document for `key: value` would
    inherit whichever of them happened to look like one.
    """
    path = tmp_path / "sprint-status.yaml"
    path.write_text(
        "notes:\n"
        "  2-2-not-a-story: done\n"
        "development_status:\n"
        "  3-3-real: done\n"
        "epic_summary:\n"
        "  4-4-also-not-a-story: done\n",
        encoding="utf-8",
    )
    assert story_keys(path.read_text(encoding="utf-8")) == {"3-3": "done"}


def test_two_stories_under_one_key_raise_rather_than_collapse(tmp_path: Path) -> None:
    """A dict would silently keep the second and the first would vanish.

    The story that lost is a story nothing checks, which is the same shape as
    an empty scan and gets the same treatment. It raises rather than asserting
    because this module runs as `python -m …` in CI, where `PYTHONOPTIMIZE`
    compiles an `assert` out — `scripts/lint_log_phi.py::sources`' argument.
    """
    status = write_status(tmp_path, {"1-1-first": "done", "1-1-second": "done"})
    with pytest.raises(RuntimeError, match="two stories under the key '1-1'"):
        story_keys(status.read_text(encoding="utf-8"))


# --- the bijection ---------------------------------------------------------


def test_a_tracker_and_a_spec_directory_that_agree_produce_no_findings(tmp_path: Path) -> None:
    """The premise. Every test below perturbs exactly one thing about this."""
    status, specs = healthy(tmp_path)
    assert run(status, specs) == []


def test_a_story_with_no_spec_is_reported_by_key_and_by_the_file_it_wants(tmp_path: Path) -> None:
    """The orphan story key — the failure `ci.yaml`'s PR stage cannot see.

    The derive step only counts a key `ls stories/"$k"-*.spec.ts` resolves, so a
    story with no spec contributes no grep term at all: the job runs the smoke
    set, passes, and reports the story as gated.
    """
    status, specs = healthy(tmp_path)
    (specs / "8-4-gate.spec.ts").unlink()
    found = run(status, specs)
    assert len(found) == 1
    assert "story 8-4" in found[0]
    assert "e2e/stories/8-4-gate.spec.ts" in found[0], found[0]


def test_a_spec_for_a_story_nobody_tracks_is_reported(tmp_path: Path) -> None:
    """The other direction, which is the quieter one.

    A spec named for a story key that does not exist runs on the merge stage
    forever and is attributed to nothing — no report names it, and its failure
    would be investigated as a regression in whichever story it was copied from.
    """
    status, specs = healthy(tmp_path)
    write_spec(specs, "9-9-invented.spec.ts", "9-9")
    found = run(status, specs)
    assert len(found) == 1
    assert "9-9-invented.spec.ts" in found[0]
    assert "sprint-status.yaml does not track" in found[0]


def test_a_spec_file_with_no_story_key_in_its_name_is_reported(tmp_path: Path) -> None:
    """`stories/` has exactly one kind of member, so a file that is not one is a finding.

    Skipping it instead would be the carve-out that makes the whole directory
    optional: rename `8-4-gate.spec.ts` to `gate.spec.ts` and both directions of
    the bijection go quiet at once.
    """
    status, specs = healthy(tmp_path)
    write_spec(specs, "helpers.spec.ts", "8-4")
    found = run(status, specs)
    assert any("helpers.spec.ts" in problem for problem in found), found


def test_two_specs_claiming_one_story_are_reported(tmp_path: Path) -> None:
    """A `dict[str, Path]` would drop one of them and call the bijection intact.

    Which is the reason `spec_files` maps to a list: the PR stage greps one tag,
    both files match it, and nobody can say which is the story's gate — while a
    detector that kept only the last would report nothing at all.
    """
    status, specs = healthy(tmp_path)
    write_spec(specs, "8-4-gate-part-two.spec.ts", "8-4")
    found = run(status, specs)
    # Two findings, and both are true of this tree: one story claimed twice, and
    # the second file is not named for the story's key either. Asserting only the
    # first would let the count drift silently.
    assert len(found) == 2
    assert any("claimed by 2 spec files" in problem for problem in found), found
    assert any("8-4-gate-part-two.spec.ts" in problem for problem in found), found


def test_a_backlog_story_needs_no_spec(tmp_path: Path) -> None:
    """The one status that legitimately has none.

    A backlog story "only exists in the epic file" (sprint-status.yaml's own
    definitions). Requiring a spec for it would make this lint red on every
    story nobody has started, which is a lint that gets deleted rather than a
    gate that holds.
    """
    status = write_status(tmp_path, {"1-1-skeleton": "done", "9-1-future": "backlog"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")
    assert run(status, specs) == []


def test_a_spec_for_a_backlog_story_is_reported(tmp_path: Path) -> None:
    """…and the exemption runs in one direction only.

    A spec that exists for a story marked `backlog` means either the status is
    stale or the file belongs to somebody else, and both are worth a line. If
    this passed, `backlog` would be the status that switches the lint off for a
    key — which is a bypass with a one-word diff.
    """
    status = write_status(tmp_path, {"1-1-skeleton": "done", "9-1-future": "backlog"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")
    write_spec(specs, "9-1-future.spec.ts", "9-1")
    found = run(status, specs)
    assert len(found) == 1
    assert "9-1-future.spec.ts" in found[0]
    assert "backlog" in found[0]


# --- the tag ---------------------------------------------------------------


def test_a_spec_tagged_for_another_story_is_reported(tmp_path: Path) -> None:
    """The filename and the tag are one claim, and copying a spec is how they part.

    Both the file check and the key check pass here: `8-4-gate.spec.ts` exists
    and `8-4` is tracked. What is broken is the thing the PR stage actually
    greps for, so `--grep "@story:8-4\\b"` selects nothing and the job is green.
    """
    status, specs = healthy(tmp_path)
    write_spec(specs, "8-4-gate.spec.ts", "8-3")
    found = run(status, specs)
    assert len(found) == 1
    assert "8-4-gate.spec.ts" in found[0]
    assert "@story:8-3" in found[0]


def test_a_spec_with_no_tag_at_all_is_reported(tmp_path: Path) -> None:
    """An untagged spec can never be selected by the PR stage nor attributed by any report."""
    status, specs = healthy(tmp_path)
    (specs / "8-4-gate.spec.ts").write_text(
        'test.describe("the gate", () => {});\n', encoding="utf-8"
    )
    found = run(status, specs)
    assert len(found) == 1
    assert "carries no @story: tag" in found[0]


def test_a_second_describe_tagged_for_another_story_is_reported(tmp_path: Path) -> None:
    """Why `story_tags` returns a set rather than the first match.

    `6-6-honest-degradation.spec.ts` has two describe blocks and tags both, so
    a multi-describe spec is a shape this repository actually has — and a
    detector that stopped at the first tag would read a file whose *second*
    describe belongs to another story as entirely correct.
    """
    status, specs = healthy(tmp_path)
    path = specs / "8-4-gate.spec.ts"
    path.write_text(
        path.read_text(encoding="utf-8")
        + 'test.describe("@story:7-1 @epic:7 borrowed", () => {});\n',
        encoding="utf-8",
    )
    found = run(status, specs)
    assert len(found) == 1
    assert "@story:7-1, 8-4" in found[0]


# --- AD-15's prefix trap, from both directions -----------------------------


def test_a_short_key_does_not_claim_a_long_keys_spec(tmp_path: Path) -> None:
    """`1-3` and `1-30`, with only `1-30`'s spec present.

    A `ls 1-3-*.spec.ts` — which is what `ci.yaml`'s derive step does and what
    the obvious implementation of this lint would do — resolves
    `1-30-thirtieth.spec.ts` and reports story `1-3` as gated. It is not: the
    file's tag says `1-30`, the PR stage's grep for `@story:1-3\\b` selects
    nothing, and story `1-3` has no test.
    """
    status = write_status(tmp_path, {"1-3-third": "done", "1-30-thirtieth": "done"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-30-thirtieth.spec.ts", "1-30")
    found = run(status, specs)
    assert len(found) == 1
    assert "story 1-3 " in found[0], found[0]
    assert "e2e/stories/1-3-third.spec.ts" in found[0], found[0]


def test_a_long_key_does_not_borrow_a_short_keys_spec(tmp_path: Path) -> None:
    """The same pair with the files the other way round.

    Fixing the glob usually fixes this direction too — usually, and the whole
    point of asserting both is that "usually" is not a property a gate has.
    Here `1-3-third.spec.ts` exists and `1-30` must still be reported missing.
    """
    status = write_status(tmp_path, {"1-3-third": "done", "1-30-thirtieth": "done"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-3-third.spec.ts", "1-3")
    found = run(status, specs)
    assert len(found) == 1
    assert "story 1-30 " in found[0], found[0]
    assert "e2e/stories/1-30-thirtieth.spec.ts" in found[0], found[0]


def test_the_tag_matcher_is_anchored_in_both_directions() -> None:
    """`@story:1-3` is not `@story:1-30`, read as text rather than as a filename.

    The detector is asserted directly here because the two tests above go
    through the filename path, and a lint could be anchored in one matcher and
    not the other — which would pass every fixture in this file that pairs a
    correct filename with a correct tag.
    """
    assert story_tags('test.describe("@story:1-30 @epic:1 x", …)') == {"1-30"}
    assert story_tags('test.describe("@story:1-3 @epic:1 x", …)') == {"1-3"}
    assert "1-3" not in story_tags('test.describe("@story:1-30 @epic:1 x", …)')


def test_the_prefix_pair_is_clean_when_each_has_its_own_spec(tmp_path: Path) -> None:
    """The negative control: anchoring must not turn a correct pair into a finding.

    Without this, a matcher that refused every key sharing a prefix with another
    would pass all four assertions above and fail the first epic to reach ten
    stories.
    """
    status = write_status(tmp_path, {"1-3-third": "done", "1-30-thirtieth": "done"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-3-third.spec.ts", "1-3")
    write_spec(specs, "1-30-thirtieth.spec.ts", "1-30")
    assert run(status, specs) == []


# --- vacuity ---------------------------------------------------------------


def test_an_empty_status_block_is_a_finding_rather_than_a_pass(tmp_path: Path) -> None:
    """The rule AD-15 states, applied to this file first.

    Rename `development_status:` — or reorganise `_bmad-output/` — and every
    per-key comparison below it has nothing to iterate over. That exits 0 and is
    indistinguishable in CI from a repository whose bijection is perfect.
    """
    status = write_status(tmp_path, {}, header=False)
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")
    found = run(status, specs)
    assert found == [f for f in found if "empty key set" in f]
    assert found


def test_an_empty_spec_directory_is_a_finding_rather_than_a_pass(tmp_path: Path) -> None:
    """The other input, and it lives outside `server/` too.

    `STORY_SPEC_DIR` is resolved relative to this module, so a move of `e2e/`
    leaves the lint reading an empty directory rather than raising. Reported
    alone: the per-key loop would otherwise bury it under one orphan line per
    story in the sprint.
    """
    status = write_status(tmp_path, {"1-1-skeleton": "done"})
    specs = tmp_path / "stories"
    specs.mkdir()
    found = run(status, specs)
    assert len(found) == 1
    assert "empty spec set" in found[0]


def test_a_missing_tracker_exits_non_zero_without_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing tracker must read as a gate that failed, not as a crashing script.

    This is a CI step whose output is read at a glance. A traceback there gets
    triaged as a broken lint and disabled; a `::error::` naming the path it
    wanted gets fixed.
    """
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")
    assert main([str(tmp_path / "gone.yaml"), str(specs)]) == 1
    assert "::error::" in capsys.readouterr().out


def test_a_missing_spec_directory_exits_non_zero_without_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same, for the input this module has the least control over."""
    status = write_status(tmp_path, {"1-1-skeleton": "done"})
    assert main([str(status), str(tmp_path / "gone")]) == 1
    assert "::error::" in capsys.readouterr().out


def test_the_cli_passes_on_a_healthy_pair_and_says_how_many(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A green run states the size of the set it checked.

    `check_model_ports.py`'s convention, and here it is the only thing in the
    output that distinguishes a bijection that held from one whose inputs went
    missing in a way the guards above did not anticipate.
    """
    status, specs = healthy(tmp_path)
    assert main([str(status), str(specs)]) == 0
    assert "ok: 2 non-backlog story keys" in capsys.readouterr().out


# --- the real repository ---------------------------------------------------


def test_this_repository_satisfies_the_bijection_it_ships() -> None:
    """The assertion that makes the tree's own bijection a test rather than a CI claim.

    Every fixture above proves the detector works. This one proves the thing
    the detector is for, and it runs in the `server` job, in the `migrations`
    job and on a developer's machine — three places `ci.yaml`'s lint step is
    not. The remedy is never to delete a spec: AD-15 says specs are amended.
    """
    sprint_status_text = SPRINT_STATUS.read_text(encoding="utf-8")
    found = problems(
        story_keys(sprint_status_text),
        spec_files(STORY_SPEC_DIR),
        story_slugs(sprint_status_text),
    )
    assert found == [], (
        f"the sprint-status ↔ e2e/stories bijection is broken: {found}. "
        "Run `uv run python -m scripts.lint_story_specs` for the remedy."
    )


def test_the_lints_two_inputs_are_where_it_thinks_they_are() -> None:
    """Both paths leave `server/`, which is the one thing no other test here covers.

    `SPRINT_STATUS` climbs to the repository root and `STORY_SPEC_DIR` crosses
    into `e2e/`; a move of either is a rename in a tree this module does not
    own, and the failure it produces — an empty scan — is the one the vacuity
    guards turn into a red build rather than a silent green. This test is what
    tells the reader *which* path moved.
    """
    assert SPRINT_STATUS.is_file(), f"{SPRINT_STATUS} — the sprint tracker moved"
    assert STORY_SPEC_DIR.is_dir(), f"{STORY_SPEC_DIR} — the story specs moved"


def test_the_lint_module_is_where_ci_expects_it() -> None:
    """`uv run python -m scripts.lint_story_specs`, as the `server` job spells it.

    A module path in a workflow file is not type-checked, not imported by
    anything and not exercised by any other test — `tests/test_log_phi_lint.py`
    ends on the same assertion for the same reason.
    """
    server_root = Path(__file__).resolve().parents[1]
    assert (server_root / "scripts" / "lint_story_specs.py").is_file()


# --- the full-key filename rule (AD-15: renaming a story renames its spec) ---


def test_slugs_carry_the_whole_sprint_status_key(tmp_path: Path) -> None:
    """The numeric key stays the unit of work; the slug is what the file must be named."""
    status = write_status(tmp_path, {"8-4-complete-quality-operations-gate": "ready-for-dev"})
    assert story_slugs(status.read_text(encoding="utf-8")) == {
        "8-4": "8-4-complete-quality-operations-gate"
    }


def test_a_spec_named_for_the_key_but_not_the_slug_fails(tmp_path: Path) -> None:
    """The rename AD-15 is about: the tracker moves on, `e2e/stories/` keeps the old name.

    Every other check in this file passes here — the numeric key resolves, there
    is exactly one file, the tag agrees — which is precisely why this one exists.
    """
    status = write_status(tmp_path, {"8-4-complete-quality-operations-gate": "ready-for-dev"})
    specs = tmp_path / "stories"
    write_spec(specs, "8-4-ci-gate.spec.ts", "8-4")

    found = run(status, specs)

    assert len(found) == 1
    assert "8-4-ci-gate.spec.ts" in found[0]
    assert "8-4-complete-quality-operations-gate.spec.ts" in found[0]


def test_a_spec_named_for_the_whole_slug_passes(tmp_path: Path) -> None:
    """The negative control: without it the test above would pass on a broken matcher."""
    status = write_status(tmp_path, {"8-4-complete-quality-operations-gate": "ready-for-dev"})
    specs = tmp_path / "stories"
    write_spec(specs, "8-4-complete-quality-operations-gate.spec.ts", "8-4")

    assert run(status, specs) == []


# --- entries the scan cannot classify (review: the lint's own silent absence) ---


@pytest.mark.parametrize(
    "entry",
    [
        "8-5-Complete-Gate: ready-for-dev",  # an uppercase letter in the slug
        "8-5-complete_gate: ready-for-dev",  # an underscore
        "8-5: ready-for-dev",  # no slug at all
        "8-5-gate: 'ready for dev'",  # a quoted, multi-word value
    ],
)
def test_a_tracked_entry_the_scan_cannot_read_is_a_finding(tmp_path: Path, entry: str) -> None:
    """Each of these was silently dropped, and each hid a story with no spec.

    The lint printed `ok: N non-backlog story keys, each with exactly one spec`
    while a tracked, non-backlog story had none — which is the whole subject of
    the file, missed. The empty-key-set guard could not help: it fires only when
    *every* key is unreadable.
    """
    status = tmp_path / "sprint-status.yaml"
    status.write_text(
        f"development_status:\n  epic-8: done\n  1-1-skeleton: done\n  {entry}\n",
        encoding="utf-8",
    )
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")

    text = status.read_text(encoding="utf-8")
    assert unreadable_entries(text), f"{entry!r} was dropped in silence"
    found = run(status, specs)
    assert any("neither a story key nor an epic" in problem for problem in found), found


def test_epic_and_retrospective_rows_are_not_findings(tmp_path: Path) -> None:
    """The negative control: without it the check above could report every line."""
    status = write_status(tmp_path, {"1-1-skeleton": "done"})
    assert unreadable_entries(status.read_text(encoding="utf-8")) == []


# --- tags come from describe titles, not from anywhere in the file ---


def test_a_story_named_in_a_comment_is_not_read_as_a_tag(tmp_path: Path) -> None:
    """A spec may talk about its neighbours; only its describes claim a story."""
    status = write_status(tmp_path, {"8-4-gate": "ready-for-dev"})
    specs = tmp_path / "stories"
    path = write_spec(specs, "8-4-gate.spec.ts", "8-4")
    path.write_text(
        "// The rest of this flow is covered by @story:7-3.\n" + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert story_tags(path.read_text(encoding="utf-8")) == {"8-4"}
    assert run(status, specs) == []


def test_a_describe_that_lost_its_tag_is_reported_though_a_comment_names_it(
    tmp_path: Path,
) -> None:
    """The direction that matters: the PR stage greps the tag, and there is none.

    Scanning the whole file passed this — the comment supplied the tag the
    describe had dropped — while `--grep "@story:8-4\b"` selected nothing and the
    job went green having tested none of the story.
    """
    status = write_status(tmp_path, {"8-4-gate": "ready-for-dev"})
    specs = tmp_path / "stories"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "8-4-gate.spec.ts").write_text(
        'import { expect, test } from "../fixtures/test";\n\n'
        "// @story:8-4 @epic:8 — named here and nowhere the runner can see it.\n"
        'test.describe("complete quality and operations gate", () => {\n'
        '  test("@smoke it works", async ({ page }) => {\n'
        '    await page.goto("/");\n'
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    found = run(status, specs)
    assert any("carries no @story: tag" in problem for problem in found), found


# --- the spec scan is as recursive as playwright's testMatch ---


def test_a_spec_in_a_subdirectory_is_seen(tmp_path: Path) -> None:
    r"""`/stories\/.*\.spec\.ts/` crosses a `/`; a non-recursive scan did not."""
    status = write_status(tmp_path, {"1-1-skeleton": "done"})
    specs = tmp_path / "stories"
    write_spec(specs, "1-1-skeleton.spec.ts", "1-1")
    write_spec(specs / "sub", "9-9-orphan.spec.ts", "9-9")

    found = run(status, specs)
    assert any("9-9-orphan.spec.ts" in problem for problem in found), found
