"""`update_claim_field` — the second and last write this story registers (6.5).

One service call, `services/claims/edit.update_claim_fields`, wrapped exactly as
thinly as the seven reads beside it. No validation, no composition, no session,
no repository: the command owns the whitelist, the normalisation, the
compare-and-swap, the audit row, the timeline event and the mark-stale call, and
this function converts its refusals into the `{ok, data, display}` envelope.

## Why this tool exists at all, when the story is about a letter

Two write tools ship, and the split is deliberate rather than incidental.
`save_rtw_letter` inserts a *child* row guarded on its **parent's** version;
this one updates a row guarded on **its own**. Those are the two
compare-and-swap shapes the build has, and registering one of each is what makes
the approval gate's version pinning a tested property of both rather than a
claim about the shape that happens to have shipped.

It is also the vehicle for two scenarios the gate has to survive and a letter
cannot exercise. A **free-chat** write — the model, unprompted by any quick
action, deciding a field should change — reaches the gate through
`HumanInTheLoopMiddleware` and produces the identical interrupt payload the
letter's deterministic save produces; that identity is the AC the whole story
turns on, and it is not assertable with one write origin. And the
**injection-shaped unrequested write** — a model scripted to call a write tool
the handler never asked for — needs a tool the model can plausibly select from a
chat turn, which a letter save with a three-hundred-word body is not.

## What the model can and cannot reach

`field` and `value` are model-facing, and the blast radius of both is bounded by
`update_claim_fields` rather than by anything here. That command's whitelist is
seven columns of the injury card — `injury_type`, `cause`, `body_key`, `icd`,
`icd_desc`, `disability`, `recovery` — and it refuses everything else with a
422: no financial column, no `stage`, no `severity_score`, no `version`. A model
that asked to set `reserve_cents` gets a structured failure, not a write, and it
gets it *before* the approval gate has anything to show a handler.

`expected_version` is an argument for `save_rtw_letter`'s reason: it is a fact
recorded when the write was drafted, not something to re-read at execution time.
Re-reading it here would turn every stale approval into a silent force-write.

## Diary, meetings and emails are deliberately not registered

AC 1 names five entity classes, and the gate is node-agnostic by construction —
it fires on any `kind: write` registry entry, so what the AC governs is what
happens when one is proposed rather than how many exist. The other three are
append-only and carry no version to pin: `diary_note` and `email_log` have no
`version` column and `create_meeting` takes no `expected_version`, each argued
in its own module as a deliberate design rather than an omission. Registering
them would widen the model's reach without exercising anything the two here do
not, and it would put entries behind the gate for which "version-pinned
approval" has no meaning. Adding one later changes no wire shape, which is the
property that makes deferring them safe.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from data.context import CallerContext
from services.claims.detail import ClaimNotVisible
from services.claims.edit import (
    EDITABLE_FIELDS,
    EditNotPermitted,
    InvalidPatch,
    StaleClaim,
    update_claim_fields,
)

#: The fields a model may name, as a sentence for its own tool description.
#:
#: Derived from `EDITABLE_FIELDS` rather than restated, so a story that widens
#: or narrows the command's whitelist cannot leave the model reading a list the
#: command no longer honours — which would surface as a proposal a handler
#: approved and the command then refused 422.
EDITABLE_FIELD_LIST: str = ", ".join(EDITABLE_FIELDS)


@dataclass(frozen=True)
class ClaimFieldUpdate:
    """What an applied field edit reports back.

    `version` is the claim's **new** version, which is the number the next write
    on this claim has to pin. It is the one genuinely useful thing to hand back:
    a run that made two edits in sequence would otherwise draft the second
    against a version the first has just superseded.

    The written value is deliberately absent, `SavedLetter`'s reason: this
    payload lands in a checkpoint, and the value is already in the audit row
    where it belongs. `field` is a whitelisted column name and is not claim
    content.
    """

    claim_id: str
    field: str
    version: int


async def update_claim_field(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    field: str,
    value: str,
    expected_version: int,
) -> ToolResult[ClaimFieldUpdate]:
    """Apply one whitelisted field edit, version-pinned and audited.

    Wraps exactly one command, which is what makes "the copilot's write is the
    same write a handler's own inline edit performs" true rather than similar:
    the same whitelist, the same normalisation, the same audit action
    (`update_claim_fields`), the same timeline sentence, and the same
    `mark_claim_stale` call it already makes. This wrapper adds nothing to any
    of them — in particular it does **not** add a second mark-stale call, which
    would be a tool composing a write.

    `display` is empty: nothing in this payload is a money amount or a date a
    service formatted.
    """
    try:
        detail = await update_claim_fields(
            db,
            ctx,
            claim_business_id,
            expected_version=expected_version,
            patch={field: value},
        )
    except EditNotPermitted:
        return ToolResult.failed("only a claims handler can edit a case file")
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")
    except InvalidPatch as exc:
        # The command's own sentence: it names the field and the rule and never
        # the value it refused (AD-11).
        return ToolResult.failed(f"the edit was not accepted: {exc}")
    except StaleClaim:
        return ToolResult.failed(
            "the claim changed since this edit was drafted, so nothing was "
            "written — re-read the claim and propose it again"
        )
    return ToolResult.succeeded(
        ClaimFieldUpdate(claim_id=detail.claim_id, field=field, version=detail.version)
    )
