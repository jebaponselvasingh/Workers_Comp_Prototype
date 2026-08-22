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
from datetime import UTC, date, datetime, timedelta
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


# --- Story 5.2: handler benchmarking, restated independently -------------
#
# The eight parameters of the `handler_performance` document, written out here
# rather than loaded from it — `HIGH_RISK_MIN`'s discipline over a whole rule
# block. It matters more here than for any oracle since the queue's: a benchmark
# row is the product of three segment averages, a substitution rule, a
# percentage deviation, a three-way band, a weighted blend, a cap, a second
# three-way band and a sort, and an oracle that shared any one of them with the
# implementation would be validating the rest by accident.
#
# `COMPLEXITY_HIGH_MIN` is 65 and so is `HIGH_RISK_MIN`, and the two are
# deliberately separate constants: one bands a claim's severity score, the other
# bands a handler's blended mix. A single shared constant here would make the
# most plausible way to get this story wrong — pointing the complexity band at
# the risk threshold — invisible to every assertion below.
ON_TRACK_DEVIATION_PCT_MAX = -8
ATTENTION_DEVIATION_PCT_MIN = 8
SEVERITY_WEIGHT_BP = 10_000
SURGERY_RATE_WEIGHT_BP = 1_000
LITIGATION_RATE_WEIGHT_BP = 1_500
COMPLEXITY_SCORE_MAX = 100
COMPLEXITY_HIGH_MIN = 65
COMPLEXITY_MED_MIN = 40

BASIS_POINTS_PER_UNIT = 10_000
PERCENT = 100

#: The three cycle-time segments a composite is the sum of, in the order the
#: prototype adds them.
CYCLE_SEGMENTS = ("pick", "approve", "settle")

#: The precision the *composite* is published at — one decimal, which is finer
#: than the whole days the settle tile above is displayed at. That difference is
#: the rule this oracle exists to pin: the composite is summed from the
#: UNROUNDED segment means and rounded once, so the ranking cannot be decided by
#: a display convention. Summing the published tiles instead admits half a day of
#: error, which is larger than the gap between four of the six seeded handlers
#: and reorders two of them.
COMPOSITE_DECIMALS = 1


def _quantized(value: Decimal, decimals: int = 0) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _handler_ordinal(name: str) -> int:
    """The handler's position in `app_user.id` order, restated from the seed file.

    The implementation's third sort key is `handler_id`, and this oracle used to
    stop at the second on the grounds that the seed has no two handlers sharing a
    name. That is true and is not the point: the spec asks for **both oracles
    carrying the same tie-break**, and a key that is merely never exercised is
    still a key an implementation could drop without either oracle noticing.

    Ids are recoverable here without querying: migration 0004 inserts
    `app_users` in the seed file's order, so a handler's index in that list is
    its id order. Restated rather than imported, like every other rule in this
    file — if the migration ever stopped inserting in order, this would have to
    be re-derived along with everything else that assumes it.
    """
    for index, user in enumerate(seed()["app_users"]):
        if user["name"] == name and user["role"] == HANDLER_ROLE:
            return index
    raise AssertionError(f"no seeded handler {name!r}")


def _cycle_segments(claims: list[dict[str, Any]]) -> dict[str, Decimal | None]:
    """The three durations a composite is the sum of, **unrounded**.

    The metric definitions restated from Story 1.5's story text: pick and
    approve average over *every* claim carrying the duration, settle averages
    over *settled* claims carrying it. `None` for an empty segment, never zero.

    Exact quotients rather than the tiles' rounded figures. `expected_sla_strip`
    above rounds each one to its own display precision because that is what a
    tile shows and what its verdict is decided on; a composite is a quantity
    rows are *compared* by, and rounding before comparing hands the ordering to
    the renderer.
    """
    settled = [c for c in claims if c["stage"] == "settled"]
    values = {
        "pick": [c["sla_pick_days"] for c in claims if c["sla_pick_days"] is not None],
        "approve": [c["sla_approve_days"] for c in claims if c["sla_approve_days"] is not None],
        "settle": [c["settlement_days"] for c in settled if c["settlement_days"] is not None],
    }
    return {
        key: (Decimal(sum(members)) / Decimal(len(members))) if members else None
        for key, members in values.items()
    }


def _composite(
    segments: dict[str, Decimal | None], fallback: dict[str, Decimal | None]
) -> Decimal | None:
    """The three unrounded segments added up, substituting the portfolio's for a gap.

    The generalised rule, restated: `renderSV` substitutes only the settle
    average and lets a missing pick or approve fall through as zero. `None` when
    a segment is missing from both, which never happens on the seeded book.
    """
    total = Decimal(0)
    for key in CYCLE_SEGMENTS:
        value = segments[key] if segments[key] is not None else fallback[key]
        if value is None:
            return None
        total += value
    return total


def _rtw_pct(claims: list[dict[str, Any]]) -> float | None:
    """Settled claims that came back fully recovered, over all settled claims."""
    settled = [c for c in claims if c["stage"] == "settled"]
    if not settled:
        return None
    return _mean([100 if c["return_status"] == FULLY_RECOVERED else 0 for c in settled], 0)


