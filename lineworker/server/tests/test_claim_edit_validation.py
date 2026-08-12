"""Story 2.3 — the patch whitelist, its normalisation, and the diff it builds.

No database. Everything here is about the pure half of the command: which
keys are accepted, what a value must look like, which columns a patch
actually writes, and what the timeline sentence says. The DB-backed half —
compare-and-swap, same-transaction atomicity, the endpoint's four refusals —
is `test_claim_edit.py`.

The expectations are written from the story's acceptance criteria and the
prototype's own option lists, not from the module under test: the ICD-10
pattern is re-derived from the seed file below, and the vocabularies are
compared against the prototype's HTML rather than against `reference.py`.
"""

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data.models.enums import Disability, RecoveryWindow
from services.claims.edit import (
    EDITABLE_FIELDS,
    MAX_LENGTHS,
    InvalidPatch,
    _Change,
    _changes,
    describe,
    normalise,
)
from services.claims.reference import (
    BODY_PART_LABELS,
    BODY_PART_OPTIONS,
    FIELD_LABELS,
    ICD_PATTERN,
)
from tests import seed_fixture

PROTOTYPE = Path(__file__).resolve().parents[3] / "docs" / "Workers_Comp_Prototype.html"

# Migration 0013's display-string -> token map, loaded by path because
# `data/versions/` is an Alembic script directory, not an importable package.
_MIGRATION = Path(__file__).resolve().parents[1] / "data" / "versions"
_spec = importlib.util.spec_from_file_location(
    "_m0013", _MIGRATION / "20260812_0013_recovery_window_enum.py"
)
assert _spec and _spec.loader
_m0013 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0013)
TEXT_TO_TOKEN: dict[str, str] = _m0013.TEXT_TO_TOKEN


@dataclass
class FakeClaim:
    """Just enough of a `Claim` to satisfy `EditableClaim`.

    A stand-in rather than a real model instance because `_changes` reads
    eight columns, and constructing a `Claim` would mean supplying forty
    unrelated NOT NULL ones to test a diff over seven. The `EditableClaim`
    protocol is what makes that substitution a typed statement rather than a
    cast — and it is why `body_part` differs from `body_key`'s label here,
    exactly as it does in the seed.
    """

    injury_type: str = "Laceration"
    cause: str = "Contact with Machine Guard"
    body_key: str = "hand_right"
    body_part: str = "Wrist(s) & Hand(s)"
    icd: str = "S61.412A"
    icd_desc: str = "Laceration of left wrist"
    disability: Disability = Disability.temporary
    recovery: RecoveryWindow = RecoveryWindow.weeks_2_4


# --- the vocabularies are the prototype's -------------------------------


def test_the_body_part_options_are_the_prototypes_eleven() -> None:
    """Ported from `BODY_PART_OPTIONS`, keys and labels and order.

    Read out of the prototype rather than restated, so a transcription slip
    (`lumbar` vs `lower_back`) fails here instead of surfacing in Story 2.4
    as a diagram region that never highlights.
    """
    source = PROTOTYPE.read_text()
    block = source.split("const BODY_PART_OPTIONS=[", 1)[1].split("];", 1)[0]
    pairs = re.findall(r'\{key:"([^"]+)",label:"([^"]+)"\}', block)

    assert pairs == [(option.key, option.label) for option in BODY_PART_OPTIONS]
    assert len(pairs) == 11


def test_the_recovery_windows_are_the_prototypes_five() -> None:
    """Five members, in the prototype's order — matched through 0013's map.

    The column holds tokens since the Story 2.3 code review, so the tie to
    the prototype runs through the migration's `TEXT_TO_TOKEN` rather than
    through the stored value. Asserted here because that mapping is the only
    thing connecting `weeks_6_8` to the option a handler picked in the
    prototype, and nothing else would notice if a member were dropped.
    """
    source = PROTOTYPE.read_text()
    block = source.split("const RECOVERY_OPTIONS=[", 1)[1].split("];", 1)[0]
    prototype_options = re.findall(r'"([^"]+)"', block)

    assert [TEXT_TO_TOKEN[option] for option in prototype_options] == [
        member.value for member in RecoveryWindow
    ]


