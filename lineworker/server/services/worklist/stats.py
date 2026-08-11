"""Top-bar caseload aggregates (FR-TOP-2).

Three counts over one scoped set. What makes them interesting is where each
piece comes from:

- The *set* is decided by the caller context, in the repository (AD-7).
  Nothing in this module can widen it, and nothing here asks who the caller
  is beyond handing the context along.
- "High risk" is decided by the registered `risk` derivation (AD-10). This
  module does not know where the band boundary sits and must not learn it —
  the predicate it passes down is built by the derivation from its
  configured thresholds. (`test_no_module_outside_the_registry_hardcodes_the_band`
  enforces that literally, down to this docstring.)
- "Active treatment" is a plain column value (`stage = treatment`), not a
  derived one, so it is expressed directly. If it ever grows a rule — "in
  treatment *and* not blocked" — it becomes a registered derivation first.
"""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Claim
from data.models.enums import Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds
from services import derivations
from services.derivations import RiskBand


@dataclass(frozen=True)
class TopBarStats:
    """The three tiles, counted over the caller's book and nobody else's."""

    caseload: int
    active_tx: int
    high_risk: int


async def topbar_stats(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
) -> TopBarStats:
    """The three tiles. Takes its thresholds rather than fetching them.

    Story 2.1 moved the band cut-offs into a rule document, and this
    signature is the ripple: the caller loads the parameter block and hands
    it down, exactly as it used to hand down `Settings`. The alternative —
    loading the document in here — would drag the rules engine into every
    aggregate that happens to mention a band, and would make this function
    impossible to call without a database session it does not otherwise
    need.
    """
    risk = derivations.risk.for_thresholds(thresholds)
    counts = await claim_repo.count_claims_matching(
        db,
        ctx,
        {
            # A tautology on purpose: "everything in scope" is a bucket like
            # any other, so the caseload count goes through the same scoped
            # statement as the two narrower ones and cannot drift from them.
            "caseload": sa.true(),
            "active_tx": Claim.stage == Stage.treatment,
            "high_risk": risk.sql_is(RiskBand.high),
        },
    )
    return TopBarStats(**counts)
