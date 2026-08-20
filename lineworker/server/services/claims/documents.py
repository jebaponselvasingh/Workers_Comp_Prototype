"""The Documents & ID tab, assembled server-side (Story 2.5, FR-H-8, FR-DET-4).

Three things the prototype's `docsHTML` and `openDoc` decide in the browser are
decided here (AD-1): which statutory forms a claim requires, what the employee
ID card reads, and which rows a document's viewer shows. The SPA receives a
finished block and a finished sheet.

## The forms card is a join, not a constant

`pathDocsHTML` indexes a module-level `PATH_DOCS` object with `c.path || "B"`,
against a dataset in which no claim has a `path` field — so it renders the same
four forms for all 100 claims. Here the path is a registered derivation
(`services/derivations/path_classification.py`) and the forms are reference rows
(`path_required_form`), so a fatality claim gets the death-benefit filings and a
first-aid-only claim gets the two minor-injury ones. `pathVersion` names the
rule document that classified, for `thresholdsVersion`'s reason.

## The viewer sheet is rows, and the rows are the server's

`openDoc` builds two different HTML bodies depending on `d.type === "FROI"` —
the full injury detail for a first report, a short summary for everything else.
That dispatch is here, and what crosses the wire is a **list of labelled
rows**, not a rendered document: which fields a FROI sheet shows, in which
order, under which labels, is a statement about a statutory filing and not a
layout choice the browser gets to make.

**Money and dates still cross as data.** A row carries either `text` or
`cents`, never both, and the UI formats the second — because "integer cents end
to end, formatted only in the UI" is the money convention, and a server that
sent `"$1,432.00"` would be the one place it did not hold. Dates cross as ISO
strings in `text`, which is exactly what `formatDate` in the SPA already
renders (and renders as an em dash when absent — 101 seeded documents carry a
timing note where a filing date belongs).

## Bytes

There are none yet: `blob_key` is null on all 563 seeded rows, because the
prototype has no files behind its document rows. The sheet is therefore
generated from claim and document columns, and `blob_key` is resolved through
the one `BlobStore` protocol so the route already knows where to ask on the day
a real PDF is attached. The MinIO-versus-volume decision stays deferred; the
boundary is what this story binds.

## The first writer (Story 6.5), and the one thing it deliberately does not do

Until Story 6.5 this module was **entirely read-only** — the only writer of a
`document` row was `set_document_review`, flipping two booleans, and every row
in the table came from the Story 2.2 seed migration. `create_document` below is
the first command that creates one, and it exists for exactly one caller: the
copilot's approved return-to-work letter save. It is an AD-4 command in
`add_additional_injury`'s shape — role gate, scoped re-read, version pre-check,
compare-and-swapped `INSERT … SELECT`, audit and timeline in the same
transaction, commit, re-read.

**It does not call `services/rag.mark_claim_stale`, and the omission is a
decision rather than a gap.** AD-12 asks a command that mutates an *embedded
source field* to mark the claim's embedding stale in the same transaction, and
`update_claim_fields`, `update_claim_severity`, `add_additional_injury` and
`remove_additional_injury` all do. This one must not, because
`services/rag/claim_text.py` composes the embeddable claim summary from clinical
fields only — injury type, cause, body part, ICD-10, severity, disability,
recovery window, surgery, sector, stage, status, the two return-to-work dates
and the secondary injuries. Filing a letter writes none of them: the claim is
not less similar to its neighbours than it was a moment ago, and re-embedding it
would put a vector through the model to produce the same vector. A stale flag
raised for a change the composer cannot see is noise in the queue the scheduled
refresh drains first.

`services/claims/comp_rate.py` is the precedent for writing this down rather
than leaving a reader to wonder whether the call was forgotten — which is the
only reason a deliberate absence is worth a paragraph at all.

## Two imports are function-local, and the cycle is why

`services/claims/detail.py` imports `documents_block` from *this* module — the
Documents & ID tab is part of the case file it assembles — so this module cannot
import `detail` (or `edit`, which imports `detail`) at module scope without a
circular import at startup. `create_document` returns a `ClaimDetail` and reuses
`edit.py`'s refusal ladder rather than restating it, both of which are the right
shapes; so the two imports happen inside the function, under a `TYPE_CHECKING`
block for the annotations. `api/routers/copilot.py` breaks the same kind of
cycle the same way and records the same reason: a local import is a smaller
price than a second `ClaimDetail` assembler or a fifth spelling of "only a
handler may write to a case file".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim, Document
from data.models.enums import ClaimPath, DocType, TimelineTag, UserRole
from data.repositories import claims as claim_repo
from data.repositories import statutory_forms as forms_repo
from rules.parameters import DerivationThresholds
from services import audit, derivations
from services.blobstore import BlobStore
from services.claims import timeline

if TYPE_CHECKING:  # pragma: no cover - see the module docstring on the cycle
    from services.claims.detail import ClaimDetail

#: The sheet a first report of injury renders, versus the sheet everything else
#: renders. Two variants, discriminated on the wire, for the reason the stage
#: overviews are a discriminated union: a payload with every field optional can
#: describe a summary sheet carrying an ICD-10 code, and the client would need
#: eleven truthiness checks where the prototype has one `if`.
FROI_VARIANT = "froi"
SUMMARY_VARIANT = "summary"

#: The third variant (Story 6.5): a document that carries its own words.
#:
#: **Dispatched on `body_text is not None`, never on `doc_type`.** A document is
#: rendered as a letter because it *is* one — because somebody's prose is stored
#: on the row — and not because of how it was classified. The distinction has
#: teeth for exactly the type this story files under: the 563 seeded rows include
#: `rtw` documents with no body, filed by the prototype's data as a record that a
#: return-to-work letter exists somewhere, and those keep the summary sheet they
#: belong on. A `doc_type is DocType.rtw` test would have shown each of them a
#: letter variant with an empty body.
LETTER_VARIANT = "letter"

#: How a filed generated letter is classified — **`create_document`'s decision,
#: published so a caller can name it without importing an ORM enum**.
#:
#: The type itself is fixed inside the command and is not a parameter, for the
#: reason `agents/tools/documents.py` gives at length: a model able to choose it
#: could file chat-drafted prose as a claim's statutory First Report of Injury.
#: What this constant is for is the *reporting* side — a tool that wants to tell
#: the model what was filed needs the token, and reaching into
#: `data/models/enums` for it made `agents/tools/documents.py` the one tool in
#: the build importing `data/`, against AD-13's "a tool holds no session and
#: touches no data layer" (review of Story 6.5). The owning service publishes
#: the fact; the tool quotes it.
#:
#: A `str` rather than the member, because that is what crosses the envelope:
#: `DocType` is a `StrEnum`, so the token is the same either way, and a plain
#: string is what a JSON payload carries.
RTW_DOC_TYPE: Final[str] = DocType.rtw.value

#: The AD-4 `action` for a filed document — the command's own name, so an audit
#: row read months later names the function that wrote it (`edit.py`'s rule).
CREATE_ACTION: Final[str] = "create_document"
DOCUMENT_ENTITY: Final[str] = "document"

#: How long a document's name may be. The column is `Text`, so this is a product
#: rule rather than a storage one: the name is rendered on one line of the
#: documents list beside five other rows, and a paste accident should be refused
#: at the boundary rather than discovered by a table that has stopped lining up.
#: The seeded names are 12–34 characters.
NAME_MAX_LENGTH: Final[int] = 120

#: How long a generated letter may be, in characters.
#:
#: Generous — a return-to-work offer is two or three hundred words — and present
#: for `MAX_LENGTHS`' reason in `edit.py`: an unbounded body is a column a
#: mis-configured client can put a megabyte of prose in, on a table with a
#: seven-year retention floor behind it. The run endpoint caps a *message* at
#: 4,000 characters; a letter the handler has edited is a different kind of
#: payload and gets its own, larger, bound.
BODY_MAX_LENGTH: Final[int] = 20_000

#: The field names the two length rules above are reported against.
NAME_FIELD: Final[str] = "name"
BODY_FIELD: Final[str] = "body_text"


@dataclass(frozen=True)
class RequiredForm:
    """One statutory form a claim's path requires (AC 1)."""

    form_code: str
    form_name: str
    description: str
    timing: str
    #: An external blank, not `BlobStore` content — see the model's docstring
    #: on why these are placeholders pending NFR-4 jurisdiction validation.
    download_url: str