def test_every_seeded_icd_code_satisfies_the_pattern() -> None:
    """The pattern must not be stricter than the data it will be applied to.

    100 seeded claims carry real ICD-10-CM codes in four shapes (`H83.3`,
    `M54.50`, `S46.001A`, `I21.9`). If the pattern rejected one of them, a
    handler correcting an unrelated field on that claim would be told their
    ICD-10 is malformed — the validation refusing the value it shipped with.
    """
    codes = {claim["icd"] for claim in seed_fixture.seed()["claims"]}
    assert codes, "the seed has no claims"
    assert [code for code in sorted(codes) if not ICD_PATTERN.match(code)] == []


def test_every_editable_field_has_a_label() -> None:
    """The timeline sentence names each field, so a new one without a label
    would render `KeyError` into the case file's log."""
    assert set(FIELD_LABELS) == set(EDITABLE_FIELDS)


# --- the whitelist ------------------------------------------------------


def test_a_patch_may_only_name_editable_fields() -> None:
    with pytest.raises(InvalidPatch) as caught:
        normalise({"severity_score": "99"})
    # The *allowed* set is named; the rejected key is not (AD-11 — see
    # `test_no_refusal_message_echoes_a_caller_supplied_key`).
    assert "severity_score" not in str(caught.value)
    assert "injury_type" in str(caught.value)


@pytest.mark.parametrize(
    "field",
    [
        # The four the story explicitly reserves to later stories, plus the
        # two that would let a caller forge the audit trail itself.
        "severity_score",
        "reserve",
        "stage",
        "status",
        "version",
        "claim_id",
    ],
)
def test_the_fields_other_stories_own_are_refused(field: str) -> None:
    """Story 2.4 owns the severity score, Epic 3 the money, 3.5 the stage.

    Named individually rather than trusted to the whitelist's shape, because
    "the whitelist happens to exclude it today" and "this field is
    deliberately not editable here" are different statements and only the
    second survives somebody adding a field.
    """
    with pytest.raises(InvalidPatch):
        normalise({field: "anything"})


@pytest.mark.parametrize("field", ["cause", "injury_type", "body_key", "disability"])
def test_an_explicit_null_is_refused_by_the_command_not_only_by_the_api(field: str) -> None:
    """The refusal moved out of `ClaimFieldPatch` (code review, 2026-08-12).

    It used to live in `edited_fields()`, which the router evaluates while
    *building* the call — so it fired before the command's role check, and a
    supervisor sending `{"cause": null}` was told their patch was malformed
    rather than that their role cannot edit. That inverts the refusal ladder
    this module documents, and `_answer` would have handed the inversion to
    every Epic 3 command.

    Refusing here also covers the AD-13 agent tools, which never construct a
    Pydantic model at all — before this, a `None` from one of them reached
    `require_text` and came back as "cause must be text".
    """
    with pytest.raises(InvalidPatch) as refusal:
        normalise({field: None})
    assert "cannot be null" in str(refusal.value)
    # Naming the keys is safe: the whitelist check runs first, so every key
    # left is one of `EDITABLE_FIELDS` — a constant, not caller text (AD-11).
    assert field in str(refusal.value)


def test_an_unknown_key_is_still_refused_before_a_null_is_named() -> None:
    """Order matters: the null message names its keys, so it must not be
    reachable with a key the caller invented."""
    with pytest.raises(InvalidPatch) as refusal:
        normalise({"cause": None, "attacker_chosen_key": None})
    assert "attacker_chosen_key" not in str(refusal.value)
    assert "editable fields are" in str(refusal.value)


def test_an_empty_patch_is_refused() -> None:
    """A PATCH naming no fields is a request with no meaning — better a 422
    than a version bump and a timeline event that says nothing changed."""
    with pytest.raises(InvalidPatch):
        normalise({})


# --- value rules --------------------------------------------------------


@pytest.mark.parametrize("field", ["injury_type", "cause", "icd", "icd_desc"])
def test_blank_and_whitespace_only_text_is_refused(field: str) -> None:
    # The ICD pair travels together, so blanking one still sends the other.
    partner = {"icd": {"icd_desc": DESC}, "icd_desc": {"icd": "S61.412A"}}.get(field, {})
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(InvalidPatch):
            normalise({field: blank, **partner})


