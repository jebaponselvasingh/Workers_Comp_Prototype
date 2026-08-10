"""Expected numbers, read from the seed file rather than typed into tests.

Story 1.4's Dev Notes: "derive from the seed at test time (count the seed
rows per employer) rather than hardcoding magic numbers — the seed is the
fixture". A hardcoded 27 tells a future reader nothing about *why* it is
27, and it goes quietly wrong the day someone adds a claim to the seed.

These helpers read `data/seed/seed_data.json` — the same file the migration
loads — and count it independently of the query under test. They are a
second implementation of the expectation on purpose: a test that computed
its expected value with the code under test would pass no matter what that
code did.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "seed" / "seed_data.json"

# The `risk` band boundary, restated here as an independent oracle. This is
# the one place outside `services/derivations` allowed to name 65 (see
# `test_derivations.py::test_no_module_outside_the_registry_hardcodes_the_band`,
# which exempts the tests directory for exactly this reason): a test that
# imported the threshold from the code under test would agree with it even
# if both were wrong.
HIGH_RISK_MIN = 65


@lru_cache
def seed() -> dict[str, Any]:
    with SEED_PATH.open() as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def employers_of(persona_name: str, role: str) -> set[str]:
    """The employer names a seeded persona is assigned to (empty if scope_all)."""
    for user in seed()["app_users"]:
        if user["name"] == persona_name and user["role"] == role:
            return set(user["employers"])
    raise AssertionError(f"no seeded persona {persona_name!r}/{role!r}")


def scope_all_of(persona_name: str, role: str) -> bool:
    for user in seed()["app_users"]:
        if user["name"] == persona_name and user["role"] == role:
            scope_all: bool = user["scope_all"]
            return scope_all
    raise AssertionError(f"no seeded persona {persona_name!r}/{role!r}")


def claims_for(persona_name: str, role: str) -> list[dict[str, Any]]:
    """Every seeded claim the persona should be able to see (AD-7 scope)."""
    claims: list[dict[str, Any]] = seed()["claims"]
    if scope_all_of(persona_name, role):
        return claims
    employers = employers_of(persona_name, role)
    return [claim for claim in claims if claim["employer"] in employers]


def expected_topbar_stats(persona_name: str, role: str) -> dict[str, int]:
    visible = claims_for(persona_name, role)
    return {
        "caseload": len(visible),
        "activeTx": sum(1 for c in visible if c["stage"] == "treatment"),
        "highRisk": sum(1 for c in visible if c["severity_score"] >= HIGH_RISK_MIN),
    }


def expected_claim_ids(persona_name: str, role: str) -> set[str]:
    return {claim["claim_id"] for claim in claims_for(persona_name, role)}


# --- Story 1.5: the SLA strip, restated independently -------------------
#
# Same principle as HIGH_RISK_MIN above: the targets and the metric
# definitions are written out here rather than imported from
# `config.Settings` or `services.worklist.sla`, so this stays an oracle
# rather than an echo. If the two ever disagree, one of them is wrong and
# the test says so — which is the entire value of the arrangement.

SLA_TARGETS = {"pick": 1.0, "approve": 5.0, "settle": 30.0, "rtwRate": 80.0}
FULLY_RECOVERED = "returned_and_fully_recovered"


def _mean(values: list[int], decimals: int) -> float:
    """Half-up, matching the display precision the service rounds to."""
    quantized = (Decimal(sum(values)) / Decimal(len(values))).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
    )
    return float(quantized)


def expected_sla_strip(persona_name: str, role: str) -> dict[str, dict[str, Any]]:
    """`{metric: {value, status}}` for a persona's seeded book.

    `target` and `direction` are not restated per metric here — they are
    asserted once against `SLA_TARGETS` in the endpoint test, and repeating
    them in every expectation would add noise, not coverage.
    """
    visible = claims_for(persona_name, role)
    settled = [c for c in visible if c["stage"] == "settled"]

    picks = [c["sla_pick_days"] for c in visible if c["sla_pick_days"] is not None]
    approves = [c["sla_approve_days"] for c in visible if c["sla_approve_days"] is not None]
    settles = [c["settlement_days"] for c in settled if c["settlement_days"] is not None]

    values: dict[str, float | None] = {
        "pick": _mean(picks, 1) if picks else None,
        "approve": _mean(approves, 1) if approves else None,
        "settle": _mean(settles, 0) if settles else None,
        "rtwRate": (
            _mean([100 if c["return_status"] == FULLY_RECOVERED else 0 for c in settled], 0)
            if settled
            else None
        ),
    }

    def status(metric: str, value: float | None) -> str:
        if value is None:
            return "no_data"
        target = SLA_TARGETS[metric]
        met = value > target if metric == "rtwRate" else value < target
        return "pass" if met else "warn"

    return {
        metric: {"value": value, "status": status(metric, value)}
        for metric, value in values.items()
    }
