"""Two demo meetings for every handler persona (Story 4.1, AC 6).

The prototype's `seedMeetingsIfEmpty` (line 1840) runs at *login* and writes
into a browser-lifetime object. Here it is a migration, which is what closes
FR-LOGIN-3's seeded-meetings clause: a handler signing in sees two meetings
because two rows exist, not because a login handler put them there.

**The two titles are mapped, not copied, and the mapping is on the record.**
The prototype seeds "RTW Check-In Call" and "Case Review — Reserve & Treatment
Plan" — free-text titles its own scheduler could never have produced, because
that modal offers ten fixed options and neither is among them. `meeting_type`
is an enum, so the two rows take `rtw_conference` and
`claim_review_supervisor`, the nearest members, and the prototype's fuller
wording is carried in `notes` where fidelity costs nothing. Story 4.1's Dev
Notes flag this; a free-text type would be a schema change request, not dev
discretion.

**The date is the migration's run date, deliberately.** The prototype seeds
"today at login", so its demo always shows today's meetings; a migration runs
once, so day one matches the prototype and later days show two past meetings.
That is acceptable demo behaviour and it is the honest option — the
alternatives are a hardcoded date that is wrong immediately, or a login-time
writer, which is the throwaway store this story replaces. It also exercises
the `meeting_status` derivation in both directions without any test having to
fabricate a row.

**Which two claims.** Each persona's meetings link to the two lowest-sorted
claim business ids **in their employer scope** — not their two assigned
claims. Scope is what `list_meetings` filters by (AD-7), so a meeting linked
to a claim the persona cannot see would be seeded invisible, and the AC would
read as a bug. Lowest-sorted rather than "first treatment claim" (the
prototype's pick) because a deterministic, orderable rule is what lets the
e2e oracle recompute the expectation from `seed_data.json` without importing
the code under test.

**A persona with fewer than two scoped claims is a failure, not a shrug.**
The AC says *two* meetings per handler; seeding one would leave a login that
half-satisfies it with nothing anywhere saying so. All six seeded handlers
have at least four scoped claims today (the narrowest, Fatima Al-Mansoori on
Lockheed Martin, has four), so the check below is a tripwire for a future
re-scoping rather than a live constraint.

Revision ID: 0033_seed_meetings
Revises: 0032_meeting
Create Date: 2026-08-17

"""

from collections.abc import Sequence
from datetime import date, time
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0033_seed_meetings"
down_revision: str | None = "0032_meeting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HANDLER_ROLE = "handler"

#: How many claims a persona must have in scope for the two demo meetings to
#: link to two different files. Named rather than inlined because the refusal
#: below is the only thing standing between a re-scoped seed and an AC that
#: quietly stops holding.
MEETINGS_PER_HANDLER = 2

#: The two demo meetings, in the prototype's order — its two `meetingsStore`
#: pushes, with the free-text titles moved into `notes` and the types mapped
#: onto `MeetingType` members. See the module docstring on the mapping.
#: [Source: docs/Workers_Comp_Prototype.html lines 1841-1849]
DEMO_MEETINGS: tuple[dict[str, Any], ...] = (
    {
        "meeting_type": "rtw_conference",
        "meeting_time": time(10, 30),
        "location": "Phone",
        "notes": (
            "RTW Check-In Call — confirm light-duty availability and physician clearance status."
        ),
        "participants": ["employee", "employer_hr"],
    },
    {
        "meeting_type": "claim_review_supervisor",
        "meeting_time": time(15, 0),
        "location": "Video Call",
        "notes": (
            "Case Review — Reserve & Treatment Plan: review treatment "
            "progress and confirm reserve adequacy."
        ),
        "participants": ["ncm", "supervisor"],
    },
)


