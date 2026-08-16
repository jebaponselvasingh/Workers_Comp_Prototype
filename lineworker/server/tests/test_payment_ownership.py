"""AD-12 and the payment transition contract, as structural tests (Story 3.4).

Two claims are made about this build that no behavioural test can check,
because they are claims about *absence*:

1. **`services/financials` is the only writer of `payment_schedule_week`,
   `bill` and `expense`.** A test that approves a payment and sees the right
   status proves the sanctioned path works; it says nothing about a second
   path somebody adds next year.
2. **Nothing but the batch marks a payment `paid`.** This is the story's
   one-sentence invariant, and it is the thing the source spreadsheet gets
   wrong (row 29 says approval marks Paid; rows 64–66 say approval sets
   Payment Scheduled). The spine resolves it in favour of 64–66, and a
   resolution that lives only in prose is one review away from being undone.

Both are asserted by reading the tree as text. That is blunt on purpose — the
fix for a failure is to move the write into a command, not to spell it
differently — and it is the same instrument
`tests/test_claim_edit_validation.py` uses for the append-only tables and
`tests/test_derivations.py` for the risk band.

## The one honest exception, stated rather than exempted quietly

The `paid` guard cannot be "the token appears nowhere else", because it does,
in a place that is not a defect: `services/financials/schedule.py` **projects**
`paid` for a week the calendar has passed, and `materialize.py` writes what the
projection produced. That is the prototype's own rule and the reason the seeded
portfolio has any disbursement history at all.

So the guard is written to the invariant that is actually true:

> An **approved** week is paid only by the batch.

which holds structurally because `materialize.plan_materialization` never
touches a row whose status is in `DECIDED_STATUSES` — asserted below directly,
over the whole cross-product of statuses, rather than inferred from the fact
that a `paid` literal appears in one more file. The residual (an *unapproved*
week going `upcoming → paid` because seven days passed) is recorded in
`deferred-work.md`, because changing it is a decision about what the seeded
portfolio means.
"""

import re
from pathlib import Path

import pytest

from data.models.enums import LineItemStatus, ScheduleWeekStatus
from services.financials.batch import TARGETS, entity_names
from services.financials.materialize import DECIDED_STATUSES, plan_materialization
from services.financials.schedule import ScheduleWeek

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: The three tables `services/financials` owns (AD-12), by ORM class name.
OWNED_MODELS = ("PaymentScheduleWeek", "Bill", "Expense")

#: The package that owns them. A path prefix rather than a module list, so a
#: seventh module added to the package is covered without anybody widening a
#: constant — which is the failure mode of every allowlist.
OWNER = "services/financials/"

#: The repository is where a scoped statement is *expressed*; the service is
#: the only thing allowed to *call* one. `services/claims/edit.py` and
#: `update_claim_fields_cas` already stand in exactly this relationship for the
#: `claim` table, so the shape is the build's, not this story's.
REPOSITORY = "data/repositories/claims.py"


