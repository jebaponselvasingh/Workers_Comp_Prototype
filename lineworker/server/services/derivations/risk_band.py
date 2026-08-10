"""`risk` — the severity band, and the first entry in the AD-10 registry.

`risk` is not a column (Story 1.2 banned derived columns): it is a band of
`claim.severity_score`. Everything that shows risk — the top-bar High Risk
tile (1.4), queue cards (2.1), the detail gauge (2.2), dashboard KPIs (5.1)
— calls this one function, which is why it exists before anything but the
tile needs it.

**Two forms, one source.** `of()` bands a score in Python; `sql_is()` bands
it in SQL so an aggregate can count a band without loading a hundred rows.
They are not two implementations: `sql_is` compares against the CASE
expression built from the same two attributes `of()` reads, so a threshold
change moves both or neither. The Dev Notes permit pushing the band into a
query on exactly that condition.
"""

from dataclasses import dataclass
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from data.models import Claim
from services.derivations.registry import Derivation, register


class RiskBand(StrEnum):
    """Snake/lowercase values per the enum convention — the UI owns labels."""

    high = "high"
    med = "med"
    low = "low"


@dataclass(frozen=True)
class RiskDerivation:
    """Bands `severity_score`: high >= `high_min`, med >= `med_min`, else low.

    The thresholds arrive from `Settings` (AD-8: parameters are
    configuration, formulas are Python). They are validated as
    `med_min <= high_min` there, so this class does not re-check an
    ordering it cannot fix.
    """

    high_min: int
    med_min: int

    def of(self, severity_score: int) -> RiskBand:
        if severity_score >= self.high_min:
            return RiskBand.high
        if severity_score >= self.med_min:
            return RiskBand.med
        return RiskBand.low

    def sql_band(self) -> ColumnElement[str]:
        """The band as a SQL expression over `claim.severity_score`."""
        return sa.case(
            (Claim.severity_score >= self.high_min, sa.literal(RiskBand.high.value)),
            (Claim.severity_score >= self.med_min, sa.literal(RiskBand.med.value)),
            else_=sa.literal(RiskBand.low.value),
        )

    def sql_is(self, band: RiskBand) -> ColumnElement[bool]:
        """Predicate for "this claim is in `band`", for aggregate queries.

        Expressed as `sql_band() == band` rather than as a hand-written
        range comparison: a range would be a second encoding of the same
        rule, and the two would drift the first time a band was added.
        (Not index-friendly, deliberately — correctness of the single
        source outranks a plan shape on a hundred-row table. Revisit with a
        functional index if a portfolio ever makes it matter.)
        """
        return self.sql_band() == band.value


risk = register(
    Derivation(
        name="risk",
        describes="severity band of claim.severity_score (high/med/low)",
        build=lambda settings: RiskDerivation(
            high_min=settings.risk_high_min,
            med_min=settings.risk_med_min,
        ),
    )
)
