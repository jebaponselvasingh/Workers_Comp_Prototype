"""Repositories — the only layer that talks SQL (AD-1).

AD-7 binds every repository that reads *claim-scoped* data: those methods
take a caller context and apply one unconditional employer filter. The
identity module below is the deliberate carve-out — it is the plumbing that
*builds* that context (sessions and app_user rows), so it necessarily runs
before one exists, and it touches no claim-scoped entity.

`claims` (Story 1.4) is the first scoped repository and the template for
the rest: read its module docstring before writing another one.
"""

from data.repositories import claims, identity

__all__ = ["claims", "identity"]