def sources(*roots: str) -> list[Path]:
    found = [
        path
        for root in roots
        for path in (SERVER_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert found, f"nothing scanned under {roots} — did a package move?"
    return found


def relative(path: Path) -> str:
    return str(path.relative_to(SERVER_ROOT))


#: A write against one of the owned tables: an ORM construction, a Core DML
#: statement naming the class, or an attribute assignment to its status. The
#: `(?<!class )` guard is `test_claim_edit_validation`'s — the class definition
#: in `data/models/core.py` is where the table is *described*, not written.
def write_pattern(model: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<!class ){model}\s*\(|"  # constructing a row
        rf"sa\.(?:insert|update|delete)\s*\(\s*{model}\b|"  # Core DML
        rf"\b{model}\.status\s*="  # a status assignment
    )


def test_only_services_financials_writes_the_three_payment_tables() -> None:
    """AD-12's ownership registry, as a test.

    Scans everything that could hold a write — routers, services, agents, and
    the data layer — and asserts that the files mentioning a write against
    these three tables are the owning package, the repository that expresses
    the statements, and the model module that declares them. Migrations are
    excluded: a seed is a reviewed, one-time record of what the schema became,
    and 0026–0028 create and populate these tables by definition.
    """
    for model in OWNED_MODELS:
        pattern = write_pattern(model)
        offenders = sorted(
            relative(path)
            for path in sources("api", "services", "agents", "data/repositories", "data/models")
            if pattern.search(path.read_text())
        )
        allowed = {REPOSITORY, "data/models/core.py", "data/models/__init__.py"}
        unexpected = [
            offender
            for offender in offenders
            if not offender.startswith(OWNER) and offender not in allowed
        ]
        assert unexpected == [], (
            f"{model} is written outside {OWNER}: {unexpected}. AD-12 gives "
            "services/financials sole ownership; request the change through "
            "one of its commands."
        )


def test_the_worklist_approval_command_writes_nothing_itself() -> None:
    """AD-12 names this delegation by name, so it gets its own assertion.

    "worklist approval calls financials' command" is the spine's wording, and
    the failure it prevents is specific: the approval surface is exactly where
    a second writer of `payment_schedule_week` would appear, because it is the
    one place outside `services/financials` that has a reason to.
    """
    source = (SERVER_ROOT / "services/worklist/approvals.py").read_text()
    for model in OWNED_MODELS:
        assert not write_pattern(model).search(source), (
            f"services/worklist/approvals.py writes {model} directly"
        )
    assert "approve_schedule_week" in source, "the delegation is gone"


@pytest.mark.parametrize(
    "model, smell",
    [
        ("PaymentScheduleWeek", "db.add(PaymentScheduleWeek(claim_id=1))"),
        ("Bill", "await db.execute(sa.update(Bill).values(status=paid))"),
        ("Expense", "await db.execute(sa.delete(Expense).where(Expense.id == 1))"),
        ("Bill", "Bill.status = LineItemStatus.paid"),
    ],
)
def test_the_write_guard_would_notice_a_new_writer(model: str, smell: str) -> None:
    """A guard that only ever reads clean files cannot tell "nothing is wrong"
    from "nothing is checked"."""
    assert write_pattern(model).search(smell), smell


@pytest.mark.parametrize(
    "smell",
    [
        "row.status = LineItemStatus.paid",
        "        bill.status  =  LineItemStatus.paid",
        "paid_status=LineItemStatus.paid",
    ],
)
def test_the_paid_guard_would_notice_a_second_setter(smell: str) -> None:
    """The same check for the guard below, which is the story's invariant."""
    assert PAID_ASSIGNMENT.search(smell), smell


# --- the sole-`paid`-setter invariant ------------------------------------


#: An *assignment* of `paid`, as opposed to a mention of it. The distinction
#: is the whole guard: `approval.py` names `LineItemStatus.paid` in the status
#: it refuses to start from, and `claim_financials.py` names it to partition a
#: list into paid and unpaid — neither writes one.
PAID_ASSIGNMENT = re.compile(
    r"(?:=\s*|paid_status=\s*)(?:ScheduleWeekStatus|LineItemStatus)\.paid\b"
)


def test_only_the_batch_sets_a_line_item_to_paid() -> None:
    """For `bill` and `expense` the invariant is exact and checkable.

    Nothing projects a line item's status — unlike a schedule week, whose
    calendar-derived `paid` is the exception this module's docstring argues
    about — so "the batch is the only thing that pays a bill" is a statement
    about the whole tree with no carve-out at all.

    The files permitted to *name* `LineItemStatus.paid` are the batch (which
    writes it), the derivations that partition a list into paid and unpaid, the
    approval module (which names it as a status it refuses to start from), and
    the model that declares it. Everything else reads a status; nothing else
    assigns one.
    """
    assigners = sorted(
        relative(path)
        for path in sources("api", "services", "agents", "data/repositories")
        if PAID_ASSIGNMENT.search(path.read_text())
    )
    assert assigners == ["services/financials/batch.py"], (
        f"something other than the payment batch assigns LineItemStatus.paid: {assigners}"
    )


def test_no_approval_command_names_paid_as_a_destination() -> None:
    """The Excel's row-29 reading, refused in the source.

    `approval.py` may *mention* `paid` — it is a status the commands refuse to
    approve from, and the module docstring argues about it at length — but it
    must never assign one. This is the difference between naming a state and
    writing it.
    """
    source = (SERVER_ROOT / "services/financials/approval.py").read_text()
    assert not re.search(r"=\s*(?:ScheduleWeekStatus|LineItemStatus)\.paid\b", source)
    assert not re.search(r"new_status=\s*(?:ScheduleWeekStatus|LineItemStatus)\.paid\b", source)
    # And the positive half: the one status these commands do write.
    assert "new_status=scheduled" in source


def test_the_batch_covers_every_table_that_can_hold_an_approved_payment() -> None:
    """A fourth payment table added without a batch entry would be money that
    is approved and never disbursed — silently, for ever."""
    assert set(entity_names()) == {"payment_schedule_week", "bill", "expense"}
    for target in TARGETS:
        assert target.paid in (ScheduleWeekStatus.paid, LineItemStatus.paid)
        assert target.scheduled in (
            ScheduleWeekStatus.payment_scheduled,
            LineItemStatus.payment_scheduled,
        )


# --- the schedule-week qualification, held directly ----------------------


@pytest.mark.parametrize("decided", sorted(DECIDED_STATUSES))
@pytest.mark.parametrize(
    "projected",
    [
        ScheduleWeekStatus.pending_approval,
        ScheduleWeekStatus.due_this_week,
        ScheduleWeekStatus.upcoming,
        ScheduleWeekStatus.paid,
    ],
)
def test_a_regeneration_can_never_pay_an_approved_week(
    decided: ScheduleWeekStatus, projected: ScheduleWeekStatus
) -> None:
    """The half of the invariant that survives the calendar caveat.

    A week the calendar pays is one *nobody had decided*. Once a handler has
    approved a week — or once the batch has paid it — no regeneration touches
    it, whatever the projection now says, which is what makes the batch the
    only exit from `payment_scheduled`.

    Ranged over the whole cross-product rather than asserted on one example,
    because the interesting case is precisely the one a reader would not think
    to write: a stored `payment_scheduled` week whose projection says `paid`.
    """

    class Stored:
        week_no = 1
        period_start = period_end = __import__("datetime").date(2026, 4, 5)
        amount_cents = 100_000
        status = decided

    plan = plan_materialization(
        [
            ScheduleWeek(
                week=1,
                start=Stored.period_start,
                end=Stored.period_end,
                amount_cents=999_999,
                status=projected,
            )
        ],
        [Stored()],
    )
    assert plan.is_empty(), (
        f"a regeneration would rewrite a {decided.value} week as {projected.value}"
    )