@dataclass(frozen=True)
class EmployeeIdCard:
    """The branded ID card's fields (AC 3).

    Every one of them already appears somewhere else on the case file, and
    they are published again here rather than assembled by the client from the
    header and the intake variant. Two reasons: the card is available at every
    stage while `employeeBusinessId` and `plant` are on the *intake* variant
    only, and a client stitching one card out of two blocks would render a
    different card depending on which stage the claim happened to be in.
    """

    employee_business_id: str
    worker_name: str
    worker_role: str
    policy_num: str
    doi: date
    handler_name: str
    plant: str
    state: str
    region: str


@dataclass(frozen=True)
class DocumentRow:
    """One row of the claim documents list (AC 3).

    `filed_date` is optional because 101 seeded documents carry a timing note
    (`Post-surgery`, `Closed`) where a filing date belongs — the prototype
    prints the note, a `DATE` column cannot hold it, and the honest reading is
    that the date is unknown.
    """

    id: int
    name: str
    doc_type: DocType
    filed_date: date | None


@dataclass(frozen=True)
class DocumentsBlock:
    """Everything the Documents & ID tab draws (AC 1, 3, 5)."""

    path: ClaimPath
    required_forms: tuple[RequiredForm, ...]
    id_card: EmployeeIdCard
    documents: tuple[DocumentRow, ...]
    #: Which rule document classified the path. Published for
    #: `thresholdsVersion`'s reason — every rule that decided something in this
    #: response is named in it.
    path_version: int


