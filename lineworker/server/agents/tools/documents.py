"""`save_rtw_letter` — the copilot's first write, and the story's subject (6.5).

One service call, `services/claims/documents.create_document`, and nothing else.
AD-13's thin-wrapper rule applies to a write exactly as it applies to a read:
this function validates nothing, decides nothing, composes nothing, holds no
session and touches no repository. It converts one command's exceptions into the
`{ok, data, display}` envelope the model reads and gets out of the way.

## What makes this tool safe is not this file

Nothing here checks an approval. The gate is `HumanInTheLoopMiddleware` on the
`create_agent` core (AD-6): the model may *select* this tool — it is bound like
every other registry entry, because §5.2 says write tools are "gated, not
hidden" — and the middleware pauses the run before it executes. On approve or
edit, `agents/approval.py` records the marker keyed by the tool-call id, and
`agents/registry.invoke` refuses any call that arrives without one. Three
mechanisms, in three files, and this file is deliberately none of them.

## `doc_type` is not an argument, and that is the AD-16 half

`create_document` fixes the filing type to `DocType.rtw`. It is *not* a
parameter this wrapper passes through, and there is no field for it on the
argument schema (`registry.RtwLetterArgs`). The reason is the one AD-16 gives
for every model-facing field: a hijacked model that could choose the type could
file three hundred words of chat-drafted prose as this claim's statutory First
Report of Injury, under a name of its own choosing, and the approval card would
have shown a handler a plausible-looking save. What the model can decide is what
the letter says; what it cannot decide is what kind of document a letter is.

The `name` it *can* set is bounded the same way every other free-text field on a
case file is — trimmed, control-character-free, length-capped by
`normalise_new_document` — and, for the path this story actually ships, the
handler's own modal never lets the model near it: `api/routers/copilot.py`
composes the name server-side for the deterministic save.

The token this file reports back is `services.claims.documents.RTW_DOC_TYPE`,
**not `data.models.enums.DocType`** — the owning service publishes the fact and
this quotes it. Importing the ORM enum made this the only tool in the build
reaching into `data/`, which AD-13 rules out for a wrapper that is supposed to
hold no session and touch no data layer (review of Story 6.5). It is a small
import either way; what it costs is the rule.

## The version comes from the draft, not from here

`expected_version` is an argument because it is a *fact recorded at draft time*
— `rtw_reader` reads the claim's `version`, the draft is composed against it,
and the save pins it. A wrapper that re-read the version here would be a wrapper
that quietly refreshed the pin and turned a stale approval into a silent
force-write, which is the precise failure AD-6's version-pinned approvals exist
to prevent. It is also why it is on the argument schema despite being a number
no handler types: it travels with the proposal, the approval card renders it,
and an `edit` decision may not touch it (`agents/approval.py` enforces that).

## The failures a write can have, and why none of them raises

`StaleClaim` is the one this story is about: the claim moved between the draft
and the approval, so nothing was inserted. It comes back as `ok: false` with a
sentence a handler can act on, because a lost race is a *normal outcome* of a
gated write and not a broken deployment — the run continues, the graph discards
the proposal, and the handler is told the claim changed. The asymmetry with
`WriteNotApproved`, which does raise, is `agents/registry.py`'s and is the same
one: an ungated write reaching a command is a broken deployment; a claim that
moved is Tuesday.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from data.context import CallerContext
from services.claims.detail import ClaimNotVisible
from services.claims.documents import RTW_DOC_TYPE, create_document
from services.claims.edit import EditNotPermitted, InvalidPatch, StaleClaim


@dataclass(frozen=True)
class SavedLetter:
    """What a filed letter reports back — identity, never content.

    The letter's own words are deliberately **not** here, and the omission is
    the same one `services/audit.record_copilot_approval` makes for the same
    reason: this payload goes into the model's context as a `ToolMessage` and
    from there into a checkpoint. Echoing three hundred words of a letter about
    an injured worker back at the model buys nothing — the model does not need
    to be told what was just saved, because it is not the author; the handler is
    — and it doubles the PHI surface of a turn that already has one.

    `claim_version` is the claim's version *after* the filing, which is the
    same number it was before: `create_document` deliberately does not bump it
    (see that command). It is published so a narration can say the case file is
    unchanged apart from the new document, which is the true and slightly
    surprising thing about this write.

    `document_count` is how many documents the claim now has. A count rather
    than the new row's id, because the id is a surrogate key that means nothing
    in a sentence, and because the count is what a handler can check against the
    Documents tab in front of them.
    """

    claim_id: str
    name: str
    doc_type: str
    claim_version: int
    document_count: int


async def save_rtw_letter(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    name: str,
    body_text: str,
    expected_version: int,
) -> ToolResult[SavedLetter]:
    """File the return-to-work letter on the claim, version-pinned and audited.

    Wraps exactly one command. `display` is empty for `next_actions`' recorded
    reason — there is no money and no date in this payload that a service
    formatted, and an empty map is a statement rather than an omission.

    Every refusal comes back as `ok: false` with a short, content-free sentence
    (AD-11): the caller's role, a claim that has left their book, a body the
    command would not accept, and the stale race this story's gate exists to
    survive. None of them is a stack trace and none of them echoes the letter.
    """
    try:
        detail = await create_document(
            db,
            ctx,
            claim_business_id,
            expected_version=expected_version,
            name=name,
            body_text=body_text,
        )
    except EditNotPermitted:
        return ToolResult.failed("only a claims handler can file a document on a case file")
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")
    except InvalidPatch as exc:
        # The command's own sentence, which names the field and the rule and
        # never the value it refused (`normalise_new_document` argues why).
        return ToolResult.failed(f"the letter was not accepted: {exc}")
    except StaleClaim:
        return ToolResult.failed(
            "the claim changed since this letter was drafted, so nothing was "
            "filed — re-read the claim and draft it again"
        )
    return ToolResult.succeeded(
        SavedLetter(
            claim_id=detail.claim_id,
            name=str(name).strip(),
            doc_type=RTW_DOC_TYPE,
            claim_version=detail.version,
            document_count=len(detail.documents.documents),
        )
    )
