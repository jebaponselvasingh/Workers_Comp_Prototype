"""`total_paid` and `cost_split` — the money figures the case file shows.

Two derived values that look like arithmetic and are therefore easy to leave
in a component, which is exactly why they are here. Every surface that shows
"what has this claim cost" — the investigation card and the settled payout
breakdown now, the dashboard and the analyst workspace later — reads these,
so no two of them can disagree by rounding differently or by forgetting the
expense column.

**A naming discrepancy, stated once and preserved.** The prototype labels
`paidIndemnity + paidMedical + paidExpense` "Total incurred". In insurance
practice "incurred" is normally *paid plus reserve*, so the prototype's label
names a different quantity from the one it computes. The prototype is this
story's design contract, so the **figure** is ported exactly and the label is
the UI's; the derivation is named for what it actually adds up. Changing the
number is a product decision, not a port, and it is recorded as a discrepancy
in the story's Dev Agent Record.

**Parameterless, and registered anyway** — `days_open`'s argument: the
registry answers "who computes this?", not "what is configurable?".
"""

from dataclasses import dataclass
from typing import Protocol

from services.derivations.registry import Derivation, register


class PaidColumns(Protocol):
    """The three columns both derivations read.

    A structural type rather than `Claim`, so a service may pass a row
    projection and a test may pass a small stand-in — and so this module does
    not import the ORM to add three integers.
    """

    @property
    def paid_indemnity(self) -> int: ...

    @property
    def paid_medical(self) -> int: ...

    @property
    def paid_expense(self) -> int: ...


@dataclass(frozen=True)
class CostSplit:
    """The three-segment cost bar, as whole percentages.

    Computed server-side for AD-1's reason and one practical one: the
    prototype rounds each share independently with `toFixed(0)`, so its three
    segments can total 99 or 101 and the bar under- or overflows its track.
    Here the first two are rounded and the third takes the remainder, so the
    three always sum to exactly 100 — which is what a bar drawn as three
    widths actually requires.
    """

    indemnity_pct: int
    medical_pct: int
    expense_pct: int


@dataclass(frozen=True)
class TotalPaidDerivation:
    def of(self, claim: PaidColumns) -> int:
        """Indemnity + medical + expense, in cents."""
        return claim.paid_indemnity + claim.paid_medical + claim.paid_expense


@dataclass(frozen=True)
class CostSplitDerivation:
    def of(self, claim: PaidColumns) -> CostSplit | None:
        """The split, or `None` when nothing has been paid.

        `None` rather than three zeros: a claim with no payments has no
        composition, and a bar of three zero-width segments is a *drawing* of
        a fact the sentence beside it states better ("Active — payments
        pending"). Returning zeros would leave that choice to the component.
        """
        total = TotalPaidDerivation().of(claim)
        if total <= 0:
            return None
        indemnity_pct = round(claim.paid_indemnity * 100 / total)
        medical_pct = round(claim.paid_medical * 100 / total)
        return CostSplit(
            indemnity_pct=indemnity_pct,
            medical_pct=medical_pct,
            expense_pct=100 - indemnity_pct - medical_pct,
        )


total_paid = register(
    Derivation(
        name="total_paid",
        describes="paid indemnity + medical + expense, in cents (the prototype's 'total incurred')",
        build=lambda _thresholds: TotalPaidDerivation(),
    )
)

cost_split = register(
    Derivation(
        name="cost_split",
        describes="the paid-cost bar's three shares as whole percentages summing to 100",
        build=lambda _thresholds: CostSplitDerivation(),
    )
)