@dataclass(frozen=True)
class SheetRow:
    """One labelled line of a document viewer sheet.

    **Exactly one of `text` and `cents` is set**, and the split is the money
    convention rather than fussiness: the amounts on a FROI are integer cents
    end to end and formatted only in the UI, so a row that carried
    `"$1,432.00"` would be the single place in the system where the server
    formatted money. `of_text` and `of_cents` are the only constructors, so the
    invariant holds by construction rather than by review.
    """

    label: str
    text: str | None = None
    cents: int | None = None

    @classmethod
    def of_text(cls, label: str, value: object) -> "SheetRow":
        return cls(label=label, text=str(value))

    @classmethod
    def of_date(cls, label: str, value: date | None) -> "SheetRow":
        # ISO, or absent. Not "—": the em dash is the browser's rendering of an
        # absence (`formatDate`), and a server that sent the dash would be
        # sending a display string that no other date field sends.
        return cls(label=label, text=value.isoformat() if value else None)

    @classmethod
    def of_cents(cls, label: str, value: int) -> "SheetRow":
        return cls(label=label, cents=value)


@dataclass(frozen=True)
class DocumentSheet:
    """A read-only document viewer's whole content (AC 4).

    `blob_url` and `has_blob` are the `BlobStore` boundary's two answers, and
    both are false/None for every seeded document because none has bytes behind
    it. They are on the contract now so that attaching a real PDF later is a
    change to the store and the ingest path, not to this shape, the route or
    the dialog.
    """

    document_id: int
    name: str
    doc_type: DocType
    sheet_variant: str
    rows: tuple[SheetRow, ...]
    #: The two signature lines the prototype's `.sign` block draws. Server-side
    #: because they are part of what the filing *is*, and because a UI that
    #: hardcoded them would have to be edited when a jurisdiction wants a third.
    signatures: tuple[str, ...]
    has_blob: bool
    blob_url: str | None
    #: The document's own words, for the `letter` variant (Story 6.5).
    #:
    #: `None` for every other variant and for every seeded row, which is what
    #: the variant is dispatched on — see `LETTER_VARIANT`. It travels as text
    #: rather than as `SheetRow`s because it is *prose*: a letter has
    #: paragraphs, not labelled fields, and forcing one into a row list would
    #: mean this service deciding where its line breaks are. **The viewer
    #: renders it as pre-wrapped plain text and never as markup** — the body
    #: began as model output that a handler edited, so `Transcript.tsx`'s
    #: sanitized-only discipline is the standard it has to meet (AD-16).
    body_text: str | None = None


SIGNATURE_LINES: tuple[str, ...] = ("Supervisor / Date", "Adjuster / Date")


