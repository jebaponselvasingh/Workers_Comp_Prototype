"""Server-side session store (Story 1.3).

Infrastructure, not a domain entity — deliberately absent from the ERD's 25
and carrying no ``version`` column (nothing compare-and-swaps a session; the
AD-4 write-concurrency convention binds mutable claim aggregates).

The invariant this table exists to keep: **a session row references a user
and nothing else.** No role, no employer ids, no scope of any kind — AD-7
requires scope to be re-resolved from ``app_user`` + ``user_employer_assignment``
on every request, so there is nowhere here for a stale book of business to
hide. The cookie carries an opaque token; only its SHA-256 is stored, so a
leaked database dump cannot be replayed as a live session.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Identity, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from data.models.base import Base


class Session(Base):
    __tablename__ = "session"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # SHA-256 hex of the opaque cookie token — never the token itself.
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
