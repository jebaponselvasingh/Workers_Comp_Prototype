"""AC 1's other half: every CI job is a **required** check, and the list says so.

"Required for merge" is a property of a GitHub branch-protection rule. It lives
on a settings page, it cannot be created or read from this working tree, and no
test can assert it. That leaves two honest options and one dishonest one. The
dishonest one is a sentence in a runbook saying the checks are required, which
does not fail when somebody adds a sixth job and forgets to require it.

The two honest ones are: record nothing and admit the gate is unverifiable, or
split the claim into the half that *is* checkable and the half that is not.
`.github/required-checks.yml` is that split — a settings-as-code manifest naming
the check runs that must be required, with a reason each — and this file holds
it to `.github/workflows/ci.yaml` in **set equality, both ways**. What stays
manual is the act of typing those names into the settings page; what stops being
manual is knowing what the list should contain. It is the same trade
`tests/test_deploy_tls_posture.py` makes about a compose file CI can render and
never boot, and the residual goes to `deferred-work.md` rather than being
implied away.

## Both directions, because the two failures are different failures

A job in the workflow that is **not** in the manifest is a check nobody made
required: it runs, it goes red, and the merge button stays green. That is the
failure this story exists to prevent and it arrives as an addition — the shape
review is worst at.

A check in the manifest with **no** such job is worse in a quieter way. Typed
into the settings page it becomes a required status that nothing ever reports,
and a pull request waiting on a check that will never arrive is
indistinguishable, on the page, from one whose checks are still running.

## No YAML parser

`tests/test_deploy_tls_posture.py` states the rule for this suite: a YAML parser
is a dependency this project does not declare, and nothing here is worth adding
one for. Both files are read with anchored, indentation-exact patterns — a job
key is two spaces and a bare name, a job's `name:` is exactly four — which is
also why a `- name:` inside a `steps:` list (six spaces) and a `name:` inside a
`with:` block (ten) cannot be mistaken for a job's. The patterns are strict
enough that a change to either file's shape makes the parse go *empty*, which
the vacuity guards below turn into a red build rather than a silent pass.
"""

import re
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
WORKFLOW: Final[Path] = REPO_ROOT / ".github" / "workflows" / "ci.yaml"
MANIFEST: Final[Path] = REPO_ROOT / ".github" / "required-checks.yml"

#: A job key in `ci.yaml`: exactly two spaces, a bare name, a colon, nothing
#: else. `on:`'s `push:`/`pull_request:` and `defaults:`' `run:` have the same
#: shape, which is why the scan is gated on being inside the `jobs:` block
#: rather than on the pattern alone.
_JOB_KEY: Final[re.Pattern[str]] = re.compile(r"^ {2}(?P<job>[A-Za-z_][A-Za-z0-9_-]*):\s*(?:#.*)?$")

#: A job's display name: exactly four spaces. A step's is `      - name:` (six)
#: and an action input's is deeper still, so the indent is the discriminator and
#: the anchor is doing real work.
_JOB_NAME: Final[re.Pattern[str]] = re.compile(r"^ {4}name:\s*(?P<name>\S.*?)\s*$")

#: One manifest entry. Two lines, in this order, because a `job:` without its
#: `name:` is a declaration that names no check run.
_ENTRY_JOB: Final[re.Pattern[str]] = re.compile(r"^ {2}- job:\s*(?P<job>[a-z][a-z0-9_-]*)\s*$")
_ENTRY_NAME: Final[re.Pattern[str]] = re.compile(r"^ {4}name:\s*(?P<name>\S.*?)\s*$")
_ENTRY_REASON: Final[re.Pattern[str]] = re.compile(r"^ {4}reason:\s*(?P<inline>.*?)\s*$")


