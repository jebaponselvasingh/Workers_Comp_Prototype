"""The comp-rate override — Epic 3's first audited write (Story 3.1, AC 4).

The handler's one lever over the statutory benefit calculation: a comp rate
they set themselves, and a ↺ that puts the claim back on the default. Both go
through this module, and both are `services/claims/edit.py`'s shape — the same
five refusals in the same order, the same compare-and-swap through the same
repository function, the same 409 carrying the fresh entity. This is a *second
command*, not a second write idiom.

**Why the write lives in `services/claims` and not in `services/financials`.**
`comp_rate_override_bp` is a column on `claim`, and `claim`'s write-owner is
this package (AD-12). `services/financials` computes the benefit from the
column and never writes it, which is what keeps "who can change this row?"
answerable by looking at one directory.

## Set and reset are one command, because they are one field

`comp_rate_bp=None` *is* the reset. A second command would duplicate the
refusal ladder to express "write NULL instead of 6667", and the audit row would
then have two action names for one column's history — so a reader reconstructing
how a claim's rate moved would have to know both. One action, and the diff says
what happened: `{"comp_rate_override_bp": 7000} → {"comp_rate_override_bp":
null}` is a reset, unambiguously.

That is also why the API's field is **required but nullable**, unlike
`ClaimFieldPatch`'s optional members: there, `null` is a caller error (no
editable field has a meaningful empty value); here `null` is the whole point.

## Basis points, refused rather than clamped

The value is integer basis points — 6667 is 66.67% — matching the column and
the rules tier, so no float enters the write path. The bound is the comp rate's
domain (`services/financials`' `COMP_RATE_MIN_BP` / `COMP_RATE_MAX_BP`, 0% to
150%), and it is **refused, never clamped**: the prototype's `updateCompRate`
silently discards an out-of-range entry by re-rendering, so a handler who typed
`700` sees the field snap back to its previous value with no explanation. A
refusal the handler can read is the honest answer (NFR-3, and
`normalise_severity` makes the same argument about `Math.max(0, Math.min(100,
n))`).
"""

from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim
from data.models.enums import TimelineTag, UserRole
from data.repositories import claims as claim_repo
from services import audit
from services.claims import timeline
from services.claims.detail import ClaimDetail, ClaimNotVisible, claim_detail
from services.claims.edit import EditNotPermitted, InvalidPatch, conflict
from services.financials import COMP_RATE_MAX_BP, COMP_RATE_MIN_BP, format_comp_rate

#: The AD-4 `action` — the command's own name, so an audit row read months
#: later names the function that wrote it.
ACTION: Final[str] = "update_comp_rate_override"
ENTITY: Final[str] = "claim"
COLUMN: Final[str] = "comp_rate_override_bp"


def normalise_comp_rate(raw: object) -> int | None:
    """A whole number of basis points inside the comp rate's domain, or `None`.

    Exported for `normalise`'s and `normalise_severity`'s reason: an AD-13
    agent tool calls the command directly and never passes through the API
    layer's Pydantic model, so the command has to be safe on its own.

    **`None` passes through untouched** — it is the reset, not a missing value.

    **`bool` is refused before `int`.** `True` is an `int` in Python, so a
    caller sending `true` would otherwise record a comp rate of one basis
    point — 0.01% of wage, a number nobody typed, on the column that decides
    what an injured worker is paid every week.

    **The message states the rule in percent, not in basis points.** It reaches
    a handler's screen, and "must be between 0 and 15000" is a sentence about
    an implementation detail. It names no submitted value (AD-11).
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise InvalidPatch("comp rate must be a whole number of basis points (6667 = 66.67%)")
    if not COMP_RATE_MIN_BP <= raw <= COMP_RATE_MAX_BP:
        raise InvalidPatch(
            f"comp rate must be between {format_comp_rate(COMP_RATE_MIN_BP)}% and "
            f"{format_comp_rate(COMP_RATE_MAX_BP)}% of AWW"
        )
    return raw


def describe(before: int | None, after: int | None) -> str:
    """The timeline row's prose.

    **Names the field, never the rate** — Story 2.4's rule for the severity
    score, and the argument is if anything stronger here: a log line quoting
    one percentage out of a sequence of adjustments reads as *the* comp rate on
    the claim rather than as one step towards it. The audit row holds both
    values, which is where a reader reconstructing the history should be
    looking.

    Set, changed and cleared are three different events, though, and the
    sentence says which: "the handler removed their override" and "the handler
    moved it" are not the same action on a case file.
    """
    if after is None:
        return "Comp rate override removed — statutory default restored"
    if before is None:
        return "Comp rate override applied"
    return "Comp rate override changed"


async def update_comp_rate_override(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    comp_rate_bp: object,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Set or clear the comp-rate override under compare-and-swap, audited.

    Returns the freshly assembled case file — with `benefit` recomputed
    server-side from the new column (AC 4), which is the property that makes
    this a *server-side* recomputation rather than a value the client echoes:
    nothing in the response was calculated by the caller.

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404), `InvalidPatch`
    (422) or `StaleClaim` (409) — see `services/claims/edit.py`'s module
    docstring for the order and the reasons. The role check is first and
    happens before the claim is looked up, so a supervisor learns nothing about
    which claims exist.

    **This command owns the transaction on `db`**, exactly as
    `update_claim_fields` does, with the same warning: a later *composite*
    command must call the pieces this is built from rather than wrapping it.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can edit a case file.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    value = normalise_comp_rate(comp_rate_bp)

    if claim.version != expected_version:
        return await conflict(db, ctx, claim_business_id, as_of)

    before = claim.comp_rate_override_bp
    if value == before:
        # 2.3's rule: the log is a record of changes, and re-submitting the
        # rate already stored — or resetting a claim that was never
        # overridden — is not one. The caller still gets the current entity.
        return await claim_detail(db, ctx, claim_business_id, as_of=as_of)

    changed = await claim_repo.update_claim_fields_cas(
        db, ctx, claim_business_id, expected_version, {COLUMN: value}
    )
    if changed == 0:
        # The row moved between the SELECT above and the UPDATE. Nothing was
        # written, but the transaction has taken a snapshot, so roll it back
        # before re-reading or the "fresh" entity would be the stale one.
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=ACTION,
        entity=ENTITY,
        entity_id=claim.claim_id,
        # Integers and nulls, not their string forms — `update_claim_severity`'s
        # argument: the column is numeric, and an audit log that has to be
        # parsed to be compared is a log that will be compared wrongly. `null`
        # on either side is what a reset looks like, and it has to survive as
        # a null rather than as the string "None".
        before={COLUMN: before},
        after={COLUMN: value},
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim.id,
        description=describe(before, value),
        # A tag of its own rather than `edit`: this is a financial decision on
        # the case file, and a later reader scanning the timeline for what
        # moved a claim's money should not have to parse descriptions to find
        # it. `TimelineTag` is a `Text` column precisely so a story can add one
        # without a migration.
        tag=TimelineTag.benefit,
        # `at.date()`, not `as_of` — Story 2.3's code review: `as_of` selects
        # which rule version answers, and letting it date the timeline would
        # record the change as having happened then. One event, one clock.
        event_date=at.date(),
    )
    await db.commit()

    # `expire_on_commit=False` keeps committed objects readable, so the
    # identity map still holds the pre-write `Claim`. Expire it, or the
    # response could carry the previous rate. See `update_claim_fields` for
    # what `synchronize_session="evaluate"` does and does not guarantee here.
    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)
