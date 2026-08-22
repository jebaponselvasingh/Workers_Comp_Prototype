"""AC 2 as a property of the tree: **no other code path deletes PHI**.

Story 8.1's second acceptance criterion is a claim about *absence*, and no
behavioural test can check one. A test that purges a claim and finds every store
empty proves the sanctioned path works; it says nothing about the delete
somebody adds to `services/claims/documents.py` next year because it was easier
than routing through the cascade. So this reads the tree.

`tests/test_payment_ownership.py` makes the same kind of assertion about
`payment_schedule_week`, `bill` and `expense`, and its docstring says why the
instrument is blunt on purpose: the fix for a failure here is to move the delete
into the cascade, not to spell it differently.

## AST rather than grep, and this file is why

`tests/test_layering.py` learned this lesson the hard way and records it: every
module that argues about a rule names the thing the rule forbids while doing so.
This one is worse than most. `data/models/core.py` carries the comment "without
this every `DELETE FROM claim` (Story 8.1's purge) scans this table"; `purge.py`
names nineteen PHI tables in prose; migration 0043's downgrade guard is a
paragraph about not dropping conversations. A regex over the source would fire
on all of them, and a guard whose enforcement fires on the prose explaining the
guard is a guard somebody deletes.

So deletes are read from `ast.Call` — `sa.delete(Model)`, `db.delete(row)`,
`session.delete(row)` — and raw SQL from **string literals only**, with
docstrings excluded, which is exactly where SQL lives and where an explanatory
sentence does not.

## Two mandatory controls

`tests/test_scoped_repository.py`'s rule, restated everywhere a structural guard
appears in this suite: a guard that only ever reads a clean tree cannot tell
"nothing is wrong" from "nothing is checked". So there is a positive control
proving the detector fires on each shape of delete, a negative control proving
it leaves prose alone, and `test_every_phi_delete_exemption_still_exists`,
because an allowlist entry for a deleted file is both invisible and the shape a
bypass takes — rename a module onto an exempt path and the guard stops covering
it.
"""

import ast
import re
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: Every PHI-class store, by ORM class name.
#:
#: "Everything claim-derived is PHI-class" (Epic 8's context, AD-11) — so the
#: list is the claim aggregate plus the derived stores that quote it:
#: embeddings, the insight cache and its cursor, conversations, and the audit
#: log itself. `Employee` is in here because the injured worker's identity is
#: the most obviously protected row in the schema, and `AuditEvent` because a
#: delete against it outside `services/audit` would bypass the RLS-bounded
#: retention floor that is the whole of AD-4's exception.
#:
#: Deliberately **absent**: `Session` (a token hash and two timestamps — see
#: `PHI_DELETE_EXEMPTIONS`), and the reference tables (`GlossaryTerm`,
#: `RuleDocument`, `PathRequiredForm`, `StateRateSchedule`, `EmailTemplate`,
#: `KnowledgeChunk`), which hold no claim's data and which nothing deletes.
PHI_MODELS: tuple[str, ...] = (
    "AdditionalInjury",
    "AiInsight",
    "AiInsightAttempt",
    "AuditEvent",
    "Bill",
    "Claim",
    "ClaimEmbedding",
    "CopilotThread",
    "DiaryNote",
    "Document",
    "EmailLog",
    "Employee",
    "Expense",
    "Meeting",
    "PaymentScheduleWeek",
    "Photo",
    "TimelineEvent",
    "TreatmentPlanStep",
)

#: The same stores as SQL identifiers, for the raw-statement half of the scan.
PHI_TABLES: tuple[str, ...] = (
    "additional_injury",
    "ai_insight",
    "ai_insight_attempt",
    "audit_event",
    "bill",
    "claim",
    "claim_embedding",
    "copilot_thread",
    "diary_note",
    "document",
    "email_log",
    "employee",
    "expense",
    "meeting",
    "payment_schedule_week",
    "photo",
    "timeline_event",
    "treatment_plan_step",
)

#: The package that owns the cascade. A path prefix rather than a module list,
#: so a fourth module added to `services/audit` is covered without anybody
#: widening a constant — `test_payment_ownership.py::OWNER`'s reason, which is
#: that widening-a-constant is the failure mode of every allowlist.
OWNER = "services/audit/"

#: `DELETE FROM <phi table>` or `TRUNCATE <phi table>` in a string literal.
#:
#: Matched against literals only and never against docstrings, for the module
#: docstring's reason. The SQL keyword must precede the name: `"the purge
#: deletes claim rows"` is prose and `sa.text("DELETE FROM claim")` is not.
_RAW_DELETE = re.compile(
    r"\b(?:delete\s+from|truncate(?:\s+table)?)\s+(?:" + "|".join(PHI_TABLES) + r")\b",
    re.IGNORECASE,
)

