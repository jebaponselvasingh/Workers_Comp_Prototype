"""One-off extraction: prototype ALL_CLAIMS → normalized seed_data.json.

The prototype (docs/Workers_Comp_Prototype.html) is a DATA source only.
Run from the server/ directory:

    uv run python data/seed/extract_prototype_seed.py ../docs/Workers_Comp_Prototype.html

Rules applied (Story 1.2):
- dollars → integer cents (×100)
- display strings → snake_case enum tokens (mechanical mapping)
- derived values (days_open, risk, severity band, totals, initials,
  age_group, supervisor, cp_* narratives) are dropped — never seed columns
- nested collections (timeline, documents, photos, treatmentPlan, prognosis)
  are carried through untouched under "deferred" for later stories' seeds
"""

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

SHORT_NAMES = {
    "3M Company": "3M",
    "Boeing": "Boeing",
    "Caterpillar Inc.": "Caterpillar",
    "General Electric (GE)": "GE",
    "General Motors": "GM",
    "Honeywell International Inc.": "Honeywell",
    "John Deere": "John Deere",
    "Lockheed Martin": "Lockheed",
    "Toyota Motor Manufacturing": "Toyota",
    "Whirlpool Corporation": "Whirlpool",
}

# Personas per the story's authoritative table (HANDLER_MAP + pickRole).
PERSONAS: list[dict[str, object]] = [
    {
        "name": "Kaya Johnson",
        "role": "handler",
        "scope_all": False,
        "employers": [
            "Caterpillar Inc.",
            "General Electric (GE)",
            "Whirlpool Corporation",
            "John Deere",
        ],
    },
    {
        "name": "Dante Reyes",
        "role": "handler",
        "scope_all": False,
        "employers": ["Boeing", "Honeywell International Inc."],
    },
    {
        "name": "Marcus Chen",
        "role": "handler",
        "scope_all": False,
        "employers": ["Toyota Motor Manufacturing", "General Motors"],
    },
    {"name": "Sarah Williams", "role": "handler", "scope_all": False, "employers": ["3M Company"]},
    {"name": "Liam O'Sullivan", "role": "handler", "scope_all": False, "employers": ["John Deere"]},
    {
        "name": "Fatima Al-Mansoori",
        "role": "handler",
        "scope_all": False,
        "employers": ["Lockheed Martin"],
    },
    {"name": "David Bline", "role": "supervisor", "scope_all": True, "employers": []},
    {
        "name": "Jennifer Park",
        "role": "supervisor",
        "scope_all": False,
        "employers": ["Toyota Motor Manufacturing", "General Motors", "3M Company"],
    },
    {
        "name": "Ken Stoker",
        "role": "supervisor",
        "scope_all": False,
        "employers": ["John Deere", "Lockheed Martin"],
    },
    {"name": "David Bline", "role": "analyst", "scope_all": True, "employers": []},
]