@pytest.mark.parametrize("field, limit", sorted(MAX_LENGTHS.items()))
def test_text_longer_than_its_cap_is_refused(field: str, limit: int) -> None:
    filler = "S61.412A" if field == "icd" else "x"
    partner = {"icd": {"icd_desc": DESC}, "icd_desc": {"icd": "S61.412A"}}.get(field, {})
    with pytest.raises(InvalidPatch):
        normalise({field: filler * (limit + 2), **partner})


def test_text_is_trimmed_rather_than_stored_with_its_padding() -> None:
    assert normalise({"cause": "  Slip on Coolant  "}) == {"cause": "Slip on Coolant"}


DESC = "Laceration of left wrist"


@pytest.mark.parametrize("code", ["S61.412A", "H83.3", "M54.50", "I21.9", "G56.00"])
def test_well_formed_icd_codes_are_accepted(code: str) -> None:
    assert normalise({"icd": code, "icd_desc": DESC}) == {"icd": code, "icd_desc": DESC}


@pytest.mark.parametrize(
    "code",
    [
        "s61.412a",  # accepted, but only after upper-casing (below)
        "61.412A",  # no letter
        "U07.1",  # `U` is reserved and never a claim's diagnosis
        "S61.",  # a dot with nothing after it
        "S61.4123456",  # extension too long
        "S6",  # too short
        "S61 412A",  # a space where the dot goes
    ],
)
def test_malformed_icd_codes_are_refused_or_normalised(code: str) -> None:
    if code == "s61.412a":
        assert normalise({"icd": code, "icd_desc": DESC}) == {"icd": "S61.412A", "icd_desc": DESC}
        return
    with pytest.raises(InvalidPatch):
        normalise({"icd": code, "icd_desc": DESC})


def test_a_body_key_outside_the_diagram_is_refused() -> None:
    with pytest.raises(InvalidPatch):
        normalise({"body_key": "left_elbow"})
    assert normalise({"body_key": "lumbar"}) == {"body_key": "lumbar"}


def test_a_recovery_window_outside_the_five_is_refused() -> None:
    """Free text here would silently change the treatment phase: the phase
    derivation parses this string for an expected number of days, and
    anything it cannot parse falls onto a default window."""
    with pytest.raises(InvalidPatch):
        normalise({"recovery": "about a month"})
    # The display string is refused too: the wire carries tokens, and a
    # client sending what it renders is a client that has not been updated.
    with pytest.raises(InvalidPatch):
        normalise({"recovery": "6-8 Weeks"})
    assert normalise({"recovery": "weeks_6_8"}) == {"recovery": "weeks_6_8"}


def test_disability_takes_only_the_two_enum_values() -> None:
    assert normalise({"disability": "permanent"}) == {"disability": "permanent"}
    with pytest.raises(InvalidPatch):
        normalise({"disability": "Permanent"})  # the UI's display label, not the token


@pytest.mark.parametrize("value", [1, None, True, ["a"], {"a": 1}])
def test_a_non_string_value_is_refused(value: Any) -> None:
    with pytest.raises(InvalidPatch):
        normalise({"cause": value})


def test_no_refusal_message_echoes_the_submitted_value() -> None:
    """AD-11: a 422 body is a place claim data must not appear.

    The messages name the field and the rule; the value the handler typed
    stays out of the problem document, the structlog line and the browser
    console. Asserted with a value distinctive enough that a leak cannot be
    coincidence.
    """
    secret = "Marcus Delgado lumbar disc herniation"
    for patch in (
        {"icd": secret, "icd_desc": "x"},
        {"body_key": secret},
        {"recovery": secret},
        {"disability": secret},
        {"injury_type": secret * 5},
    ):
        with pytest.raises(InvalidPatch) as caught:
            normalise(patch)
        assert secret not in str(caught.value)


def test_no_refusal_message_echoes_a_caller_supplied_key() -> None:
    """The half the value-only version of this test missed (code review).

    An unknown *key* is caller-supplied text too, and this command is
    reachable directly from an AD-13 agent tool whose arguments are untrusted
    content (AD-16). Echoing the key put attacker-chosen text into a problem
    document and a log line; the allowed set is a constant, so it is named
    instead.
    """
    secret = "ssn_123_45_6789"
    with pytest.raises(InvalidPatch) as caught:
        normalise({secret: "anything"})

    assert secret not in str(caught.value)
    assert "injury_type" in str(caught.value)


