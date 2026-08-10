"""Services — where policy lives (AD-1).

Routers stay thin and repositories stay dumb: anything that decides *what a
number means* happens in this package. Two members arrive with Story 1.4:

- `derivations` — the registry of derived-value computers (AD-10).
- `worklist` — read aggregates over the scoped repository. Its write-owner
  role (action checklists, approvals) starts in Epic 3.
"""