#: Files outside `services/audit` that delete a PHI-class row, each with the
#: reason it is not a purge path.
#:
#: An explicit map rather than a directory carve-out, so adding an entry is a
#: diff a reviewer has to agree with — `test_layering.py::
#: CHECKPOINT_SQL_EXEMPTIONS`' convention, and here it carries the whole weight
#: of AD-12's distinction between a *user feature* that removes one row and a
#: *cascade* that removes a subject.
PHI_DELETE_EXEMPTIONS: dict[str, str] = {
    "services/financials/materialize.py": (
        "Story 3.3's schedule re-materialisation. The delete removes weeks a "
        "regenerated projection no longer contains — the same claim's own rows, "
        "replaced in place — and never touches a week a handler has decided "
        "(DECIDED_STATUSES). It is an AD-4 command owned by services/financials "
        "with its own audit event, not a deletion of a subject."
    ),
    "data/repositories/claims.py": (
        "Two AD-4 user-feature commands whose statements this repository "
        "expresses and whose services alone call them: Story 2.4's "
        "remove-secondary-injury (additional_injury) and Story 4.1's "
        "delete_meeting (meeting). Both are scoped, both are version-checked, "
        "both emit an audit event naming the claim, and both remove one row a "
        "handler asked to remove. The story text names the first of them by "
        "name as in-scope-of-AD-4 rather than a purge path."
    ),
}

#: Files whose deletes are of rows that are not PHI at all.
#:
#: Separate from `PHI_DELETE_EXEMPTIONS` on purpose: those are PHI deletes that
#: are allowed, and this is a claim that the row is not PHI. Conflating the two
#: would let a future reader believe a session carries claim data.
NON_PHI_DELETERS: dict[str, str] = {
    "data/repositories/identity.py": (
        "Story 1.3's session mint and revoke. A `session` row is a token hash "
        "and two timestamps — no claim data, no actor's words — which is the "
        "same argument test_claim_edit_validation.py makes when it exempts the "
        "auth router from the no-router-writes rule. `Session` is therefore "
        "absent from PHI_MODELS and this entry records why."
    ),
}

#: The trees a delete could hide in. `data/versions/` is excluded: a migration
#: is a reviewed, one-time record of what the schema became, and several of them
#: legitimately delete seed rows they inserted moments earlier (0048's
#: `downgrade` removes its own rule document).
#:
#: **`scripts/` is in the list because this story is what put it there.** It was
#: the one tree in `server/` outside the mypy-checked layer rules until Story
#: 8.1 made it the home of the PHI-deleting management command — the composition
#: root lives there precisely because wiring a checkpointer means importing
#: `agents/`, which `test_layering.py` forbids inside `services/`. That
#: exemption from one rule is not an exemption from this one: a tree that holds
#: the purge command is the most obvious place for the *second* purge command to
#: appear, written by somebody who reads `scripts/purge_claim.py`, copies its
#: shape and issues the deletes inline instead of calling the cascade. Leaving
#: it unscanned would have made this guard blind to the exact file it was
#: written alongside.
SCAN_ROOTS = ("api", "services", "rules", "agents", "data", "scripts")

EXCLUDED = ("data/versions",)


def _sources() -> list[Path]:
    found = [
        path
        for root in SCAN_ROOTS
        for path in (SERVER_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
        and not any(str(path.relative_to(SERVER_ROOT)).startswith(skip) for skip in EXCLUDED)
    ]
    assert found, f"nothing scanned under {SCAN_ROOTS} — did a package move?"
    return found


def _docstrings(tree: ast.AST) -> set[int]:
    """Every docstring node in a module, by identity — `test_layering.py`'s helper.

    Restated here rather than imported, for the reason every oracle in this
    suite is restated: a guard that shared its detector with another guard would
    be two tests agreeing with one implementation, and this one has a
    materially different job — it scans for SQL that deletes, not for SQL that
    reads a vendored table.
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


def deletes_phi(tree: ast.Module) -> bool:
    """Whether a module deletes a PHI-class row, by any of the four routes.

    1. **`sa.delete(Model)`** — the Core DML this codebase actually writes.
    2. **`delete(Model)`**, in case somebody imports the constructor directly.
    3. **`db.delete(row)` / `session.delete(row)`** — the ORM's instance delete.
       Matched by *method name* rather than by argument, because the argument is
       a variable and its type is not in the AST; the false positive that
       matters (`BlobStore.delete(key)`, which this cascade itself calls) is
       excluded by requiring the receiver to be a session-shaped name.
    4. **Raw SQL** naming a PHI table after `DELETE FROM` or `TRUNCATE`, in a
       non-docstring string literal.

    A `TRUNCATE` counts as a delete: it is the fastest way to empty a PHI table
    and the one a test fixture reaches for. `tests/` is not scanned at all —
    `test_copilot_persistence.py` truncates the conversation store on purpose,
    and the tests are the independent oracle rather than the code under rule.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_phi_delete_call(node):
            return True
    skip = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and _RAW_DELETE.search(node.value)
        for node in ast.walk(tree)
    )


