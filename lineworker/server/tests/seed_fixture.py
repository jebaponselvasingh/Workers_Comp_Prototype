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

import importlib.util
import json
from datetime import UTC, date, datetime
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


# --- Story 5.1: the portfolio KPI cards, restated independently ---------
#
# `HIGH_RISK_MIN` above is one of the two thresholds these cards read; this is
# the other. Written out here rather than imported from `DerivationThresholds`
# for that constant's reason, and with one extra edge: it is *deliberately not*
# `SIU_FRAUD_SCORE_MIN` further down. The dashboard's Fraud Flags card counts a
# wider population than the queue's SIU chip (13 seeded claims against 9), and
# an oracle that shared one number with the other would have agreed with the
# single most plausible way to get this story wrong.
FRAUD_FLAG_SCORE_MIN = 55


def expected_portfolio_summary(persona_name: str, role: str) -> dict[str, int]:
    """The ten KPI figures plus the dataset chip's counts, from the seed file.

    Every rule restated from the prototype's `renderSV` (line 1002) and the
    story text, with the two deliberate departures written out here so the
    oracle disagrees loudly if either is quietly reverted:

    - **`settled_closed` counts `stage == "settled"`**, where `renderSV`
      filters `status === "Settled & Closed"`. Sixty-two seeded claims against
      the status rule's fifty-four; the console groups by stage everywhere else
      and a card that disagreed with 5.3's donut is the failure AD-10 exists to
      prevent.
    - **`plant_count` is counted**, where the prototype's chip hardcodes "15 US
      plants" over an array holding twenty-nine distinct ones.

    `employer` is counted by the seed file's employer *name* while the service
    counts `claim.employer_id`; the two are one-to-one by construction (the
    migration inserts one `employer` row per distinct name), and counting the
    name here rather than resolving a surrogate id keeps this an oracle over
    the file rather than a second reading of the schema.
    """
    visible = claims_for(persona_name, role)
    return {
        "totalClaims": len(visible),
        "underTreatment": sum(1 for c in visible if c["stage"] == "treatment"),
        "settledClosed": sum(1 for c in visible if c["stage"] == "settled"),
        "highRisk": sum(1 for c in visible if c["severity_score"] >= HIGH_RISK_MIN),
        "totalPaidCents": sum(
            c["paid_indemnity"] + c["paid_medical"] + c["paid_expense"] for c in visible
        ),
        "totalReserveCents": sum(c["reserve"] for c in visible),
        "fraudFlagged": sum(
            1 for c in visible if c["fraud_flag"] and c["fraud_score"] >= FRAUD_FLAG_SCORE_MIN
        ),
        "oshaRecordable": sum(1 for c in visible if c["osha_recordable"]),
        "litigation": sum(1 for c in visible if c["litigation_flag"]),
        "surgeryRequired": sum(1 for c in visible if c["surgery_required"]),
        "employerCount": len({c["employer"] for c in visible}),
        "plantCount": len({c["plant"] for c in visible}),
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


# --- Story 1.6: the glossary, read from its own seed file ----------------
#
# Not an independent restatement like the two above — there is no rule to
# restate. The glossary's whole contract is "the file's rows, verbatim, in
# the file's order", so the file *is* the oracle, and a test that typed 25
# into itself would go stale the day the prototype's GLOSS changed.

GLOSSARY_PATH = SEED_PATH.parent / "glossary_terms.json"


@lru_cache
def _glossary_terms_cached() -> list[dict[str, Any]]:
    with GLOSSARY_PATH.open(encoding="utf-8") as handle:
        terms: list[dict[str, Any]] = json.load(handle)
    return sorted(terms, key=lambda term: term["sort_order"])


def glossary_terms() -> list[dict[str, Any]]:
    """Every seeded term, in the prototype's display order.

    A copy per call, not the cached list itself. `lru_cache` handing out its
    own object means one caller's in-place `.sort()` or `.reverse()` — a
    reasonable thing to write in a test about ordering — silently rewrites
    the expectation for every later test in the session, and the failure
    surfaces in a different file with no visible cause.
    """
    return [dict(term) for term in _glossary_terms_cached()]


# --- Story 2.1: the queue, restated independently ------------------------
#
# Every rule the queue applies, written out here from the story text and the
# prototype rather than imported from `services/` or read out of the JDM
# documents. Same reasoning as `HIGH_RISK_MIN` above, and it matters more
# here than anywhere else so far: the queue's payload is the product of five
# derivations, thirteen weights, a filter predicate, a sort and a marker
# rule, and an oracle that shared any one of them with the implementation
# would validate the other four by accident.

MED_RISK_MIN = 35
SIU_FRAUD_SCORE_MIN = 60
RTW_HASH_MODULUS = 5
PAYMENT_HASH_MODULUS = 3

STAGE_ORDER = ("intake", "investigation", "treatment", "settled")

WEIGHTS = {
    "litigation": 40,
    "siu_review": 35,
    "rtw_blocked": 30,
    "pending_approval": 25,
    "payment_due": 20,
    "surgery": 15,
}
SEVERITY_FACTOR = 0.3
DAYS_OPEN_FACTOR = 0.2
DAYS_OPEN_CAP = 60
SETTLED_PENALTY = -100
MARKER_THRESHOLD = 30
MARKER_COUNT = 3
PENDING_APPROVAL_STATUSES = {"initial", "ch_assessment_process"}
UNDER_TREATMENT = "under_treatment"


def hash_bucket(business_id: str) -> int:
    """The prototype's `hashStr` (line 646), restated."""
    h = 0
    for char in business_id:
        h = (h * 31 + ord(char)) & 0xFFFFFFFF
    return h


def risk_band(severity_score: int) -> str:
    if severity_score >= HIGH_RISK_MIN:
        return "high"
    if severity_score >= MED_RISK_MIN:
        return "med"
    return "low"


def queue_flags(claim: dict[str, Any]) -> dict[str, Any]:
    """The four derived values a card shows, per the prototype's pass."""
    bucket = hash_bucket(claim["claim_id"])
    band = risk_band(claim["severity_score"])
    return {
        "risk": band,
        "siu_review": bool(claim["fraud_flag"]) and claim["fraud_score"] >= SIU_FRAUD_SCORE_MIN,
        "rtw_blocked": (
            claim["stage"] == "treatment"
            and claim["return_status"] == UNDER_TREATMENT
            and (bucket % RTW_HASH_MODULUS == 0 or band == "high")
        ),
        "payment_due": claim["stage"] == "treatment" and bucket % PAYMENT_HASH_MODULUS != 0,
    }


def days_open(claim: dict[str, Any], as_of: date) -> int:
    froi = date.fromisoformat(claim["froi_date"])
    return max((as_of - froi).days, 0)


def expected_score(claim: dict[str, Any], as_of: date) -> float:
    """The prototype's `priorityScore` (lines 1120-1132), restated."""
    flags = queue_flags(claim)
    score = 0.0
    if claim["litigation_flag"]:
        score += WEIGHTS["litigation"]
    if flags["siu_review"]:
        score += WEIGHTS["siu_review"]
    if flags["rtw_blocked"]:
        score += WEIGHTS["rtw_blocked"]
    if claim["status"] in PENDING_APPROVAL_STATUSES:
        score += WEIGHTS["pending_approval"]
    if flags["payment_due"]:
        score += WEIGHTS["payment_due"]
    if claim["surgery_required"]:
        score += WEIGHTS["surgery"]
    score += claim["severity_score"] * SEVERITY_FACTOR
    score += min(days_open(claim, as_of), DAYS_OPEN_CAP) * DAYS_OPEN_FACTOR
    if claim["stage"] == "settled":
        score += SETTLED_PENALTY
    return float(score)


def matches_filter(claim: dict[str, Any], queue_filter: str) -> bool:
    """The prototype's `renderQ` filter mapping (lines 1155-1165), restated."""
    flags = queue_flags(claim)
    selected: bool = {
        "all": True,
        "active": claim["stage"] == "treatment",
        "high_risk": flags["risk"] == "high",
        "fraud": bool(claim["fraud_flag"]),
        "litigation": bool(claim["litigation_flag"]),
        "payment_due": flags["payment_due"],
        "surgery": bool(claim["surgery_required"]),
        "siu": flags["siu_review"],
    }[queue_filter]
    return selected


def expected_queue(
    persona_name: str,
    role: str,
    queue_filter: str = "all",
    as_of: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """`{stage: [{claimId, priorityMarker, …}]}` for a persona's seeded book.

    Ordering is `(-score, claim_id)` — the tie-break included, because ties
    are common in the settled group and an oracle without it would disagree
    with a correct implementation about which of two equal claims comes
    first.
    """
    today = as_of or datetime.now(UTC).date()
    visible = [c for c in claims_for(persona_name, role) if matches_filter(c, queue_filter)]

    groups: dict[str, list[dict[str, Any]]] = {}
    for stage in STAGE_ORDER:
        members = sorted(
            (c for c in visible if c["stage"] == stage),
            key=lambda c: (-expected_score(c, today), c["claim_id"]),
        )
        marked = 0
        cards = []
        for claim in members:
            score = expected_score(claim, today)
            marker = marked < MARKER_COUNT and score > MARKER_THRESHOLD
            if marker:
                marked += 1
            cards.append(
                {
                    "claimId": claim["claim_id"],
                    "priorityScore": score,
                    "priorityMarker": marker,
                    "daysOpen": days_open(claim, today),
                    "stage": stage,
                    **{
                        "risk": queue_flags(claim)["risk"],
                        "siuReview": queue_flags(claim)["siu_review"],
                        "rtwBlocked": queue_flags(claim)["rtw_blocked"],
                        "paymentDue": queue_flags(claim)["payment_due"],
                    },
                }
            )
        groups[stage] = cards
    return groups


# --- Story 2.2: the case file, restated independently --------------------

CASE_FILE_PATH = SEED_PATH.parent / "case_file_seed.json"


@lru_cache
def _case_file_cached() -> dict[str, Any]:
    with CASE_FILE_PATH.open() as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def timeline_events_for(claim_business_id: str) -> list[dict[str, Any]]:
    """A claim's seeded timeline, in the order the migration inserts it.

    Copies, for `glossary_terms`' reason: the order is what most of these
    assertions are about, and an in-place sort in one test must not rewrite
    the expectation for the next.
    """
    return [
        dict(event)
        for event in _case_file_cached()["timeline_events"]
        if event["claim_id"] == claim_business_id
    ]


def documents_for(claim_business_id: str) -> list[dict[str, Any]]:
    return [
        dict(document)
        for document in _case_file_cached()["documents"]
        if document["claim_id"] == claim_business_id
    ]


def all_timeline_events() -> list[dict[str, Any]]:
    return [dict(event) for event in _case_file_cached()["timeline_events"]]


def all_documents() -> list[dict[str, Any]]:
    return [dict(document) for document in _case_file_cached()["documents"]]


# The intake checklist's required set, restated from the story text ("the
# prototype's doc-type set FROI/INCIDENT/MEDAUTH/WAGE") rather than read out
# of the JDM document the service loads — an oracle that shared the document
# would agree with a mis-parsed parameter block.
REQUIRED_INTAKE_DOC_TYPES = ("froi", "incident", "medauth", "wage")


def expected_intake_checklist(claim_business_id: str) -> list[dict[str, Any]]:
    """Received/Missing per required document type, in the required order."""
    present = {document["doc_type"] for document in documents_for(claim_business_id)}
    return [
        {"docType": doc_type, "received": doc_type in present}
        for doc_type in REQUIRED_INTAKE_DOC_TYPES
    ]


# The treatment overview shows the last six events (the prototype's
# `.slice(-6)`), restated here as a number rather than imported.
RECENT_TIMELINE_COUNT = 6


def expected_recent_timeline(claim_business_id: str) -> list[dict[str, Any]]:
    return timeline_events_for(claim_business_id)[-RECENT_TIMELINE_COUNT:]


# Migration 0013's display-string -> token map, loaded by path because
# `data/versions/` is an Alembic script directory rather than a package.
_RECOVERY_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "versions"
    / "20260812_0013_recovery_window_enum.py"
)
_recovery_spec = importlib.util.spec_from_file_location("_m0013", _RECOVERY_MIGRATION)
assert _recovery_spec and _recovery_spec.loader
_recovery_module = importlib.util.module_from_spec(_recovery_spec)
_recovery_spec.loader.exec_module(_recovery_module)


def recovery_token(display: str) -> str:
    """The `RecoveryWindow` token a seeded display string converts to.

    Story 1.2 seeded `claim.recovery` as the prototype's own text and Story
    2.3's code review turned the column into an enum, so an oracle reading
    the seed file has to apply the same conversion the migration did. Reading
    it *from* the migration rather than restating it keeps the two from
    drifting — and a value the migration cannot map is a `KeyError` here,
    which is the same refusal `upgrade()` makes.
    """
    token: str = _recovery_module.TEXT_TO_TOKEN[display]
    return token


# --- Story 2.4: the injury diagram, restated independently ---------------
#
# `treatment_plan_step` and the four prognosis columns come from
# `seed_data.json`'s `deferred` block — the part of the prototype's dataset
# Story 1.2 extracted but had no columns for. These helpers read the same
# file the migration reads and re-do the transformation independently, which
# is the discipline every other oracle in this module follows.


@lru_cache
def _deferred_by_claim() -> dict[str, dict[str, Any]]:
    return {row["claim_id"]: row for row in seed()["deferred"]}


def treatment_plan_for(claim_business_id: str) -> list[str]:
    """A claim's treatment-plan steps, in the array's order.

    The array index *is* the step number (1-based, as the card renders it);
    the migration writes it out as a column so the ordering does not depend
    on insertion order the way `timeline_event`'s does.
    """
    row = _deferred_by_claim().get(claim_business_id)
    assert row is not None, f"no seeded deferred block for {claim_business_id!r}"
    steps: list[str] = row["treatment_plan"]
    return steps


def prognosis_for(claim_business_id: str) -> dict[str, str]:
    """A claim's four prognosis strings, keyed as the wire keys them.

    The seed file's keys (`mmi`, `rtw`, `impairment`, `litigation`) happen to
    be the response's too, so no renaming is needed — asserted by use rather
    than restated, since a change on either side fails the comparison.
    """
    row = _deferred_by_claim().get(claim_business_id)
    assert row is not None, f"no seeded deferred block for {claim_business_id!r}"
    prognosis: dict[str, str] = dict(row["prognosis"])
    return prognosis


# --- Story 4.1: the two demo meetings per handler persona ----------------
#
# Recomputed from `seed_data.json` rather than read back from the migration
# that wrote them, which is what makes this an oracle: migration 0033 picks
# the two lowest-sorted claim business ids in each handler's *employer* scope,
# and this restates that rule independently. If the two ever disagree, one of
# them is wrong and the test says so.

#: The two demo meetings' types, in the order 0033 inserts them — the mapping
#: of the prototype's two free-text titles onto `MeetingType` members. See
#: that migration's docstring on why they are mapped rather than copied.
DEMO_MEETING_TYPES = ("rtw_conference", "claim_review_supervisor")

HANDLER_ROLE = "handler"


def handler_personas() -> list[str]:
    """Every seeded persona with the `handler` role, in the seed file's order."""
    return [user["name"] for user in seed()["app_users"] if user["role"] == HANDLER_ROLE]


def expected_meetings_for(persona_name: str) -> list[dict[str, str]]:
    """The two meetings migration 0033 seeds for one handler.

    `(claim_id, meeting_type)` pairs — the date is the migration's run date and
    therefore not something a static oracle can name.
    """
    claim_ids = sorted(claim["claim_id"] for claim in claims_for(persona_name, HANDLER_ROLE))
    assert len(claim_ids) >= len(DEMO_MEETING_TYPES), (
        f"{persona_name!r} has {len(claim_ids)} scoped claims; "
        "Story 4.1 AC 6 needs two for its two demo meetings"
    )
    return [
        {"claim_id": claim_id, "meeting_type": meeting_type}
        for claim_id, meeting_type in zip(claim_ids, DEMO_MEETING_TYPES, strict=False)
    ]
