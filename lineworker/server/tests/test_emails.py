"""Story 4.3 — the six templates, the merge, the send-as-log and its list.

The pure half is a good deal larger here than in `test_diary_notes.py`, and
deliberately so: `render_template` is a substitution engine, `MERGE_FIELDS` is
a vocabulary, and `STAGE_PROSE`/`STATUS_PROSE`/`MEETING_TYPE_PROSE` are three
maps that have to stay total over their enums. Every one of those is checkable
without a session, and a laptop with no Postgres is exactly where somebody
would most like them to run — so `requires_db` is a per-test decorator here,
never a `pytestmark` (`test_meetings.py` and `test_diary_notes.py` carry the
same correction).

What needs a transaction is everything the I/O matrix is actually about — that
the row and its audit event land together, that a claim outside the caller's
book is refused before anything is written, that one handler's sent log is
invisible to another, that walking the cursor visits every row exactly once in
newest-first order, and that `total` is answered on the first page alone.

Driven through the app for the contract tests and through the command for the
atomicity ones, because a rollback is not observable over HTTP —
`test_meetings.py`'s division.

**These tests mutate the seeded portfolio**, and like diary notes they start
from nothing: `email_log` has no seed (only `email_template` does). The
module-scoped `seeded_db_url` fixture rebuilds the schema for this file, so the
rows created below are contained here — but they persist *between* tests in
this module, which is why nothing hardcodes a count the earlier tests decide.
"""

import base64
import inspect
import json
import locale
import pathlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, EmailLog, TimelineEvent
from data.models.enums import ClaimStatus, EmailPriority, MeetingParticipant, MeetingType, Stage
from data.repositories.identity import employer_ids_for
from services.claims.edit import EditNotPermitted, InvalidPatch
from services.claims.emails import (
    ENTITY as EMAIL_ENTITY,
)
from services.claims.emails import (
    MAX_BODY_LENGTH,
    MAX_SUBJECT_LENGTH,
    MEETING_AGENDA_FALLBACK,
    MEETING_DRAFT_BODY,
    MEETING_DRAFT_BODY_UNTAGGED,
    MEETING_DRAFT_SUBJECT,
    MEETING_DRAFT_SUBJECT_UNTAGGED,
    MEETING_LOCATION_FALLBACK,
    MEETING_MERGE_FIELDS,
    MEETING_TYPE_PROSE,
    MERGE_FIELDS,
    RECIPIENTS_FIELD,
    SEND_ACTION,
    STAGE_PROSE,
    STATUS_PROSE,
    SUBJECT_FIELD,
    TEMPLATE_KEY_FIELD,
    Cursor,
    EmailClaimNotVisible,
    EmailTemplateNotFound,
    InvalidCursor,
    UnknownMergeField,
    _meeting_date_time,
    decode_cursor,
    encode_cursor,
    list_email_logs,
    list_email_templates,
    meeting_email_draft,
    merged_template,
    normalise_body,
    normalise_priority,
    normalise_recipients,
    normalise_subject,
    normalise_template_key,
    render_template,
    send_email,
)
from services.claims.meetings import MeetingNotVisible, create_meeting
from tests import seed_fixture
from tests.conftest import requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
JENNIFER = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")

TODAY = date(2026, 8, 18)
NOW = datetime(2026, 8, 18, 9, 30, tzinfo=UTC)

#: The six keys, labels and default recipient sets, restated here as an
#: independent oracle. A test that imported `EMAIL_TEMPLATES` from the migration
#: would agree with it however wrong it was — `seed_fixture`'s standing argument
#: for reading (or in this case re-typing) the expectation separately.
EXPECTED_TEMPLATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("three_point_contact", "3-Point Contact", ("employee", "employer_hr", "treating_physician")),
    ("rtw_offer", "RTW Offer", ("employee", "employer_hr", "ncm")),
    ("ncm_referral", "NCM Referral", ("employer_hr", "ncm", "treating_physician")),
    ("status_update", "Status Update", ("employer_hr", "supervisor")),
    ("ime_request", "IME Request", ("ncm", "treating_physician")),
    ("settlement_notice", "Settlement Notice", ("employee", "attorney")),
)

#: The bracketed prompts the prototype leaves for the handler to fill in. They
#: are template *text* and must survive a merge byte for byte — AD-2 is explicit
#: that the settlement figures are not computed from the claim's financials.
HANDLER_FILL_TEXT: tuple[tuple[str, str], ...] = (
    ("rtw_offer", "[Transitional Duty — to be completed by supervisor]"),
    ("status_update", "[Please add current activity summary]"),
    ("status_update", "[Please add next steps]"),
    ("ime_request", "[IME Physician / IME Coordinator]"),
    ("settlement_notice", "$[AMOUNT]"),
    ("settlement_notice", "[Compromise & Release / Stipulation]"),
    ("settlement_notice", "[RATING]%"),
)

_LEFTOVER = re.compile(r"\{\{.*?\}\}")

#: A placeholder the merge can both *see* and resolve. Deliberately stricter
#: than `render_template`'s own pattern (which tolerates inner whitespace) and
#: used below to assert that no other brace pair exists in any seeded text: a
#: `{{Claim_Id}}` or a `{{ claim id }}` matches neither this nor the engine, so
#: the vocabulary tests would pass while the letter shipped with braces in it.
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")

_SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- the seed's shape, without a database -------------------------------