async def documents_block(
    db: AsyncSession,
    row: sa.Row[Any],
    thresholds: DerivationThresholds,
    documents: Sequence[Document],
) -> DocumentsBlock:
    """Assemble the Documents & ID tab for one already-resolved claim.

    Takes the joined row rather than re-reading the claim: `claim_detail` has
    already resolved it under the caller's scope, and a second lookup here
    would be a second place the tab's identity could come from.

    `documents` arrives the same way and for a sharper reason (code review,
    2026-08-12): the intake variant's checklist reads the *same* rows, so a
    read here meant every intake case file ran one scoped query twice. Taking
    them as an argument is also what leaves this function without a
    `CallerContext` — everything it still reads for itself (`forms_for_path`)
    is unscoped reference data, and a context parameter it did not use would
    be exactly the decoration `data/repositories/statutory_forms.py` argues
    against.
    """
    claim: Claim = row.Claim
    path = derivations.claim_path.for_thresholds(thresholds).of_claim(claim)

    return DocumentsBlock(
        path=path,
        required_forms=tuple(
            RequiredForm(
                form_code=form.form_code,
                form_name=form.form_name,
                description=form.description,
                timing=form.timing,
                download_url=form.download_url,
            )
            for form in await forms_repo.forms_for_path(db, path)
        ),
        id_card=EmployeeIdCard(
            employee_business_id=row.employee_business_id,
            worker_name=row.worker_name,
            worker_role=row.worker_role,
            policy_num=claim.policy_num,
            doi=claim.doi,
            handler_name=row.handler_name,
            plant=claim.plant,
            state=claim.state,
            region=claim.region,
        ),
        documents=tuple(
            DocumentRow(
                id=document.id,
                name=document.name,
                doc_type=document.doc_type,
                filed_date=document.filed_date,
            )
            for document in documents
        ),
        path_version=thresholds.version,
    )


@dataclass(frozen=True)
class NewDocument:
    """A validated document, ready to insert (Story 6.5).

    Returned by `normalise_new_document` so that validation is callable — and
    testable — without a database, exactly as `edit.normalise` and
    `injuries.normalise_new_injury` are.

    `doc_type` is **not** a field, and its absence is the point: `create_document`
    fixes it, so nothing a caller can send decides how a filing is classified.
    See that function.
    """

    name: str
    body_text: str


def normalise_new_document(name: object, body_text: object) -> "NewDocument":
    """Check a filed document's two free-text fields, or raise `InvalidPatch` (a 422).

    Its own validator rather than `edit.require_text`, and the reason is one
    character: **a letter contains newlines**. `require_text` refuses the whole
    C0 range because none of it means anything in a clinical field and all of it
    corrupts a log line downstream — which is right for `injury_type` and wrong
    for a document body, where a paragraph break is the formatting. So the body
    keeps the NUL refusal (PostgreSQL `text` cannot hold one and asyncpg raises
    rather than truncating, which reached a request as an unhandled 500 before
    Story 2.3's review caught it) and admits the two whitespace controls a
    handler can actually type.

    Messages name the field and the rule and never echo the value (AD-11) — a
    validation message that quoted a rejected body would put a letter about an
    injured worker into an error response and, from there, into a log.
    """
    from services.claims.edit import InvalidPatch

    if not isinstance(name, str):
        raise InvalidPatch("the document name must be text")
    trimmed = name.strip()
    if not trimmed:
        raise InvalidPatch("the document name cannot be empty")
    if any(character < " " or character == "\x7f" for character in trimmed):
        raise InvalidPatch("the document name cannot contain control characters")
    if len(trimmed) > NAME_MAX_LENGTH:
        raise InvalidPatch(f"the document name must be {NAME_MAX_LENGTH} characters or fewer")

    if not isinstance(body_text, str):
        raise InvalidPatch("the document body must be text")
    body = body_text.strip()
    if not body:
        raise InvalidPatch("the document body cannot be empty")
    if any(
        character < " " and character not in "\n\r\t" or character == "\x7f" for character in body
    ):
        raise InvalidPatch("the document body cannot contain control characters")
    if len(body) > BODY_MAX_LENGTH:
        raise InvalidPatch(f"the document body must be {BODY_MAX_LENGTH} characters or fewer")

    return NewDocument(name=trimmed, body_text=body)


