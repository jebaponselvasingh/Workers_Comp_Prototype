"""The AD-7 caller context — one definition, imported by every layer.

Story 1.3 introduced these types inside `api/deps.py`, next to the
dependency that builds them. Story 1.4 gives them their own module because
the *repositories* now require one, and a repository that imported from
`api/` would invert the dependency direction the whole server is arranged
around (`api` → `services` → `data`, never back).

The builder still lives in exactly one place (`api.deps.get_caller_context`)
— this module holds only the vocabulary, so nothing here can decide what a
caller may see.
"""

from dataclasses import dataclass
from typing import Final

from data.models.enums import UserRole


class AllEmployers:
    """Sentinel for unbounded employer scope — `scope_all` and nothing else.

    A distinct type rather than `None` or an empty set: "sees everything"
    and "sees nothing" must never be one typo apart, and a repository that
    forgets to handle this case fails at type-check time.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ALL_EMPLOYERS"


ALL_EMPLOYERS: Final = AllEmployers()

EmployerScope = frozenset[int] | AllEmployers


@dataclass(frozen=True)
class CallerContext:
    """Who is asking, in the shape every scoped repository requires.

    Deliberately not the materialized scope of a session: it is rebuilt per
    request (AD-7), and it is a frozen dataclass so no service can widen it
    on the way down.
    """

    user_id: int
    role: UserRole
    employer_ids: EmployerScope

    @property
    def scopes_all_employers(self) -> bool:
        return isinstance(self.employer_ids, AllEmployers)
