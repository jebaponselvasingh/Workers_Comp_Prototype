"""When the next payment batch runs — one computer (Story 3.4, AD-10).

The prototype's `nextBatchDate` (line 808), which its Bills summary renders as
"Approved payments are disbursed in the next scheduled batch run (Tuesdays &
Fridays). Next batch: Friday, Aug 15." Story 3.4 makes that sentence true — a
batch now exists — and this is the function that answers the date.

**Registered although it reads no threshold, and although it is four lines.**
That is `installments_paid`'s argument and it applies harder here: the date
appears in three places (the Bills summary's note, an approved schedule week's
sheet, an approved line item's sheet), and three components each adding days
to today until they hit a Tuesday is exactly the drift AD-10 exists to stop.
The first one to get the "today *is* a batch day" boundary wrong would put a
different date on the same screen as the other two.

**The cadence is an argument, not a threshold.** `Derivation.build` takes
`DerivationThresholds` because AD-8 puts business parameters in the rules tier,
and which weekdays a carrier's disbursement file goes out is not one — it is a
deployment fact, and the conventions row files schedules under config. So the
builder ignores its thresholds and `of` takes the configured days, which keeps
"where did this cadence come from?" answerable at the call site (`config.py`'s
`payment_batch_weekday_numbers`).

## The one rule worth stating: the next batch is never today

The prototype's loop starts at `i = 1`, so a handler approving a payment on a
Tuesday is told the next batch is Friday. That looks like an off-by-one and is
not: the sentence is shown *beside an approval the handler is about to make*,
and today's batch may already have run. Promising same-day disbursement for
money approved an hour after the file went out is the one direction this
sentence must not be wrong in, so the answer is always strictly in the future.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from services.derivations.registry import Derivation, register

DAYS_PER_WEEK: Final[int] = 7


@dataclass(frozen=True)
class NextBatchDateDerivation:
    def of(self, as_of: date, weekdays: frozenset[int]) -> date:
        """The first configured batch day strictly after `as_of`.

        `weekdays` holds `date.weekday()` values (Mon=0 … Sun=6) and is
        non-empty by construction — `Settings.payment_batch_weekday_numbers`
        refuses an empty cadence, because a batch that never runs would render
        here as an infinite loop rather than as a configuration error.

        At most seven candidates are examined, and the loop cannot fall
        through: any non-empty subset of seven consecutive weekdays contains
        one. The `raise` below is therefore unreachable and is present so that
        a future caller passing an empty set fails with a sentence rather than
        with `UnboundLocalError`.
        """
        for offset in range(1, DAYS_PER_WEEK + 1):
            candidate = as_of + timedelta(days=offset)
            if candidate.weekday() in weekdays:
                return candidate
        raise ValueError("no payment-batch weekday configured; the batch would never run")


next_batch_date = register(
    Derivation(
        name="next_batch_date",
        describes=(
            "the date the next scheduled payment batch runs — the first "
            "configured batch weekday strictly after a given day"
        ),
        build=lambda _thresholds: NextBatchDateDerivation(),
    )
)