def _migration() -> Any:
    """The 0036 seed module, loaded by path.

    Migrations are not an importable package (the filenames start with a date),
    so the constant is reached the way Alembic reaches the module.
    """
    import importlib.util

    path = _SERVER_ROOT / "data" / "versions" / "20260818_0036_seed_email_templates.py"
    spec = importlib.util.spec_from_file_location("seed_email_templates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _structure_migration() -> Any:
    import importlib.util

    path = _SERVER_ROOT / "data" / "versions" / "20260818_0035_email_tables.py"
    spec = importlib.util.spec_from_file_location("email_tables", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all_template_text() -> tuple[tuple[str, str], ...]:
    """Every letter this story can compose, as `(name, text)` pairs.

    The six seeded templates and both meeting-draft variants — everything that
    reaches `render_template`, so the brace-pair guard below covers the whole
    of it rather than the seed alone.
    """
    seeded = tuple(
        (str(row["template_key"]), str(row["subject_template"]) + str(row["body_template"]))
        for row in _migration().EMAIL_TEMPLATES
    )
    return (
        *seeded,
        ("meeting_draft", MEETING_DRAFT_SUBJECT + MEETING_DRAFT_BODY),
        (
            "meeting_draft_untagged",
            MEETING_DRAFT_SUBJECT_UNTAGGED + MEETING_DRAFT_BODY_UNTAGGED,
        ),
    )


_ALL_TEMPLATE_TEXT = _all_template_text()


def test_the_priority_enum_and_the_migrations_member_tuple_agree() -> None:
    """The migration writes its members out literally — a frozen historical
    record — so the two can drift, and the drift would be a column that cannot
    hold a value the application produces. 0032 pins `MeetingType` the same way.
    """
    assert (
        tuple(member.value for member in EmailPriority) == _structure_migration().EMAIL_PRIORITIES
    )


def test_the_seed_is_the_six_templates_in_the_composers_button_order() -> None:
    """AC 3's reference data, checked against an oracle typed out separately."""
    seeded = _migration().EMAIL_TEMPLATES
    assert (
        tuple(
            (row["template_key"], row["label"], tuple(row["default_recipients"])) for row in seeded
        )
        == EXPECTED_TEMPLATES
    )


def test_every_seeded_recipient_is_a_member_of_the_shared_vocabulary() -> None:
    """One six-value set, shared with a meeting's participants, so that
    convert-to-email is an identity mapping rather than a translation table.

    The prototype's email modal keys two of them `employer` and `physician`;
    those are the same roles as the meeting modal's `employer_hr` and
    `treating_physician`, and the longer tokens win.
    """
    known = {member.value for member in MeetingParticipant}
    vocabulary = [member.value for member in MeetingParticipant]
    for row in _migration().EMAIL_TEMPLATES:
        recipients = list(row["default_recipients"])
        assert set(recipients) <= known, row["template_key"]
        # No duplicates.
        assert len(set(recipients)) == len(recipients), row["template_key"]
        # **And stored in the vocabulary's own order**, which the seed's own
        # docstring claims and this used to leave unasserted: two templates
        # addressing the same set have to be byte-identical, and it is the same
        # invariant `normalise_recipients` enforces on a send. Two rows were
        # written in the prototype's emphasis order and this is the assertion
        # that caught them.
        assert recipients == sorted(recipients, key=vocabulary.index), row["template_key"]


def test_every_placeholder_the_seed_uses_is_one_the_merge_can_resolve() -> None:
    """The half of "no `{{…}}` survives a merge" that does not need a database.

    `render_template` raises on an unknown token, so a template naming one would
    500 the merge endpoint for that claim rather than render a hole — this
    catches it in the seed instead.
    """
    for row in _migration().EMAIL_TEMPLATES:
        used = set(_PLACEHOLDER.findall(row["subject_template"] + row["body_template"]))
        assert used <= set(MERGE_FIELDS), (row["template_key"], sorted(used - set(MERGE_FIELDS)))


@pytest.mark.parametrize(("name", "text"), _ALL_TEMPLATE_TEXT)
def test_no_brace_pair_survives_that_the_merge_cannot_even_see(name: str, text: str) -> None:
    """The hole in the two vocabulary tests above, closed.

    Both of them (and `render_template` itself) look for `{{lower_snake}}`. A
    seeded `{{Claim_Id}}`, `{{days_open2}}` or `{{ claim id }}` matches none of
    those patterns, so it is not an unknown token — it is not a token at all:
    nothing raises, nothing resolves it, and the braces go out to a physician
    inside a letter this console composed. Counting brace pairs against the
    recognised pattern is what turns "every placeholder resolves" into "every
    brace pair *is* a placeholder".
    """
    recognised = len(_PLACEHOLDER.findall(text))
    assert text.count("{{") == recognised, name
    assert text.count("}}") == recognised, name


def test_every_merge_field_is_used_by_at_least_one_template() -> None:
    """The other direction: a placeholder nothing renders is as much a defect as
    one nothing resolves — it is a claim column being read, and shipped to a
    stakeholder, for nobody."""
    used: set[str] = set()
    for row in _migration().EMAIL_TEMPLATES:
        used |= set(_PLACEHOLDER.findall(row["subject_template"] + row["body_template"]))
    assert set(MERGE_FIELDS) == used


def test_the_meeting_draft_only_names_its_own_placeholders() -> None:
    used = set(_PLACEHOLDER.findall(MEETING_DRAFT_SUBJECT + MEETING_DRAFT_BODY))
    assert used == set(MEETING_MERGE_FIELDS)


def test_the_claim_less_draft_names_the_same_letter_minus_the_claim() -> None:
    """The untagged pair is the same letter with one clause removed, so its
    vocabulary is the tagged one's minus exactly the two claim placeholders —
    not a second letter that could drift into naming something else."""
    tagged = set(_PLACEHOLDER.findall(MEETING_DRAFT_SUBJECT + MEETING_DRAFT_BODY))
    untagged = set(
        _PLACEHOLDER.findall(MEETING_DRAFT_SUBJECT_UNTAGGED + MEETING_DRAFT_BODY_UNTAGGED)
    )
    assert untagged == tagged - {"claim_id", "claim_reference"}


@pytest.mark.parametrize(("template_key", "literal"), HANDLER_FILL_TEXT)
def test_the_handler_fill_prompts_are_template_text_not_merge_fields(
    template_key: str, literal: str
) -> None:
    """AD-2: `$[AMOUNT]` and `[RATING]%` stay handler-filled, and the rest of the
    square-bracket prompts are prose. They are in the seeded text, so a merge
    cannot remove them — this pins that they were ported at all."""
    row = next(r for r in _migration().EMAIL_TEMPLATES if r["template_key"] == template_key)
    assert literal in row["body_template"]


def test_no_template_carries_the_prototypes_claimless_fallbacks() -> None:
    """The prototype renders `${c?c.name:'[Employee]'}` and produces letters full
    of holes when nothing is selected. The merge requires a claim instead, so
    the fallback has no state to cover and must not have been ported as text."""
    for row in _migration().EMAIL_TEMPLATES:
        assert "[Employee]" not in row["body_template"]
        assert "[Handler]" not in row["body_template"]


# --- the prose maps, without a database ---------------------------------


def test_the_prose_maps_are_total_over_their_enums() -> None:
    """A merged body is a stored document composed on the server (AD-1), not a
    component, so it carries prose rather than tokens — and a new enum member
    must fail here rather than merge as "Current Stage: treatment"."""
    assert set(STAGE_PROSE) == set(Stage)
    assert set(STATUS_PROSE) == set(ClaimStatus)
    assert set(MEETING_TYPE_PROSE) == set(MeetingType)


def test_the_prose_matches_the_labels_the_browser_renders() -> None:
    """Copied verbatim from `web/src/features/claim-detail/labels.ts` and
    `web/src/features/diary/labels.ts`. Spot-checked on the three that carry
    punctuation a mechanical title-casing would get wrong."""
    assert STATUS_PROSE[ClaimStatus.ch_assessment_process] == "CH Assessment Process"
    assert STATUS_PROSE[ClaimStatus.settled_closed] == "Settled — Closed"
    assert MEETING_TYPE_PROSE[MeetingType.claim_review_supervisor] == "Claim Review — Supervisor"


# --- the merge engine, without a database -------------------------------


def test_render_substitutes_every_occurrence() -> None:
    assert render_template("{{a}} and {{a}} and {{b}}", {"a": "x", "b": "y"}) == "x and x and y"


def test_render_raises_on_a_token_it_cannot_resolve() -> None:
    """**Loud, not empty** — this is what makes "no `{{…}}` survives a merge" an
    enforced property rather than an observation. A template naming an unknown
    field is a defect in reference data, so it raises rather than answering a
    4xx: no request is at fault."""
    with pytest.raises(UnknownMergeField):
        render_template("Dear {{nobody}},", {"worker_name": "Derek Hill"})


def test_a_substituted_value_is_not_re_scanned() -> None:
    """No recursion: the values come from claim columns, and a worker whose name
    contained a placeholder must not reach a second round of substitution."""
    assert render_template("{{a}}", {"a": "{{b}}"}) == "{{b}}"


def test_literal_braces_and_square_brackets_survive() -> None:
    """`str.format` would choke on the first and this must not: a letter may
    legitimately contain a brace, and every square-bracket prompt is text."""
    assert render_template("cost { $[AMOUNT] } [RATING]%", {}) == "cost { $[AMOUNT] } [RATING]%"


# --- validation, without a database -------------------------------------


def test_a_subject_is_trimmed_and_kept() -> None:
    assert normalise_subject("  Status update  ") == "Status update"


@pytest.mark.parametrize("raw", ["", "   ", "\n", "\t "])
def test_an_empty_subject_is_refused(raw: str) -> None:
    """AC 2's server half. The prototype answers this with
    `alert('Please enter a subject.')`; NFR-3 forbids the native dialog, so it
    is a refusal the composer renders inline at the field."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise_subject(raw)
    assert SUBJECT_FIELD in str(refusal.value)


def test_a_subject_with_a_line_break_is_refused() -> None:
    """The card draws `✉ {subject}` on one line beside its badge."""
    with pytest.raises(InvalidPatch):
        normalise_subject("first line\nsecond line")


def test_a_subject_past_the_cap_is_refused_by_the_command_whatever_the_schema_did() -> None:
    """AD-16: an Epic 6 agent tool is a caller that never met the Pydantic model."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise_subject("x" * (MAX_SUBJECT_LENGTH + 1))
    assert str(MAX_SUBJECT_LENGTH) in str(refusal.value)


def test_a_body_is_prose_and_keeps_its_whitespace() -> None:
    assert normalise_body("line one\r\nline two\tindented") == "line one\r\nline two\tindented"


def test_an_empty_body_is_stored_as_nothing_rather_than_refused() -> None:
    assert normalise_body("   ") is None
    assert normalise_body(None) is None


def test_a_body_carrying_a_nul_is_a_422_and_not_a_500() -> None:
    with pytest.raises(InvalidPatch):
        normalise_body("before\x00after")


def test_a_body_past_the_cap_is_refused() -> None:
    with pytest.raises(InvalidPatch) as refusal:
        normalise_body("x" * (MAX_BODY_LENGTH + 1))
    assert str(MAX_BODY_LENGTH) in str(refusal.value)


def test_recipients_are_de_duplicated_and_re_ordered_into_enum_order() -> None:
    assert normalise_recipients(["attorney", "employee", "attorney"]) == (
        MeetingParticipant.employee,
        MeetingParticipant.attorney,
    )


def test_no_recipient_is_refused_where_the_prototype_allowed_it() -> None:
    """The prototype's `sendEmail` logs a send with nothing ticked. AC 1 asks
    for at least one, and an email addressed to nobody records nothing about who
    was told."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise_recipients([])
    assert RECIPIENTS_FIELD in str(refusal.value)


def test_an_unknown_recipient_token_is_refused_and_never_echoed() -> None:
    """Refused rather than dropped (AD-16), and the token stays out of the
    message (AD-11) — it is caller-supplied text."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise_recipients(["ceo"])
    assert "ceo" not in str(refusal.value)


@pytest.mark.parametrize(
    "raw",
    [
        # Not iterable at all: the `for` itself raises `TypeError`.
        42,
        None,
        # A bare string *is* a `Sequence`, and iterating it would refuse a
        # plausible call one character at a time.
        "employee",
        # Iterable, but not a list of tokens: a mapping would silently be read
        # as its keys.
        {"employee": True},
        # Unhashable member — `MeetingParticipant(["ceo"])` raises `TypeError`
        # on the way into the set, not `ValueError`.
        [["ceo"]],
        [42],
    ],
)
def test_an_unusable_recipient_shape_is_a_422_and_not_a_500(raw: object) -> None:
    """The guard `normalise_priority` already had, applied to the field that
    needed it more: only an *unknown token* raises `ValueError`, and every other
    way of getting this argument wrong raises `TypeError` — which escapes as a
    500 for what is a malformed request (AD-16's caller is not the SPA)."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise_recipients(raw)
    assert RECIPIENTS_FIELD in str(refusal.value)


@pytest.mark.parametrize("raw", [42, ["rtw_offer"], {"key": "rtw_offer"}, object()])
def test_a_template_key_that_is_not_text_is_a_422_and_not_a_driver_error(raw: object) -> None:
    """The key is a free input like the other four: a non-string reaches the
    repository with nothing to encode it as and surfaces as a driver error."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise_template_key(raw)
    assert TEMPLATE_KEY_FIELD in str(refusal.value)


def test_a_template_key_is_type_checked_and_nothing_else() -> None:
    """It must not pre-empt the 404: whether a key names one of the six is
    `select_email_template`'s answer, and an absent key and a malformed one get
    the same one. So nothing is trimmed and nothing is pattern-matched here."""
    assert normalise_template_key(None) is None
    assert normalise_template_key("  Rtw Offer  ") == "  Rtw Offer  "


@pytest.mark.parametrize("raw", ["screaming", 42, ["high"], None])
def test_an_unusable_priority_is_a_422_and_not_a_500(raw: object) -> None:
    """`EmailPriority(["high"])` raises `TypeError` on an unhashable argument,
    which would escape a bare `except ValueError` as a 500."""
    with pytest.raises(InvalidPatch):
        normalise_priority(raw)


def test_a_refusal_never_echoes_the_letter_back(caplog: pytest.LogCaptureFixture) -> None:
    """AD-11. A merged letter carries the worker's name, their date of injury and
    their ICD-10 code, so the one thing a refusal must not do is quote it."""
    secret = "Worker disclosed a prior back injury\x00"
    with pytest.raises(InvalidPatch) as refusal:
        normalise_body(secret)
    assert "prior back injury" not in str(refusal.value)
    assert "prior back injury" not in caplog.text


# --- the letter's clock line, without a database -------------------------


def test_the_clock_line_reads_the_way_the_prototype_wrote_it() -> None:
    """The prototype's `toLocaleString` option bag: "Monday, August 24 at
    10:30 AM", including its zero-padded hour. Midnight and noon are the two
    the 12-hour arithmetic has to get right, and an all-day meeting drops the
    clock rather than printing 12:00 AM."""
    when = date(2026, 8, 24)
    assert _meeting_date_time(when, time(10, 30)) == "Monday, August 24 at 10:30 AM"
    assert _meeting_date_time(when, time(0, 5)) == "Monday, August 24 at 12:05 AM"
    assert _meeting_date_time(when, time(12, 0)) == "Monday, August 24 at 12:00 PM"
    assert _meeting_date_time(when, time(17, 45)) == "Monday, August 24 at 05:45 PM"
    assert _meeting_date_time(when, None) == "Monday, August 24"


def test_the_clock_line_does_not_read_the_containers_locale() -> None:
    """A stored, audited document must not be worded by an environment variable.

    `%A`, `%B` and `%p` all read `LC_TIME` from the process, so a container
    whose locale image differs from the developer's would draft "Montag,
    August 24" — or, in several locales, an empty `%p` that turns "10:30 AM"
    into "10:30 " and a morning appointment into an ambiguous one. Every other
    formatted instant in this console is explicitly `en-US`, in the browser.

    Asserted twice, because either half alone is weak: the sweep is the real
    thing but is silent on an image with no other locale installed, and the
    source guard holds everywhere but only says the directives are absent.
    """
    when, at = date(2026, 8, 24), time(10, 30)
    expected = "Monday, August 24 at 10:30 AM"

    previous = locale.setlocale(locale.LC_TIME)
    proved: list[str] = []
    try:
        for candidate in ("de_DE.UTF-8", "fr_FR.UTF-8", "ja_JP.UTF-8", "de_DE", "fr_FR"):
            try:
                locale.setlocale(locale.LC_TIME, candidate)
            except locale.Error:
                continue
            assert _meeting_date_time(when, at) == expected, candidate
            if when.strftime("%A") != "Monday":
                # The locale really is in force — a `strftime` implementation
                # would have failed the assertion above.
                proved.append(candidate)
    finally:
        locale.setlocale(locale.LC_TIME, previous)

    body = inspect.getsource(_meeting_date_time).replace(_meeting_date_time.__doc__ or "", "")
    assert "%A" not in body and "%B" not in body and "%p" not in body, proved


# --- the cursor, without a database -------------------------------------


def test_a_cursor_round_trips() -> None:
    cursor = Cursor(last_sent_at=NOW, last_id=42, limit=25, issued_on=TODAY)
    assert decode_cursor(encode_cursor(cursor), TODAY) == cursor


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64-at-all!!",
        "e30",
        # A forged page size past the route's ceiling — the cursor is *reused*
        # when the request omits a limit, so this is the way round the validator
        # if it were not bounded here.
        encode_cursor(Cursor(NOW, 1, 100_000, TODAY)),
        # A position that cannot exist.
        encode_cursor(Cursor(NOW, 0, 25, TODAY)),
        # Dated in the future: no cursor this service issued can name one.
        encode_cursor(Cursor(NOW, 1, 25, TODAY + timedelta(days=1))),
        # Older than MAX_CURSOR_AGE.
        encode_cursor(Cursor(NOW, 1, 25, TODAY - timedelta(days=8))),
    ],
)
def test_a_cursor_that_does_not_describe_this_list_is_refused(raw: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


def test_a_cursor_carrying_a_naive_timestamp_is_refused_at_the_boundary() -> None:
    naive = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "n": NOW.replace(tzinfo=None).isoformat(),
                    "i": 1,
                    "l": 25,
                    "s": TODAY.isoformat(),
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(InvalidCursor):
        decode_cursor(naive, TODAY)


# --- no egress ----------------------------------------------------------


#: Every way this codebase could name a module at import time: the two
#: statements, and the two dynamic forms. A **substring** scan for
#: `"import smtplib"` was what this used to do, and it was wrong in both
#: directions — a sentence in a docstring saying "no `import smtplib` here"
#: failed it, and `importlib.import_module("smtplib")` sailed through.
_IMPORT_STATEMENT = re.compile(
    r"^[ \t]*(?:from[ \t]+([.\w]+)[ \t]+import\b|import[ \t]+([^\n#]+))", re.MULTILINE
)
_DYNAMIC_IMPORT = re.compile(r"""(?:import_module|__import__)\(\s*["']([.\w]+)["']""")


def imported_modules(source: str) -> set[str]:
    """Every module name `source` imports, statically or by name."""
    found: set[str] = set()
    for from_module, plain in _IMPORT_STATEMENT.findall(source):
        if from_module:
            found.add(from_module)
            continue
        for clause in plain.split(","):
            # `import a.b as c` → `a.b`.
            found.add(clause.strip().split(" as ")[0].strip())
    found |= set(_DYNAMIC_IMPORT.findall(source))
    return {name for name in found if name}


def server_sources() -> list[pathlib.Path]:
    """Every `.py` file this project owns — the vendored tree excluded.

    `.venv/` holds thousands of third-party modules, several of which import
    `smtplib` perfectly legitimately, and scanning it makes every structural
    assertion in this file both slow and a lie about what the project does.
    """
    paths = [
        path
        for path in sorted(_SERVER_ROOT.rglob("*.py"))
        if not path.relative_to(_SERVER_ROOT).as_posix().startswith(".venv/")
        and "__pycache__" not in path.parts
    ]
    assert paths, "nothing scanned — did the server root move?"
    return paths


def test_nothing_in_the_server_imports_a_mail_library() -> None:
    """The deferred-egress decision, asserted structurally rather than trusted.

    "Send" is a log row; real SMTP is a later epic with its own compliance
    review. The failure this guards against is not a malicious one — it is a
    later story reading "send email" and reaching for `smtplib` because the
    function is called `send_email`.

    Import-shaped rather than substring-shaped: prose about egress is exactly
    what this module and its migrations are full of, and a test that a comment
    can break is one somebody eventually deletes.
    """
    forbidden = (
        "smtplib",
        "email.mime",
        "aiosmtplib",
        "sendgrid",
        "boto3",
        "mailgun",
        "postmarker",
        "icalendar",
    )
    offenders: list[str] = []
    for path in server_sources():
        relative = path.relative_to(_SERVER_ROOT).as_posix()
        if relative.startswith("tests/"):
            continue
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if any(module == name or module.startswith(f"{name}.") for name in forbidden):
                offenders.append(f"{relative} imports {module}")
    assert offenders == [], offenders


def test_the_egress_guard_can_actually_see_an_import() -> None:
    """The scanner, scanned. A structural test that cannot fail is worse than
    no test, and the substring version of this one could not see the dynamic
    form at all."""
    assert imported_modules("import smtplib") == {"smtplib"}
    assert imported_modules("from email.mime.text import MIMEText") == {"email.mime.text"}
    assert imported_modules("import os, smtplib as mail") == {"os", "smtplib"}
    assert imported_modules('importlib.import_module("smtplib")') == {"smtplib"}
    # And not a mention of one.
    assert imported_modules("# never import smtplib here") == set()
    assert imported_modules('"""Nothing here does `import smtplib`."""') == set()


# --- plumbing -----------------------------------------------------------


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def a_claim_of(persona: tuple[str, str]) -> str:
    return sorted(seed_fixture.expected_claim_ids(*persona))[0]


def a_claim_of_another_handler(persona: tuple[str, str]) -> tuple[str, str]:
    """A claim in the persona's book that somebody *else* is assigned, and who.

    Scope is by employer (AD-7), so a handler sees claims their colleagues are
    assigned — which is the only arrangement in which "the signature is the
    caller, not the claim's handler" is a statement with any content. Picking
    `sorted(...)[0]` as `a_claim_of` does gives a claim assigned to the caller,
    where the two names are the same and any implementation passes.
    """
    for claim in sorted(seed_fixture.claims_for(*persona), key=lambda row: str(row["claim_id"])):
        if claim["handler"] != persona[0]:
            return str(claim["claim_id"]), str(claim["handler"])
    raise AssertionError(f"every claim {persona[0]} sees is their own; nothing to prove")


def a_claim_outside(persona: tuple[str, str]) -> str:
    visible = seed_fixture.expected_claim_ids(*persona)
    outside = sorted({claim["claim_id"] for claim in seed_fixture.seed()["claims"]} - visible)
    assert outside, f"{persona[0]} sees the whole portfolio; no out-of-scope claim to use"
    return str(outside[0])


def seeded_claim(claim_business_id: str) -> dict[str, Any]:
    """The claim's seed row — the independent oracle for a merge."""
    for claim in seed_fixture.seed()["claims"]:
        if claim["claim_id"] == claim_business_id:
            return dict(claim)
    raise AssertionError(f"no seeded claim {claim_business_id!r}")


def seeded_worker(claim_business_id: str) -> str:
    employee_id = seeded_claim(claim_business_id)["employee_id"]
    for employee in seed_fixture.seed()["employees"]:
        if employee["employee_id"] == employee_id:
            return str(employee["name"])
    raise AssertionError(f"no seeded employee for {claim_business_id!r}")


def expected_days_open(claim_business_id: str, as_of: date) -> int:
    """`days_open`, recomputed from the seed rather than from the derivation.

    A second implementation on purpose: a test that asked
    `services/derivations` for the expectation would agree with it however wrong
    it was. The rule itself — whole days from `froi_date`, floored at zero — is
    `open_duration.py`'s, and it is restated here for the reason
    `seed_fixture.HIGH_RISK_MIN` is.
    """
    froi = date.fromisoformat(seeded_claim(claim_business_id)["froi_date"])
    return max((as_of - froi).days, 0)


async def audit_rows(db: AsyncSession, action: str, entity_id: str) -> list[AuditEvent]:
    rows = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.action == action, AuditEvent.entity_id == entity_id)
    )
    return list(rows.all())