async def create_document(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    name: object,
    body_text: object,
    as_of: date | None = None,
    now: datetime | None = None,
) -> "ClaimDetail":
    """File one generated document on a claim, audited (Story 6.5, AD-4, AD-12).

    Returns the freshly assembled case file, like every other command in this
    package — the new row is in `documents.documents`, the timeline carries the
    event, and the caller needs no second request to see either.

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404), `InvalidPatch`
    (422) or `StaleClaim` (409), in that order and for
    `services/claims/edit.py`'s reasons: role before scope so that a supervisor
    learns nothing about which claims exist, the patch before the version
    because a 422 tells the caller something actionable about what they sent.

    **`doc_type` is fixed to `DocType.rtw` here and is not a parameter.** The
    only caller is the copilot's `save_rtw_letter` write tool, whose arguments
    are model-facing (AD-16): a `doc_type` argument would be a field a hijacked
    model could set to `froi`, filing a chat-drafted letter as this claim's
    statutory First Report of Injury. The type is a property of the command, so
    it lives in the command. A second kind of generated document is a second
    command, or a parameter added deliberately with its own argument.

    **The version is the claim's, not the document's**, which is
    `add_additional_injury`'s precedent: a command that inserts a child row
    compare-and-swaps on the parent the handler was actually looking at. It is
    also what gives Story 6.5's approval gate something real to pin — the claim
    `version` is read at draft time, travels in the proposal's arguments, and is
    the predicate inside `insert_document_cas`' `INSERT … SELECT`. A claim that
    moved between the draft and the approval inserts nothing.

    **The claim's own `version` is deliberately not bumped**, `add_additional_
    injury`'s decision and its reasoning: no column of `claim` changed, so the
    case file a client is already holding stays a valid basis for its next edit,
    and two handlers filing two documents on one claim are recording two things
    rather than overwriting one.

    **No `mark_claim_stale`** — see the module docstring, where the omission is
    argued rather than assumed.
    """
    from services.claims.detail import ClaimNotVisible, claim_detail
    from services.claims.edit import EditNotPermitted, conflict

    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can file a document on a case file.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    document = normalise_new_document(name, body_text)

    at = now or datetime.now(UTC)

    # The pre-check answers a *stale* client before the statement runs, for
    # `update_claim_fields`' reason: it is the more useful answer, and the
    # statement's own predicate below is still what makes the guard sound.
    if claim.version != expected_version:
        return await conflict(db, ctx, claim_business_id, as_of)

    document_id = await claim_repo.insert_document_cas(
        db,
        ctx,
        claim_business_id,
        expected_version,
        {
            "name": document.name,
            "doc_type": DocType.rtw,
            "filed_date": at.date(),
            "body_text": document.body_text,
        },
    )
    if document_id is None:
        # The claim moved between the SELECT above and the INSERT. Nothing was
        # written — the source query matched no rows — but the transaction has
        # taken a snapshot, so roll it back before re-reading or the "fresh"
        # entity would be the stale one we already have.
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    await audit.record(
        db,
        ctx,
        action=CREATE_ACTION,
        entity=DOCUMENT_ENTITY,
        entity_id=str(document_id),
        before=None,
        # **The row's identity, never its body** (AD-11). `injuries._diff`
        # records the whole inserted row because the row *is* the change and a
        # secondary injury is four short typed fields; a letter's change is
        # three hundred words of prose about an injured worker, and an audit
        # table with a seven-year retention floor is the last place to copy it.
        # The document id resolves to the row for as long as the claim exists,
        # and `claim_id` is here for `injuries._diff`'s reason: Story 8.1's
        # purge cascade needs to find this event from the claim it belongs to.
        after={
            "claim_id": claim.claim_id,
            "name": document.name,
            "doc_type": DocType.rtw.value,
            "filed_date": at.date().isoformat(),
        },
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim.id,
        # The **document's own name**, which is server-composed for the only
        # caller there is — `agents/tools/documents.py` does not take it from
        # the model. `injuries.py` records why this matters: a timeline
        # description that echoed arbitrary caller text would be a log a tool's
        # untrusted arguments could write prose into (AD-16).
        description=f"Return-to-work letter filed ({document.name})",
        tag=TimelineTag.rtw,
        event_date=at.date(),
    )
    # No `rag.mark_claim_stale` — the deliberate omission, argued in the module
    # docstring. This line exists so that a reader comparing this command with
    # its four siblings finds an answer here rather than a difference.
    await db.commit()

    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


