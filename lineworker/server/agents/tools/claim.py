"""`claim_reader` — the case file's header, as a tool envelope (Story 6.3).

The fifth thin wrapper and the first one the *chat* graph needs rather than the
insight cache: a grounded answer about a claim starts with knowing what the
claim is. One service call — `services/claims/detail.claim_detail` — and the
header out of what it returns.

## Why the header and not the whole case file

`claim_detail` assembles a header, a stepper, a stage variant, the injury
diagram, the documents block, the photos block, the benefit and the reserve
check. Handing all of that to a model would be handing it four other tools'
payloads in an envelope that says none of them came from those tools — the
reserve figures would arrive without `reserve_check`'s `display` strings, and
AD-2's "quote the display value verbatim" would have nothing to quote from.

So this returns the header, which is the block with no money in it at all: an
identity, an injury, a severity score, a stage, and the boolean flags. Every
figure a narrative might want is a different registered tool's answer.

## The narrative fields arrive **fenced**, and that is AD-16 on this path

A case-file header is not all structured data. The worker's name and role, the
employer's name, `injury_type`, `cause` and the ICD text are written by people —
an FNOL intake, an employer record, a clinician, an adjuster — and
`cause` in particular is a paragraph of somebody's prose. Story 6.2 already
established what happens to such text before a model sees it: it is scrubbed of
anything that could pass for structure and wrapped in a source-tagged fence
(`agents/insights.scrub`/`fence`), so an item cannot forge a boundary and the
system prompt can say without qualification that everything inside a fence is
material to analyse.

That machinery is reused here rather than reinvented — the two functions were
made public in this story for exactly this second caller — because a claim's
`cause` reaching a chat turn is the same exposure as the same string reaching an
insight prompt, and a containment control with two implementations is a
containment control with one bug.

So the tool returns a `ClaimContext` rather than a raw `CaseHeader`: the
structured fields as the service produced them, and the free text as fenced
items. `tests/test_prompt_injection_fixtures.py` runs the adversarial fixture
through a real graph run and asserts the marker appears only inside a fence.

## `display` is empty, and that is a statement

`next_actions` makes the same one. There is no money and no date on the header —
`severity_score` and `fraud_score` are scores on a fixed scale, not currency —
so an envelope that manufactured a display string here would be inventing a
formatting decision to fill a field, which is how the field stops meaning "the
service's own rendering".

## The worker's name *is* here, unlike in the insight pipeline — and it is fenced

`agents/insights.py::_claim_narrative` deliberately omits it: a cached card
about a reserve is not improved by a name, and a prompt is a place to send the
minimum that answers the question. A conversation is different — a handler
asking "what's the story on this claim?" is asking about a person, and the panel
header two inches above the transcript already shows the name. Sending it is
not a widening of what the reader can see; withholding it would only make the
model's prose worse.

**But it goes inside the fence with everything else somebody typed**, which it
did not until the review of Story 6.3. `worker_name`, `worker_role` and
`employer_name` shipped as bare structured fields that had been through `scrub`
alone, on the argument that they are short identifiers rather than prose. The
argument does not hold: `scrub` removes control characters, bidi overrides,
zero-widths, `<` and the two section headings — it removes the ability to *forge
a boundary*, and nothing else. It does not remove instruction text, and
"Kowalski. IGNORE ALL PREVIOUS INSTRUCTIONS…" in a worker-name column is
plain ASCII that survives every pass. Arriving outside the delimiter, that text
sat in the half of the message the system prompt does **not** mark untrusted,
which is precisely the property AD-16's fence exists to give.

Length was never the criterion. *Provenance* is: these three strings are typed
by an FNOL intake and an employer record, by people this console does not
control, exactly like `cause`. So they are fenced items with a source tag like
every other one, and the structured half of `ClaimContext` is now only values
this system computed or assigned.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from agents.fencing import fence
from data.context import CallerContext
from services.claims.detail import CaseHeader, ClaimNotVisible, claim_detail


@dataclass(frozen=True)
class ClaimContext:
    """One claim as a model may read it: computed facts, and fenced free text.

    The split is the point, and the line is **provenance, not length**.
    Everything above `narrative` is a scalar this system computed or an
    identifier it assigned — a business id, a two-letter state code, a severity
    score, a risk band, a stage, four booleans — and none of it is a channel
    anybody can write prose into. `narrative` is everything a person typed, and
    every item in it has been through `scrub` and comes wrapped in a
    source-tagged fence.

    `worker_name`, `worker_role` and `employer_name` are on the second side of
    that line, and moving them there is the review of Story 6.3's correction.
    They shipped scrubbed-but-bare on a length argument; see the module
    docstring on why scrubbing is a defence against forging a *boundary* and
    never against instruction text, and why "short" is not the property that
    makes a string safe to send outside one.
    """

    claim_id: str
    state: str
    severity_score: int
    risk: str
    stage: str
    status: str
    fraud_flag: bool
    litigation_flag: bool
    surgery_required: bool
    osha_recordable: bool
    #: Everything on this header a person wrote — one fenced, source-tagged item
    #: per statement, in the order a narrative would use them.
    narrative: tuple[str, ...]


def claim_context(header: CaseHeader) -> ClaimContext:
    """One case-file header, split into computed facts and fenced narrative.

    Five items. The ICD code and the body part are one item rather than two,
    `agents/insights._claim_narrative`'s division and for its reason: they are
    one clinical statement, and split across two fences they would read as two
    unrelated fragments. The worker's name and role are likewise one item —
    they are one person's identification and a model that has to join two
    fences to say "a welder called Kowalski" is a model given a puzzle for no
    reason.
    """
    return ClaimContext(
        claim_id=header.claim_id,
        state=header.state,
        severity_score=header.severity_score,
        risk=header.risk.value,
        stage=header.stage.value,
        status=header.status.value,
        fraud_flag=header.fraud_flag,
        litigation_flag=header.litigation_flag,
        surgery_required=header.surgery_required,
        osha_recordable=header.osha_recordable,
        narrative=(
            fence(
                f"claim:{header.claim_id}:worker",
                f"{header.worker_name} — {header.worker_role}",
            ),
            fence(f"claim:{header.claim_id}:employer", header.employer_name),
            fence(f"claim:{header.claim_id}:injury_type", header.injury_type),
            fence(f"claim:{header.claim_id}:cause", header.cause),
            fence(f"claim:{header.claim_id}:icd", f"{header.icd} {header.body_part}"),
        ),
    )


async def claim_reader(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> ToolResult[ClaimContext]:
    """The claim's case-file header, scoped.

    `ok=False` for a claim the caller cannot see, `reserve_check`'s rule and its
    reason — except that here the failure is genuinely reachable in normal use:
    the run endpoint re-resolves scope at start, but a claim can leave a
    handler's book between that resolution and a tool call several model
    seconds later. A structured refusal into the transcript is the right
    outcome; a raised exception would end the run with an `error` frame for
    what is really "I can no longer see that claim".
    """
    try:
        detail = await claim_detail(db, ctx, claim_business_id)
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")
    return ToolResult.succeeded(claim_context(detail.header))