def workflow_jobs(text: str | None = None) -> dict[str, str]:
    """Job key → check-run name, read out of `ci.yaml`'s `jobs:` block.

    The name is the job's `name:` when it has one and the key otherwise, because
    that is exactly how GitHub derives the name of the check run a protection
    rule matches. Every job in this workflow sets one; the fallback is here so
    that a job added without a `name:` is compared against the string the
    settings page would actually show rather than being skipped.
    """
    found: dict[str, str] = {}
    inside = False
    job: str | None = None
    if text is None:
        text = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            # A column-0 comment is not a top-level key. Treating it as one ended
            # the scan at the first `# ---` banner inside `jobs:` and dropped every
            # job after it from *both* set comparisons — a required-check list that
            # silently stops covering the workflow, which is this file's own
            # subject. Found in review; `test_a_comment_inside_the_jobs_block_does_
            # not_end_the_scan` pins it.
            continue
        if line and not line[0].isspace():
            inside = line.rstrip() == "jobs:"
            job = None
            continue
        if not inside:
            continue
        key = _JOB_KEY.match(line)
        if key is not None:
            job = key["job"]
            found[job] = job
            continue
        name = _JOB_NAME.match(line)
        if name is not None and job is not None:
            found[job] = name["name"]
    return found


def declared_checks() -> dict[str, str]:
    """Job key → declared check-run name, read out of `.github/required-checks.yml`."""
    found: dict[str, str] = {}
    job: str | None = None
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        entry = _ENTRY_JOB.match(line)
        if entry is not None:
            job = entry["job"]
            continue
        name = _ENTRY_NAME.match(line)
        if name is not None and job is not None:
            found[job] = name["name"]
            job = None
    return found


def declared_reasons() -> dict[str, str]:
    """Job key → the prose arguing why that check must block a merge.

    Folded scalars (`reason: >-` followed by indented lines), read by collecting
    everything under the key that is indented past it. The *content* is nobody's
    business here; what is asserted is that there is some, because a manifest of
    five names with no arguments is a list somebody will one day shorten to four
    without having to write down why.
    """
    found: dict[str, str] = {}
    job: str | None = None
    collecting: str | None = None
    parts: list[str] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        entry = _ENTRY_JOB.match(line)
        if entry is not None:
            if collecting is not None:
                found[collecting] = " ".join(parts).strip()
            job, collecting, parts = entry["job"], None, []
            continue
        reason = _ENTRY_REASON.match(line)
        if reason is not None and job is not None:
            collecting, parts = job, []
            if reason["inline"] not in (">-", ">", "|", "|-", ""):
                parts.append(reason["inline"])
            continue
        if collecting is not None and line.startswith(" " * 6):
            parts.append(line.strip())
    if collecting is not None:
        found[collecting] = " ".join(parts).strip()
    return found


def test_the_two_files_are_actually_being_read() -> None:
    """The vacuity guard, first, because every assertion below is a set comparison.

    Two empty sets are equal. Change `ci.yaml`'s indentation, rename `jobs:`, or
    reshape the manifest, and both parsers here go quiet — and every equality
    below passes having compared nothing, which is the exact failure AD-15
    names and which Story 8.3's review found in a posture test of this suite.

    Five is the number of jobs this workflow has at the time the manifest was
    written. It is asserted as a floor rather than as an equality so that adding
    a sixth job is caught by the set comparisons — which name it — rather than
    by this test, which would only say a count changed.
    """
    jobs = workflow_jobs()
    declared = declared_checks()
    assert len(jobs) >= 5, f"only {len(jobs)} jobs parsed out of {WORKFLOW} — did its shape change?"
    assert len(declared) >= 5, f"only {len(declared)} checks parsed out of {MANIFEST}"


def test_every_workflow_job_is_declared_a_required_check() -> None:
    """The direction that catches an addition, which is the one review is worst at.

    A new job is a diff that reads as more coverage. It is more coverage only if
    somebody also opens the settings page: until then it runs, it can go red,
    and the merge button stays green — a check that exists and does not gate is
    strictly worse than no check, because it is counted.
    """
    undeclared = sorted(set(workflow_jobs()) - set(declared_checks()))
    assert undeclared == [], (
        f"these ci.yaml jobs are not declared required: {undeclared}. Add each to "
        ".github/required-checks.yml with its reason, then add its `name:` to the "
        "branch-protection rule on `main` — the file does not enforce itself."
    )


