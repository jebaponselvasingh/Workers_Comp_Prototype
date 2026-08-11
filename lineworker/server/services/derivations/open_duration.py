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


days_open = register(
    Derivation(
        name="days_open",
        describes="whole days from claim.froi_date to a given date, floored at 0",
        build=lambda _thresholds: OpenDurationDerivation(),
    )
)
