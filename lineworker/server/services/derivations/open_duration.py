"""`days_open` — how long a claim has been open, from the date it opened.

**Why `froi_date`.** The prototype ships a static `daysOpen` per claim that
reconciles with **no** date in the record: checked against every date column
and every stored duration on `claim`, no consistent anchor exists, which is
why Story 1.2 deliberately did not seed it. The First Report of Injury is
when the claim came into existence for the carrier — the moment the pick-up
clock starts — so claim age counts from there. Counting
from `doi` would age a claim for the days before anyone knew about it, and
the queue would rank a late-reported injury above a claim the handler has
actually been sitting on.

**Why `as_of` is a required argument and not `date.today()`.** Two reasons,
both about agreement. A queue request must score every claim against *one*
day, or a request straddling midnight would rank two claims by different
clocks. And a test — or the e2e oracle, running in a different timezone
from the container — has to be able to name the day, or its expectations
would be right only until the calendar moved. `utc_today()` is the default
the callers pass, in one place each.

**Why it floors at zero.** `froi_date` may legitimately be in the future in
the seeded portfolio (the dataset runs to the end of the year). A negative
age would then *subtract* from the priority score through the days-open
term, ranking a not-yet-open claim below an identical open one — an ordering
nobody could defend. Zero says "no age yet", which is the truth.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

# `utc_today` is re-exported from `rules.engine` rather than redefined here:
# the queue resolves its rule documents and its claim ages against the same
# day, and two definitions of "today" is exactly the drift that produces a
# payload whose ages disagree with the weights that ranked them.
from rules.engine import utc_today as utc_today
from services.derivations.registry import Derivation, register


@dataclass(frozen=True)
class OpenDurationDerivation:
    """Claim age in whole days, floored at zero.

    Parameterless — and registered anyway. The registry is not a
    configuration mechanism, it is the answer to "who computes this?" (AD-10),
    and a derived value with no tunables still needs exactly one computer.
    Registering it is also what makes `derivations.get("days_open")`
    resolvable from the copilot's tool registry and Epic 5's aggregates
    without either of them knowing which date the count runs from.
    """

    def of(self, froi_date: date, as_of: date) -> int:
        return max((as_of - froi_date).days, 0)


class SettlementColumns(Protocol):
    """The two columns the settlement duration reads.

    Structural rather than `Claim` so this module does not import the ORM to
    read two attributes, and so a projection or a test stand-in works.
    """

    @property
    def settlement_days(self) -> int | None: ...

    @property
    def froi_date(self) -> date: ...


@dataclass(frozen=True)
class SettlementDurationDerivation:
    """How long a settled claim took: its recorded duration, or its age.

    The prototype's `c.settlementDays || c.daysOpen` (line 1400), which is a
    *fallback rule* rather than a column read — a settled claim with no
    recorded duration still has an age, and an em dash would be a worse
    answer than the number of days it has been open.

    **Why this lives here rather than in the settled overview.**
    `claim.settlement_days` is one of the three columns AD-2 reserves to the
    SLA aggregation (`services/worklist/sla.py`), and
    `tests/test_sla_aggregation.py` greps for direct reads of it. That guard
    is about a *second aggregation* — a dashboard averaging the column beside
    the strip that already does — and a per-claim display value is not one.
    But "not one" is an argument, and the way to make an argument checkable
    is to give the value a registered computer instead of scattering the
    column read across whichever card happens to need it. Epic 5's dashboard
    wants the same figure per claim; it now has one place to get it.
    """

    def of(self, claim: SettlementColumns, as_of: date) -> int:
        # The claim rather than the two values, deliberately: passing
        # `claim.settlement_days` in would leave the column named at the call
        # site, and the guard in `tests/test_sla_aggregation.py` would then
        # have to allowlist every consuming service instead of this one
        # module. `total_paid` takes its columns the same way and for the
        # same reason.
        #
        # `is not None`, not a truthiness test: the prototype's `||` sends a
        # recorded **zero** — a same-day settlement — down the fallback and
        # reports the claim's age instead of the 0 the data holds.
        if claim.settlement_days is not None:
            # Floored for the reason the fallback below is: a negative
            # duration is not a number to render, and the two halves of one
            # derivation disagreeing about that was an inconsistency inside
            # a single method (code review, 2026-08-12).
            return max(claim.settlement_days, 0)
        return OpenDurationDerivation().of(claim.froi_date, as_of)


days_open = register(
    Derivation(
        name="days_open",
        describes="whole days from claim.froi_date to a given date, floored at 0",
        build=lambda _thresholds: OpenDurationDerivation(),
    )
)

days_to_settlement = register(
    Derivation(
        name="days_to_settlement",
        describes="claim.settlement_days when recorded, otherwise the claim's age in days",
        build=lambda _thresholds: SettlementDurationDerivation(),
    )
)
