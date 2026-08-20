"""The seeded case-summary greeting — deterministic output, not model prose (AD-2).

UX-DR8 puts a "seeded case-summary greeting" at the top of the copilot panel:
the first sentence a handler reads when they open a claim's conversation. This
module composes it, from a versioned template in `agents/prompts/` filled with
figures read out of `claim_reader`'s envelope, and **no model is involved at
any point**.

## Why a model does not write it

AD-2 draws the line at figures, and every clause of this greeting is one: a
claim id, a name, an employer, a state, an injury and its ICD-10 code, a
severity score, a risk band, a stage, a status, and four booleans. There is no
prose here for a model to contribute — only a sentence's worth of scaffolding
around values a service already computed. Asking a model to assemble them would
be asking it to originate the first thing anybody reads about a claim, on a
surface where a single transposed severity score reads as a claim fact.

It also has to be **the same every time**. The panel re-renders it on every
open, and a greeting that varied would make "the panel loaded" an assertion
about prose, which AD-15 forbids and which `e2e/stories/6-3-…spec.ts` would
have no way to write.

## Why the text is nevertheless a prompt file

`agents/prompts/copilot_greeting.md` carries it, with the same
`<!-- prompt: key vN -->` header, the same loader validation and the same
version. The package's argument for that is not "this is an instruction" — it
is that **user-visible text belongs in a file a reviewer reads as prose**
rather than in an f-string three call frames from where it renders, and this is
the single most-read sentence the copilot produces. The version travels with it
so a greeting written before a wording change is distinguishable from one
written after. `agents/prompts/__init__.py::GREETING_KEY` records the same
reasoning from the other side.

## Why this reads the case file directly rather than through `claim_reader`

The registered tool fences the claim's free text, because its consumer is a
**model** and AD-16 says untrusted text reaches one only inside a delimiter.
This greeting's consumer is a **person**, reading a sentence in a panel beside
the case file that contains the same words — so a fence here would put
`<<<LINEWORKER-ITEM …>>>` on screen and would be a containment control applied
where there is nothing to contain.

So it calls `services/claims/detail.claim_detail`, the same one call the tool
makes, and reads the header. That is one service call in a function that does
nothing else, which is the same thinness the wrappers in `agents/tools/` keep;
the difference is only what it does with the answer.

## AD-11

The greeting names the injured worker, and that is correct here where
`agents/insights.py::_claim_narrative` deliberately omits it: the panel header
two inches above already shows the name, so this adds nothing a reader cannot
see, and a case summary that would not say who the case is about is not a case
summary. It is claim data all the same — it is rendered, never logged.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agents import prompts
from data.context import CallerContext
from services.claims.detail import CaseHeader, ClaimNotVisible, claim_detail

#: The badge sentence, and the four flags that can put a clause in it.
#:
#: Only flags that are *set* are named. A greeting listing "not in litigation,
#: no surgery required, not OSHA recordable, not flagged for fraud" would be
#: four negatives a handler has to read past to find the one positive, and the
#: case file's own badges already render the full set.
_FLAG_LABELS: tuple[tuple[str, str], ...] = (
    ("litigation_flag", "in litigation"),
    ("surgery_required", "surgery required"),
    ("osha_recordable", "OSHA recordable"),
    ("fraud_flag", "flagged for fraud"),
)


@dataclass(frozen=True)
class Greeting:
    """The rendered greeting and the template version that produced it.

    Two fields because the API publishes both: the sentence the panel renders,
    and the version, so a transcript recorded against one wording is
    distinguishable from one recorded against the next. `ai_insight.content`
    carries `prompt_version` for the same reason and it is the same property.
    """

    text: str
    version: int


def case_summary_greeting(header: CaseHeader) -> Greeting:
    """The greeting for one claim, from its case-file header. No model, no I/O.

    A pure function of a `CaseHeader`, which is what makes it assertable: the
    test compares it against a second call, and the e2e spec asserts the
    element renders without asserting one word of prose.

    Every value is quoted as the service produced it. `stage`, `status` and
    `risk` are `StrEnum`s, so their `.value` is the snake_case token — rendered
    with underscores turned to spaces, which is a *presentation* choice made
    once here rather than a re-derivation: the token is still the service's, and
    nothing downstream re-maps it.
    """
    template = prompts.greeting_template()
    flags = [label for field, label in _FLAG_LABELS if getattr(header, field)]
    flag_clause = f" Flags: {', '.join(flags)}." if flags else ""
    text = template.text.format(
        claim_id=header.claim_id,
        worker_name=header.worker_name,
        worker_role=header.worker_role,
        employer_name=header.employer_name,
        state=header.state,
        injury_type=header.injury_type,
        body_part=header.body_part,
        icd=header.icd,
        severity_score=header.severity_score,
        risk=_words(header.risk.value),
        stage=_words(header.stage.value),
        status=_words(header.status.value),
        flags=flag_clause,
    )
    return Greeting(text=text.strip(), version=template.version)


async def claim_greeting(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> Greeting | None:
    """The greeting for one claim, scoped, or `None` when the caller cannot see it.

    `None` rather than a raised `ClaimNotVisible`, because the route's answer to
    an invisible claim is the case file's own 404 and a second exception type
    reaching the router would be a second place that decision is made.
    """
    try:
        detail = await claim_detail(db, ctx, claim_business_id)
    except ClaimNotVisible:
        return None
    return case_summary_greeting(detail.header)


def _words(token: str) -> str:
    """A snake_case enum value as words. Presentation only — never a re-mapping.

    Deliberately not a lookup table. A table would be a second vocabulary to
    keep in step with `data/models/enums.py`, and the one that drifts is the one
    that renders a stage nobody recognises; `.replace("_", " ")` cannot drift
    because it does not know anything.
    """
    return token.replace("_", " ")


__all__ = ["Greeting", "case_summary_greeting", "claim_greeting"]