# --- templates and merge, against the database --------------------------


@requires_db
async def test_the_six_templates_are_served_in_the_button_order(seeded_db_url: str) -> None:
    """AC 3, over HTTP: the button row's data, and no template text on the wire."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.get("/claims-diary/email-templates")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [
        (item["templateKey"], item["label"], tuple(item["defaultRecipients"])) for item in items
    ] == [(key, label, recipients) for key, label, recipients in EXPECTED_TEMPLATES]
    # The raw template text stays on the server — a client that received it
    # would be one `replace()` away from merging in the browser (AD-1).
    assert all("subjectTemplate" not in item and "bodyTemplate" not in item for item in items)
    # And no surrogate id: `templateKey` is the identity every surface uses.
    assert all("id" not in item for item in items)


@requires_db
async def test_the_template_list_is_the_same_for_a_supervisor(seeded_db_url: str) -> None:
    """Reference data with no owner — the read is unscoped in the sense
    `/glossary` is, and still behind the session dependency."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        handler = (await client.get("/claims-diary/email-templates")).json()
        await login_as(client, *JENNIFER)
        supervisor = (await client.get("/claims-diary/email-templates")).json()

    assert handler == supervisor


@requires_db
@pytest.mark.parametrize("template_key", [key for key, _label, _to in EXPECTED_TEMPLATES])
async def test_every_template_merges_with_nothing_left_unresolved(
    db: AsyncSession, template_key: str
) -> None:
    """The merge's central promise (AC 3): every `{{…}}` resolved, server-side."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)

    merged = await merged_template(db, ctx, template_key, claim_business_id=claim_id, as_of=TODAY)

    assert _LEFTOVER.search(merged.subject) is None, merged.subject
    assert _LEFTOVER.search(merged.body) is None, merged.body
    assert merged.claim_business_id == claim_id
    assert claim_id in merged.subject


@requires_db
async def test_a_merge_resolves_every_claim_field_from_the_seed(db: AsyncSession) -> None:
    """The IME template is the one that names the most columns — worker, claim,
    DOI, injury, body part and ICD-10 — so it is the one worth checking against
    the seed rather than against the code that read it."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)
    claim = seeded_claim(claim_id)

    merged = await merged_template(db, ctx, "ime_request", claim_business_id=claim_id, as_of=TODAY)

    assert f"Worker: {seeded_worker(claim_id)}" in merged.body
    assert f"Claim ID: {claim_id}" in merged.body
    assert f"Date of Injury: {claim['doi']}" in merged.body
    assert f"Injury: {claim['injury_type']} — {claim['body_part']}" in merged.body
    assert f"ICD-10: {claim['icd']}" in merged.body


