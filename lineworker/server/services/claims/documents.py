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
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim, Document
from data.models.enums import ClaimPath, DocType
from data.repositories import claims as claim_repo
from data.repositories import statutory_forms as forms_repo
from rules.parameters import DerivationThresholds
from services import derivations
from services.blobstore import BlobStore

#: The sheet a first report of injury renders, versus the sheet everything else
#: renders. Two variants, discriminated on the wire, for the reason the stage
#: overviews are a discriminated union: a payload with every field optional can
#: describe a summary sheet carrying an ICD-10 code, and the client would need
#: eleven truthiness checks where the prototype has one `if`.
FROI_VARIANT = "froi"
SUMMARY_VARIANT = "summary"


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
    if document.doc_type is DocType.froi:
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
    )