def test_every_declared_check_names_a_real_job() -> None:
    """The direction that catches a deletion, and it fails more quietly in production.

    A required status check that no workflow reports is a pull request that can
    never merge — and on the settings page it is indistinguishable from one
    whose checks are still running. Renaming a job without renaming the manifest
    entry produces exactly this.
    """
    phantom = sorted(set(declared_checks()) - set(workflow_jobs()))
    assert phantom == [], (
        f"these required checks name no job in ci.yaml: {phantom}. A required check "
        "nothing reports is a branch that can never merge."
    )


def test_every_declared_check_run_name_matches_the_job_it_names() -> None:
    """GitHub matches on the check-run **name**, so the key agreeing is not enough.

    `job: e2e` with `name: playwright smoke` is a manifest that passes both set
    comparisons above and still tells an operator to type a string no check run
    will ever produce. The `name:` is the load-bearing half of every entry; the
    key exists so a failure can say which job to look at.
    """
    jobs = workflow_jobs()
    for job, declared in sorted(declared_checks().items()):
        assert declared == jobs[job], (
            f"required-checks.yml calls job {job!r} {declared!r}; ci.yaml calls it "
            f"{jobs[job]!r}. Branch protection matches the check-run name character "
            "for character, so the two must be the same string."
        )


def test_every_declared_check_carries_a_reason() -> None:
    """A list of five names with no arguments is a list somebody shortens to four.

    The reasons are what make removing an entry a decision rather than a
    cleanup, which is the same argument `logging_config.LOG_KEY_ALLOWLIST` makes
    about a permission nobody exercised. Length rather than content: this test
    cannot judge an argument, but it can refuse an entry that does not make one.
    """
    reasons = declared_reasons()
    for job in sorted(declared_checks()):
        assert len(reasons.get(job, "")) > 40, (
            f"the required check for job {job!r} carries no reason. Say what it "
            "gates and what would get through without it."
        )


def test_the_manifest_says_plainly_that_it_does_not_enforce_itself() -> None:
    """The header is the part an operator reads, so it is asserted rather than trusted.

    Everything above proves the *list* is complete. None of it makes the checks
    required — that is a branch-protection rule in repository settings, and a
    reader who took this file for the enforcement would be reading a green test
    suite as evidence of a protection that does not exist. The header has to say
    so in as many words, and a header that can be edited without anything
    noticing is a header that eventually is.
    """
    header = MANIFEST.read_text(encoding="utf-8").split("required_checks:", 1)[0]
    assert "repository settings" in header
    assert "Branch protection" in header or "branch protection" in header
    assert "not read by CI" in header or "nothing in CI reads this file" in header


def test_a_comment_inside_the_jobs_block_does_not_end_the_scan() -> None:
    """A `# ---` banner at column 0 used to truncate the job list silently.

    `ci.yaml` is heavily commented by house convention, so a banner between two
    jobs is a matter of time. Every job after it vanished from both directions of
    the set comparison — leaving a required-check manifest that looked complete
    and covered a prefix of the workflow.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    lines = workflow.splitlines()
    # The banner has to go *inside* the `jobs:` block to reproduce it. An earlier
    # version of this test inserted at the first two-space key in the file, which
    # is under `on:` — before `jobs:` — so `jobs:` set `inside` back to True and
    # the test passed against the unfixed scan. Verified the other way round:
    # with the banner here and the comment guard removed, this scan returns
    # `['server']` and drops the other four jobs.
    jobs_at = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    first_job = next(i for i, line in enumerate(lines) if i > jobs_at and _JOB_KEY.match(line))
    lines.insert(first_job + 1, "# --- a banner between two jobs ---")

    # Parsed from text rather than by writing the repository's own workflow: a
    # test that edits a tracked file leaves it edited when it fails.
    assert workflow_jobs("\n".join(lines) + "\n") == workflow_jobs()