class DocumentNotVisible(LookupError):
    """No such document on a claim in the caller's scope.

    One exception for "no such document", "not this claim's" and "not your
    claim", for `ClaimNotVisible`'s reason — see
    `claim_repo.select_document`.
    """


async def document_content(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    document_id: int,
    *,
    store: BlobStore | None = None,
) -> DocumentSheet:
    """The read-only sheet for one document, or `DocumentNotVisible` (AC 4).

    `store` is optional and `None` in every deployment today, because no
    document has bytes: passing one makes `blobUrl` real without changing a
    caller. That is the whole of what the protocol boundary buys here, and it
    is deliberately not more — a default store wired into this signature would
    be a storage decision taken by a viewer.
    """
    document = await claim_repo.select_document(db, ctx, claim_business_id, document_id)
    if document is None:
        raise DocumentNotVisible(f"{claim_business_id}/documents/{document_id}")

    # Scoped, and already resolved once by `select_document`'s join — read
    # again for the claim's own columns, which the sheet is mostly made of.
    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:  # pragma: no cover - the document's join proved the claim
        raise DocumentNotVisible(claim_business_id)
    claim: Claim = row.Claim

    header = (
        SheetRow.of_text("Claim ID", claim.claim_id),
        SheetRow.of_text("Policy Number", claim.policy_num),
        SheetRow.of_text("Employee", f"{row.worker_name} ({row.employee_business_id})"),
        SheetRow.of_text("Employer / Plant", claim.plant),
    )

    rows: tuple[SheetRow, ...]
    if document.body_text is not None:
        # **The letter variant (Story 6.5), dispatched on the body and not on
        # the type.** See `LETTER_VARIANT`: a document renders as a letter
        # because it carries one, so a `doc_type: rtw` row from the Story 2.2
        # seed — which records that a letter exists somewhere without holding
        # its words — keeps the summary sheet below, where it belongs.
        #
        # The header rows, then the filing date and the handler who filed it.
        # Everything a reader needs to know *about* the filing stays a labelled
        # row; the letter itself is prose and travels as `body_text`.
        rows = (
            *header,
            SheetRow.of_date("Filed", document.filed_date),
            SheetRow.of_text("Handler", row.handler_name),
        )
        variant = LETTER_VARIANT
    elif document.doc_type is DocType.froi:
        rows = (
            *header,
            SheetRow.of_date("Date of Injury", claim.doi),
            SheetRow.of_date("Filed", document.filed_date),
            SheetRow.of_text("Injury Type", claim.injury_type),
            SheetRow.of_text("Body Part", claim.body_part),
            SheetRow.of_text("ICD-10", claim.icd),
            SheetRow.of_text("Cause", claim.cause),
            # The **score**, not a band. The prototype prints its own
            # `severity` display string ("High"/"Medium"/"Low"), which is a
            # third banding of one column — the queue dot and the gauge already
            # disagree with it. A first report of injury records what was
            # assessed; the band is a reading of it, and AD-10 says there is
            # one of those.
            SheetRow.of_text("Severity", claim.severity_score),
            SheetRow.of_cents("AWW", claim.aww),
            SheetRow.of_text("Handler", row.handler_name),
            # No Supervisor row, unlike `openDoc`. Story 1.2 deliberately did
            # not seed `supervisor` and Story 2.2 left the same row off the
            # treatment card for it — "a row that always reads '—' is
            # furniture, not honesty". A statutory filing is the last place to
            # print a blank where a name goes.
            SheetRow.of_text(
                "OSHA Recordable", "Yes — OSHA 300 Filed" if claim.osha_recordable else "No"
            ),
        )
        variant = FROI_VARIANT
    else:
        rows = (
            *header,
            SheetRow.of_date("Filed", document.filed_date),
            SheetRow.of_text("Status", "On file"),
            SheetRow.of_text("Handler", row.handler_name),
        )
        variant = SUMMARY_VARIANT

    return DocumentSheet(
        document_id=document.id,
        name=document.name,
        doc_type=document.doc_type,
        sheet_variant=variant,
        rows=rows,
        signatures=SIGNATURE_LINES,
        has_blob=document.blob_key is not None,
        blob_url=(
            store.url(document.blob_key)
            if store is not None and document.blob_key is not None
            else None
        ),
        body_text=document.body_text,
    )