def _complexity(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """The prototype's blend (line 1022), restated in basis points.

    `min(100, round(avgSev + 10 * surgeryRate + 15 * litigationRate))`, with the
    two rates as fractions of the book — written here as one exact quotient so
    the oracle cannot disagree with the implementation about float ordering.
    """
    count = len(claims)
    weighted = (
        SEVERITY_WEIGHT_BP * sum(c["severity_score"] for c in claims)
        + SURGERY_RATE_WEIGHT_BP * sum(1 for c in claims if c["surgery_required"]) * PERCENT
        + LITIGATION_RATE_WEIGHT_BP * sum(1 for c in claims if c["litigation_flag"]) * PERCENT
    )
    score = min(
        COMPLEXITY_SCORE_MAX,
        int(_quantized(Decimal(weighted) / Decimal(count * BASIS_POINTS_PER_UNIT))),
    )
    if score >= COMPLEXITY_HIGH_MIN:
        band = "high"
    elif score >= COMPLEXITY_MED_MIN:
        band = "med"
    else:
        band = "low"
    return {"complexityScore": score, "complexityBand": band}


def _cycle_status(deviation_pct: int) -> str:
    """The prototype's `statusOf` (line 1032), restated — both edges inclusive."""
    if deviation_pct <= ON_TRACK_DEVIATION_PCT_MAX:
        return "on_track"
    if deviation_pct >= ATTENTION_DEVIATION_PCT_MIN:
        return "attention"
    return "watch"


def expected_handler_benchmarks(persona_name: str, role: str) -> dict[str, Any]:
    """The ranked handler table for a persona's seeded book, as the wire keys it.

    **Grouped over the persona's scoped claims, never over the roster**, which is
    the property the whole story turns on: Kaya Johnson is assigned John Deere in
    `user_employer_assignment` and handles none of its seven claims, so Ken
    Stoker's table must not contain her. An oracle built from `employers_of`
    would list her with an empty book and would agree with the wrong
    implementation.

    Ordering is `(composite, handlerName, handlerId)` ascending on the **exact**
    composite — fastest first, ties broken by name and then by id — and `rank` is
    the 1-based position in that order. All three keys, matching the
    implementation's: the seed has no two handlers sharing a display name, so the
    third never decides anything here, but an oracle that carried two keys would
    agree with an implementation that had quietly dropped the third. Ids come
    from `_handler_ordinal`, which restates the seed file's `app_users` order.

    `rank` is `None` wherever `compositeDays` is, which the seeded book never
    reaches (every seeded handler has settled claims) and which is restated here
    anyway so the oracle would disagree with an implementation that numbered
    unrankable rows.
    """
    visible = claims_for(persona_name, role)
    portfolio_segments = _cycle_segments(visible)
    portfolio_composite = _composite(portfolio_segments, portfolio_segments)

    by_handler: dict[str, list[dict[str, Any]]] = {}
    for claim in visible:
        by_handler.setdefault(claim["handler"], []).append(claim)

    rows: list[tuple[Decimal | None, str, int, dict[str, Any]]] = []
    for handler, claims in by_handler.items():
        composite = _composite(_cycle_segments(claims), portfolio_segments)
        if composite is None or portfolio_composite is None:
            deviation: int | None = None
        elif portfolio_composite == 0:
            deviation = 0
        else:
            deviation = int(
                _quantized((composite - portfolio_composite) / portfolio_composite * PERCENT)
            )
        rows.append(
            (
                composite,
                handler,
                _handler_ordinal(handler),
                {
                    # Story 5.5 publishes the id a drill-through link filters
                    # on. `_app_user_id` rather than `_handler_ordinal`: the
                    # ordinal is the 0-based sort tie-break above, and the wire
                    # carries the 1-based surrogate key.
                    "handlerId": _app_user_id(handler, HANDLER_ROLE),
                    "handlerName": handler,
                    "caseCount": len(claims),
                    "compositeDays": (
                        None
                        if composite is None
                        else float(_quantized(composite, COMPOSITE_DECIMALS))
                    ),
                    "rtwPct": _rtw_pct(claims),
                    "pendingApprovals": sum(
                        1 for c in claims if c["status"] in PENDING_APPROVAL_STATUSES
                    ),
                    "deviationPct": deviation,
                    "cycleStatus": None if deviation is None else _cycle_status(deviation),
                    **_complexity(claims),
                },
            )
        )

    rows.sort(key=lambda row: (row[0] is None, row[0] or Decimal(0), row[1], row[2]))
    composites = [composite for composite, _handler, _id, _row in rows if composite is not None]
    slowest = max(composites, default=None)

    items = []
    ordinal = 0
    for composite, _handler, _id, row in rows:
        if composite is None or slowest is None:
            # No composite, no ordinal: a `#` beside a row of em dashes would be
            # a ranking claim over rows that are in name order.
            items.append({"rank": None, "cycleSpeedPct": None, **row})
            continue
        ordinal += 1
        bar = PERCENT if slowest == 0 else int(_quantized(composite / slowest * PERCENT))
        items.append({"rank": ordinal, "cycleSpeedPct": bar, **row})

    rankable = [item for item in items if item["rank"] is not None]
    return {
        "items": items,
        "portfolioCompositeDays": (
            None
            if portfolio_composite is None
            else float(_quantized(portfolio_composite, COMPOSITE_DECIMALS))
        ),
        "leader": rankable[0]["handlerName"] if rankable else None,
        "laggard": rankable[-1]["handlerName"] if rankable else None,
    }


# --- Story 5.3: the portfolio analytics charts, restated independently ----
#
# Six distributions, two orderings and two limits, all written out here from the
# story text and the prototype (`renderSV`, lines 1003-1010) rather than
# imported from `services/worklist/charts.py`. `HIGH_RISK_MIN`'s discipline, and
# it earns its keep twice over on this story:
#
# - The severity donut bands on the *same* rule as the High Risk card, so the
#   oracle deliberately reuses `risk_band` above — the assertion that matters is
#   an equality between two aggregates, and an oracle with its own third
#   banding rule would be checking neither of them against the document.
# - The two ranked series cut *inside a tie* on the seeded portfolio (injury
#   ranks 7-11 all count five, state ranks 9-11 all count five), so an oracle
#   without the tie-break would disagree with a correct implementation about
#   which five claims are on screen — which is precisely the failure the
#   tie-break exists to prevent, reproduced in the test.

#: The two chart caps, restated. Module constants on the service side rather
#: than rule-document parameters — a chart's category count is UX-DR7's shape,
#: not a business rule — so they are written out here rather than loaded, the
#: same way `SLA_TARGETS` restates deployment configuration.
INJURY_TYPE_LIMIT = 8
STATE_LIMIT = 10

#: The declaration order of the three enums the donuts and the recovery bars
#: group on, restated from `data/models/enums.py`. Enum-keyed series publish in
#: this order rather than in count order: a legend has a fixed reading order and
#: must not reshuffle when two categories cross. `STAGE_ORDER` above is the same
#: tuple for `Stage` and is reused rather than restated a second time.
RISK_BAND_ORDER = ("high", "med", "low")
RECOVERY_STATUS_ORDER = (
    "under_treatment",
    "returned_and_under_therapy",
    "returned_and_fully_recovered",
)


def employer_short_names() -> dict[str, str]:
    """`{employer name: short_name}` from the seed file.

    The employer bars are labelled with `employer.short_name` — "Toyota", not
    "Toyota Motor Manufacturing" — because that is the column
    `select_claim_columns_with_employer` joins and `select_queue_rows` already
    uses. The prototype produces the same labels by stripping four suffixes off
    the full name with a chain of `String.replace` calls (`renderSV`, line
    1005), which is a client-side canonicalization of exactly the kind AD-1
    removes; the short name is a column and this is what it is for.
    """
    return {row["name"]: row["short_name"] for row in seed()["employers"]}


def _employer_id(employer_name: str) -> int:
    """The `employer.id` a seeded employer holds, restated from the file's order.

    `_handler_ordinal`'s argument for a surrogate key: migration 0004 inserts
    `employers` in the seed file's order against an identity column, so the
    id is the 1-based index. Restated rather than queried, because an oracle
    that read the id back from the database would agree with an implementation
    that had joined the wrong row.
    """
    for index, row in enumerate(seed()["employers"], start=1):
        if row["name"] == employer_name:
            return index
    raise AssertionError(f"no seeded employer {employer_name!r}")


def _declared_series(counts: dict[str, int], order: tuple[str, ...]) -> dict[str, Any]:
    """An enum-keyed distribution: declaration order, absent categories omitted.

    Omitted rather than zeroed, which is the contract the UI is built against:
    Jennifer Park's book has no `intake` stage, and a zero row would put an
    invisible slice in a donut. `truncated` is False and `limit` is None —
    an enum series shows every category it has.
    """
    return {
        "items": [{"key": key, "count": counts[key]} for key in order if key in counts],
        "total": sum(counts.values()),
        "totalCategories": len(counts),
        "truncated": False,
        "limit": None,
    }


def _ranked_series(counts: dict[str, int], limit: int) -> dict[str, Any]:
    """A free-text distribution: count descending, then label ascending, then cut.

    The tie-break is restated rather than shared with the implementation because
    it is the rule most likely to be quietly dropped — a sort on the count alone
    passes every "the top three are right" assertion and reorders the tail on a
    different Python build.
    """
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    return {
        "items": [{"label": label, "count": count} for label, count in ordered[:limit]],
        "total": sum(counts.values()),
        "totalCategories": len(counts),
        "truncated": len(counts) > limit,
        "limit": limit,
    }


def expected_portfolio_charts(persona_name: str, role: str) -> dict[str, Any]:
    """The six distributions for a persona's seeded book, as the wire keys them.

    The SLA strip is deliberately **not** here: it is `expected_sla_strip`
    above, unchanged, because the dashboard tiles and the top-bar strip are two
    renderings of one server value and the oracle says so by having one entry
    for them. A second copy in this function would let the two drift in the
    expectations even while the implementation kept them identical.

    Money is `paid_indemnity + paid_medical + paid_expense`, matching
    `expected_portfolio_summary`'s `totalPaidCents` term deliberately: the
    employer bars sum the same derivation as the Total Paid card, including its
    recorded exclusion of `status = paid` bills, and the test that the two agree
    is only meaningful if the oracle computes them the same way.
    """
    visible = claims_for(persona_name, role)
    short_names = employer_short_names()

    stages: dict[str, int] = {}
    severities: dict[str, int] = {}
    recoveries: dict[str, int] = {}
    injuries: dict[str, int] = {}
    states: dict[str, int] = {}
    paid: dict[str, int] = {}

    for claim in visible:
        stages[claim["stage"]] = stages.get(claim["stage"], 0) + 1
        band = risk_band(claim["severity_score"])
        severities[band] = severities.get(band, 0) + 1
        recoveries[claim["return_status"]] = recoveries.get(claim["return_status"], 0) + 1
        injuries[claim["injury_type"]] = injuries.get(claim["injury_type"], 0) + 1
        states[claim["state"]] = states.get(claim["state"], 0) + 1
        paid[claim["employer"]] = (
            paid.get(claim["employer"], 0)
            + claim["paid_indemnity"]
            + claim["paid_medical"]
            + claim["paid_expense"]
        )

    # Paid descending, then label ascending — the employer series' ordering, on
    # the *short* name, because that is the label the ties are broken by on the
    # wire. Uncapped: the employers in a book are bounded by the assignment.
    by_employer = sorted(paid.items(), key=lambda entry: (-entry[1], short_names[entry[0]]))

    return {
        "byStage": _declared_series(stages, STAGE_ORDER),
        "bySeverity": _declared_series(severities, RISK_BAND_ORDER),
        "byRecoveryStatus": _declared_series(recoveries, RECOVERY_STATUS_ORDER),
        "byInjuryType": _ranked_series(injuries, INJURY_TYPE_LIMIT),
        "byEmployer": {
            "items": [
                {
                    "employerId": _employer_id(name),
                    "label": short_names[name],
                    "paidCents": cents,
                }
                for name, cents in by_employer
            ],
            "total": sum(paid.values()),
            "totalCategories": len(paid),
            "truncated": False,
            "limit": None,
        },
        "byState": _ranked_series(states, STATE_LIMIT),
    }


# --- Story 5.4: the priority claims worklist, restated independently ------
#
# Three rules and an ordering, all built on blocks already in this file rather
# than restated a second time — which is a departure from `HIGH_RISK_MIN`'s
# discipline and a deliberate one, argued per rule:
#
# - The **ordering** reuses Story 2.1's `expected_score` and its `(-score,
#   claim_id)` key, because the assertion this story is actually about is that
#   the worklist and the queue rank identically. A third scoring rule written
#   here would check the worklist against itself and would agree with an
#   implementation that had quietly re-weighted, so long as this oracle
#   re-weighted the same way.
# - The **fraud arm** reuses Story 5.1's `FRAUD_FLAG_SCORE_MIN` for the same
#   reason one level down: the population's fraud arm and the Fraud Flags card
#   count one rule, and an oracle with its own number could not tell the two
#   apart. It is still deliberately **not** `SIU_FRAUD_SCORE_MIN` — 13 seeded
#   claims against 9 — which is the single most plausible way to get this
#   population wrong.
# - The **cap** is written out, because it is this story's own parameter and
#   nothing else in this file knows it.
#
# What this oracle deliberately does **not** predict is `next_best_action`.
# That column's oracle is Story 3.5's generator itself — the test asserts each
# row against `generate_actions(...)[0].label` for the same claim, inputs and
# `as_of`, which is an equality between two call sites rather than a
# transliteration of eleven trigger rules into a fourth language. Restating the
# generator here would be several hundred lines that agree with the
# implementation exactly as often as they were copied from it.

SUPERVISOR_WORKLIST_CAP = 30
SUPERVISOR_WORKLIST_PAGE_LIMIT = 10

TREATMENT_STAGE = "treatment"


def qualifies_for_worklist(claim: dict[str, Any]) -> bool:
    """The population: active treatment ∪ fraud-flagged ∪ litigation-flagged.

    One `or` rather than three filters, restating the union the service spells
    the same way — a claim matching two arms is in the set once, and a claim
    matching only the third is in it at all.

    "Active treatment" is the **stage**, not the status: Story 5.1's ruling,
    which `expected_portfolio_summary` above records at length and which is
    worth 62 seeded claims against 54 on the settled side of the same rule.
    """
    return (
        claim["stage"] == TREATMENT_STAGE
        or (claim["fraud_flag"] and claim["fraud_score"] >= FRAUD_FLAG_SCORE_MIN)
        or bool(claim["litigation_flag"])
    )


def expected_priority_claims(
    persona_name: str,
    role: str,
    as_of: date | None = None,
    cap: int = SUPERVISOR_WORKLIST_CAP,
) -> dict[str, Any]:
    """`{population, total, claimIds}` for a persona's seeded book.

    `total` is the size of the population **before** the cap — the number the
    table's caption reads "of" — and `claimIds` is the capped, scorer-ordered
    sequence the rows must appear in. Both, because the two are different facts
    and a test that had only the second could not tell "the cap applied" from
    "the book was that small".

    Ordering is `(-score, claim_id)`, the tie-break included, for
    `expected_queue`'s reason and more sharply here: this list is ungrouped, so
    every tie in the whole book competes in one sequence rather than within a
    stage, and the cursor's stability depends on the key being total.

    The cap is a parameter so a test can demonstrate the rules tier — supersede
    the document with a smaller number, pass the same number here, and the two
    move together or the assertion fails. That is
    `test_a_superseded_document_with_a_smaller_cap_shortens_the_table`, which is
    the one caller that passes it.

    `population` is the whole ordered sequence before the cut, published beside
    the capped one so a test can assert what the cap *removed* rather than only
    what it kept.
    """
    today = as_of or datetime.now(UTC).date()
    population = [c for c in claims_for(persona_name, role) if qualifies_for_worklist(c)]
    ordered = sorted(population, key=lambda c: (-expected_score(c, today), c["claim_id"]))
    return {
        "population": [c["claim_id"] for c in ordered],
        "total": len(population),
        "claimIds": [c["claim_id"] for c in ordered[:cap]],
    }


# --- Story 5.5: the dashboard drill-through, restated independently -------
#
# Twelve predicates, an ordering and a page size, built on blocks already in
# this file rather than restated a second time — `expected_priority_claims`'
# departure from `HIGH_RISK_MIN`'s discipline, and here the argument is the
# *whole story* rather than an exception to it.
#
# What this endpoint promises is that the list a KPI card opens holds exactly
# the claims that card counted. An oracle that wrote its own twelfth banding
# rule could not check that: it would agree with a drill-through that had
# quietly banded differently from the card, so long as this file had banded the
# same way. So every predicate below reuses the block that already restates the
# surface it reconciles with — `risk_band` for the severity donut and the High
# Risk card, `FRAUD_FLAG_SCORE_MIN` for the Fraud Flags card (deliberately
# **not** `SIU_FRAUD_SCORE_MIN`: 13 seeded claims against 9),
# `qualifies_for_worklist` for the priority worklist's population, and the stage
# column for the settlement donut (62 seeded claims against `status`'s 54).
#
# The ordering reuses Story 2.1's `expected_score` and its `(-score, claim_id)`
# key for `expected_priority_claims`' reason, sharpened: this list is ungrouped
# *and* uncapped, so every tie in the whole filtered book competes in one
# sequence and the cursor's stability depends on the key being total.
#
# The two id-valued facets need surrogate keys, and both are recoverable from
# the file's own order — see `_employer_id` above and `_app_user_id` below.

#: The drill-through's page size: `priority_weights.pageLimit`, restated.
#:
#: Written out here rather than loaded for `HIGH_RISK_MIN`'s reason, and it is
#: load-bearing on the full portfolio: 100 claims at 50 a page is a two-page
#: walk, which is what exercises the cursor end to end. The worklist's own
#: `SUPERVISOR_WORKLIST_PAGE_LIMIT` above is a *different* number from a
#: different document, and the two must not be confused — that is why this one
#: carries the surface's name.
DRILL_PAGE_LIMIT = 50


def _app_user_id(name: str, role: str) -> int:
    """An `app_user.id`, restated from the seed file's order.

    `_employer_id`'s argument: migration 0004 inserts `app_users` in the seed
    file's order against an identity column, so the id is the 1-based index.
    Restated rather than queried, because an oracle that read the id back from
    the database would agree with an implementation that had joined the wrong
    row.

    Note the off-by-one against `_handler_ordinal` above, which is deliberate
    and not a duplicate: that helper returns a *position* used as a sort
    tie-break and is 0-based; this one returns the surrogate key a
    `filter[handlerId]` carries.
    """
    for index, user in enumerate(seed()["app_users"], start=1):
        if user["name"] == name and user["role"] == role:
            return index
    raise AssertionError(f"no seeded persona {name!r}/{role!r}")


def handler_id_of(handler_name: str) -> int:
    """The `filter[handlerId]` value for a seeded handler, by name."""
    return _app_user_id(handler_name, HANDLER_ROLE)


def employer_id_of(employer_name: str) -> int:
    """The `filter[employerId]` value for a seeded employer, by its full name."""
    return _employer_id(employer_name)


def _drill_matches(claim: dict[str, Any], key: str, value: Any) -> bool:
    """One facet, restated — each against the surface it has to reconcile with.

    A mapping rather than a chain of `if`s so the twelve read as one table, the
    way the service's `_PREDICATES` does: an oracle that expressed the same
    twelve as branches would be checkable a facet at a time and never as a set.
    """
    answers: dict[str, bool] = {
        # The stage column, never `status` — Story 5.1's ruling.
        "stage": claim["stage"] == value,
        # `risk_band`, the same restatement the High Risk card's oracle uses.
        "severity_band": risk_band(claim["severity_score"]) == value,
        # The *review* threshold, never the SIU referral one.
        "fraud_flagged": (
            bool(claim["fraud_flag"]) and claim["fraud_score"] >= FRAUD_FLAG_SCORE_MIN
        )
        == value,
        "litigation": bool(claim["litigation_flag"]) == value,
        "surgery": bool(claim["surgery_required"]) == value,
        "osha_recordable": bool(claim["osha_recordable"]) == value,
        "recovery_status": claim["return_status"] == value,
        # The exact stored string, with no trim, case-fold or merge — the
        # ruling the injury-type and state bars are folded under.
        "injury_type": claim["injury_type"] == value,
        "state": claim["state"] == value,
        "employer_id": _employer_id(claim["employer"]) == value,
        "handler_id": handler_id_of(claim["handler"]) == value,
        # The worklist's population, before its cap.
        "priority": qualifies_for_worklist(claim) == value,
        # Story 7.1's two. `fraud_band` bands the score **alone** — no
        # `fraud_flag` conjunct — so it is neither of the two fraud rules above
        # it, and `siu_review` is the queue's referral rule, which is neither of
        # the other two either. Three facets, three restatements, one column
        # pair: an oracle that shared a number between any two of them would
        # agree with an implementation that had collapsed the same pair.
        "fraud_band": fraud_band(claim["fraud_score"]) == value,
        "siu_review": siu_review(claim) == value,
    }
    if key not in answers:
        raise AssertionError(f"no seeded oracle for filter {key!r}")
    return answers[key]


def expected_drill_claims(
    persona_name: str,
    role: str,
    as_of: date | None = None,
    **filters: Any,
) -> dict[str, Any]:
    """`{total, claimIds, pages}` for one persona's book under one filter set.

    `total` is the whole filtered population — there is **no cap on this list**,
    which is the difference from `expected_priority_claims` and the property the
    reconciliation tests rest on: a drill-through's count has to equal the
    number on the card that opened it. `claimIds` is every one of them in
    ranked order, and `pages` is that sequence cut into `DRILL_PAGE_LIMIT`-sized
    pages, so a walk can be asserted page by page rather than only in aggregate.

    Filters arrive as snake_case keyword arguments matching the service's field
    names (`severity_band=...`, `employer_id=...`), and an unknown one raises
    rather than being ignored: an oracle that silently dropped a facet would
    agree with an implementation that had dropped the same one.
    """
    today = as_of or datetime.now(UTC).date()
    visible = [
        claim
        for claim in claims_for(persona_name, role)
        if all(_drill_matches(claim, key, value) for key, value in filters.items())
    ]
    ordered = sorted(visible, key=lambda c: (-expected_score(c, today), c["claim_id"]))
    claim_ids = [claim["claim_id"] for claim in ordered]
    return {
        "total": len(claim_ids),
        "claimIds": claim_ids,
        "pages": [
            claim_ids[start : start + DRILL_PAGE_LIMIT]
            for start in range(0, max(len(claim_ids), 1), DRILL_PAGE_LIMIT)
        ],
    }


# --- Story 7.1: the fraud workspace, restated independently ---------------
#
# Three fraud rules over one column pair, and this block is where the seed
# stops being able to tell them apart — which is the point of restating them
# rather than reaching for one:
#
#   siu_review    = fraud_flag and fraud_score >= SIU_FRAUD_SCORE_MIN   (60)
#   fraud_flagged = fraud_flag and fraud_score >= FRAUD_FLAG_SCORE_MIN  (55)
#   fraud_band    = band(fraud_score) against MED (35) and HIGH (55)
#
# On the seeded portfolio `fraud_flag` and `fraud_score >= 55` coincide exactly,
# so `fraud_flagged` and the `high` band happen to name the same thirteen claims
# and an oracle that shared one number between them would still agree with an
# implementation that had collapsed the two. It is a fact about the dataset, and
# the reason `tests/test_fraud_analytics.py` also folds synthetic projections
# where the three disagree.
#
# `FRAUD_BAND_HIGH_MIN` is written out separately from `FRAUD_FLAG_SCORE_MIN`
# above even though both are 55, and `FRAUD_BAND_MED_MIN` separately from
# `MED_RISK_MIN` even though both are 35. Both duplications are deliberate:
# sharing either name would make the oracle unable to notice the day the
# document moved one of them, which is the single most plausible way to break
# this story.

FRAUD_BAND_HIGH_MIN = 55
FRAUD_BAND_MED_MIN = 35

#: The `FraudBand` declaration order — low to high, which is a *distribution's*
#: reading order and deliberately the opposite of `RISK_BAND_ORDER`'s high-first
#: legend order. Every member is always present: the vocabulary is a rule's, so
#: an empty band is a fact about the portfolio rather than a category it lacks.
FRAUD_BAND_ORDER = ("low", "medium", "high")

#: The injury-type cut the rate breakdown applies, restated. The same number
#: `INJURY_TYPE_LIMIT` above carries for the injury-type *chart*, and written out
#: twice on purpose: two views of one dimension must not disagree about where the
#: tail starts, and an oracle sharing one constant could not notice if they did.
FRAUD_INJURY_TYPE_LIMIT = 8


def fraud_band(fraud_score: int) -> str:
    """`fraud_score` banded — the score alone, with no `fraud_flag` conjunct.

    That absence is the whole rule. `queue_flags` above conjoins the flag for
    `siu_review`, and `_drill_matches` does the same for `fraud_flagged`; this
    one does not, so a claim nobody triaged still has a band. It is what lets
    the analyst's distribution answer "how much of this book scores high and was
    never flagged", and it is the difference an oracle that reused either of the
    other two would have hidden.
    """
    if fraud_score >= FRAUD_BAND_HIGH_MIN:
        return "high"
    if fraud_score >= FRAUD_BAND_MED_MIN:
        return "medium"
    return "low"


def fraud_flagged(claim: dict[str, Any]) -> bool:
    """The dashboard's fraud *review* rule, restated — never the referral one."""
    return bool(claim["fraud_flag"]) and claim["fraud_score"] >= FRAUD_FLAG_SCORE_MIN


def siu_review(claim: dict[str, Any]) -> bool:
    """The queue's SIU *referral* rule, restated — never the review one.

    Written out here rather than read off `queue_flags(claim)["siu_review"]`
    because this story's whole subject is that the three rules are three, and an
    oracle reaching into a bundle built for the queue would make the pipeline's
    population a property of that bundle rather than of the rule.
    """
    return bool(claim["fraud_flag"]) and claim["fraud_score"] >= SIU_FRAUD_SCORE_MIN


def expected_fraud_panel(persona_name: str, role: str) -> dict[str, Any]:
    """The band distribution and the SIU pipeline for one persona's book.

    Rendered as the payload shapes them, so a test compares whole objects rather
    than picking figures out one at a time — `expected_portfolio_charts`'
    discipline.

    **`byBand` is zero-filled and the two pipelines are not**, which is the one
    asymmetry this oracle has to reproduce rather than smooth over: the band
    vocabulary is a rule's and is always complete, while a stage with no referred
    claim is a stage the pipeline does not reach. An oracle that treated both the
    same way would agree with an implementation that had got either wrong.
    """
    visible = claims_for(persona_name, role)

    bands: dict[str, int] = {}
    stages: dict[str, int] = {}
    handlers: dict[int, int] = {}
    handler_names: dict[int, str] = {}
    for claim in visible:
        band = fraud_band(claim["fraud_score"])
        bands[band] = bands.get(band, 0) + 1
        if siu_review(claim):
            stages[claim["stage"]] = stages.get(claim["stage"], 0) + 1
            handler_id = handler_id_of(claim["handler"])
            handlers[handler_id] = handlers.get(handler_id, 0) + 1
            handler_names[handler_id] = claim["handler"]

    ordered_handlers = sorted(
        handlers.items(), key=lambda entry: (-entry[1], handler_names[entry[0]], entry[0])
    )
    return {
        "byBand": {
            "items": [{"key": band, "count": bands.get(band, 0)} for band in FRAUD_BAND_ORDER],
            "total": sum(bands.values()),
            "totalCategories": len(FRAUD_BAND_ORDER),
            "truncated": False,
            "limit": None,
        },
        "siuByStage": _declared_series(stages, STAGE_ORDER),
        "siuByHandler": {
            "items": [
                {
                    "handlerId": handler_id,
                    "handlerName": handler_names[handler_id],
                    "count": count,
                }
                for handler_id, count in ordered_handlers
            ],
            "total": sum(handlers.values()),
            "totalCategories": len(handlers),
            "truncated": False,
            "limit": None,
        },
        "claimsInScope": len(visible),
        "flaggedClaims": sum(1 for claim in visible if fraud_flagged(claim)),
        "siuClaims": sum(1 for claim in visible if siu_review(claim)),
    }


def _rate_bp(flagged: int, claims: int) -> int:
    """`flagged / claims` in basis points, half-up — the service's arithmetic.

    `Decimal` and `ROUND_HALF_UP` rather than `round()`, which is banker's
    rounding in Python and would disagree with the service on exactly the ties a
    small denominator produces (one flagged of eight is 1 250 either way; one of
    sixteen is 625 either way; but 3/8 of a percent lands on a .5 basis point).
    Restated rather than imported for `HIGH_RISK_MIN`'s reason.
    """
    return int(
        (Decimal(flagged) / Decimal(claims) * 10_000).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


def _rate_rows(
    tallies: dict[Any, tuple[str, int, int]], sort: str, limit: int | None
) -> list[dict[str, Any]]:
    """One breakdown's rows — **cut first, then ordered** — the five orders restated.

    `tallies` maps the drill key to `(label, flagged, claims)`. Every order ends
    in `(label, key)`, which is the half that matters: ties are reachable on real
    data — every dimension member with no flagged claim shares a rate of zero —
    so an order decided by the figure alone would leave the tail to whatever the
    dict happened to hold, and the oracle would disagree with a correct
    implementation for a reason neither of them could explain.

    **The cut does not read `sort`**, and restating that here is the point of the
    oracle rather than a detail of it: which eight injury types a capped breakdown
    carries is a fact about the *population* (claim count descending, then label),
    the same ranking the portfolio's injury-type chart cuts on, and a cut that
    followed the caller's order would mean `sort=rate_asc` published the eight
    buckets with no flagged claim in them under a heading about flagged rates.
    `services/worklist/fraud.py::_breakdown` carries the argument in full.
    """
    rows = [
        (key, label, flagged, claims, _rate_bp(flagged, claims))
        for key, (label, flagged, claims) in tallies.items()
    ]
    if limit is not None:
        rows = sorted(rows, key=lambda row: (-row[3], row[1], str(row[0])))[:limit]
    keys: dict[str, Any] = {
        "rate_desc": lambda row: (-row[4], row[1], str(row[0])),
        "rate_asc": lambda row: (row[4], row[1], str(row[0])),
        "flagged_desc": lambda row: (-row[2], row[1], str(row[0])),
        "claims_desc": lambda row: (-row[3], row[1], str(row[0])),
        "label_asc": lambda row: (row[1], str(row[0])),
    }
    if sort not in keys:
        raise AssertionError(f"no seeded oracle for sort {sort!r}")
    return [
        {"key": key, "label": label, "flagged": flagged, "claims": claims, "rateBp": rate}
        for key, label, flagged, claims, rate in sorted(rows, key=keys[sort])
    ]


def expected_fraud_rates(
    persona_name: str,
    role: str,
    injury_sort: str = "rate_desc",
    employer_sort: str = "rate_desc",
    handler_sort: str = "rate_desc",
) -> dict[str, Any]:
    """The three flagged-rate breakdowns for one persona's book.

    `flagged` is the **review** rule everywhere — `fraud_flagged`, the same one
    the Fraud Flags card counts and `filter[fraudFlagged]=true` opens — and
    deliberately not `siu_review`. The near-miss is the one this whole file keeps
    restating, and it is sharper on a rate than on a count: a referral numerator
    over a review denominator would produce a plausible smaller percentage on
    every row.

    Rows are returned in a neutral `{key, label, flagged, claims, rateBp}` shape
    rather than in the payload's three different spellings, because the *rows*
    are what a test compares and the three published shapes differ only in how
    they name their subject. A test that wants the wire shape maps it.
    """
    visible = claims_for(persona_name, role)

    injury: dict[Any, tuple[str, int, int]] = {}
    employer: dict[Any, tuple[str, int, int]] = {}
    handler: dict[Any, tuple[str, int, int]] = {}
    short_names = employer_short_names()

    def add(bucket: dict[Any, tuple[str, int, int]], key: Any, label: str, flagged: bool) -> None:
        _label, hits, total = bucket.get(key, (label, 0, 0))
        bucket[key] = (label, hits + int(flagged), total + 1)

    for claim in visible:
        hit = fraud_flagged(claim)
        add(injury, claim["injury_type"], claim["injury_type"], hit)
        add(
            employer,
            _employer_id(claim["employer"]),
            short_names[claim["employer"]],
            hit,
        )
        add(handler, handler_id_of(claim["handler"]), claim["handler"], hit)

    return {
        "byInjuryType": {
            "items": _rate_rows(injury, injury_sort, FRAUD_INJURY_TYPE_LIMIT),
            "totalCategories": len(injury),
            "truncated": len(injury) > FRAUD_INJURY_TYPE_LIMIT,
            "limit": FRAUD_INJURY_TYPE_LIMIT,
            "sort": injury_sort,
        },
        "byEmployer": {
            "items": _rate_rows(employer, employer_sort, None),
            "totalCategories": len(employer),
            "truncated": False,
            "limit": None,
            "sort": employer_sort,
        },
        "byHandler": {
            "items": _rate_rows(handler, handler_sort, None),
            "totalCategories": len(handler),
            "truncated": False,
            "limit": None,
            "sort": handler_sort,
        },
        "claimsInScope": len(visible),
        "flaggedClaims": sum(1 for claim in visible if fraud_flagged(claim)),
    }


# --- Story 7.2: the trend & cohort series, restated independently ---------
#
# A trend is two cuts and a partition stacked on one another — a *window* cuts
# the population, a *bucket* partitions what survives, and a *cohort* partitions
# that again — and Story 7.1's review found the exact hazard that arrangement
# invites: `e2e/fixtures/seed.ts::topRate` carried the same sort-then-cut defect
# as the implementation, so it agreed with it. So this block shares **no** shape
# with `services/worklist/trends.py`.
#
# What that means concretely, because "independent" is otherwise a word:
#
# - The window is walked **backwards from `as_of` one bucket at a time**, by
#   re-bucketing the day before the current bucket started. The service computes
#   it forwards from an integer bucket index. Two different algorithms that have
#   to agree about February, about the turn of a year and about the 53-week ISO
#   year.
# - The bucket boundaries are computed here from the calendar, not read off the
#   payload. A test that keyed on the response's own `bucketKey` would agree with
#   any bucketing at all.
# - Every constant is restated, including the ones that coincide with constants
#   already in this file: `TREND_HIGH_RISK_MIN` beside `HIGH_RISK_MIN` and
#   `TREND_SETTLE_TARGET_DAYS` beside `SLA_TARGETS["settle"]` are 65 and 30
#   twice on purpose. Sharing either name would make this oracle unable to
#   notice the day a document or a setting moved one of them, which is the
#   single most plausible way to break this story quietly. **Every one of them
#   is read into the answer below** — the five that describe the payload's
#   rule-derived scalars go into the returned object rather than sitting in this
#   file as decoration. A constant nothing reads protects nothing, and the
#   sentence above would have been a claim about a guard that did not exist.
#   The tests keep asserting those same fields against the *loaded documents*
#   as well, which is a different failure and not a redundant one: the document
#   assertion says the payload quotes the rules it used, and this file says the
#   rules are still the ones the seeded expectations were written against.
# - The two SLA figures are folded from the seed's own columns here, at this
#   file's own precision. That is exactly what `expected_sla_strip` does for the
#   top bar, and doing it a second time per bucket is the point: the service is
#   forbidden from re-averaging (AD-2) and calls `sla.strip_of` per bucket
#   instead, so an oracle that also called it would be testing that the call
#   happened rather than that the answer is right.
#
# What is deliberately *not* restated is `claims_for` and `days_open`. Those are
# oracles for scope and for one registered derivation — one restatement each,
# already in this file, reused the way every block here reuses `claims_for`.
# What is written fresh is everything the *pipeline* does: the window, the
# buckets, the banding into cohorts, the ordering, the palette slots and the
# null-versus-zero split.

TREND_DEFAULT_BUCKETS = 12
TREND_MAX_BUCKETS = 24
TREND_LOW_CONFIDENCE_CLAIM_MAX = 3

#: The severity edges the trend payload publishes, restated separately from
#: `HIGH_RISK_MIN` and `MED_RISK_MIN` above even though all four are the same two
#: numbers — this file's standing rule, and 7.1's `FRAUD_BAND_HIGH_MIN` is the
#: precedent. The severity *cohort* itself goes through `risk_band` below rather
#: than through these, and that is not an inconsistency: the whole claim being
#: asserted is that a cohort here and a slice on the severity donut are one band,
#: so an oracle that banded them differently would be testing the wrong thing.
TREND_HIGH_RISK_MIN = 65
TREND_MED_RISK_MIN = 35

#: The two SLA targets the trend payload republishes as reference lines, on the
#: series' own scales — whole days, and basis points for the rate. Restated
#: separately from `SLA_TARGETS` for the reason directly above.
TREND_SETTLE_TARGET_DAYS = 30
TREND_RTW_TARGET_BP = 8_000

#: The five series, in the order the payload publishes them.
TREND_METRIC_ORDER = (
    "volume",
    "avg_days_open",
    "avg_settlement_days",
    "rtw_rate_bp",
    "paid_cents",
)

#: The two metrics that zero-fill an empty bucket and carry a window total. The
#: other three are a mean and two rates over a possibly-empty denominator, and
#: `null` is the only honest answer for those — the prohibition AC 3 names.
TREND_SUMMABLE_METRICS = ("volume", "paid_cents")

#: The month names a bucket label is written with. Server-side copy, unusually,
#: because a bucket's label is a rendering of a *computed* period rather than an
#: enum member whose copy the SPA owns.
TREND_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def employer_sectors() -> dict[str, str]:
    """`{employer name: sector}` from the seed file — the sector cohort's key.

    An employer *attribute*, not an industry rollup, and the stored string is the
    key and the label both: nine distinct values across ten employers, because
    two of them are Automotive.
    """
    return {row["name"]: row["sector"] for row in seed()["employers"]}


def _trend_bucket(day: date, grain: str) -> tuple[str, str, date, date]:
    """`(key, label, first day, last day)` of the bucket `day` falls in.

    Written from the calendar rather than from an index, and the ends are
    computed from the *next* period's first day so February, leap years and the
    53-week ISO year need no table.
    """
    if grain == "week":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
        iso = start.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}", f"Wk {iso.week:02d} {iso.year}", start, end
    if grain == "quarter":
        quarter = (day.month - 1) // 3 + 1
        start = date(day.year, quarter * 3 - 2, 1)
        after = date(day.year + 1, 1, 1) if quarter == 4 else date(day.year, quarter * 3 + 1, 1)
        return f"{day.year:04d}-Q{quarter}", f"Q{quarter} {day.year}", start, after - timedelta(1)
    if grain != "month":
        raise AssertionError(f"no seeded oracle for grain {grain!r}")
    start = date(day.year, day.month, 1)
    after = date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
    label = f"{TREND_MONTHS[day.month - 1]} {day.year}"
    return f"{day.year:04d}-{day.month:02d}", label, start, after - timedelta(1)


def trend_window(
    grain: str, as_of: date, buckets: int = TREND_DEFAULT_BUCKETS
) -> list[tuple[str, str, date, date]]:
    """The `buckets` periods ending in the one `as_of` falls in, oldest first.

    **Walked backwards, one bucket at a time**, by re-bucketing the day before
    the current bucket began — deliberately not the service's forward walk from
    an integer bucket index. Two algorithms that have to agree, rather than one
    written twice.
    """
    walked: list[tuple[str, str, date, date]] = []
    cursor = as_of
    for _ in range(buckets):
        bucket = _trend_bucket(cursor, grain)
        walked.append(bucket)
        cursor = bucket[2] - timedelta(days=1)
    return list(reversed(walked))


def _trend_cohort_key(claim: dict[str, Any], cohort: str, sectors: dict[str, str]) -> str | None:
    """Which cohort a claim belongs to, or `None` when the split is off.

    `risk_band` for the severity dimension — the *same* restatement the High Risk
    card's and the severity donut's oracles use, because "the cohort and the
    donut slice are one band" is the assertion rather than a coincidence.
    """
    if cohort == "none":
        return None
    if cohort == "severity_band":
        return risk_band(claim["severity_score"])
    if cohort == "disability":
        disability: str = claim["disability"]
        return disability
    if cohort == "sector":
        return sectors[claim["employer"]]
    raise AssertionError(f"no seeded oracle for cohort {cohort!r}")


def _trend_half_up(numerator: int, denominator: int) -> int:
    """A whole-number mean, half-up — never Python's banker's rounding."""
    return int(
        (Decimal(numerator) / Decimal(denominator)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


def _trend_cell_claims(metric: str, cell: list[dict[str, Any]]) -> int:
    """How many claims fed this cell's figure — the *metric's* population.

    Not `len(cell)`, and that is the whole point of writing it out: a bucket is
    the population of three of the five series and of neither of the other two.
    A settlement mean is over the settled claims of the bucket that carry a
    recorded duration and an RTW rate is over its settled claims whether they
    carry one or not, so a month in which seven claims were filed and three
    settled has three kinds of denominator in it and publishing seven beside all
    five figures describes one of them.

    Derived here from the seed's own columns rather than from anything the
    service reaches for: the server asks the SLA aggregation which claims fed
    which figure (AD-2 forbids it knowing), and an oracle that asked the same
    question of the same module would agree with a wrong answer — Story 7.1's
    `topRate`, which is the failure this whole banner exists to avoid. These are
    the two denominators `expected_sla_strip` restates portfolio-wide, restated
    again per cell, on purpose.

    The three that *are* the bucket's population are spelled as `len(cell)` three
    times over rather than as one `else`, so a reader can see that the count and
    the mean of days open share a denominator by coincidence of definition rather
    than by falling through the same branch.
    """
    if metric == "volume":
        return len(cell)
    if metric == "paid_cents":
        return len(cell)
    if metric == "avg_days_open":
        return len(cell)
    settled = [c for c in cell if c["stage"] == "settled"]
    if metric == "avg_settlement_days":
        return len([c for c in settled if c["settlement_days"] is not None])
    if metric == "rtw_rate_bp":
        return len(settled)
    raise AssertionError(f"no seeded oracle for metric {metric!r}")


def _trend_cell_value(metric: str, cell: list[dict[str, Any]], as_of: date) -> int | None:
    """One (cohort, bucket) cell's published figure — the null-vs-zero rule, restated.

    Four denominators and they are not the same four: `volume` and `paid_cents`
    are folds over a set that may be empty, `avg_days_open` needs a claim,
    `avg_settlement_days` needs a *settled* claim carrying a recorded duration,
    and `rtw_rate_bp` needs a settled claim whether or not it carries one. The
    last two are `expected_sla_strip`'s two denominators, restated here rather
    than shared, because the service reaches them through `sla.strip_of` per
    bucket and an oracle that did the same would be testing that the call
    happened rather than that the answer is right.
    """
    if metric == "volume":
        return len(cell)
    if metric == "paid_cents":
        return sum(
            int(c["paid_indemnity"]) + int(c["paid_medical"]) + int(c["paid_expense"]) for c in cell
        )
    if metric == "avg_days_open":
        if not cell:
            return None
        return _trend_half_up(sum(days_open(c, as_of) for c in cell), len(cell))
    settled = [c for c in cell if c["stage"] == "settled"]
    if metric == "avg_settlement_days":
        durations = [int(c["settlement_days"]) for c in settled if c["settlement_days"] is not None]
        if not durations:
            return None
        return _trend_half_up(sum(durations), len(durations))
    if metric != "rtw_rate_bp":
        raise AssertionError(f"no seeded oracle for metric {metric!r}")
    if not settled:
        return None
    # Whole percent first, then onto the basis-point scale — the order matters
    # and is the service's own consequence of not re-averaging: the RTW tile is
    # decided at whole-percent precision, so every published rate here is a
    # multiple of a hundred. Scaling before rounding would disagree by up to 99.
    recovered = sum(100 for c in settled if c["return_status"] == FULLY_RECOVERED)
    return _trend_half_up(recovered, len(settled)) * 100


def expected_trends(
    persona_name: str,
    role: str,
    as_of: date,
    grain: str = "month",
    anchor: str = "fnol",
    cohort: str = "none",
) -> dict[str, Any]:
    """The whole Trends payload for one persona, one window and one split.

    Payload-shaped and camelCase so a test compares whole objects rather than
    picking figures out one at a time — `expected_fraud_panel`'s discipline, over
    a payload with two levels of nesting where picking figures out would be the
    slowest possible way to miss a transposition.

    `as_of` is **required**, unlike every other oracle in this file: the window
    ends in the bucket today falls in, so an oracle that defaulted the clock
    would silently disagree with a test that passed one, and the disagreement
    would appear only in the last week of a month.

    **The rule-derived scalars are here, and the tests assert them twice.** The
    two severity edges, the two SLA targets and the window cap are compared
    against the numbers restated at the top of this block, *and* against the
    loaded `derivation_thresholds` / `trend_periods` documents. Neither
    assertion subsumes the other: the document comparison says the payload
    quotes the rules the fold actually used and survives a retune, while this
    one fails the day a document or a deployment setting moves one of them —
    which is exactly the notice the banner promises and is worth a deliberate
    edit here. `defaultBuckets` and `lowConfidenceClaimMax` are not repeated as
    fields because the window this oracle walks and the verdict it marks are
    already computed from their restatements. The two document *versions* stay
    out: a version is not a rule, and pinning one here would fail on every
    unrelated republication.
    """
    window = trend_window(grain, as_of, TREND_DEFAULT_BUCKETS)
    window_from, window_to = window[0][2], window[-1][3]
    sectors = employer_sectors()
    date_key = "froi_date" if anchor == "fnol" else "doi"
    if anchor not in ("fnol", "doi"):
        raise AssertionError(f"no seeded oracle for anchor {anchor!r}")

    in_window = [
        claim
        for claim in claims_for(persona_name, role)
        if window_from <= date.fromisoformat(claim[date_key]) <= window_to
    ]
    cohort_keys = sorted({_trend_cohort_key(c, cohort, sectors) or "" for c in in_window})
    ordered: list[str | None] = [None] if cohort == "none" else list(cohort_keys)

    def cell_of(cohort_key: str | None, bucket_key: str) -> list[dict[str, Any]]:
        return [
            claim
            for claim in in_window
            if _trend_cohort_key(claim, cohort, sectors) == cohort_key
            and _trend_bucket(date.fromisoformat(claim[date_key]), grain)[0] == bucket_key
        ]

    series: list[dict[str, Any]] = []
    for metric in TREND_METRIC_ORDER:
        for slot, cohort_key in enumerate(ordered):
            points = []
            for key, label, start, end in window:
                cell = cell_of(cohort_key, key)
                behind = _trend_cell_claims(metric, cell)
                points.append(
                    {
                        "bucketKey": key,
                        "bucketLabel": label,
                        "bucketFrom": start.isoformat(),
                        "bucketTo": end.isoformat(),
                        "value": _trend_cell_value(metric, cell, as_of),
                        "claimCount": behind,
                        "lowConfidence": 0 < behind <= TREND_LOW_CONFIDENCE_CLAIM_MAX,
                        # A period the clock has not reached the end of. Decided
                        # from the bucket's own last day rather than from its
                        # position in the window: "the newest one" is true today
                        # and false for a window asked for with a `to` in the
                        # future, and an oracle that keyed on the index would
                        # agree with an implementation that marked the wrong one.
                        "partial": end > as_of,
                    }
                )
            series.append(
                {
                    "metric": metric,
                    "cohortKey": cohort_key,
                    "cohortLabel": None,
                    "paletteSlot": None if cohort_key is None else slot,
                    "points": points,
                    "seriesTotal": sum(point["claimCount"] for point in points),
                    "valueTotal": (
                        sum(point["value"] or 0 for point in points)
                        if metric in TREND_SUMMABLE_METRICS
                        else None
                    ),
                    "noDataBuckets": sum(1 for point in points if point["value"] is None),
                }
            )

    return {
        "asOf": as_of.isoformat(),
        "grain": grain,
        "anchor": anchor,
        "cohort": cohort,
        "windowFrom": window_from.isoformat(),
        "windowTo": window_to.isoformat(),
        "bucketCount": len(window),
        "claimsInScope": len(claims_for(persona_name, role)),
        "claimsInWindow": len(in_window),
        "series": series,
        "maxBuckets": TREND_MAX_BUCKETS,
        "settleTargetDays": TREND_SETTLE_TARGET_DAYS,
        "rtwTargetBp": TREND_RTW_TARGET_BP,
        "highRiskSeverityMin": TREND_HIGH_RISK_MIN,
        "medRiskSeverityMin": TREND_MED_RISK_MIN,
    }


# --- Story 7.3: segmentation, restated independently ----------------------
#
# **A filter is a cut, and 7.1's review found an oracle carrying the
# implementation's own cut and therefore agreeing with it.** So the ten
# predicates below are written from the *dimension's* definition — which column,
# which rule — rather than from the shape of `services/worklist/segmentation.py`,
# and the expectations they build assert **which claims survive** rather than how
# many. A count is satisfiable by the wrong population; a set of claim ids is not.
#
# What is reused rather than restated is `claims_for` (scope, one restatement,
# already here) and `risk_band` (the one registered `risk` derivation this whole
# console bands severity with — a second restatement of it would be pretending
# there is a fourth rule). What is written fresh is the age band, the employee
# join, and every predicate.
#
# **The age edges are restated under their own names although two of them
# coincide with numbers already in this file** — `AGE_YOUNGER_MIN` beside
# `MED_RISK_MIN` and `FRAUD_BAND_MED_MIN`, `AGE_OLDEST_MIN` beside
# `FRAUD_FLAG_SCORE_MIN` and `FRAUD_BAND_HIGH_MIN`. That is this file's standing
# ruling (`:1229-1234`) applied to a third column: sharing a name would make the
# oracle unable to notice the day the document moved one of them, and the failure
# it would hide is the one nobody would look for — a fraud threshold silently
# re-banding a workforce.
#
# **Every constant below is read into an answer.** `AGE_OLDER_MIN` decides a band
# in `age_band`, the other two decide the bands either side of it, and all three
# are returned by `expected_segmentation_values` because the payload publishes
# them for the UI to compose a range label from. A constant nothing reads
# protects nothing.

AGE_YOUNGER_MIN = 35
AGE_OLDER_MIN = 45
AGE_OLDEST_MIN = 55

#: The `AgeBand` declaration order — youngest to oldest, which is a scale's
#: reading order and the order a picker draws. Deliberately not `RISK_BAND_ORDER`'s
#: high-first legend order: an age segmentation is scanned like a histogram.
AGE_BAND_ORDER = ("youngest", "younger", "older", "oldest")

#: The ten dimensions, in the order the chip row draws them — which is
#: `DrillFilters`' field order restricted to this vocabulary, restated here so an
#: oracle that agreed with a re-ordered chip row would fail.
SEGMENTATION_ORDER = (
    "severityBand",
    "injuryType",
    "state",
    "employerId",
    "disability",
    "sector",
    "region",
    "icd10",
    "ageGroup",
    "gender",
)


def age_band(age: int) -> str:
    """`employee.age` banded — four ordinal groups from three edges.

    Written downward from the oldest edge, which is the order the derivation
    tests them in and the only order under which three comparisons produce four
    bands. The members carry no numbers, deliberately: the label an analyst reads
    is composed in the browser from the published edges, so this vocabulary
    survives a retune that would have falsified a member named for a range.
    """
    if age >= AGE_OLDEST_MIN:
        return "oldest"
    if age >= AGE_OLDER_MIN:
        return "older"
    if age >= AGE_YOUNGER_MIN:
        return "younger"
    return "youngest"


def _employees_by_id() -> dict[str, dict[str, Any]]:
    """`{employee business id: row}` from the seed file — the two worker columns.

    The claim carries `employee_id` and the two segmentation dimensions that are
    *not* a claim's own facts — the worker's age and gender — are on `employee`.
    Joined here rather than assumed, because the join is precisely what Story 7.3
    added a fourth projection-family read for.
    """
    return {row["employee_id"]: row for row in seed()["employees"]}


def _segment_matches(claim: dict[str, Any], key: str, value: str) -> bool:
    """One dimension, restated against the surface it has to reconcile with.

    A mapping rather than a chain of `if`s so the ten read as one table, the way
    the service's `_PREDICATES` does — `_drill_matches`' discipline, and the
    values are compared as the **wire** spells them (strings), because that is
    what a URL carries and what a chip publishes.
    """
    employee = _employees_by_id()[claim["employee_id"]]
    employer = next(row for row in seed()["employers"] if row["name"] == claim["employer"])
    answers: dict[str, bool] = {
        # The one registered `risk` band, reused — the High Risk card's, the
        # severity donut's and the trend cohort's, so a segmentation by `high`
        # and a slice of that donut are one band rather than two readings.
        "severityBand": risk_band(claim["severity_score"]) == value,
        # The exact stored string, with no trim, case-fold or merge — the ruling
        # the injury-type and state bars are folded under.
        "injuryType": claim["injury_type"] == value,
        "state": claim["state"] == value,
        "employerId": str(_employer_id(claim["employer"])) == value,
        "disability": claim["disability"] == value,
        # The **employer's** attribute, reached through the join.
        "sector": employer["sector"] == value,
        # A `claim` column nothing in this console read before Story 7.3, and a
        # different column from `state`: a region is where a plant is, a state is
        # the jurisdiction a benefit is calculated under.
        "region": claim["region"] == value,
        # The column is `icd`; the facet says which coding system its values are
        # in. Compared as stored, like every free-text dimension here.
        "icd10": claim["icd"] == value,
        # The **employee's** age, banded — the only dimension that is a number
        # before it is a value, which is the whole reason it has a rule.
        "ageGroup": age_band(employee["age"]) == value,
        "gender": employee["gender"] == value,
    }
    if key not in answers:
        raise AssertionError(f"no seeded oracle for segmentation dimension {key!r}")
    return answers[key]


def expected_segmented_ids(persona_name: str, role: str, **filters: str) -> set[str]:
    """**Which claims** survive one segmentation over one persona's book.

    A set of claim ids rather than a count, which is this block's whole
    discipline: a filtered aggregate's count is satisfiable by the wrong
    population, and the reconciliation tests compare this set against the drill
    list's own items.

    Dimensions arrive as camelCase keyword arguments spelled the way the URL
    spells them (`severityBand=...`, `ageGroup=...`), and an unknown one raises
    rather than being ignored — an oracle that silently dropped a dimension would
    agree with an implementation that had dropped the same one.
    """
    return {
        claim["claim_id"]
        for claim in claims_for(persona_name, role)
        if all(_segment_matches(claim, key, value) for key, value in filters.items())
    }


def _expected_option_order(
    options: dict[str, str | None], declared: tuple[str, ...] | None
) -> list[str]:
    """One dimension's values, put in the order the story says a picker draws them.

    **Derived from the two rules in words, never from the fold that implements
    them**, and this function exists because the first version of it was not: it
    read `sorted(options, key=lambda value: (options[value] or value, value))`,
    which is `_ordered_values`' expression character for character — the `or`
    included, so an empty-string label would have been treated as absent by the
    oracle for the same reason and in the same place as by the server. That is
    the failure the last two reviews found twice: an oracle carrying the
    implementation's own reading cannot notice it.

    So the two rules are executed rather than transcribed.

    **A vocabulary keeps its declaration order.** Walked forwards over the
    declared members, keeping the ones some claim is in — no sort and no rank
    table, because "the order the enum declares" is a traversal rather than a
    comparison. Anything the vocabulary does not contain is kept and appended,
    ordered among itself, which is the endpoint's stated tolerance: a value
    dropped from a picker is a claim population the analyst cannot see they
    cannot reach.

    **Everything else sorts ascending by the string a reader sees, then by the
    value behind it.** The pair is built first and sorted as a pair, so the
    ordering is a property of the list rather than of a key function that
    happens to be spelled the same way twice. The string a reader sees is the
    label *when the payload carries one* — `is None`, not falsiness: a label
    that is the empty string is a label the picker renders, and reading it as
    "absent" is a judgement the server may make but an oracle must not inherit.
    """
    shown = [(value if label is None else label, value) for value, label in options.items()]
    if declared is None:
        return [value for _, value in sorted(shown)]
    present = set(options)
    ordered = [member for member in declared if member in present]
    unknown = sorted(value for value in present if value not in set(declared))
    return ordered + unknown


def expected_segmentation_values(persona_name: str, role: str, **filters: str) -> dict[str, Any]:
    """The values endpoint's options, chips and counts for one persona and one filter.

    Payload-shaped and camelCase so a test compares whole objects rather than
    picking figures out one at a time — `expected_fraud_panel`'s discipline.

    **The options are over the unfiltered book and `claimsMatching` is over the
    filtered one**, which is the asymmetry the endpoint exists for: a picker cut
    by its own filter could not be used to widen one. An oracle that narrowed both
    would agree with an implementation that had made every filter a one-way door.

    The order is restated rather than sorted uniformly: the two bands and the two
    enum columns come in their **declaration** order, because a vocabulary has one
    and "High, Low, Medium" is a severity picker nobody can scan; the other six
    sort ascending by the string on screen, with the value behind it for the one
    dimension where two options may share a label. `_expected_option_order`
    carries the argument for why that is executed here rather than transcribed
    from the fold.

    **`rulesVersion` is deliberately not returned, and the caller's name says
    so.** Every other key here is a fact about the seed file, which is what this
    module is an oracle for. The rules version is a fact about the *document
    loaded into the database at the moment of the request* — a migration away
    from anything in `seed.json` — so restating it here would be this file
    asserting a number it has no independent source for. `test_fraud_analytics`
    checks it against `thresholds_for(db)`, which is where a version belongs.
    """
    visible = claims_for(persona_name, role)
    employees = _employees_by_id()
    employers = {row["name"]: row for row in seed()["employers"]}

    found: dict[str, dict[str, str | None]] = {key: {} for key in SEGMENTATION_ORDER}
    for claim in visible:
        employee = employees[claim["employee_id"]]
        found["severityBand"][risk_band(claim["severity_score"])] = None
        found["injuryType"][claim["injury_type"]] = None
        found["state"][claim["state"]] = None
        found["employerId"][str(_employer_id(claim["employer"]))] = employers[claim["employer"]][
            "short_name"
        ]
        found["disability"][claim["disability"]] = None
        found["sector"][employers[claim["employer"]]["sector"]] = None
        found["region"][claim["region"]] = None
        found["icd10"][claim["icd"]] = None
        found["ageGroup"][age_band(employee["age"])] = None
        found["gender"][employee["gender"]] = None

    declared: dict[str, tuple[str, ...]] = {
        "severityBand": RISK_BAND_ORDER,
        "ageGroup": AGE_BAND_ORDER,
        "disability": ("temporary", "permanent"),
        "gender": ("female", "male", "other"),
    }

    dimensions = [
        {
            "key": key,
            "values": [
                {"value": value, "label": found[key][value]}
                for value in _expected_option_order(found[key], declared.get(key))
            ],
        }
        for key in SEGMENTATION_ORDER
    ]

    return {
        "dimensions": dimensions,
        "appliedFilters": expected_segmentation_chips(persona_name, role, **filters),
        "claimsInScope": len(visible),
        "claimsMatching": len(expected_segmented_ids(persona_name, role, **filters)),
        "ageYoungerMin": AGE_YOUNGER_MIN,
        "ageOlderMin": AGE_OLDER_MIN,
        "ageOldestMin": AGE_OLDEST_MIN,
    }


def expected_segmentation_chips(
    persona_name: str, role: str, **filters: str
) -> list[dict[str, Any]]:
    """The chip row the server must publish for one segmentation.

    **In `SEGMENTATION_ORDER`, never in the caller's order**, which is the whole
    property: a chip row built by walking the request's parameters would render
    differently depending on which one FastAPI happened to bind first, and the
    workspace's chips and the drill list's would stop being one sequence.

    **One of the ten carries a `display` and nine do not.** An employer id is not
    a name, so the server resolves it — and resolves it *from the caller's own
    rows*, so an employer outside the book gets `null` rather than that
    employer's name and the chip cannot become an oracle for the existence of
    something the caller cannot see (AD-7). Every other dimension's stored value
    is already what a reader reads, or is a token the browser owns copy for.

    An unknown dimension raises rather than being skipped, `expected_segmented_
    ids`' rule: an oracle that quietly dropped one would agree with an
    implementation that had dropped the same one.
    """
    visible = claims_for(persona_name, role)
    employers = {row["name"]: row for row in seed()["employers"]}
    for key in filters:
        if key not in SEGMENTATION_ORDER:
            raise AssertionError(f"no seeded oracle for segmentation dimension {key!r}")

    chips: list[dict[str, Any]] = []
    for key in SEGMENTATION_ORDER:
        if key not in filters:
            continue
        value = filters[key]
        display: str | None = None
        if key == "employerId":
            display = next(
                (
                    employers[claim["employer"]]["short_name"]
                    for claim in visible
                    if str(_employer_id(claim["employer"])) == value
                ),
                None,
            )
        chips.append({"key": key, "value": value, "display": display})
    return chips


# --- Story 7.4: the financial decomposition, restated independently --------
#
# **Money is where an oracle that agrees with the implementation does the most
# damage**, so this block restates *what each figure sums* rather than reaching
# for a helper that already sums something similar. Three of the four blocks
# above hold a paid figure of some kind — `expected_portfolio_summary` sums the
# three paid columns for a KPI card, `expected_portfolio_charts` sums them per
# employer for a bar, and `expected_priority_claims` does not sum money at all —
# and reusing any of them would have made this oracle unable to notice the day
# the *basis* moved, which is the one change `deferred-work.md` records as
# pending with an owner.
#
# What is reused rather than restated is `claims_for` (scope, one restatement,
# already here and reused by every block in this file), `risk_band` (the one
# registered `risk` derivation this console bands severity with everywhere) and
# `_segment_matches`/`age_band` from the Story 7.3 block directly above — the
# segmentation vocabulary, restated once, and a second restatement of it here
# would be pretending there are two filters. What is written fresh is every
# money rule, every cohort predicate and the reserve band arithmetic.
#
# **The three money words are three quantities and the names below say which:**
#
#     PAID       = paid_indemnity + paid_medical + paid_expense   (the columns)
#     RESERVE    = reserve                                        (the column)
#     PROJECTED  = PAID + RESERVE
#
# `_total_paid_cents` is written out here although `expected_portfolio_summary`
# already adds the same three columns forty lines up, and the duplication is the
# point: that one is an oracle for a **KPI card** and this is an oracle for a
# **decomposition**, and the open product decision at `deferred-work.md` moves
# exactly one of them first. An oracle that shared the expression could not fail
# on the day they diverged, which is the day it would matter most.
#
# **The reserve verdict is restated as a *rule*, not as a distribution.** The
# per-claim exposure terms come from tables this file cannot see — the payment
# schedule is materialized by a migration at `upgrade head` time and its statuses
# move with the calendar — so a static count of light/adequate/heavy claims would
# be a number that goes stale overnight rather than an oracle. What *is* static,
# and is what the tests actually need, is (a) the two band ratios, (b) the
# closed-final population, which is a seed column, (c) that `indeterminate` is
# unreachable against this seed because every claim carries bills, and (d) the
# band arithmetic itself, which `expected_reserve_verdict` executes over exposure
# terms a caller supplies. The distribution is then asserted against
# `reserve_check_for_claim` claim by claim, which is the only form AC 2's "never
# a re-derivation" can take.

#: The two reserve band ratios, in basis points — `reserve_bands` v1, restated.
#:
#: Written out here rather than loaded for `HIGH_RISK_MIN`'s reason, and under
#: their own names although nothing else in this file carries either number: the
#: point of restating is that a document retune has to break a test rather than
#: move an expectation with it.
RESERVE_LIGHT_RATIO_BP = 11_500
RESERVE_HEAVY_RATIO_BP = 6_000

#: The scale a ratio is expressed on. 11 500 is 115%.
RESERVE_BASIS_POINTS_PER_UNIT = 10_000

#: The stage a claim has to be in for the band arithmetic not to run.
#:
#: Its own constant beside `TREATMENT_STAGE` above rather than a bare string at
#: the two call sites: "settled" decides a *verdict* here and a *cohort* in three
#: other blocks, and the reason it is checked first in `expected_reserve_verdict`
#: is that a settled claim has been judged rather than not judged.
SETTLED_STAGE = "settled"


def _total_paid_cents(claim: dict[str, Any]) -> int:
    """The `total_paid` derivation, restated for this block — the three columns.

    A second expression of the same addition `expected_portfolio_summary`
    performs, deliberately: see this block's banner. It is *not* "paid to date" —
    the columns are zero on every open seeded claim while the bills and the
    schedule show real disbursements — and the decomposition publishes this
    figure precisely so it cannot disagree with the KPI card.
    """
    total: int = claim["paid_indemnity"] + claim["paid_medical"] + claim["paid_expense"]
    return total


def _projected_cents(claim: dict[str, Any]) -> int:
    """`total_claim_projected`, restated — paid to date plus the reserve held.

    Named for what it adds up rather than "incurred", which is the discrepancy
    `services/derivations/claim_money.py` records and this console refuses to
    spread: the case file already labels the paid-only figure "Total incurred".
    """
    reserve: int = claim["reserve"]
    return _total_paid_cents(claim) + reserve


def expected_reserve_verdict(
    stage: str,
    reserve_cents: int,
    remaining_indemnity_cents: int,
    remaining_medical_cents: int | None,
) -> str:
    """The reserve band rule, restated — a claim's verdict from its exposure.

    **The exposure terms are arguments and not read from the seed**, and that is
    what makes this an oracle rather than a copy: the indemnity term comes from
    `payment_schedule_week` rows a migration materialized at `upgrade head` time
    against that day's calendar, and the medical term from `bills.json`. A
    version of this function that reached for either would be restating the
    implementation's *inputs* as well as its rule; a caller supplies them, and
    the tests that use it get them from the claim-level path — which is the
    agreement AC 2 asks for.

    Four rules, in the order they decide, and each is a fact about the claim
    rather than a step in the server's function:

    1. **A settled claim is `closed_final`** and the band arithmetic does not run
       at all. It has been judged, which is different from having no answer.
    2. **An unknown medical term withholds two of the three bands.** The unknown
       quantity is non-negative, so the exposure computed without it is a *lower
       bound*: `light` still holds (an addition cannot bring an exposure back
       under a threshold it has passed) while `adequate` and `heavy` are claims
       about an upper bound and become `indeterminate`. Unreachable against this
       seed — every claim carries bills — and restated because the day it becomes
       reachable is the day it must not silently default.
    3. **A zero reserve is light while exposure remains and adequate when none
       does.** The prototype's sentinel, and a verdict rather than a division
       guard: a claim carrying exposure against no reserve *is* under-reserved.
    4. **Otherwise the ratio bands, strictly, on cross-multiplied integers.** A
       claim sitting exactly on either boundary is `adequate`, which is the
       answer that asks a handler to do nothing.
    """
    known = remaining_indemnity_cents + (remaining_medical_cents or 0)
    if stage == SETTLED_STAGE:
        return "closed_final"
    complete = remaining_medical_cents is not None
    if reserve_cents > 0:
        light = known * RESERVE_BASIS_POINTS_PER_UNIT > RESERVE_LIGHT_RATIO_BP * reserve_cents
        heavy = known * RESERVE_BASIS_POINTS_PER_UNIT < RESERVE_HEAVY_RATIO_BP * reserve_cents
    else:
        light = known > 0
        heavy = False
    if light:
        return "light"
    if not complete:
        return "indeterminate"
    return "heavy" if heavy else "adequate"


def expected_closed_final_claims(persona_name: str, role: str, **filters: str) -> set[str]:
    """Which claims are `closed_final` — a pure seed fact, and the one bucket
    of the five this file can name without a schedule.

    Settled *stage*, never `status == "settled_closed"` — Story 5.1's ruling,
    restated here because the two differ by eight claims on the seeded book and
    this is a count the adequacy distribution publishes.
    """
    return {
        claim["claim_id"]
        for claim in claims_for(persona_name, role)
        if claim["stage"] == SETTLED_STAGE
        and all(_segment_matches(claim, key, value) for key, value in filters.items())
    }


def expected_financials(persona_name: str, role: str, **filters: str) -> dict[str, Any]:
    """The portfolio totals and both cost-driver pairs, for one persona and filter.

    Payload-shaped and camelCase so a test compares whole objects rather than
    picking figures out one at a time — `expected_fraud_panel`'s discipline.

    **The two cohorts of each pair partition the segment**, which is the property
    the payload's shape encodes and this oracle reproduces by construction: every
    visible claim goes into exactly one side of each pair, so a card cannot end
    up comparing a surgery cohort against a portfolio total that contains it.

    **`averageProjectedCents` is `None` for an empty cohort, never `0`** — a mean
    over an empty set is not zero, and a cohort reporting nothing would read as a
    cohort that costs nothing. Floor division, because the figure is an average
    of *cents* that nothing multiplies.

    The breakdown is deliberately **not** here: it is `expected_financial_breakdown`
    below, because a ranked and cut series is a different kind of assertion from a
    sum and putting them in one object would let a test that only cared about the
    totals drag the cut along with it.
    """
    visible = [
        claim
        for claim in claims_for(persona_name, role)
        if all(_segment_matches(claim, key, value) for key, value in filters.items())
    ]

    def totals(claims: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "paidCents": sum(_total_paid_cents(claim) for claim in claims),
            "reserveCents": sum(claim["reserve"] for claim in claims),
            "projectedCents": sum(_projected_cents(claim) for claim in claims),
        }

    def cohort(key: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
        summed = totals(claims)
        return {
            "key": key,
            "claimCount": len(claims),
            "totals": summed,
            "averageProjectedCents": (
                None if not claims else summed["projectedCents"] // len(claims)
            ),
        }

    def pair(facet: str, column: str) -> dict[str, Any]:
        return {
            "facet": facet,
            "withDriver": cohort("true", [c for c in visible if c[column]]),
            "withoutDriver": cohort("false", [c for c in visible if not c[column]]),
        }

    return {
        "totals": totals(visible),
        "claimsInScope": len(visible),
        # The two columns the story names, each already a shipped drill facet —
        # which is why the cost-driver drills cost Story 7.4 no new facet.
        "surgery": pair("surgery", "surgery_required"),
        "litigation": pair("litigation", "litigation_flag"),
    }


def expected_financial_breakdown(
    persona_name: str, role: str, dimension: str, limit: int, **filters: str
) -> dict[str, Any]:
    """One dimension's money groups, ranked and cut — the payload's own shape.

    **Ranked by projected cents descending, then by key ascending**, and the
    tie-break is executed rather than transcribed: the pair is built first and
    sorted as a pair, so the ordering is a property of the list rather than of a
    key expression that happens to be spelled the same way on both sides —
    `_expected_option_order`'s ruling, which exists because the first version of
    that function was the implementation's own `sorted(...)` key.

    **Projected rather than paid**, and the reason is a fact about this seed
    rather than a preference: the paid columns are zero on all 38 open claims, so
    a paid ranking would put the whole open book in one tie and the cut would
    land inside it. The oracle ranks the way the payload has to be ranked for its
    caption to mean anything, and the test that reads it asserts the *kept set*.

    `label` is `None` for nine of the ten dimensions and the employer's short
    name for the tenth — an id is not a name, and it is the only value on this
    payload a browser could not read.
    """
    visible = [
        claim
        for claim in claims_for(persona_name, role)
        if all(_segment_matches(claim, key, value) for key, value in filters.items())
    ]
    employers = {row["name"]: row for row in seed()["employers"]}
    employees = _employees_by_id()

    def key_of(claim: dict[str, Any]) -> tuple[str, str | None]:
        employer = employers[claim["employer"]]
        employee = employees[claim["employee_id"]]
        keys: dict[str, tuple[str, str | None]] = {
            "severityBand": (risk_band(claim["severity_score"]), None),
            "injuryType": (claim["injury_type"], None),
            "state": (claim["state"], None),
            "employerId": (str(_employer_id(claim["employer"])), employer["short_name"]),
            "disability": (claim["disability"], None),
            "sector": (employer["sector"], None),
            "region": (claim["region"], None),
            "icd10": (claim["icd"], None),
            "ageGroup": (age_band(employee["age"]), None),
            "gender": (employee["gender"], None),
        }
        if dimension not in keys:
            raise AssertionError(f"no seeded oracle for breakdown dimension {dimension!r}")
        return keys[dimension]

    grouped: dict[str, dict[str, Any]] = {}
    for claim in visible:
        key, label = key_of(claim)
        bucket = grouped.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "claimCount": 0,
                "totals": {"paidCents": 0, "reserveCents": 0, "projectedCents": 0},
            },
        )
        bucket["claimCount"] += 1
        bucket["totals"]["paidCents"] += _total_paid_cents(claim)
        bucket["totals"]["reserveCents"] += claim["reserve"]
        bucket["totals"]["projectedCents"] += _projected_cents(claim)

    ranked = [
        group
        for _rank, group in sorted(
            ((-group["totals"]["projectedCents"], group["key"]), group)
            for group in grouped.values()
        )
    ]
    return {
        "dimension": dimension,
        "items": ranked[:limit],
        "groupCount": len(grouped),
        "truncated": len(grouped) > limit,
        "limit": limit,
    }


# --- Story 7.5: the exported tables, restated independently ----------------
#
# **This block restates the FILE, not the fold.** The two are different claims
# and only one of them belongs here.
#
# What the export must be right about is *rendering*: which columns exist, in
# what order, with what names, and what each value looks like once it is a cell
# rather than a JSON member. So `expected_claim_export` below writes out
# thirty-six column names and thirty-six cell expressions, from the seed file and
# from this file's own restatements of the derivations — a genuinely second
# implementation of what a downloaded row says.
#
# What the export must *not* be is a second fold, and that is why the two
# aggregate oracles below deliberately **reuse** the Story 7.1 and 7.4 blocks
# rather than restating them. The whole claim of this story is that an exported
# figure cannot differ from the figure on the card it was exported from, so the
# oracle for the card *is* the oracle for the export, projected into rows. A
# second restatement here would create two oracles for one number, free to
# disagree with each other, and a test built on it could go green while the file
# and the screen were both wrong in the same way. The reuse is the assertion.
#
# What is reused for the claim export is likewise the narrowest possible set:
# `claims_for` (scope), `_drill_matches` (the drill vocabulary, restated once in
# the Story 5.5 block), `expected_score` and `days_open` (the queue's scorer and
# age, restated once in the Story 2.1 block), `queue_flags`, `risk_band`,
# `fraud_band`, `fraud_flagged`, `siu_review`, `age_band` and `recovery_token`.
# Every one of those is already an independent restatement of exactly the rule
# the corresponding column publishes, and restating any of them a second time
# would be pretending this console has two of that rule.
#
# **Every cell is a string here**, including the numbers, because a CSV field is
# a string and the point of this oracle is what the *file* holds. The XLSX half
# is asserted against the CSV in the test rather than against a second oracle —
# a spreadsheet whose cells equal the CSV's cells is the property, and two
# oracles would be two chances to encode the same mistake.

#: The claim export's header, written out — never read off the service's tuple.
#:
#: Thirty-six names in one order, and both halves matter: a header a consumer
#: joins on is a contract, and an oracle that imported the contract from the code
#: under test could not notice a column being renamed, reordered or dropped.
#:
#: Twenty-eight stored columns (the drill projection, which is what
#: `select_drill_rows` already loads under the caller's scope), then the seven
#: derived values the ranking was computed with, then the score it ranked on.
#: `reserve_verdict` is deliberately absent: it is loaded only when
#: `filter[reserveVerdict]` is set, so a column for it would be blank on almost
#: every export and a blank a consumer cannot tell from an answer is worse than
#: an absent column.
EXPORT_CLAIM_COLUMNS: tuple[str, ...] = (
    "claim_id",
    "stage",
    "status",
    "return_status",
    "severity_score",
    "days_open",
    "injury_type",
    "worker_name",
    "employer_short_name",
    "surgery_required",
    "litigation_flag",
    "fraud_flag",
    "fraud_score",
    "employer_id",
    "handler_id",
    "handler_name",
    "state",
    "osha_recordable",
    "froi_date",
    "doi",
    "disability",
    "sector",
    "region",
    "icd",
    "age",
    "gender",
    "reserve_cents",
    "recovery",
    "risk_band",
    "fraud_band",
    "age_group",
    "fraud_flagged",
    "siu_review",
    "rtw_blocked",
    "payment_due",
    "priority_score",
)

#: The fraud band export's header, written out for `EXPORT_CLAIM_COLUMNS`' reason.
EXPORT_FRAUD_BAND_COLUMNS: tuple[str, ...] = ("fraud_band", "claims")

#: The money breakdown export's header, likewise.
EXPORT_BREAKDOWN_COLUMNS: tuple[str, ...] = (
    "dimension",
    "key",
    "label",
    "claim_count",
    "paid_cents",
    "reserve_cents",
    "projected_cents",
)


def export_cell(value: Any) -> str:
    """One value as the file spells it — this file's own rendering rules.

    Four branches, restated from the story's "a data product, not a UI" ruling
    rather than read off the service:

    - `None` is the **empty field**. Not `"null"`, not `"None"`, and above all
      not `0`: a mean over nothing is not zero, and a CSV that said so would be
      the line drawn through zero the Trends section refuses, saved to disk.
    - `bool` **before** `int`, because `True` is an `int` in Python and would
      otherwise render as `1`. The words are `true`/`false` — the API's spelling
      and the query string's, so a boolean in a file and a boolean in a URL are
      one word.
    - `date` is ISO-8601. The seed already holds ISO strings, so this branch is
      reached only by a caller that passed a real `date`; it is here because the
      *rule* is ISO rather than because this file has a date object to convert.
    - everything else is `str(...)`.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def expected_claim_export(
    persona_name: str,
    role: str,
    as_of: date | None = None,
    **filters: Any,
) -> dict[str, Any]:
    """`{columns, rowCount, rows}` for the claim export under one filter set.

    **The rows are the whole filtered population in ranked order**, which is the
    property AC 2 turns on and the reason this is not built on top of
    `expected_drill_claims`' `pages`: the export has no page, and an oracle that
    reproduced the paging would be agreeing with a shape the file does not have.
    The ordering rule is that block's, restated in one line because it is one
    line — descending score, then claim id — over `expected_score`, which is the
    Story 2.1 block's independent restatement of the scorer.

    Filters arrive as snake_case keyword arguments matching the *drill*
    vocabulary (`severity_band=...`, `sector=...`), and an unknown one raises
    rather than being ignored: an oracle that silently dropped a facet would
    agree with an implementation that had dropped the same one.

    `days_open` and `priority_score` both move with `as_of`, which is why it is a
    parameter here rather than a clock read inside: a test comparing a file
    against this oracle has to be able to name the day both were computed for.
    """
    today = as_of or datetime.now(UTC).date()
    employers = {row["name"]: row for row in seed()["employers"]}
    employees = _employees_by_id()

    visible = [
        claim
        for claim in claims_for(persona_name, role)
        if all(_drill_matches(claim, key, value) for key, value in filters.items())
    ]
    ordered = sorted(visible, key=lambda c: (-expected_score(c, today), c["claim_id"]))

    def row_of(claim: dict[str, Any]) -> list[str]:
        employer = employers[claim["employer"]]
        employee = employees[claim["employee_id"]]
        flags = queue_flags(claim)
        return [
            export_cell(value)
            for value in (
                claim["claim_id"],
                claim["stage"],
                claim["status"],
                claim["return_status"],
                claim["severity_score"],
                days_open(claim, today),
                claim["injury_type"],
                employee["name"],
                employer["short_name"],
                claim["surgery_required"],
                claim["litigation_flag"],
                claim["fraud_flag"],
                claim["fraud_score"],
                _employer_id(claim["employer"]),
                handler_id_of(claim["handler"]),
                claim["handler"],
                claim["state"],
                claim["osha_recordable"],
                claim["froi_date"],
                claim["doi"],
                claim["disability"],
                employer["sector"],
                claim["region"],
                claim["icd"],
                employee["age"],
                employee["gender"],
                claim["reserve"],
                recovery_token(claim["recovery"]),
                risk_band(claim["severity_score"]),
                fraud_band(claim["fraud_score"]),
                age_band(employee["age"]),
                fraud_flagged(claim),
                siu_review(claim),
                flags["rtw_blocked"],
                flags["payment_due"],
                expected_score(claim, today),
            )
        ]

    return {
        "columns": list(EXPORT_CLAIM_COLUMNS),
        "rowCount": len(ordered),
        "rows": [row_of(claim) for claim in ordered],
    }


def expected_fraud_band_export(persona_name: str, role: str) -> dict[str, Any]:
    """`{columns, rowCount, rows}` for the fraud band distribution export.

    **Built on `expected_fraud_panel` rather than on a second fold**, which is
    this block's banner argument arriving where it does the most work: the file
    has to hold the arcs the donut drew, so the oracle for the donut projected
    into rows *is* the oracle for the file. Restating the band tally here would
    be a second implementation of one rule, free to disagree with the first, and
    a test built on it could pass while the card and the file were both wrong.

    **All three bands, including one no claim reached**, because the panel is
    zero-filled and the file has to be: a two-row spreadsheet cannot be told from
    a three-row one that lost a row, and "no claim in this book scored high" is
    the most valuable thing this export can say.
    """
    items = expected_fraud_panel(persona_name, role)["byBand"]["items"]
    return {
        "columns": list(EXPORT_FRAUD_BAND_COLUMNS),
        "rowCount": len(items),
        "rows": [[export_cell(item["key"]), export_cell(item["count"])] for item in items],
    }


def expected_breakdown_export(
    persona_name: str,
    role: str,
    dimension: str,
    limit: int,
    **filters: str,
) -> dict[str, Any]:
    """`{columns, rowCount, rows}` for the money breakdown export.

    `expected_fraud_band_export`'s reuse argument, over the figures where it
    matters most: money is where an oracle that agrees with the implementation
    does the most damage, and `expected_financial_breakdown` is already this
    file's second implementation of what each group sums. Projecting it into rows
    is what makes "the file holds the bars" an assertion rather than a hope.

    **Cut at `limit`, because the card is** — the twelve groups the bars draw,
    never the whole segment. An oracle that expected the untruncated set would be
    describing a file whose caption ("top 12 of 34") it contradicted.

    `label` is `None` for nine of the ten dimensions and renders as the empty
    field; only an employer id carries a name, because only an id is not already
    the string a reader reads.
    """
    breakdown = expected_financial_breakdown(persona_name, role, dimension, limit, **filters)
    return {
        "columns": list(EXPORT_BREAKDOWN_COLUMNS),
        "rowCount": len(breakdown["items"]),
        "rows": [
            [
                export_cell(dimension),
                export_cell(group["key"]),
                export_cell(group["label"]),
                export_cell(group["claimCount"]),
                export_cell(group["totals"]["paidCents"]),
                export_cell(group["totals"]["reserveCents"]),
                export_cell(group["totals"]["projectedCents"]),
            ]
            for group in breakdown["items"]
        ],
    }
