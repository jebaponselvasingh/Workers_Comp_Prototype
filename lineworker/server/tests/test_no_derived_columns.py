"""AC 4 regression guard: no derived value is a persisted column (AD-10)."""

from data.models import Claim, Employee

BANNED_CLAIM_COLUMNS = {
    "days_open",
    "risk",
    "risk_level",
    "severity",  # band is a pure function of severity_score
    "siu_review",
    "rtw_blocked",
    "payment_due",
    "next_payment_date",
    "total_paid",
    "total_incurred",
    "supervisor",  # supervisor visibility is employer-scope-based (AD-7)
}

BANNED_EMPLOYEE_COLUMNS = {"initials", "age_group"}


def test_claim_has_no_derived_columns() -> None:
    columns = {c.name for c in Claim.__table__.columns}
    assert not columns & BANNED_CLAIM_COLUMNS


def test_employee_has_no_derived_columns() -> None:
    columns = {c.name for c in Employee.__table__.columns}
    assert not columns & BANNED_EMPLOYEE_COLUMNS
