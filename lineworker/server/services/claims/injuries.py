"""Secondary injuries — add and remove, audited (Story 2.4, FR-H-6, AD-4).

The prototype keeps these in a browser-lifetime map (`additionalInjuries`,
line 656) that starts empty and is discarded on reload. Here they are rows,
and the two operations that produce them are AD-4 commands with the same
shape as `services/claims/edit.py`'s: the refusal ladder, the
compare-and-swap, the same-transaction audit event and timeline event, and
the same `StaleClaim` carrying fresh state. **Nothing here is a second write
idiom** — the exceptions, the conflict response and the 403/404/422/409
mapping are Story 2.3's, imported rather than restated, so the router keeps
one vocabulary for four commands.

## Two different things are compare-and-swapped, and that is not an oversight

`add` guards on the **claim's** version and `remove` guards on the **injury
row's**.

- Adding is guarded by the claim because that is what the handler was
  looking at: the case file they read carried `version`, and a claim edited
  underneath them is a claim whose diagram they may no longer be reasoning
  about correctly. It is a genuine compare-and-swap rather than a check —
  the predicate rides inside the `INSERT … SELECT` (see
  `insert_additional_injury_cas`), so a claim that moved in the gap inserts
  nothing.
- Removing is guarded by the row because that is what is being destroyed,
  and the case file publishes each row's own `version` for exactly this.
  Guarding a delete with the *claim's* version would refuse a handler's ✕
  because somebody had corrected an unrelated ICD-10 code, while still
  permitting the one deletion that actually matters — removing a row another
  handler had just re-classified.

## Adding does not bump the claim's version

The insert leaves `claim.version` alone, so the case file the client already
holds stays a valid basis for its next edit. That is the honest reading: no
column of `claim` changed. It also means two handlers can record injuries on
the same claim concurrently without either being refused, which is the
correct outcome for an append — they are recording two different findings,
not overwriting one.

## The timeline sentence never echoes what a caller typed

`injury_type` is free text from a handler. The event says which **region**
was recorded, and the region is a member of a closed server-owned vocabulary
(`BODY_PART_LABELS`), so the log cannot be made to carry arbitrary text by
whoever calls the command — including an AD-13 agent tool, whose arguments
are untrusted content (AD-16). The audit row carries the whole row, where it
belongs.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim
from data.models.enums import TimelineTag, UserRole
from data.repositories import claims as claim_repo
from services import audit
from services.claims import timeline
from services.claims.detail import ClaimDetail, ClaimNotVisible, claim_detail
from services.claims.edit import (
    EditNotPermitted,
    InvalidPatch,
    conflict,
    normalise_severity,
    require_text,
)
from services.claims.reference import BODY_PART_LABELS

ADD_ACTION: Final[str] = "add_additional_injury"
REMOVE_ACTION: Final[str] = "remove_additional_injury"
ENTITY: Final[str] = "additional_injury"

#: `injury_type` shares the claim column's cap — it is the same kind of value
#: shown in the same list, and two different limits on one concept is how a
#: handler learns the rule by being refused.
INJURY_TYPE_FIELD: Final[str] = "injury_type"


@dataclass(frozen=True)
class NewInjury:
    """A validated secondary injury, ready to insert.

    Returned by `normalise_new_injury` so that validation is callable — and
    testable — without a database, exactly as `edit.normalise` is.
    """

    body_key: str
    body_part: str
    injury_type: str
    severity_score: int


def normalise_new_injury(
    body_key: object, injury_type: object, severity_score: object
) -> NewInjury:
    """Check the three fields of an add, or raise `InvalidPatch` (a 422).

    The body part **label is derived here, not accepted**: it is
    `BODY_PART_LABELS`' answer for the key, exactly as `update_claim_fields`
    rewrites `claim.body_part` when a handler picks a region. A caller that
    could supply its own label could write "Left Hand" against `head`.

    Messages name the field and the rule and never echo the value (AD-11).
    """
    if not isinstance(body_key, str) or body_key not in BODY_PART_LABELS:
        raise InvalidPatch("body part is not one of the diagram's regions")
    injury = require_text(INJURY_TYPE_FIELD, injury_type)
    return NewInjury(
        body_key=body_key,
        body_part=BODY_PART_LABELS[body_key],
        injury_type=injury,
        severity_score=normalise_severity(severity_score),
    )


async def add_additional_injury(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    body_key: object,
    injury_type: object,
    severity_score: object,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Record a secondary injury on a claim, audited (AC 2).

    Returns the freshly assembled case file, like every other command here —
    the new marker, the new summary row and its `id`/`version` all arrive
    without a second request.

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404), `InvalidPatch`
    (422) or `StaleClaim` (409).
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can edit a case file.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    injury = normalise_new_injury(body_key, injury_type, severity_score)

    # The pre-check answers a *stale* client before the statement runs, for
    # `update_claim_fields`' reason: it is the more useful answer and the
    # statement's own predicate is still what makes the guard sound.
    if claim.version != expected_version:
        return await conflict(db, ctx, claim_business_id, as_of)

    injury_id = await claim_repo.insert_additional_injury_cas(
        db,
        ctx,
        claim_business_id,
        expected_version,
        {
            "body_key": injury.body_key,
            "body_part": injury.body_part,
            "injury_type": injury.injury_type,
            "severity_score": injury.severity_score,
        },
    )
    if injury_id is None:
        # The claim moved between the SELECT above and the INSERT. Nothing
        # was written — the source query matched no rows — but the
        # transaction has taken a snapshot, so roll it back before re-reading
        # or the "fresh" entity would be the stale one we already have.
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=ADD_ACTION,
        entity=ENTITY,
        entity_id=str(injury_id),
        before=None,
        after=_diff(claim.claim_id, injury),
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim.id,
        description=f"Additional injury recorded ({injury.body_part})",
        tag=TimelineTag.edit,
        event_date=at.date(),
    )
    await db.commit()

    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


async def remove_additional_injury(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    injury_id: int,
    *,
    expected_version: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Remove a secondary injury, audited with the removed row (AC 2).

    The audit event's `before` is the whole row and its `after` is `None` —
    the two halves of "this existed and now does not". A whole-row diff is
    right here for the reason it is *wrong* in `update_claim_fields`: there,
    a one-field edit would have dragged every PHI column of the claim into a
    table with a seven-year retention floor; here, the row **is** the change,
    and a diff that recorded less would leave the log unable to say what was
    deleted (AD-11 asks for the fields the command wrote, not for fewer).

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404 — for the claim
    *and* for an injury that is not on it) or `StaleClaim` (409).
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can edit a case file.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    removed = await claim_repo.delete_additional_injury_cas(
        db, ctx, claim_business_id, injury_id, expected_version
    )
    if removed is None:
        # Nothing was deleted, and the caller deserves to know which of the
        # two reasons it was. Roll back first: the failed DELETE has taken a
        # snapshot, and both answers below are read *after* it.
        await db.rollback()
        existing = await claim_repo.select_additional_injury(db, ctx, claim_business_id, injury_id)
        if existing is None:
            # No such injury on this claim — the same 404 the claim itself
            # would get, and deliberately the same answer for "removed by
            # somebody else a moment ago" as for "never existed". A caller
            # cannot use the difference to learn what another handler did.
            raise ClaimNotVisible(f"{claim_business_id}/injuries/{injury_id}")
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=REMOVE_ACTION,
        entity=ENTITY,
        entity_id=str(injury_id),
        before=_diff(
            claim.claim_id,
            NewInjury(
                body_key=str(removed.body_key),
                body_part=removed.body_part,
                injury_type=removed.injury_type,
                severity_score=removed.severity_score,
            ),
        ),
        after=None,
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim.id,
        description=f"Additional injury removed ({removed.body_part})",
        tag=TimelineTag.edit,
        event_date=at.date(),
    )
    await db.commit()

    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


def _diff(claim_business_id: str, injury: NewInjury) -> dict[str, Any]:
    """The audit diff for one secondary injury — the row, plus its claim.

    `claim_id` is in the diff and not only in `entity_id` because `entity_id`
    holds the *injury's* surrogate id: without this, an audit row for a
    deleted injury would name a primary key that no longer resolves to
    anything, and Story 8.1's purge cascade would have no way to find the
    events belonging to a claim it is purging.
    """
    return {
        "claim_id": claim_business_id,
        "body_key": injury.body_key,
        "body_part": injury.body_part,
        "injury_type": injury.injury_type,
        "severity_score": injury.severity_score,
    }
