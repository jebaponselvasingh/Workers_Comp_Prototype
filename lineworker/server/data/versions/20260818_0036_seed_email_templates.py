"""The six claim-aware stakeholder letters (Story 4.3, AC 3).

The prototype rebuilds these in the browser every time a template button is
pressed (`loadEmailTemplate`, line 1955), interpolating whichever claim the
page happened to be holding. Here the text is **data**: six rows, seeded once,
merged on the server (AD-1) against a claim resolved under the caller's scope.

**The subject and body are ported verbatim**, with two mechanical
substitutions and nothing else:

1. Each JS interpolation becomes a `{{placeholder}}` named after the column it
   reads — `${c.claimId}` → `{{claim_id}}`, `${c.name}` → `{{worker_name}}`,
   `${c.doi}` → `{{doi}}`, `${c.injuryType}` → `{{injury_type}}`,
   `${c.cause}` → `{{cause}}`, `${c.bodyPart}` → `{{body_part}}`,
   `${c.icd}` → `{{icd}}`, `${c.daysOpen}` → `{{days_open}}`,
   `${c.stage}` → `{{stage}}`, `${c.status}` → `{{status}}`,
   `${handler}` → `{{handler_name}}`. Eleven placeholders, matching the eleven
   interpolations in the prototype's six bodies.
2. The prototype's salutation fallbacks (`${c?c.name:'[Employee]'}`) are
   **not** ported, because the state they cover cannot occur here: the merge
   endpoint requires a claim, so `{{worker_name}}` always resolves. A letter
   full of holes was the prototype's answer to composing with nothing selected;
   this console's is to disable the six buttons and say why.

**Everything in square brackets stays literal.** `[Transitional Duty — to be
completed by supervisor]`, `[Please add current activity summary]`, `[Please
add next steps]`, `[IME Physician / IME Coordinator]`, `$[AMOUNT]`,
`[Compromise & Release / Stipulation]` and `[RATING]%` are prompts to the
handler, not merge fields — AD-2 is explicit that the settlement figures are
handler-filled and must not be computed from the claim's financials.

**The recipient keys are mapped onto `MeetingParticipant`, and the mapping is
on the record.** The prototype's email modal keys its checkboxes `employer` and
`physician` where its meeting modal keys the same two roles `employer_hr` and
`treating_physician`; Story 4.3 reuses 4.1's six-value vocabulary so that
convert-to-email is an identity mapping rather than a translation table, and
the longer tokens win. The values below are the enum's.

**Every set is written in the vocabulary's own order** — `employee`,
`employer_hr`, `ncm`, `treating_physician`, `supervisor`, `attorney` — which is
the same invariant `normalise_recipients` enforces on a send, and the same
reason: two templates addressing the same three roles must be byte-identical,
and a set stored in the order somebody happened to type it is a diff waiting to
happen. Two of the six were written in the prototype's own emphasis order
(`ncm_referral` led with `ncm`, `ime_request` with `treating_physician`); the
order is not information — the checkbox grid renders the six in one fixed order
either way — so they are sorted here and `tests/test_emails.py` asserts it.

**The constant is in the migration rather than in `data/seed/*.json`**, which
is where 0033 drew the line and this is well inside it: six rows with no
extractor, no upstream file to stay in step with, and text that is a frozen
historical record exactly as a migration is. `tests/test_emails.py` asserts the
seeded shape against `EmailPriority`/`MeetingParticipant` and against the
placeholder vocabulary the merge knows.

Revision ID: 0036_seed_email_templates
Revises: 0035_email_tables
Create Date: 2026-08-18

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0036_seed_email_templates"
down_revision: str | None = "0035_email_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The six templates, in the composer's button order — the prototype's own
#: object order, which is the order the six `.tpl-btn` buttons render in.
#: [Source: docs/Workers_Comp_Prototype.html lines 599-604, 1955-1999]
EMAIL_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_key": "three_point_contact",
        "label": "3-Point Contact",
        "subject_template": "3-Point Contact — WC Claim {{claim_id}}",
        "body_template": (
            "Dear {{worker_name}},\n"
            "\n"
            "I am {{handler_name}}, your assigned claims adjuster for claim "
            "{{claim_id}}. I am reaching out to make initial contact regarding "
            "your workers' compensation claim.\n"
            "\n"
            "Claim details:\n"
            "• Claim ID: {{claim_id}}\n"
            "• Date of Injury: {{doi}}\n"
            "• Injury: {{injury_type}}\n"
            "\n"
            "Please contact me at your earliest convenience so we can discuss "
            "your claim status, current medical treatment, and return-to-work "
            "options.\n"
            "\n"
            "Best regards,\n"
            "{{handler_name}}\n"
            "WC Claims Adjuster"
        ),
        "default_recipients": ["employee", "employer_hr", "treating_physician"],
    },
    {
        "template_key": "rtw_offer",
        "label": "RTW Offer",
        "subject_template": "Return-to-Work Offer — Claim {{claim_id}} — {{worker_name}}",
        "body_template": (
            "Dear {{worker_name}},\n"
            "\n"
            "Based on your treating physician's updated work restrictions, we "
            "are pleased to offer you a modified/light duty return-to-work "
            "opportunity.\n"
            "\n"
            "Claim: {{claim_id}}\n"
            "Offered Role: [Transitional Duty — to be completed by supervisor]\n"
            "Starting Hours: 4 hours/day, graduated to full hours\n"
            "Wage: Pre-injury rate maintained\n"
            "\n"
            "Physician's restrictions will be honored. Please respond within 5 "
            "business days.\n"
            "\n"
            "Best regards,\n"
            "{{handler_name}}"
        ),
        "default_recipients": ["employee", "employer_hr", "ncm"],
    },
    {
        "template_key": "ncm_referral",
        "label": "NCM Referral",
        "subject_template": "NCM Referral — Claim {{claim_id}} — {{worker_name}}",
        "body_template": (
            "Dear Nurse Case Manager,\n"
            "\n"
            "I am referring the following claim for nurse case management "
            "services:\n"
            "\n"
            "Worker: {{worker_name}}\n"
            "Claim: {{claim_id}}\n"
            "Injury: {{injury_type}}\n"
            "Cause: {{cause}}\n"
            "Days Open: {{days_open}}\n"
            "\n"
            "Reason for referral: Complex medical management, RTW coordination "
            "needed.\n"
            "\n"
            "Please contact the injured worker and treating physician to "
            "establish care coordination.\n"
            "\n"
            "Best regards,\n"
            "{{handler_name}}"
        ),
        "default_recipients": ["employer_hr", "ncm", "treating_physician"],
    },
    {
        "template_key": "status_update",
        "label": "Status Update",
        "subject_template": "Claim Status Update — {{claim_id}} — {{worker_name}}",
        "body_template": (
            "Dear Team,\n"
            "\n"
            "This is a status update for claim {{claim_id}}:\n"
            "\n"
            "Worker: {{worker_name}}\n"
            "Current Stage: {{stage}}\n"
            "Status: {{status}}\n"
            "Days Open: {{days_open}}\n"
            "Injury: {{injury_type}}\n"
            "\n"
            "Current activity: [Please add current activity summary]\n"
            "Next steps: [Please add next steps]\n"
            "\n"
            "Please contact me with any questions.\n"
            "\n"
            "Best regards,\n"
            "{{handler_name}}"
        ),
        "default_recipients": ["employer_hr", "supervisor"],
    },
    {
        "template_key": "ime_request",
        "label": "IME Request",
        "subject_template": "IME Request — Claim {{claim_id}} — {{worker_name}}",
        "body_template": (
            "Dear [IME Physician / IME Coordinator],\n"
            "\n"
            "We are requesting an Independent Medical Examination for the "
            "following claim:\n"
            "\n"
            "Worker: {{worker_name}}\n"
            "Claim ID: {{claim_id}}\n"
            "Date of Injury: {{doi}}\n"
            "Injury: {{injury_type}} — {{body_part}}\n"
            "ICD-10: {{icd}}\n"
            "\n"
            "Questions for IME physician:\n"
            "1. Has the claimant reached MMI?\n"
            "2. Are current work restrictions appropriate?\n"
            "3. What is the permanent impairment rating, if any?\n"
            "\n"
            "Medical records attached. Please confirm availability.\n"
            "\n"
            "Best regards,\n"
            "{{handler_name}}"
        ),
        "default_recipients": ["ncm", "treating_physician"],
    },
    {
        "template_key": "settlement_notice",
        "label": "Settlement Notice",
        "subject_template": "Settlement Notice — Claim {{claim_id}} — {{worker_name}}",
        "body_template": (
            "Dear {{worker_name}},\n"
            "\n"
            "We are writing regarding the settlement of your workers' "
            "compensation claim.\n"
            "\n"
            "Claim: {{claim_id}}\n"
            "Injury: {{injury_type}}\n"
            "\n"
            "We propose to resolve this claim as follows:\n"
            "• Settlement amount: $[AMOUNT]\n"
            "• Settlement type: [Compromise & Release / Stipulation]\n"
            "• PPD rating: [RATING]%\n"
            "\n"
            "Please review with your representative and respond within 10 "
            "business days.\n"
            "\n"
            "Best regards,\n"
            "{{handler_name}}"
        ),
        "default_recipients": ["employee", "attorney"],
    },
)


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    email_template = sa.Table("email_template", meta, autoload_with=bind)
    bind.execute(email_template.insert(), [dict(template) for template in EMAIL_TEMPLATES])


def downgrade() -> None:
    """Remove the six seeded templates, and **only** those six.

    A bare `DELETE FROM email_template` is the shape 0011, 0021 and 0027 use and
    it would be harmless today, because nothing but this revision writes the
    table. `template_key IN (…)` is used anyway, 0033's ruling: the predicate
    should say what this revision actually wrote, so that a later revision
    adding a seventh template does not have its row silently removed by a
    downgrade past this one.

    `email_log.template_id` references these rows, so a database with logged
    templated emails refuses the delete on the foreign key rather than orphaning
    them — which is the honest failure. Downgrading past 0035 drops `email_log`
    first and this becomes reachable again.
    """
    bind = op.get_bind()
    meta = sa.MetaData()
    email_template = sa.Table("email_template", meta, autoload_with=bind)
    bind.execute(
        email_template.delete().where(
            email_template.c.template_key.in_(
                [template["template_key"] for template in EMAIL_TEMPLATES]
            )
        )
    )
