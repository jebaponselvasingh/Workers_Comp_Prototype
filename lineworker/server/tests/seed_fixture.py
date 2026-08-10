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
