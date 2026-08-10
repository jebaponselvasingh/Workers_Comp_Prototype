"""Snake_case enum values per the conventions — the UI owns display labels.

Status values are the dataset's display strings mapped mechanically
(Story 3.5 depends on these exact tokens).
"""

from enum import StrEnum


class Stage(StrEnum):
    intake = "intake"
    investigation = "investigation"
    treatment = "treatment"
    settled = "settled"


class ClaimStatus(StrEnum):
    initial = "initial"
    ch_assessment_process = "ch_assessment_process"
    ch_approved = "ch_approved"
    denied = "denied"
    settled = "settled"
    settled_closed = "settled_closed"


class CommStatus(StrEnum):
    fnol_received = "fnol_received"
    incomplete_information = "incomplete_information"
    need_for_additional_information = "need_for_additional_information"
    documents_received_and_approved = "documents_received_and_approved"
    initial_approval_provided_treatment_underway = "initial_approval_provided_treatment_underway"


class ReturnStatus(StrEnum):
    under_treatment = "under_treatment"
    returned_and_under_therapy = "returned_and_under_therapy"
    returned_and_fully_recovered = "returned_and_fully_recovered"


class Disability(StrEnum):
    temporary = "temporary"
    permanent = "permanent"


class Gender(StrEnum):
    female = "female"
    male = "male"
    other = "other"


class UserRole(StrEnum):
    handler = "handler"
    supervisor = "supervisor"
    analyst = "analyst"
