"""The reserve-rationale paragraph — deterministic prose, written server-side.

The benefit card closes with a paragraph explaining why the reserve is what it
is: the severity and the injury, the recovery window, the cost drivers that
apply to *this* claim, the statutory bounds it was reviewed against, and the
handler's override if there is one. The prototype builds it in
`benefitCardHTML` from a template literal; here it is built by this function.

**Not an LLM narrative, and not an `ai_insight` row** (AD-2). It renders
synchronously with the card, from the claim's own columns, and the same claim
always produces the same paragraph — which is exactly what a reserve rationale
has to be if it is going to sit in a case file. Epic 6's copilot writes prose
*about* a claim; this states what the console already knows.

**Written on the server rather than assembled in the browser** (AD-1). The
alternative is a payload of six optional clauses and a template in React, which
is the same sentence with its structure moved somewhere it cannot be tested and
where a future card would reword it independently.

## The one place the server formats money, and why that is not a contradiction

The money convention is "integer cents end to end, formatted only in the UI",
and every *figure* on the benefit card obeys it — `weeklyCents`,
`stateMinCents`, `stateMaxCents` all cross as integers. This paragraph is not a
figure: it is a sentence, in the same category as a timeline event's
description, which `services/claims/timeline.py` has written server-side since
Story 2.3. A sentence with a hole in it for the browser to fill is a template,
and templates in the browser are what AD-1 removes.

The consequence is a real obligation: `format_dollars` below must agree with
`web/src/lib/money.ts`'s `formatCents`, because the reserve appears as prose
here and as a formatted figure two rows above. Both drop the cents and both
round half up; `tests/test_benefit_rationale.py` pins the pairs that differ
under the alternatives.

## The wordings are the server's, and that is deliberate rather than sloppy

`RECOVERY_PHRASES` and `SEVERITY_WORDS` duplicate wordings the browser also
owns (`RECOVERY_LABEL`, `SEVERITY_WORD`). That is the same arrangement
`services/claims/reference.py`'s `FIELD_LABELS` makes and for the same reason:
the UI owns labels it *renders*, and the server owns words it *writes into
prose*. They are not the same strings either — a select shows "6-8 Weeks" and a
sentence needs "6-8 weeks" — so sharing one map would mean lower-casing a label
at one call site and not the other.
"""

from types import MappingProxyType
from typing import Final, Protocol

from data.models.core import StateRateSchedule
from data.models.enums import Disability, RecoveryWindow
from services.derivations import IndemnityType, RiskBand

CENTS_PER_DOLLAR: Final[int] = 100
BASIS_POINTS_PER_PERCENT: Final[int] = 100

#: The band, as the paragraph's opening clause spells it. The prototype
#: interpolates the dataset's own `severity` string lower-cased (`High` /
#: `Medium` / `Low`), so "med" would read "a med severity fracture".
SEVERITY_WORDS: Final[MappingProxyType[RiskBand, str]] = MappingProxyType(
    {RiskBand.high: "high", RiskBand.med: "medium", RiskBand.low: "low"}
)

#: The recovery window, as the paragraph spells it — the prototype's
#: `c.recovery.toLowerCase()` over its five display strings, now that the
#: column holds tokens instead (Story 2.3's code review).
RECOVERY_PHRASES: Final[MappingProxyType[RecoveryWindow, str]] = MappingProxyType(
    {
        RecoveryWindow.weeks_0_2: "0-2 weeks",
        RecoveryWindow.weeks_2_4: "2-4 weeks",
        RecoveryWindow.weeks_4_6: "4-6 weeks",
        RecoveryWindow.weeks_6_8: "6-8 weeks",
        RecoveryWindow.over_1_year: "greater than 1 year",
    }
)

SURGICAL_CLAUSE: Final[str] = (
    " Includes surgical exposure — facility, surgeon, and post-op therapy costs are the "
    "largest cost drivers."
)
LITIGATION_CLAUSE: Final[str] = (
    " Attorney representation on file — reserve carries an added litigation/legal-cost buffer."
)
PERMANENT_CLAUSE: Final[str] = (
    " Permanent disability rating expected — indemnity exposure extends beyond standard "
    "TTD duration."
)


