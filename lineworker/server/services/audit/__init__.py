"""The audit log's schema owner (AD-4, AD-11) — first written by Story 2.3.

AD-4 splits audit into two responsibilities and this module is the first
half: `services/audit` **owns the shape** of an audit event and, later, its
retention and redaction (AD-11's purge cascade, Story 8.1); the *writing*
happens inside whichever command performed the mutation, in that command's
own transaction. Hence `record` takes a session it does not own and never
commits — an audit row that could be committed separately from the change it
describes is exactly the failure "in the same transaction" is about.

**Why a helper at all, rather than `db.add(AuditEvent(...))` at each call
site.** The schema is fixed by AD-4 — `(id, at, actor_id, actor_role,
action, entity, entity_id, before, after)` — and "fixed" only survives
contact with twenty commands if there is one place that spells it. The
actor is taken from the caller context rather than passed, so a command
cannot name a different actor from the one whose scope it just ran under.

**AD-11: diffs are PHI.** `before`/`after` carry only the fields the command
actually wrote — never a whole row, and never a value that was not part of
the change. The structlog line this module emits carries the action, the
entity id and *which keys* changed; it never carries a value.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import AuditEvent

log = structlog.get_logger()


async def record(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    action: str,
    entity: str,
    entity_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    at: datetime | None = None,
) -> AuditEvent:
    """Add one AD-4 audit row to the caller's transaction. Does not commit.

    `at` is a parameter with a UTC default rather than a database default,
    for the reason the column has no `server_default`: the event happened
    when the command decided it happened, and a command that emits two rows
    for one mutation should be able to stamp them identically.
    """
    event = AuditEvent(
        at=at or datetime.now(UTC),
        actor_id=ctx.user_id,
        actor_role=ctx.role,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=dict(before) if before is not None else None,
        after=dict(after) if after is not None else None,
    )
    db.add(event)
    # Keys, never values (AD-11). "Which fields did this actor change on
    # which claim" is an operational question; what they changed them *to*
    # is claim data and lives only in the row above.
    log.info(
        "audit.recorded",
        action=action,
        entity=entity,
        entity_id=entity_id,
        actor_id=ctx.user_id,
        fields=sorted(after) if after else [],
    )
    return event