def test_the_icd_code_and_its_description_must_travel_together() -> None:
    """One fact in two columns (code review).

    Moving the code alone leaves the description describing the previous
    diagnosis, with an audit diff naming only the code — a silently wrong
    claim file. The command refuses the half-patch in both directions.
    """
    for half in ({"icd": "M54.50"}, {"icd_desc": "Low back pain"}):
        with pytest.raises(InvalidPatch) as caught:
            normalise(half)
        assert "together" in str(caught.value)

    assert normalise({"icd": "M54.50", "icd_desc": "Low back pain"}) == {
        "icd": "M54.50",
        "icd_desc": "Low back pain",
    }


# --- the diff -----------------------------------------------------------


def test_unchanged_values_are_not_written() -> None:
    """A patch that restates the stored values writes nothing.

    Which is what keeps the audit log a record of *changes*: a handler who
    tabs through the card without typing should not produce a row.
    """
    claim = FakeClaim()
    assert _changes(claim, normalise({"cause": "Contact with Machine Guard"})) == ()


def test_only_the_fields_that_differ_are_written() -> None:
    claim = FakeClaim()
    changes = _changes(
        claim, normalise({"cause": "Slip on Coolant", "icd": "S61.412A", "icd_desc": DESC})
    )

    assert [change.column for change in changes] == ["cause"]
    assert changes[0].before == "Contact with Machine Guard"
    assert changes[0].after == "Slip on Coolant"


def test_choosing_a_body_key_rewrites_the_body_part_label() -> None:
    """The prototype's `updateBodyPart`, ported: picking a region on the
    diagram replaces the dataset's own body-part wording with the diagram's
    label. Both columns are written, and both appear in the diff."""
    claim = FakeClaim()
    changes = {change.column: change.after for change in _changes(claim, {"body_key": "lumbar"})}

    assert changes == {"body_key": "lumbar", "body_part": BODY_PART_LABELS["lumbar"]}


def test_an_unchanged_body_key_leaves_the_body_part_alone() -> None:
    """The seeded `body_part` and the diagram label are different
    vocabularies — "Wrist(s) & Hand(s)" against "Right Hand" — so a patch
    that does not move the key must not quietly relabel the claim."""
    claim = FakeClaim(body_key="hand_right", body_part="Wrist(s) & Hand(s)")
    assert _changes(claim, {"body_key": "hand_right"}) == ()


def test_the_timeline_sentence_names_the_fields_a_handler_edited() -> None:
    changes = (
        _Change(column="icd", before="a", after="b"),
        _Change(column="injury_type", before="c", after="d"),
    )
    # Ordered by the card, not by the order the client happened to send.
    assert describe(changes) == "Injury details updated (injury type, ICD-10)"


def test_the_timeline_sentence_omits_the_derived_body_part_column() -> None:
    """`body_part` is written by the server, not chosen by the handler.
    Listing it would report two edits where one was made."""
    changes = (
        _Change(column="body_key", before="hand_right", after="lumbar"),
        _Change(column="body_part", before="Wrist(s) & Hand(s)", after="Lower Back (Lumbar)"),
    )
    assert describe(changes) == "Injury details updated (body part)"


# --- the property the audit diff has to have ----------------------------

# Printable text only. The control-character refusal is a *validation* rule
# with its own test; this property is about the diff, so a strategy that
# spent its examples on 422s would be testing the wrong function. (It found
# the rule the first time it ran, which is the property working.)
_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip())