def _is_phi_delete_call(node: ast.Call) -> bool:
    """`sa.delete(Model)`, `delete(Model)`, or `<session>.delete(...)`."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "delete":
        return False
    receiver = (
        func.value.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
        else None
    )
    if receiver in {"db", "session", "other"}:
        # The ORM instance delete. The receiver names are this codebase's own
        # spellings for a session; anything else called `.delete(` is a blob
        # store, a dict or a cache, and matching on those would make the guard
        # unusable inside the very module that walks `BlobStore.delete`.
        return True
    return any(
        isinstance(argument, ast.Name) and argument.id in PHI_MODELS for argument in node.args
    )


def test_only_services_audit_deletes_phi() -> None:
    """AC 2, as a property of the tree rather than of the diff that introduced it.

    A second cascade is not the failure this catches — nobody writes one of
    those by accident. What it catches is the ordinary, reasonable-looking
    delete: a "remove this document" command, a cache eviction that clears
    `ai_insight`, a cleanup in a repository. Each is defensible on its own and
    each moves one store out from under the one place that knows the whole set,
    which is how a purge comes to leave PHI behind without anybody deciding it
    should.

    The exemptions are named with their reasons above and are the three deletes
    that existed before this story. Anything else is a failure with the file
    named, and the fix is to route the deletion through
    `services/audit/purge.py` rather than to add a line to the map.
    """
    allowed = set(PHI_DELETE_EXEMPTIONS) | set(NON_PHI_DELETERS)
    offenders = sorted(
        relative
        for path in _sources()
        if (relative := str(path.relative_to(SERVER_ROOT))) not in allowed
        and not relative.startswith(OWNER)
        and deletes_phi(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert offenders == [], (
        "these modules delete a PHI-class row outside services/audit: "
        f"{offenders}. AD-11 gives the purge cascade sole ownership of PHI "
        "deletion and AD-12 registers it as the exception to per-entity write "
        "ownership; request the deletion through services/audit/purge.py, or "
        "add an entry to PHI_DELETE_EXEMPTIONS with an argument a reviewer "
        "will accept."
    )


def test_the_cascade_is_where_the_deletes_actually_are() -> None:
    """The other half: the guard is not passing because nothing deletes anything.

    An assertion that no module outside a package does X is satisfied trivially
    by a tree in which nothing does X at all — including one where somebody has
    quietly emptied `purge.py`. So the positive statement is made too: the
    cascade is in the package the rule names.
    """
    cascade = SERVER_ROOT / "services/audit/purge.py"
    assert cascade.is_file()
    assert deletes_phi(ast.parse(cascade.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "smell",
    [
        "await db.execute(sa.delete(Document).where(Document.claim_id == 1))",
        "stmt = sa.delete(ClaimEmbedding)",
        "await db.execute(delete(AiInsight))",
        "await db.delete(note)",
        "session.delete(meeting)",
        'await conn.execute(sa.text("DELETE FROM diary_note WHERE id = :i"))',
        'op.execute("TRUNCATE TABLE photo")',
        'sa.text("delete from claim_embedding")',
    ],
)
def test_the_phi_delete_guard_would_notice_a_new_delete(smell: str) -> None:
    """The mandatory positive control (`test_scoped_repository.py`'s rule).

    A guard that has only ever read a clean tree cannot tell "nothing is wrong"
    from "nothing is checked", and every one of these is a shape somebody would
    plausibly write while solving a real problem: a Core DML, a bare
    constructor, an ORM instance delete under either of this codebase's two
    session names, raw SQL under either keyword, and lower case.
    """
    assert deletes_phi(ast.parse(smell)), smell


@pytest.mark.parametrize(
    "innocent",
    [
        '"""Story 8.1 is the only thing that may DELETE FROM claim."""',
        "blobs.delete(key)",
        "cache.delete(claim_id)",
        "self._pending.delete(row_id)",
        "sa.delete(Session)",
        'sa.text("SELECT count(*) FROM claim")',
        "PHI_TABLES = ('claim', 'photo')",
    ],
)
def test_the_phi_delete_guard_leaves_innocent_modules_alone(innocent: str) -> None:
    """The negative control, and it is the half that decides whether this file survives.

    `test_layering.py`'s first version was a grep and it failed on the prose
    explaining the rule it enforced. The same hazard is sharper here: the
    cascade's own docstrings name every table it deletes, `data/models/core.py`
    carries the comment "without this every `DELETE FROM claim` … scans this
    table", and a guard that fired on either would be switched off within a
    week.

    `blobs.delete(key)` is in here for a second reason: `purge.py` calls it, and
    a receiver-blind ORM-delete detector would have made this guard fire inside
    the one module it exists to protect. `sa.delete(Session)` is here because a
    session row is deliberately not PHI — see `NON_PHI_DELETERS`.
    """
    assert not deletes_phi(ast.parse(innocent)), innocent


def test_every_phi_delete_exemption_still_exists() -> None:
    """An allowlist entry for a deleted file is an allowlist entry nobody notices.

    It is also the shape a bypass takes, which is `test_layering.py::
    test_every_checkpoint_exemption_still_exists`' argument and applies with
    more force here: rename a module onto one of these paths and its deletes
    stop being checked, silently, on a rule whose whole subject is that PHI does
    not survive a purge.
    """
    for relative in (*PHI_DELETE_EXEMPTIONS, *NON_PHI_DELETERS):
        assert (SERVER_ROOT / relative).is_file(), (
            f"{relative} is exempted from the PHI-delete guard but does not exist"
        )


def test_every_exempted_file_really_does_still_delete_something() -> None:
    """…and the entry is not merely stale in the other direction.

    A file that has stopped deleting anything does not need an exemption, and an
    exemption it does not need is a hole nobody is watching: the next delete
    added to that module inherits an argument written about a different
    statement. Checked rather than trusted, because the two exempted files are
    both large and both change for reasons unrelated to this rule.

    **The two maps are checked with two different assertions, because they make
    two different claims.** The first version checked both with `deletes_phi(...)
    or "sa.delete(" in source`, and that disjunct made the whole test very nearly
    unfailable: `"sa.delete("` is satisfied by a delete of *any* model, PHI or
    not, so a `services/financials/materialize.py` that had stopped deleting
    `payment_schedule_week` and now deleted something innocuous would sail
    through with its exemption — and its argument — intact. A staleness check
    that cannot detect staleness is the same shape of hole as the stale entry it
    exists to find.

    So `PHI_DELETE_EXEMPTIONS` is held to `deletes_phi`, which is the exact
    property its entries are excused from. `NON_PHI_DELETERS` is held to the
    claim *it* makes, which is the opposite one: the file still deletes
    something, and what it deletes is still not PHI. That second half is the
    stronger check of the two — the day `identity.py` grows a delete against a
    claim-keyed table, this fails and names it, where a shared `deletes_phi`
    assertion would have quietly started passing.
    """
    for relative in PHI_DELETE_EXEMPTIONS:
        source = (SERVER_ROOT / relative).read_text(encoding="utf-8")
        assert deletes_phi(ast.parse(source)), (
            f"{relative} no longer deletes a PHI-class row; drop its exemption"
        )
    for relative in NON_PHI_DELETERS:
        source = (SERVER_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert _deletes_anything(tree), (
            f"{relative} no longer deletes anything; drop its NON_PHI_DELETERS entry"
        )
        assert not deletes_phi(tree), (
            f"{relative} is recorded as deleting no PHI, and now deletes some. "
            "Route it through services/audit/purge.py, or move the entry to "
            "PHI_DELETE_EXEMPTIONS with an argument a reviewer will accept."
        )


def _deletes_anything(tree: ast.Module) -> bool:
    """Any delete at all, PHI-class or not — `deletes_phi` with the model check off.

    Only `NON_PHI_DELETERS` needs this, and only for the staleness half of its
    assertion: the entry's whole content is "this file deletes rows and they are
    not PHI", so the check that it is not stale has to be able to see a delete
    `deletes_phi` is designed to ignore.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "delete":
                return True
    skip = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and re.search(r"\b(?:delete\s+from|truncate)\b", node.value, re.IGNORECASE)
        for node in ast.walk(tree)
    )
