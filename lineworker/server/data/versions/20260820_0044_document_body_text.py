"""`document.body_text` — where a generated letter's words live (Story 6.5).

One nullable `Text` column, and it is the whole of the schema change Story 6.5
was authorized to make. The story as written says "reuse the Epic 2 documents
path; **no new table** — if the existing `document` shape can't carry a
generated letter, stop and raise rather than inventing schema". It cannot: the
table is `id, claim_id, name, doc_type, filed_date, blob_key, reviewed,
confirmed, version`, with no column a body could go in. That raise was made and
answered — the product owner authorized extending `document` by exactly one
column, one create command and one viewer variant, and nothing wider.

## Why not `blob_key`

`blob_key` is a handle to bytes that still do not exist. All 563 seeded rows
carry `NULL` there because the prototype has no files behind its document rows,
and the `BlobStore` protocol's backing implementation — a mounted volume now, an
object store later — is an explicitly deferred architecture decision. Storing a
letter as text in its own column is what lets this story ship a saved letter
*without* taking that decision inside a copilot story. The two columns are not
alternatives and neither replaces the other: `blob_key` will point at a scanned
PDF somebody filed, `body_text` holds prose this console generated and a handler
edited.

## Why nullable, and why there is no backfill

Every existing row is a document with no body, which is the truth about it. A
`NOT NULL` column would need 563 empty strings written to say the same thing
less honestly, and `document_content` dispatches on `body_text is not None`
precisely so that "this document carries a letter" is a fact about the row
rather than about its `doc_type`. The down-revision therefore drops the column
without touching a seeded row.

## No enum migration, and that is not an oversight

`DocType.rtw` is already a member of the closed six (`data/models/enums.py`),
declared by Story 2.2 for exactly this document class, so the letter is filed
under a type that already exists. `timeline_event.tag` is a `Text` column rather
than a native enum — deliberately, and its docstring names "Epic 6's generated
letters" as one of the writers it was widened for — so `TimelineTag.rtw` costs
no `ALTER TYPE` either. The schema was built expecting this row; what was
genuinely missing is the place to put the words.

**PHI-class** (AD-11) like every other claim-derived column: a return-to-work
letter names the injured worker and quotes their clinical restrictions. It is
covered by the encrypted volume, is banned from logs, and is purged by the
Story 8.1 cascade through the row it hangs off.

Revision ID: 0044_document_body_text
Revises: 0043_copilot_threads
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_document_body_text"
down_revision: str | None = "0043_copilot_threads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document", sa.Column("body_text", sa.Text(), nullable=True))


def downgrade() -> None:
    # Dropping this destroys every generated letter's text, which is the honest
    # inverse of a migration that created the only place they were ever stored.
    # Unlike 0029's system actor there is no audit-trail hazard: the letters are
    # a handler's own drafting, the `document` rows they hang off survive, and
    # the approval that filed each one is a `copilot_approval.*` audit row this
    # migration does not touch.
    op.drop_column("document", "body_text")