@given(
    patch=st.fixed_dictionaries(
        {},
        optional={
            "injury_type": _TEXT,
            "cause": _TEXT,
            "body_key": st.sampled_from(sorted(BODY_PART_LABELS)),
            "disability": st.sampled_from([d.value for d in Disability]),
            "recovery": st.sampled_from([w.value for w in RecoveryWindow]),
        },
    ),
    # Drawn as a pair or not at all: the command refuses one without the
    # other, so a strategy offering them independently would spend most of
    # its examples on the 422 path rather than on the diff.
    icd_pair=st.one_of(
        st.none(),
        st.tuples(st.sampled_from(["S61.412A", "H83.3", "M54.50", "G56.01"]), _TEXT),
    ),
)
def test_the_diff_never_names_a_field_the_patch_did_not(
    patch: dict[str, str], icd_pair: tuple[str, str] | None
) -> None:
    """The AD-11 property: an audit diff describes the edit and nothing else.

    For *any* whitelisted patch, the columns written are a subset of the
    patch's own keys plus at most `body_part` — never a field the handler did
    not touch, and never the whole row. The story asks for "before/after keys
    equal the patch keys"; the honest form is this, because unchanged fields
    are dropped and `body_part` rides with `body_key` by design.
    """
    if icd_pair is not None:
        patch = {**patch, "icd": icd_pair[0], "icd_desc": icd_pair[1]}
    if not patch:
        return
    claim = FakeClaim()
    columns = {change.column for change in _changes(claim, normalise(patch))}

    assert columns <= set(patch) | {"body_part"}
    assert ("body_part" in columns) <= ("body_key" in patch)
    # And every written column really differs from what was stored.
    for change in _changes(claim, normalise(patch)):
        assert change.before != change.after


# --- the structural rules AD-4 and AD-12 are made of --------------------

SERVER_ROOT = Path(__file__).resolve().parents[1]


def _sources(*relative: str) -> list[Path]:
    found = [
        path
        for root in relative
        for path in (SERVER_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert found, f"nothing scanned under {relative} — did a package move?"
    return found


# `db.add(...)` or a Core DML statement — the two ways a module puts a row
# into the database itself. Deliberately blunt: the fix is to move the write
# into a command, not to spell it differently.
WRITES = re.compile(r"\.add\s*\(|\.add_all\s*\(|sa\.(?:insert|update|delete)\s*\(")

# `commit()` is checked separately, because one router legitimately has one.
COMMITS = re.compile(r"\.commit\s*\(")

# Story 1.3's session mint and revoke. Argued rather than quietly excluded:
# a `session` row is not a claim entity — it carries no PHI, it is created by
# the act of authenticating rather than by a user command, and there is no
# actor to audit it against yet (the actor *is* what it establishes). It also
# goes through `data/repositories/identity`, so the router still issues no
# DML of its own. When the Deferred IdP decision lands, this line is one of
# the things that should be revisited.
ROUTERS_THAT_MAY_COMMIT = {"api/routers/auth.py"}


def test_no_router_writes_through_a_session() -> None:
    """AD-4: commands are the only write path, and routers are thin (AD-1).

    A router that could write is a route whose mutation nobody audited —
    the whole invariant rests on there being one place a write can happen,
    and "there is one" is a property of the tree, not of the diff that
    introduced it.
    """
    offenders = [
        str(path.relative_to(SERVER_ROOT))
        for path in _sources("api")
        if WRITES.search(path.read_text())
    ]
    assert offenders == []

    committers = {
        str(path.relative_to(SERVER_ROOT))
        for path in _sources("api")
        if COMMITS.search(path.read_text())
    }
    assert committers == ROUTERS_THAT_MAY_COMMIT


def test_the_write_guard_would_notice_a_router_writing() -> None:
    """A guard that only ever reads clean files cannot tell "nothing is
    wrong" from "nothing is checked"."""
    for smell in (
        "db.add(AuditEvent(...))",
        "await db.execute(sa.update(Claim).values(cause=body.cause))",
        "db.add_all(rows)",
    ):
        assert WRITES.search(smell), smell
    assert COMMITS.search("await db.commit()")


# A *construction*, not a class definition and not an import. `class
# TimelineEvent(Base)` in `data/models/core.py` is where the row is
# described; this guard is about who brings one into existence.
def _constructor(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!class )\b{name}\s*\(")


@pytest.mark.parametrize(
    "model, owner",
    [
        # AD-12's ownership registry, as a test. `timeline_event` rows are
        # emitted only by the owning service's commands; the audit row's
        # shape belongs to `services/audit` so that "the schema is fixed"
        # survives twenty commands writing one.
        ("TimelineEvent", "services/claims/timeline.py"),
        ("AuditEvent", "services/audit/__init__.py"),
    ],
)
def test_exactly_one_module_constructs_each_append_only_row(model: str, owner: str) -> None:
    pattern = _constructor(model)
    constructors = sorted(
        str(path.relative_to(SERVER_ROOT))
        for path in _sources("api", "services", "rules", "agents", "data")
        if pattern.search(path.read_text())
    )
    assert constructors == [owner]