@requires_db
async def test_the_letter_is_signed_by_whoever_composed_it(db: AsyncSession) -> None:
    """The signature is the **caller's** name, not the claim's assigned handler.

    A covering handler signs their own letter — the prototype's `${handler}` is
    `currentUser`. **Merged against a claim in Kaya's book that Kaya is not
    assigned**, which is the whole of the test: with a claim of her own the two
    names coincide and an implementation reading `claim.handler` would pass
    exactly as this one does.
    """
    ctx = await context_for(db, *KAYA)
    claim_id, assigned_to = a_claim_of_another_handler(KAYA)
    assert assigned_to != KAYA[0]

    merged = await merged_template(db, ctx, "ime_request", claim_business_id=claim_id, as_of=TODAY)

    caller = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == KAYA[0], AppUser.role == KAYA[1]))
    ).one()
    assert merged.body.rstrip().endswith(caller.name)
    assert assigned_to not in merged.body


@requires_db
async def test_days_open_comes_from_the_registered_derivation(db: AsyncSession) -> None:
    """AD-10: one computer for claim age, never a second `(today - froi).days`
    in the merge code or the browser. Checked against the seed's own dates."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)

    merged = await merged_template(
        db, ctx, "status_update", claim_business_id=claim_id, as_of=TODAY
    )

    assert f"Days Open: {expected_days_open(claim_id, TODAY)}" in merged.body


@requires_db
async def test_stage_and_status_merge_as_prose_not_as_tokens(db: AsyncSession) -> None:
    """A merged body is a stored document read by a physician and an employer's
    HR department. It must not say "Current Stage: treatment"."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)
    claim = seeded_claim(claim_id)

    merged = await merged_template(
        db, ctx, "status_update", claim_business_id=claim_id, as_of=TODAY
    )

    assert f"Current Stage: {STAGE_PROSE[Stage(claim['stage'])]}" in merged.body
    assert f"Status: {STATUS_PROSE[ClaimStatus(claim['status'])]}" in merged.body
    assert f"Current Stage: {claim['stage']}" not in merged.body


@requires_db
@pytest.mark.parametrize(("template_key", "literal"), HANDLER_FILL_TEXT)
async def test_the_handler_fill_prompts_survive_a_merge(
    db: AsyncSession, template_key: str, literal: str
) -> None:
    """AD-2, end to end: `$[AMOUNT]` and `[RATING]%` reach the composer as
    literal text for the handler to replace, never auto-filled from financials."""
    ctx = await context_for(db, *KAYA)
    merged = await merged_template(
        db, ctx, template_key, claim_business_id=a_claim_of(KAYA), as_of=TODAY
    )
    assert literal in merged.body


