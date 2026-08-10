"""Repositories — the only layer that talks SQL (AD-1).

AD-7 binds every repository that reads *claim-scoped* data: those methods
take a caller context and apply one unconditional employer filter. Two
modules below are deliberate carve-outs, and each says so in its own
docstring: `identity` is the plumbing that *builds* that context (sessions
and app_user rows), so it necessarily runs before one exists; `glossary`
reads reference data that has no employer, no claim and no PHI, so there is
nothing for a filter to narrow.

`claims` (Story 1.4) is the first scoped repository and the template for
the rest: read its module docstring before writing another one.
"""

from data.repositories import claims, glossary, identity

__all__ = ["claims", "glossary", "identity"]
