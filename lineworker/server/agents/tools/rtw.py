"""`rtw_reader` — the return-to-work facts a draft letter may state (Story 6.4).

One service call, `services/claims/detail.claim_detail`, and a projection of the
three things on it that a return-to-work letter is about: what the treating
clinician says the worker must not do, what the prognosis says about returning,
and whether the claim's stage variant records a return status at all.

It is a separate wrapper from `claim_reader` rather than a widening of it, and
the reason is AD-13's thin-wrapper rule read forwards. `claim_reader` returns
the case-file *header* — an identity, an injury, a severity score, four flags —
and its docstring argues at length that handing a model the whole `ClaimDetail`
would be handing it four other tools' payloads in an envelope that says none of
them came from those tools. The same argument applies to widening it here: a
letter needs the injury restrictions, a conversation about "what is this claim?"
does not, and a tool whose payload grows with each new consumer is a tool whose
`display` map stops meaning anything. Two thin projections of one call is the
shape this package already has — `reserve_check` and `fraud_signals` both reach
`claim_detail` too, each taking the block it owns.

## Everything a person wrote arrives fenced

`contraindications` and `prognosis.rtw` are clinical prose, typed by a
clinician, and they are the *whole point* of the letter — the draft quotes them.
So they get `agents/tools/claim.py::claim_context`'s treatment exactly: scrubbed
of anything that could pass for structure, wrapped in a source-tagged fence, and
handed over as items rather than as bare fields. The review of Story 6.3
established the criterion and it is provenance, not length: a short string typed
by somebody outside this console is as capable of carrying "IGNORE ALL PREVIOUS
INSTRUCTIONS" as a long one.

`return_status` is not fenced, because it is a `StrEnum` member this system
assigned — a closed set of tokens, not a channel anybody can write prose into.

## **No return date, and the omission is the honest half**

There is no date anywhere in this payload, and no registered tool projects one.
`Claim.rtw_rec` and `Claim.actual_rtw` are columns on the ORM model and are on
**no** `ClaimDetail` field, so putting a target return date in a letter would
mean either a services-layer change this story has no standing to make or a date
the model made up — and AD-2 forbids the second in the same sentence it forbids
a money amount.

"No registered tool" rather than "nothing in the build", which is the stronger
claim and is false: `services/worklist/actions.py::_overdue_rtw` reads both
columns straight off the ORM row, so `next_actions` — one quick-action button
away — can tell the same handler the return date is overdue. The gap is a
missing *projection*, not a missing fact, and stating it the loose way had the
two actions contradicting each other about one claim.

A letter that says "the return date will be confirmed" is correct; one that
names a Tuesday nobody chose is a letter an employer might act on. **Story 6.5
closed the gap the way this paragraph predicted** — not by projecting the
columns, but by giving the handler an editable modal to type the date into. The
tool still supplies none, the model still states none, and the date in a saved
letter is a person's.

## Nothing here proposes a write — and that is still true

This wrapper is `kind: read` like the other six, and the `rtw` quick action
still streams a draft into the transcript and proposes nothing. What Story 6.5
added is one field, `version`, and it is the reason to read this docstring
twice: the *reader* is where a save's version pin comes from, because the
version that matters is the one the draft was composed against. The write tool
is `save_rtw_letter` in `agents/tools/documents.py`; the gate is
`HumanInTheLoopMiddleware` on the `create_agent` core; and the QAS node still
holds neither (AD-6: quick-action nodes hold no write tools, they route
proposals into the one gated step). 6.5 reused this key rather than adding an
eighth, which is what makes the seven keys a stable contract.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from agents.fencing import fence
from data.context import CallerContext
from services.claims.detail import (
    ClaimDetail,
    ClaimNotVisible,
    SettledOverview,
    TreatmentOverview,
    claim_detail,
)


@dataclass(frozen=True)
class RtwContext:
    """The return-to-work facts, split into assigned tokens and fenced prose.

    `claim_id` and `return_status` are what this system assigned; `restrictions`
    is everything a clinician typed, one fenced source-tagged item per
    statement, in the order a letter would use them.

    `return_status` is `None` for the two stage variants that do not carry one —
    an intake claim has not been assessed for return yet and an investigation
    claim's overview is the injury and the financials. `None` therefore means
    "the case file records no return status at this stage", which is a fact the
    letter may state, and it is deliberately not collapsed into a string like
    `"unknown"` that would read as a status somebody assigned.
    """

    claim_id: str
    return_status: str | None
    #: The claim's compare-and-swap column, **read at draft time** (Story 6.5).
    #:
    #: This is the number the letter's save is pinned on. The handler reads a
    #: draft that was composed from this version of the case file, edits it, and
    #: approves a save; `create_document`'s `INSERT … SELECT` carries this as its
    #: predicate, so a claim somebody else edited in the meantime files nothing
    #: and the approval fails safe instead of writing a letter about a case file
    #: that has moved (AD-4's Write-concurrency convention, AD-6's version-pinned
    #: approvals).
    #:
    #: It is on this projection rather than on `claim_reader`'s because this is
    #: the tool the letter is drafted from, and a version read by some *other*
    #: tool at some other moment is a number that pins nothing in particular.
    version: int
    #: Fenced items: the treating clinician's contraindications, and the
    #: prognosis for returning to work. Empty strings are omitted rather than
    #: fenced — an empty fence is a boundary around nothing, and a model asked
    #: to quote it would quote whitespace.
    restrictions: tuple[str, ...]


def _return_status(detail: ClaimDetail) -> str | None:
    """The stage variant's return status, where the variant carries one.

    An `isinstance` over the discriminated union rather than a `getattr` with a
    default, so a fifth variant added to `StageOverview` arrives as a `None`
    that a reviewer can find rather than as an attribute lookup that silently
    keeps working on a class that never had the field.
    """
    overview = detail.overview
    if isinstance(overview, TreatmentOverview | SettledOverview):
        return overview.return_status.value
    return None


def rtw_context(detail: ClaimDetail) -> RtwContext:
    """One case file, projected to what a return-to-work letter may state.

    Two fenced items rather than one, because they are two statements from two
    places on the case file: what the worker must not do (the injury tab's
    contraindications) and what the clinician expects about returning (the
    prognosis card's `rtw` line). Merged into one fence they would read as a
    single clinical opinion, and a draft that attributed a restriction to a
    prognosis would be attributing it to the wrong document.
    """
    injury = detail.injury
    items: list[str] = []
    if injury.contraindications.strip():
        items.append(fence(f"claim:{detail.claim_id}:contraindications", injury.contraindications))
    if injury.prognosis.rtw.strip():
        items.append(fence(f"claim:{detail.claim_id}:prognosis_rtw", injury.prognosis.rtw))
    return RtwContext(
        claim_id=detail.claim_id,
        return_status=_return_status(detail),
        version=detail.version,
        restrictions=tuple(items),
    )


async def rtw_reader(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> ToolResult[RtwContext]:
    """The claim's return-to-work restrictions and status, scoped.

    `ok=False` for a claim the caller cannot see, `claim_reader`'s rule and its
    reason: the run resolved scope at start, but a claim can leave a handler's
    book between that resolution and a tool call several model seconds later,
    and a structured refusal into the transcript is the right outcome for a
    race that means "I can no longer see that claim".

    `display` is empty, and `next_actions` records why that is a statement
    rather than an omission: there is no money and no date in this payload —
    see the module docstring on why there is deliberately no date at all.
    """
    try:
        detail = await claim_detail(db, ctx, claim_business_id)
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")
    return ToolResult.succeeded(rtw_context(detail))