def snake_token(display: str) -> str:
    """Mechanical display-string → snake_case enum token ('Settled & Closed' → settled_closed)."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", display.lower())).strip("_")


def cents(dollars: int) -> int:
    return dollars * 100


def iso_date(value: str | None) -> str | None:
    """Normalize dataset dates. The synthetic data contains impossible
    calendar dates (e.g. 2026-02-29); we roll them forward into the next
    month, a deterministic house rule for a known data quirk."""
    if value is None:
        return None
    y, m, d = (int(p) for p in value.split("-"))
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        rolled = date(y, m, 1) + timedelta(days=d - 1)
        print(f"  date quirk: {value} -> {rolled.isoformat()}", file=sys.stderr)
        return rolled.isoformat()


def main(html_path: str) -> None:
    html = Path(html_path).read_text(encoding="utf-8")
    match = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    if not match:
        raise SystemExit("ALL_CLAIMS not found")
    raw = json.loads(match.group(1))

    employers = sorted({c["employer"] for c in raw})
    employer_rows = [
        {
            "name": e,
            "short_name": SHORT_NAMES[e],
            "sector": next(c["sector"] for c in raw if c["employer"] == e),
        }
        for e in employers
    ]

    # One row per employee, not per claim: employee.employee_id is UNIQUE, so
    # the first claim a person appears on wins. Today's dataset happens to be
    # 1:1, but a second claim for one employee would otherwise abort migration
    # 0004 mid-`alembic upgrade head` on uq_employee_employee_id.
    employees_by_id: dict[str, dict[str, object]] = {}
    for c in raw:
        employees_by_id.setdefault(
            c["employeeId"],
            {
                "employee_id": c["employeeId"],
                "name": c["name"],
                "gender": c["gender"].lower(),
                "age": c["age"],
                "hire_date": iso_date(c["hireDate"]),
                "role": c["role"],
                "dept": c["dept"],
            },
        )
    employee_rows = list(employees_by_id.values())

    claim_rows = []
    for c in raw:
        claim_rows.append(
            {
                "claim_id": c["claimId"],
                "policy_num": c["policyNum"],
                "employer": c["employer"],  # resolved to FK at seed time
                "employee_id": c["employeeId"],  # business id, resolved to FK
                "handler": c["handler"],  # persona name, resolved to FK
                "plant": c["plant"],
                "state": c["state"],
                "region": c["region"],
                "doi": iso_date(c["doi"]),
                "froi_date": iso_date(c["froiDate"]),
                "assign_date": iso_date(c["assignDate"]),
                "approval_date": iso_date(c["approvalDate"]),
                "rtw_rec": iso_date(c["rtwRec"]),
                "actual_rtw": iso_date(c["actualRtw"]),
                "injury_type": c["injuryType"],
                "cause": c["cause"],
                "body_part": c["bodyPart"],
                "body_key": c["bodyKey"],
                "icd": c["icd"],
                "icd_desc": c["icdDesc"],
                "severity_score": c["sevScore"],
                "disability": c["disability"].lower(),
                "recovery": c["recovery"],
                "surgery_required": c["surgeryRequired"],
                "contraindications": c["contraindications"],
                "stage": c["stage"],
                "status": snake_token(c["status"]),
                "comm_status": snake_token(c["commStatus"]),
                "return_status": snake_token(c["returnStatus"]),
                "osha_recordable": c["oshaRecordable"],
                "litigation_flag": c["litigationFlag"],
                "attorney_rep": c["attorneyRep"],
                "fraud_score": c["fraudScore"],
                "fraud_flag": c["fraudFlag"],
                "paid_indemnity": cents(c["paidIndemnity"]),
                "paid_medical": cents(c["paidMedical"]),
                "paid_expense": cents(c["paidExpense"]),
                "reserve": cents(c["reserve"]),
                "aww": cents(c["aww"]),
                "days_recovery": c["daysRecovery"],
                "settlement_days": c["settlementDays"],
                "sla_pick_days": c["slaPickDays"],
                "sla_approve_days": c["slaApproveDays"],
                "sla_settle_days": c["slaSettleDays"],
            }
        )

    deferred = [
        {
            "claim_id": c["claimId"],
            "timeline": c.get("timeline"),
            "documents": c.get("documents"),
            "photos": c.get("photos"),
            "treatment_plan": c.get("treatmentPlan"),
            "prognosis": c.get("prognosis"),
            "cp_next_actions": c.get("cpNextActions"),
            "cp_similar_case": c.get("cpSimilarCase"),
            "cp_reserve_note": c.get("cpReserveNote"),
            "cp_fraud_indicators": c.get("cpFraudIndicators"),
        }
        for c in raw
    ]

    out = {
        "employers": employer_rows,
        "employees": employee_rows,
        "app_users": PERSONAS,
        "claims": claim_rows,
        "deferred": deferred,
    }
    dest = Path(__file__).parent / "seed_data.json"
    dest.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(
        f"wrote {dest} ({len(claim_rows)} claims, {len(employer_rows)} employers, "
        f"{len(employee_rows)} employees, {len(PERSONAS)} app_users)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../docs/Workers_Comp_Prototype.html")