def _seeded_links(bind: sa.Connection) -> list[tuple[int, int, dict[str, Any]]]:
    """`(app_user_id, claim_id, demo)` for every row this revision inserts.

    **`upgrade` only.** It was shared with `downgrade` on the argument that one
    function deciding the tuples keeps the two from drifting — which is true of
    the *code* and false of the *data*: this resolves employer scope from the
    assignment table as it stands when it runs, so re-running it at downgrade
    time answers about the assignments of that moment rather than about the
    rows that were written. See `downgrade` for what replaced it.
    """
    meta = sa.MetaData()
    app_user = sa.Table("app_user", meta, autoload_with=bind)
    claim = sa.Table("claim", meta, autoload_with=bind)
    assignment = sa.Table("user_employer_assignment", meta, autoload_with=bind)

    handlers = bind.execute(
        sa.select(app_user.c.id, app_user.c.name)
        .where(app_user.c.role == HANDLER_ROLE)
        .order_by(app_user.c.id)
    ).all()
    if not handlers:
        raise ValueError(
            "no handler personas in app_user — 0004 must have seeded before this revision"
        )

    links: list[tuple[int, int, dict[str, Any]]] = []
    for handler_id, handler_name in handlers:
        # The persona's scope, resolved exactly as `employer_ids_for` and
        # `employer_scope` resolve it at runtime — via the assignment table,
        # not via `claim.handler_id`. A `scope_all` handler does not exist in
        # the seed; if one ever does, this refuses rather than guessing.
        scoped = bind.execute(
            sa.select(claim.c.id, claim.c.claim_id)
            .select_from(claim)
            .join(assignment, assignment.c.employer_id == claim.c.employer_id)
            .where(assignment.c.user_id == handler_id)
            .order_by(claim.c.claim_id)
            .limit(MEETINGS_PER_HANDLER)
        ).all()
        if len(scoped) < MEETINGS_PER_HANDLER:
            raise ValueError(
                f"handler {handler_name!r} has {len(scoped)} claim(s) in employer scope, "
                f"but Story 4.1 AC 6 seeds {MEETINGS_PER_HANDLER} demo meetings against "
                "two different claims — re-scope the persona or revise the AC"
            )
        for demo, (claim_pk, _business_id) in zip(DEMO_MEETINGS, scoped, strict=True):
            links.append((handler_id, claim_pk, demo))
    return links


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    meeting = sa.Table("meeting", meta, autoload_with=bind)

    # `CURRENT_DATE` from the database rather than `date.today()` in Python:
    # the two can disagree across a UTC midnight, and the row a test reads
    # back must be dated by whatever the row was written against.
    today: date = bind.execute(sa.select(sa.func.current_date())).scalar_one()

    rows = [
        {
            **demo,
            "app_user_id": handler_id,
            "claim_id": claim_pk,
            "meeting_date": today,
            "is_done": False,
        }
        for handler_id, claim_pk, demo in _seeded_links(bind)
    ]
    bind.execute(meeting.insert(), rows)


def downgrade() -> None:
    """Remove the demo rows, and **only** the demo rows.

    A bare `DELETE FROM meeting` is the shape 0011, 0021 and 0027 use, and it
    is wrong here: those revisions seed tables nothing else writes, whereas
    `meeting` is written by three handler-facing commands from the day 0032
    lands. Downgrading past this revision would therefore destroy every meeting
    every handler had ever scheduled — PHI, per `Meeting`'s own AD-11 note —
    to undo twelve seeded rows.

    **The predicate is the constants this file froze, not a re-derivation of
    who was assigned to what.** It used to call `_seeded_links` again, which
    re-resolves each handler's employer scope *at downgrade time*, and that had
    two failure modes with the same cause. If assignments had changed — the one
    thing a long-lived database does between an upgrade and a downgrade — the
    helper computed a different `(handler, claim)` set and silently left the
    real seeded rows behind. And if any persona had since dropped below two
    scoped claims, the helper's own tripwire raised `ValueError` and the
    downgrade *refused to run at all*, which is a migration that cannot be
    rolled back because of a fact about data it is not deleting.

    `(meeting_type, notes)` is what `upgrade` actually wrote and is knowable
    from this file alone. The two `notes` sentences are the discriminator: they
    are written by this revision and by nothing else, so a handler's own
    `rtw_conference` against the same claim survives. `meeting_date` is
    deliberately not part of it — it is the migration's *run* date, which a
    downgrade cannot know — and neither is `is_done` or `version`, because a
    seeded meeting somebody ticked off is still a seeded meeting.
    """
    bind = op.get_bind()
    meta = sa.MetaData()
    meeting = sa.Table("meeting", meta, autoload_with=bind)

    for demo in DEMO_MEETINGS:
        bind.execute(
            meeting.delete().where(
                meeting.c.meeting_type == demo["meeting_type"],
                meeting.c.notes == demo["notes"],
            )
        )