@requires_db
async def test_a_merge_pre_fills_exactly_the_templates_recipient_set(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    merged = await merged_template(
        db, ctx, "settlement_notice", claim_business_id=a_claim_of(KAYA), as_of=TODAY
    )
    assert merged.recipients == (MeetingParticipant.employee, MeetingParticipant.attorney)


@requires_db
async def test_an_unknown_template_key_is_a_404(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    with pytest.raises(EmailTemplateNotFound):
        await merged_template(db, ctx, "no_such_template", claim_business_id=a_claim_of(KAYA))


@requires_db
async def test_the_merge_route_answers_a_bad_key_with_the_template_problem_type(
    seeded_db_url: str,
) -> None:
    """The mapping from `EmailTemplateNotFound` to a problem document, which the
    `pytest.raises` above cannot see.

    A malformed key answers the same 404 as an absent one — there is nothing to
    enumerate, and `TEMPLATE_KEY_PATH` deliberately carries no `pattern` that
    would split one documented answer into a 422 and a 404. Neither refusal
    quotes what was sent.
    """
    claim_id = a_claim_of(KAYA)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        absent = await client.get(
            "/claims-diary/email-templates/no_such_template/merged",
            params={"claimId": claim_id},
        )
        malformed = await client.get(
            "/claims-diary/email-templates/NOT A KEY/merged", params={"claimId": claim_id}
        )

    assert absent.status_code == malformed.status_code == 404, absent.text
    assert absent.json()["type"] == "/problems/email-template-not-found"
    assert malformed.json() == absent.json()
    assert "no_such_template" not in absent.text
    assert "NOT A KEY" not in malformed.text


@requires_db
async def test_a_claim_outside_the_book_merges_to_the_same_404_as_one_that_is_absent(
    seeded_db_url: str,
) -> None:
    """AD-7: the merge endpoint must not become an oracle a caller can walk."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        outside = await client.get(
            "/claims-diary/email-templates/rtw_offer/merged",
            params={"claimId": a_claim_outside(KAYA)},
        )
        absent = await client.get(
            "/claims-diary/email-templates/rtw_offer/merged", params={"claimId": "WC-99999"}
        )

    assert outside.status_code == absent.status_code == 404
    assert outside.json()["type"] == absent.json()["type"] == "/problems/email-claim-not-found"
    assert outside.json()["detail"].startswith("No claim ")
    assert absent.json()["detail"].startswith("No claim ")


@requires_db
async def test_a_merge_without_a_claim_is_refused_by_the_schema(seeded_db_url: str) -> None:
    """Templates are claim-aware by definition, so a claim-less merge is refused
    rather than degraded into the prototype's letter full of holes. The SPA
    disables the six buttons with a stated reason instead of sending this."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.get("/claims-diary/email-templates/rtw_offer/merged")

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/validation-error"


@requires_db
async def test_the_template_routes_are_never_cached(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        listed = await client.get("/claims-diary/email-templates")
        merged = await client.get(
            "/claims-diary/email-templates/rtw_offer/merged",
            params={"claimId": a_claim_of(KAYA)},
        )

    assert listed.headers["Cache-Control"] == "no-store"
    assert merged.headers["Cache-Control"] == "no-store"


# --- convert to email ---------------------------------------------------


@requires_db
async def test_a_meeting_drafts_a_confirmation_letter_with_its_participants(
    db: AsyncSession,
) -> None:
    """AC 5. The recipients are the meeting's participants **unmapped**: both
    sides are the same six-value vocabulary, so the prototype's substring match
    on labels disappears."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)
    meeting = await create_meeting(
        db,
        ctx,
        claim_business_id=claim_id,
        meeting_type=MeetingType.rtw_conference,
        meeting_date=date(2026, 8, 24),
        meeting_time=time(10, 30),
        location="Phone",
        notes="Confirm light-duty availability.",
        participants=["employee", "employer_hr"],
        as_of=TODAY,
    )

    draft = await meeting_email_draft(db, ctx, meeting.id, as_of=TODAY)

    assert _LEFTOVER.search(draft.subject) is None
    assert _LEFTOVER.search(draft.body) is None
    assert draft.subject == f"Meeting Confirmation: RTW Conference — {claim_id}"
    assert "📅 Type: RTW Conference" in draft.body
    assert "🕐 Date/Time: Monday, August 24 at 10:30 AM" in draft.body
    assert "📍 Location: Phone" in draft.body
    assert f"📋 Claim: {claim_id} — {seeded_worker(claim_id)}" in draft.body
    assert "Confirm light-duty availability." in draft.body
    assert draft.recipients == (MeetingParticipant.employee, MeetingParticipant.employer_hr)
    assert draft.claim_business_id == claim_id


@requires_db
async def test_a_meeting_with_nothing_recorded_uses_the_prototypes_fallbacks(
    db: AsyncSession,
) -> None:
    """`${m.loc||'TBD'}` and `${m.notes||'See attached claim file.'}`, kept.

    An all-day meeting renders the date alone, which is a correction rather than
    a port: the prototype builds `new Date(date + 'T' + null)` and prints the
    literal string "Invalid Date" into the letter.
    """
    ctx = await context_for(db, *KAYA)
    meeting = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.other,
        meeting_date=date(2026, 8, 25),
        participants=["supervisor"],
        as_of=TODAY,
    )

    draft = await meeting_email_draft(db, ctx, meeting.id, as_of=TODAY)

    assert f"📍 Location: {MEETING_LOCATION_FALLBACK}" in draft.body
    assert MEETING_AGENDA_FALLBACK in draft.body
    assert "🕐 Date/Time: Tuesday, August 25\n" in draft.body
    assert "Invalid Date" not in draft.body


@requires_db
async def test_a_meeting_with_no_claim_drafts_a_letter_that_mentions_none(
    db: AsyncSession,
) -> None:
    """The ERD's `CLAIM |o--o{ MEETING`, which 4.1's scheduler writes: `claimId`
    is optional on `POST /meetings`, so a supervisor catch-up or a plant
    walk-through carries no claim at all.

    Merging an empty string into the claim-aware letter produced a subject
    ending in a dangling em dash and a body line reading "📋 Claim: " with
    nothing after it — a letter that reads to its recipient as a merge failure.
    The clause is dropped instead.
    """
    ctx = await context_for(db, *KAYA)
    meeting = await create_meeting(
        db,
        ctx,
        claim_business_id=None,
        meeting_type=MeetingType.claim_review_supervisor,
        meeting_date=date(2026, 8, 28),
        meeting_time=time(9, 0),
        location="Room 2",
        participants=["supervisor"],
        as_of=TODAY,
    )

    draft = await meeting_email_draft(db, ctx, meeting.id, as_of=TODAY)

    assert draft.claim_business_id is None
    assert draft.subject == "Meeting Confirmation: Claim Review — Supervisor"
    assert not draft.subject.rstrip().endswith("—")
    assert "📋 Claim:" not in draft.body
    # Everything else about the letter is unchanged.
    assert _LEFTOVER.search(draft.body) is None
    assert "{{" not in draft.body
    assert "📅 Type: Claim Review — Supervisor" in draft.body
    assert "🕐 Date/Time: Friday, August 28 at 09:00 AM" in draft.body
    assert "📍 Location: Room 2" in draft.body
    assert MEETING_AGENDA_FALLBACK in draft.body


@requires_db
async def test_the_draft_route_serves_the_letter_the_composer_opens_with(
    seeded_db_url: str,
) -> None:
    """AC 5 over HTTP — the URL, the camelCase body and the no-store header.

    The two I/O-matrix rows for this route were only ever exercised by calling
    the service function, which says nothing about whether the route is mounted
    where the SPA looks for it or whether the payload is the composer's shape.
    """
    claim_id = a_claim_of(KAYA)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        created = await client.post(
            "/claims-diary/meetings",
            json={
                "claimId": claim_id,
                "meetingType": "rtw_conference",
                "meetingDate": "2026-08-24",
                "meetingTime": "10:30",
                "location": "Phone",
                "notes": "Confirm light-duty availability.",
                "participants": ["employer_hr", "employee"],
            },
        )
        assert created.status_code == 201, created.text
        response = await client.get(f"/claims-diary/meetings/{created.json()['id']}/email-draft")

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["claimId"] == claim_id
    assert body["subject"] == f"Meeting Confirmation: RTW Conference — {claim_id}"
    assert body["recipients"] == ["employee", "employer_hr"]
    assert "🕐 Date/Time: Monday, August 24 at 10:30 AM" in body["body"]
    assert f"📋 Claim: {claim_id} — {seeded_worker(claim_id)}" in body["body"]
    assert "{{" not in body["body"]


@requires_db
async def test_the_draft_route_answers_404_for_a_meeting_that_is_not_mine(
    seeded_db_url: str,
) -> None:
    """One 404 for absent, another handler's, and out-of-book — 4.1's security
    property, asserted through the route that inherits it."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        created = await client.post(
            "/claims-diary/meetings",
            json={
                "claimId": a_claim_of(SARAH),
                "meetingType": "other",
                "meetingDate": "2026-08-29",
                "participants": ["employee"],
            },
        )
        assert created.status_code == 201, created.text
        meeting_id = created.json()["id"]

        await login_as(client, *KAYA)
        hers = await client.get(f"/claims-diary/meetings/{meeting_id}/email-draft")
        absent = await client.get("/claims-diary/meetings/99999999/email-draft")

    assert hers.status_code == absent.status_code == 404, hers.text
    assert hers.json()["type"] == absent.json()["type"] == "/problems/meeting-not-found"
    # One wording for both, differing only in the id the caller itself supplied:
    # a caller comparing the two learns nothing about which meetings exist.
    assert hers.json()["detail"] == f"No meeting {meeting_id} in your diary."
    assert absent.json()["detail"] == "No meeting 99999999 in your diary."
    assert hers.headers["Cache-Control"] == "no-store"


@requires_db
async def test_another_handlers_meeting_cannot_be_drafted(db: AsyncSession) -> None:
    """The convert-to-email path reads `meeting` through Story 4.1's scoped
    query, so it inherits that module's one-404-for-three-cases answer."""
    kaya = await context_for(db, *KAYA)
    sarah = await context_for(db, *SARAH)
    meeting = await create_meeting(
        db,
        sarah,
        claim_business_id=a_claim_of(SARAH),
        meeting_type=MeetingType.other,
        meeting_date=date(2026, 8, 26),
        participants=["employee"],
        as_of=TODAY,
    )

    with pytest.raises(MeetingNotVisible):
        await meeting_email_draft(db, kaya, meeting.id, as_of=TODAY)


@requires_db
async def test_drafting_an_email_writes_nothing(db: AsyncSession) -> None:
    """AD-12: converting a meeting to an email changes nothing about the
    meeting, and the draft route writes no row of any kind."""
    ctx = await context_for(db, *KAYA)
    meeting = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.other,
        meeting_date=date(2026, 8, 27),
        participants=["employee"],
        as_of=TODAY,
    )
    before_logs = await db.scalar(sa.select(sa.func.count()).select_from(EmailLog))
    before_audit = await db.scalar(sa.select(sa.func.count()).select_from(AuditEvent))

    await meeting_email_draft(db, ctx, meeting.id, as_of=TODAY)

    assert await db.scalar(sa.select(sa.func.count()).select_from(EmailLog)) == before_logs
    assert await db.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == before_audit


# --- send ---------------------------------------------------------------


@requires_db
async def test_a_handler_logs_an_email_and_it_comes_back(seeded_db_url: str) -> None:
    """The happy path (AC 4): 201, the row, and everything the card renders."""
    claim_id = a_claim_of(KAYA)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails",
            json={
                "claimId": claim_id,
                "templateKey": "rtw_offer",
                "subject": "Return-to-Work Offer",
                "body": "Light duty from Monday.",
                "priority": "high",
                "recipients": ["employer_hr", "employee"],
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["claimId"] == claim_id
    assert body["workerName"] == seeded_worker(claim_id)
    assert body["templateKey"] == "rtw_offer"
    assert body["subject"] == "Return-to-Work Offer"
    assert body["priority"] == "high"
    # Stored in the vocabulary's own order, not the request's.
    assert body["recipients"] == ["employee", "employer_hr"]
    assert body["sentAt"]
    # Append-only and no lifecycle: a client that found either would reasonably
    # build an edit control or read the badge as a delivery state.
    assert "version" not in body
    assert "status" not in body


@requires_db
async def test_a_free_composition_with_no_claim_is_logged_untagged(seeded_db_url: str) -> None:
    """The ERD's `CLAIM |o--o{ EMAIL_LOG` optional edge. The six templates
    require a claim; typing a subject and a body by hand does not."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails",
            json={"subject": "General note to HR", "recipients": ["employer_hr"]},
        )

    assert response.status_code == 201, response.text
    assert response.json()["claimId"] is None
    assert response.json()["workerName"] is None
    assert response.json()["templateKey"] is None
    assert response.json()["priority"] == "normal"


@requires_db
async def test_an_empty_subject_is_a_422_naming_the_field_and_writes_nothing(
    seeded_db_url: str,
) -> None:
    """AC 2's server half — and the SPA renders it inline at the subject input,
    never in a native dialog."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = (await client.get("/claims-diary/emails")).json()["total"]
        response = await client.post(
            "/claims-diary/emails", json={"subject": "   ", "recipients": ["employee"]}
        )
        after = (await client.get("/claims-diary/emails")).json()["total"]

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/invalid-patch"
    assert SUBJECT_FIELD in response.json()["detail"]
    assert after == before


@requires_db
async def test_no_recipients_is_a_422_naming_the_fieldset(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails", json={"subject": "Anyone?", "recipients": []}
        )

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/invalid-patch"
    assert RECIPIENTS_FIELD in response.json()["detail"]


@requires_db
async def test_an_unknown_recipient_token_is_refused_without_echoing_it(
    seeded_db_url: str,
) -> None:
    """The schema refuses it first (`/problems/validation-error`), and the
    refusal must not quote the submitted value back (AD-11)."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails", json={"subject": "Hello", "recipients": ["ceo"]}
        )

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/validation-error"
    # The pure-function twin of this test has always asserted the token is
    # absent; the HTTP one asserted only the status, which is the half that
    # cannot see an echo added by a schema or a handler further down.
    assert "ceo" not in response.text


@requires_db
@pytest.mark.parametrize(
    ("field", "value"),
    [("subject", "x" * (MAX_SUBJECT_LENGTH + 1)), ("body", "x" * (MAX_BODY_LENGTH + 1))],
)
async def test_over_length_text_is_refused_by_the_schema(
    seeded_db_url: str, field: str, value: str
) -> None:
    """`maxLength` on the request model refuses first, so the type is
    `/problems/validation-error` rather than the command's `/problems/
    invalid-patch`. The command enforces both caps regardless (AD-16)."""
    payload = {"subject": "Subject", "recipients": ["employee"], field: value}
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post("/claims-diary/emails", json=payload)

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/validation-error"
    # Never the value.
    assert value not in response.text


@requires_db
async def test_a_body_at_the_cap_with_a_trailing_newline_is_accepted(seeded_db_url: str) -> None:
    """The two bounds measure the same string — `NewDiaryNoteRequest`'s fix,
    applied to both text fields here."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails",
            json={
                "subject": "At the cap",
                "body": "x" * MAX_BODY_LENGTH + "\n",
                "recipients": ["employee"],
            },
        )

    assert response.status_code == 201, response.text
    assert len(response.json()["body"]) == MAX_BODY_LENGTH


@requires_db
@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_log_an_email(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        response = await client.post(
            "/claims-diary/emails", json={"subject": "Hello", "recipients": ["employee"]}
        )

    assert response.status_code == 403, response.text
    assert response.json()["type"] == "/problems/edit-not-permitted"


@requires_db
async def test_the_command_refuses_a_supervisor_before_it_reads_anything(
    db: AsyncSession,
) -> None:
    """The role gate is the first rung, so the answer is identical for a claim in
    the caller's book and one that is not — `services/claims/edit.py`'s
    ordering, and it leaks nothing about the claim."""
    ctx = await context_for(db, *JENNIFER)
    with pytest.raises(EditNotPermitted):
        await send_email(
            db,
            ctx,
            subject="Hello",
            recipients=["employee"],
            claim_business_id=a_claim_of(KAYA),
        )


@requires_db
async def test_a_claim_outside_the_book_is_the_same_404_as_one_that_does_not_exist(
    seeded_db_url: str,
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        outside = await client.post(
            "/claims-diary/emails",
            json={
                "claimId": a_claim_outside(KAYA),
                "subject": "Hello",
                "recipients": ["employee"],
            },
        )
        absent = await client.post(
            "/claims-diary/emails",
            json={"claimId": "WC-99999", "subject": "Hello", "recipients": ["employee"]},
        )

    assert outside.status_code == absent.status_code == 404
    assert outside.json()["type"] == absent.json()["type"] == "/problems/email-claim-not-found"


@requires_db
async def test_an_unseeded_template_key_is_refused_rather_than_nulled(seeded_db_url: str) -> None:
    """`template_id` is a real FK, so a log row cannot claim a provenance that
    does not exist — and the refusal does not quote the key back (AD-11).

    The key used to be echoed on the argument that the six real ones are
    published anyway; what is echoed is whatever the *caller* sent, which is
    what `normalise_recipients` refuses to repeat two fields over.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails",
            json={
                "subject": "Hello",
                "recipients": ["employee"],
                "templateKey": "no_such_template",
            },
        )

    assert response.status_code == 404, response.text
    assert response.json()["type"] == "/problems/email-template-not-found"
    assert "no_such_template" not in response.text


@requires_db
async def test_a_refused_claim_reference_writes_neither_a_row_nor_an_audit_event(
    db: AsyncSession,
) -> None:
    """The refusal is inside the `INSERT … SELECT`, so there is no window and no
    partial write — but the transaction has taken a snapshot, and this asserts
    the state afterwards rather than the mechanism."""
    ctx = await context_for(db, *KAYA)
    before_logs = await db.scalar(sa.select(sa.func.count()).select_from(EmailLog))
    before_audit = await db.scalar(
        sa.select(sa.func.count()).select_from(AuditEvent).where(AuditEvent.entity == EMAIL_ENTITY)
    )

    with pytest.raises(EmailClaimNotVisible):
        await send_email(
            db,
            ctx,
            subject="Hello",
            recipients=["employee"],
            claim_business_id=a_claim_outside(KAYA),
        )

    assert await db.scalar(sa.select(sa.func.count()).select_from(EmailLog)) == before_logs
    assert (
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity == EMAIL_ENTITY)
        )
        == before_audit
    )


@requires_db
async def test_a_send_emits_one_audit_event_that_omits_the_body(db: AsyncSession) -> None:
    """AD-4, and this story's one divergence from `notes.py`/`meetings.py`.

    The diff records who, what about and to whom — `claim_id` as the **business**
    id so Story 8.1's purge can find the events belonging to a claim it is
    purging — and deliberately **not** the letter. `email_log` is append-only
    with no delete path, so the after-diff is never needed to reconstruct a row
    somebody removed, and the body is the largest PHI blob this console stores.
    """
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)
    secret = "Worker disclosed a prior back injury."

    logged = await send_email(
        db,
        ctx,
        subject="Audited send",
        body=secret,
        priority="urgent",
        recipients=["employee", "attorney"],
        claim_business_id=claim_id,
        template_key="settlement_notice",
        now=NOW,
    )

    events = await audit_rows(db, SEND_ACTION, str(logged.id))
    assert len(events) == 1
    event = events[0]
    assert event.entity == EMAIL_ENTITY
    assert event.actor_id == ctx.user_id
    assert event.before is None
    assert event.after is not None
    assert event.after["claim_id"] == claim_id
    assert event.after["subject"] == "Audited send"
    assert event.after["recipients"] == ["employee", "attorney"]
    assert event.after["priority"] == "urgent"
    assert event.after["template_key"] == "settlement_notice"
    # The whole of the divergence, asserted rather than described.
    assert "body" not in event.after
    assert secret not in json.dumps(event.after)
    # One instant for the row and the record of it.
    assert event.after["sent_at"] == logged.sent_at.isoformat()
    assert event.at == logged.sent_at


@requires_db
async def test_a_send_emits_no_timeline_event(db: AsyncSession) -> None:
    """AD-12 scopes `timeline_event` to *claim-mutating* commands, and logging an
    email changes no column of `claim`."""
    ctx = await context_for(db, *KAYA)
    before = await db.scalar(sa.select(sa.func.count()).select_from(TimelineEvent))

    await send_email(
        db,
        ctx,
        subject="No timeline entry",
        recipients=["employee"],
        claim_business_id=a_claim_of(KAYA),
    )

    assert await db.scalar(sa.select(sa.func.count()).select_from(TimelineEvent)) == before


@requires_db
async def test_the_row_and_its_audit_event_are_one_transaction(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AD-4's "same transaction", proved by breaking the commit.

    Reading the event back *after* a successful commit looks identical whether
    the two statements shared a transaction or ran in two. Failing between
    `audit.record` and `db.commit()` tells them apart: with one transaction
    neither the row nor its event survives, and with two the send would already
    be on disk with no record that it happened.
    """
    ctx = await context_for(db, *KAYA)
    marker = "One transaction or none."

    async def refuse_to_commit() -> None:
        raise RuntimeError("the commit failed")

    monkeypatch.setattr(db, "commit", refuse_to_commit)
    with pytest.raises(RuntimeError):
        await send_email(
            db,
            ctx,
            subject=marker,
            recipients=["employee"],
            claim_business_id=a_claim_of(KAYA),
            now=NOW,
        )
    await db.rollback()

    # A **separate** session, because the one above still holds the aborted
    # transaction and would see its own uncommitted rows.
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as other:
            logs = await other.scalars(sa.select(EmailLog).where(EmailLog.subject == marker))
            assert list(logs.all()) == []
            events = await other.scalars(
                sa.select(AuditEvent).where(
                    AuditEvent.entity == EMAIL_ENTITY, AuditEvent.action == SEND_ACTION
                )
            )
            assert all((event.after or {}).get("subject") != marker for event in events.all())
    finally:
        await engine.dispose()


@requires_db
async def test_an_email_that_was_logged_but_cannot_be_read_back_says_so(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`EmailLogNotVisible` is its own refusal, not the claim 404.

    Reachable only when a scope narrowing lands between the commit and the
    post-commit re-read, which is why it is forced here rather than provoked.
    The row is already committed and audited by then, so a caller told the write
    failed re-sends it — and `email_log` has no edit and no delete, so the
    console's own refusal is what duplicates the record of a communication.
    """
    from api.routers import diary as diary_router
    from services.claims.emails import EmailLogNotVisible

    async def _vanished(*_args: object, **_kwargs: object) -> object:
        raise EmailLogNotVisible(4242)

    monkeypatch.setattr(diary_router, "send_email", _vanished)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/emails", json={"subject": "Sent once.", "recipients": ["employee"]}
        )

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["type"] == "/problems/email-not-readable"
    assert "4242" in body["detail"]
    assert "logged" in body["detail"]
    assert "None" not in body["detail"]


# --- list ---------------------------------------------------------------


@requires_db
async def test_the_list_is_newest_first_and_the_order_is_total(db: AsyncSession) -> None:
    """AC 4. `sent_at DESC, id DESC` — the timestamp alone is a partial order,
    because two sends inside one clock tick tie, and a keyset page that ended
    inside the tie would repeat one row and drop the other."""
    ctx = await context_for(db, *KAYA)
    tick = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    tied = [
        await send_email(db, ctx, subject="tied", recipients=["employee"], now=tick)
        for _ in range(3)
    ]

    page = await list_email_logs(db, ctx, limit=200, as_of=TODAY)
    keys = [(item.sent_at, item.id) for item in page.items]
    assert keys == sorted(keys, reverse=True)
    # **The tie is what makes the second sort key observable**, and this used to
    # go unasserted: three rows sharing one `sent_at` are ordered by nothing but
    # `id DESC`, so an implementation that dropped it would still satisfy the
    # line above.
    assert {row.sent_at for row in tied} == {tick}
    assert [item.id for item in page.items if item.sent_at == tick] == sorted(
        (row.id for row in tied), reverse=True
    )


@requires_db
async def test_paging_visits_every_email_exactly_once(db: AsyncSession) -> None:
    """The keyset cursor, walked to exhaustion at a page size of one.

    The descending comparison is what this catches: a `>` left over from the
    ascending meetings list pages away from the rows it just served and returns
    nothing.
    """
    ctx = await context_for(db, *KAYA)
    # **Written by this test rather than inherited from the ones above it.** The
    # rows persist across this module, so depending on them passed under a full
    # run and failed under `-k` or a random order — and a keyset walk over a
    # list somebody else's test happened to fill is not a fixture, it is a
    # coincidence.
    mine = [
        await send_email(db, ctx, subject=f"page {index}", recipients=["employee"])
        for index in range(3)
    ]

    first = await list_email_logs(db, ctx, limit=1, as_of=TODAY)
    assert first.total is not None
    assert first.total >= len(mine)

    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor is not None:
        page = await list_email_logs(db, ctx, cursor=cursor, as_of=TODAY)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor

    assert len(seen) == len(set(seen)) == first.total
    assert {row.id for row in mine} <= set(seen)


@requires_db
async def test_total_is_answered_on_the_first_page_and_null_on_the_rest(
    db: AsyncSession,
) -> None:
    """`deferred-work.md`'s envelope question, answered for this table.

    `list_meetings` recounts the whole book on every "Show more" while the SPA
    reads `total` from `pages[0]` alone. Here the count is issued only when no
    cursor was supplied — the Lists convention already writes `total` as
    optional, so this is the convention rather than a divergence from it.
    """
    ctx = await context_for(db, *KAYA)
    # Two rows of this test's own, so there is a second page to ask for.
    for index in range(2):
        await send_email(db, ctx, subject=f"total {index}", recipients=["employee"])

    first = await list_email_logs(db, ctx, limit=1, as_of=TODAY)
    assert first.total is not None
    assert first.next_cursor is not None

    second = await list_email_logs(db, ctx, cursor=first.next_cursor, as_of=TODAY)
    assert second.total is None


@requires_db
async def test_one_handlers_sent_log_is_invisible_to_another(db: AsyncSession) -> None:
    """Sender scope, not employer scope: two handlers whose books overlap read
    their own correspondence and not each other's."""
    kaya = await context_for(db, *KAYA)
    sarah = await context_for(db, *SARAH)

    mine = await send_email(db, sarah, subject="Sarah's own send.", recipients=["employee"])
    yours = await send_email(db, kaya, subject="Kaya's own send.", recipients=["employee"])

    hers = await list_email_logs(db, sarah, limit=200, as_of=TODAY)
    theirs = await list_email_logs(db, kaya, limit=200, as_of=TODAY)
    assert mine.id in {item.id for item in hers.items}
    # Kaya's list is non-empty, so "Sarah's row is not in it" is a statement
    # about scope rather than about an empty table.
    assert yours.id in {item.id for item in theirs.items}
    assert mine.id not in {item.id for item in theirs.items}


@requires_db
async def test_a_supervisor_over_the_book_sees_none_of_it(db: AsyncSession) -> None:
    """A sent log is a handler's own record of what they communicated, and a
    supervisor's refused send does not turn their list into a management report
    of everybody else's.

    **The handler's send is written here first, and read back here first.**
    Asserting an empty list is only an assertion about scope if something would
    otherwise be in it: run alone, this test used to pass against a table with
    no rows in it at all, which proves nothing about anybody's visibility.
    """
    handler = await context_for(db, *SARAH)
    supervisor = await context_for(db, *JENNIFER)
    claim_id = a_claim_of(SARAH)
    # Sarah's book is inside Jennifer's, so employer scope *would* have shown
    # this send to her — which is what makes the empty list below a statement
    # about sender scope rather than about who covers what.
    assert claim_id in seed_fixture.expected_claim_ids(*JENNIFER)

    logged = await send_email(
        db,
        handler,
        subject="Not the supervisor's to read.",
        recipients=["employee"],
        claim_business_id=claim_id,
    )

    hers = await list_email_logs(db, handler, limit=200, as_of=TODAY)
    assert logged.id in {item.id for item in hers.items}

    page = await list_email_logs(db, supervisor, limit=200, as_of=TODAY)
    assert page.total == 0
    assert page.items == ()


@requires_db
async def test_the_list_answers_for_whoever_holds_the_cookie(seeded_db_url: str) -> None:
    """AD-7: there is no parameter on this route that could name a user, an
    employer or a role.

    **Both personas send first.** Two empty lists are disjoint, so without the
    two writes this asserted nothing at all when run on its own — the emptiest
    kind of passing test.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        hers = await client.post(
            "/claims-diary/emails",
            json={"subject": "Kaya's own.", "recipients": ["employee"]},
        )
        assert hers.status_code == 201, hers.text
        kaya = (await client.get("/claims-diary/emails")).json()

        await login_as(client, *SARAH)
        theirs = await client.post(
            "/claims-diary/emails",
            json={"subject": "Sarah's own.", "recipients": ["employee"]},
        )
        assert theirs.status_code == 201, theirs.text
        sarah = (await client.get("/claims-diary/emails")).json()

    kaya_ids = {item["id"] for item in kaya["items"]}
    sarah_ids = {item["id"] for item in sarah["items"]}
    assert hers.json()["id"] in kaya_ids
    assert theirs.json()["id"] in sarah_ids
    assert kaya_ids and sarah_ids
    assert kaya_ids & sarah_ids == set()


@requires_db
async def test_a_send_whose_claim_leaves_the_book_stays_in_the_senders_list(
    db: AsyncSession,
) -> None:
    """Sender scope only — `diary_note_scope`'s ruling, and it matters more here:
    the row is the only record that a communication went out, and it has no edit
    and no delete for a handler to restore it with."""
    ctx = await context_for(db, *KAYA)
    logged = await send_email(
        db,
        ctx,
        subject="About one claim.",
        recipients=["employer_hr"],
        claim_business_id=a_claim_of(KAYA),
    )

    narrowed = CallerContext(user_id=ctx.user_id, role=ctx.role, employer_ids=frozenset())
    page = await list_email_logs(db, narrowed, limit=200, as_of=TODAY)

    assert logged.id in {item.id for item in page.items}
    # `total` describes the same list, so it has to agree with the predicate.
    assert page.total == len(page.items)


@requires_db
async def test_a_bad_cursor_is_a_400_and_never_a_silent_page_one(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.get("/claims-diary/emails", params={"cursor": "nonsense!!"})

    assert response.status_code == 400, response.text
    assert response.json()["type"] == "/problems/invalid-cursor"


@requires_db
async def test_the_list_and_the_write_are_never_cached(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        listed = await client.get("/claims-diary/emails")
        written = await client.post(
            "/claims-diary/emails", json={"subject": "n", "recipients": ["employee"]}
        )

    assert listed.headers["Cache-Control"] == "no-store"
    assert written.headers["Cache-Control"] == "no-store"


# --- persistence and structure ------------------------------------------


@requires_db
async def test_a_logged_email_is_a_row_and_survives_a_new_session(seeded_db_url: str) -> None:
    """The point of FR-DIARY-3, asserted where it is cheapest: the prototype's
    `emailsStore` is a browser-lifetime object, and this reads the send back
    through a second application instance with its own connections."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        written = (
            await client.post(
                "/claims-diary/emails",
                json={"subject": "Survives a restart.", "recipients": ["employee"]},
            )
        ).json()

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        listed = (await client.get("/claims-diary/emails")).json()

    found = [item for item in listed["items"] if item["id"] == written["id"]]
    assert len(found) == 1
    assert found[0]["subject"] == "Survives a restart."


@requires_db
async def test_the_row_carries_no_version_column(db: AsyncSession) -> None:
    """The AD-4 statement for this table, asserted structurally rather than in
    prose: append-only rows are exempt from compare-and-swap, and a `version`
    here would be a column whose only possible value is 1."""
    columns: set[str] = set(EmailLog.__table__.c.keys())
    assert "version" not in columns
    assert columns == {
        "id",
        "app_user_id",
        "claim_id",
        "template_id",
        "subject",
        "body",
        "priority",
        "recipients",
        "sent_at",
    }


@requires_db
@pytest.mark.parametrize("table", ["email_log", "email_template"])
async def test_the_app_role_can_delete_so_story_8_1_can_purge(
    seeded_db_url: str, table: str
) -> None:
    """Both tables are in Epic 8's cascade, which does not exist yet. The grant
    is the handle it will need — asserted here so a later narrowing of it is a
    failure rather than a surprise in 8.1."""
    engine = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            granted = set(
                conn.execute(
                    sa.text(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE table_name = :table AND grantee = 'lineworker_app'"
                    ),
                    {"table": table},
                ).scalars()
            )
    finally:
        engine.dispose()

    assert {"SELECT", "INSERT", "DELETE"} <= granted


@requires_db
async def test_the_templates_are_seeded_and_the_log_is_not(seeded_db_url: str) -> None:
    """0036 seeds six templates; no seed migration follows for `email_log`,
    deliberately — a seeded send would be a communication nobody made attributed
    to a named handler.

    The log half is asserted against the *audit* trail rather than a row count,
    because the tests above have written rows into this module's database: a row
    with no audit event could only have come from a migration.
    """
    engine = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            templates = conn.execute(sa.text("SELECT count(*) FROM email_template")).scalar_one()
            unaudited = conn.execute(
                sa.text(
                    "SELECT count(*) FROM email_log l WHERE NOT EXISTS ("
                    "  SELECT 1 FROM audit_event e"
                    "  WHERE e.entity = 'email_log' AND e.entity_id = l.id::text)"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert templates == len(EXPECTED_TEMPLATES)
    assert unaudited == 0


@requires_db
async def test_the_seeded_text_reaches_the_database_intact(seeded_db_url: str) -> None:
    """The em dashes, the bullets and the square-bracket prompts survive the
    migration — a port that mangled one would produce letters the prototype
    never wrote, and nothing else here would notice."""
    engine = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            rows: dict[str, str] = {
                str(key): str(text)
                for key, text in conn.execute(
                    sa.text("SELECT template_key, body_template FROM email_template")
                ).all()
            }
    finally:
        engine.dispose()

    for key, literal in HANDLER_FILL_TEXT:
        assert literal in rows[key]
    assert "• Settlement amount: $[AMOUNT]" in rows["settlement_notice"]
    assert "Offered Role: [Transitional Duty — to be completed by supervisor]" in rows["rtw_offer"]


@requires_db
async def test_the_template_reads_go_through_the_service(seeded_db_url: str) -> None:
    """`list_email_templates` is the one entry point, and it answers the same
    six rows the migration wrote — the read path's end-to-end check, without a
    route in the way."""
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            ctx = await context_for(session, *KAYA)
            templates = await list_email_templates(session, ctx)
    finally:
        await engine.dispose()

    assert tuple(template.template_key for template in templates) == tuple(
        key for key, _label, _to in EXPECTED_TEMPLATES
    )


def test_the_module_that_owns_this_table_is_the_only_writer() -> None:
    """AD-12, enforced by where the code lives and asserted structurally.

    The database grant cannot say "only `services/claims/emails.py` may INSERT",
    so the rule is a property of the source tree — `test_diary_notes.py`'s guard
    for `diary_note`, and `test_payment_ownership.py`'s for the money.

    Scanned over `server_sources()`, which leaves out `.venv/`: this used to
    walk the vendored tree as well, reading every third-party module in the
    environment to decide who writes one of this project's tables.
    """
    writers: list[str] = []
    for path in server_sources():
        relative = path.relative_to(_SERVER_ROOT).as_posix()
        if relative.startswith("tests/"):
            continue
        source = path.read_text(encoding="utf-8")
        if "EmailLog" in source and any(
            verb in source for verb in ("sa.insert(EmailLog", "insert_email_log")
        ):
            writers.append(relative)
    writers.sort()

    # Two files, and the pair is the architecture: the repository holds the
    # statement (with the scope predicate inside it) and the command is the only
    # thing that calls it. The router is deliberately *not* on this list — it
    # never names the model, which is what "thin by AD-1" means in practice.
    assert writers == [
        "data/repositories/claims.py",
        "services/claims/emails.py",
    ], writers