class RationaleClaim(Protocol):
    """The claim columns this paragraph reads.

    Separate from `benefit.BenefitClaim` (which extends it) so the dependency
    runs one way: `benefit.py` imports this module, never the reverse.
    """

    @property
    def reserve(self) -> int: ...
    @property
    def severity_score(self) -> int: ...
    @property
    def injury_type(self) -> str: ...
    @property
    def recovery(self) -> RecoveryWindow: ...
    @property
    def disability(self) -> Disability: ...
    @property
    def surgery_required(self) -> bool: ...
    @property
    def litigation_flag(self) -> bool: ...


def format_dollars(cents: int) -> str:
    """Whole dollars with thousands separators — `web/src/lib/money.ts`'s twin.

    Public since Story 3.2, which writes a second deterministic sentence about
    the same reserve (`services/financials/reserve.py`). Two private copies of
    this would have been two roundings of one figure, and the whole point of
    the paragraph above is that the reserve reads identically in prose and in
    the formatted row beside it.

    Half-up on the cents, matching `Intl.NumberFormat`'s default `halfExpand`
    and `services/financials/benefit.py`'s rounding convention. No seeded claim
    has a non-zero cents component, so today every reserve formats identically
    under any rounding; the agreement is written down and tested because Epic
    3's bill lines are where that stops being true.
    """
    quotient, remainder = divmod(cents, CENTS_PER_DOLLAR)
    if remainder * 2 >= CENTS_PER_DOLLAR:
        quotient += 1
    return f"${quotient:,}"


def format_comp_rate(basis_points: int) -> str:
    """Basis points as a two-decimal percentage — `6667` → `"66.67"`.

    Integer arithmetic rather than `bp / 100`, so the string is exact by
    construction: the whole reason the rate is stored in basis points is that
    66.67 is not a float anyone can round-trip.

    Public because the SPA needs the same conversion and gets it from
    `web/src/lib/rate.ts`; this is the server-side statement of the same
    contract, used by the paragraph below and asserted against the client's in
    the e2e spec.
    """
    whole, hundredths = divmod(basis_points, BASIS_POINTS_PER_PERCENT)
    return f"{whole}.{hundredths:02d}"


def reserve_rationale(
    claim: RationaleClaim,
    *,
    band: RiskBand,
    indemnity_type: IndemnityType,
    rate: StateRateSchedule,
    comp_rate_bp: int,
    default_comp_rate_bp: int,
    is_overridden: bool,
) -> str:
    """The paragraph, in the prototype's order and wording.

    Keyword-only after the claim, because six of the seven arguments are
    single-word values that would be indistinguishable positionally — and
    swapping `comp_rate_bp` with `default_comp_rate_bp` would produce a
    perfectly fluent sentence stating the override backwards.

    The clauses are appended in the prototype's order (`benefitCardHTML`):
    opening, surgical, litigation, the permanent/temporary sentence, the
    statutory review, and the override. Order matters more than it looks — the
    paragraph is read as a narrowing argument, from what the injury is to what
    was done to the number.
    """
    parts = [
        f"Reserve set at {format_dollars(claim.reserve)} reflects a "
        f"{SEVERITY_WORDS[band]} severity "
        f"{claim.injury_type.lower()} (score {claim.severity_score}/100) with a "
        f"{RECOVERY_PHRASES[claim.recovery]} expected recovery window."
    ]
    if claim.surgery_required:
        parts.append(SURGICAL_CLAUSE)
    if claim.litigation_flag:
        parts.append(LITIGATION_CLAUSE)
    if claim.disability is Disability.permanent:
        parts.append(PERMANENT_CLAUSE)
    else:
        # The abbreviation, which the prototype recovers from its own label
        # with `.split(" — ")[0]`. Here the token *is* the abbreviation, so
        # the display label stays in the browser where the Enums convention
        # puts it and this sentence spells the short form itself.
        parts.append(
            f" Indemnity exposure estimated at {indemnity_type.value.upper()} weekly "
            "benefit through projected MMI."
        )
    parts.append(
        f" Reviewed against {rate.state_code} statutory min/max "
        f"({format_dollars(rate.weekly_min_cents)}–{format_dollars(rate.weekly_max_cents)}/wk)."
    )
    if is_overridden:
        parts.append(
            f" Comp rate manually adjusted to {format_comp_rate(comp_rate_bp)}% "
            f"(default {format_comp_rate(default_comp_rate_bp)}%) by handler."
        )
    return "".join(parts)
