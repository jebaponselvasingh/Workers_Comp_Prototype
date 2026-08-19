"""The claim-text composer — one function, and the determinism it guarantees.

`compose_claim_text` turns a claim into the paragraph that gets embedded, and
`text_hash` turns that paragraph into the `source_text_hash` stored beside the
vector. Everything about this module exists to make one sentence true:

> The same claim state produces byte-identical text, and a different claim
> state produces different text.

Both halves matter and they fail in opposite directions. If the text can vary
without the claim varying — a set iterated in hash order, a dict whose key
order shifted, a timestamp of "now" in the summary — then every refresh run
finds every hash changed, re-embeds the whole portfolio, and the `stale` column
means nothing. If the text can stay the same while the claim changes — a field
omitted from the composition that a handler can edit — then a claim's vector
silently describes its pre-edit self and a similar-case search returns the
wrong neighbours with a fresh `embedded_at` beside them.

So: a fixed field order, explicit sentinels for every nullable value, enum
*values* rather than repr, dates as ISO strings, and additional injuries sorted
by primary key. `tests/test_rag_embeddings.py` puts Hypothesis over field
permutations against the first half and asserts the second field by field.

## What is in the text, and one thing deliberately left out

In: the clinical core (injury type, cause, body part, ICD-10 and its
description, severity score), how disabling it is and how long it is expected
to take (disability, recovery window, surgery), where it happened (the
employer's sector — a lifting injury at an aerospace plant and one at a tyre
plant are different claims), where it has got to (stage, status) and how it
ended for the worker (return-to-work status and the two RTW dates). Then every
secondary injury, because a claim with a primary shoulder strain and a
secondary lumbar strain is not similar to one with a shoulder strain alone.

**Out: the risk band.** The obvious reading of "severity band" is
`services/derivations`' `risk`, and it was rejected. The band is a *rule
document's* answer — `derivation_thresholds`, versioned, effective-dated, AD-8
— so seeding a new version of that document would change what this function
composes for every claim in the portfolio, with nothing marking a single row
stale and no edit anywhere to attribute it to. The whole staleness contract
would be quietly wrong until the next unrelated edit. The raw
`severity_score` is the input the band is computed *from*, it is a column a
handler edits through an audited command that *does* mark the row stale, and it
carries strictly more information than the band it produces. (It is also why
this module is a pure function of its three arguments and needs no session: a
composer that had to load a rule document could not be a Hypothesis property.)

**Out: money.** No reserve, no paid figures, no comp rate. Two claims are not
similar because they cost the same, and `deferred-work.md` records the
reasoning for `update_comp_rate_override` staying unwired from mark-stale on
exactly this basis.

**Out: names.** No worker name, no handler name, no employer name — the sector
is the employer fact that bears on similarity. Fewer PHI identifiers in a
derived artefact is worth having for its own sake, and none of them would help
a nearest-neighbour search.

## Labels in the text at all?

Yes — `Injury: Strain` rather than a bare `Strain`. Embedding models are
sensitive to the framing of a field, and an unlabelled concatenation of a dozen
values reads to the model as one run-on noun phrase in which `Aerospace` and
`Shoulder` carry the same weight. The labels are fixed English strings that
never vary per claim, so they cost nothing in determinism.
"""

import hashlib
from collections.abc import Iterable, Sequence

from data.models import AdditionalInjury, Claim, Employer

#: What a nullable field composes to when it is null. A literal marker rather
#: than an empty string or a dropped line, because "this claim has no actual
#: return-to-work date" is a *fact about the claim* that ought to make it
#: similar to other claims with no RTW date — and a dropped line would make two
#: claims that differ in that fact compose to the same length of text with the
#: fields after it shifted, which reads to the model as a different structure
#: rather than a different value.
NOT_RECORDED = "not recorded"


def _value(raw: object) -> str:
    """One field, rendered deterministically.

    `StrEnum` members are their values by construction, but `str()` of a
    `date`, an `int` and a `bool` are three different conventions and only one
    of them is stable across Python versions in principle. Booleans in
    particular: `str(True)` is `"True"`, which is fine and permanent and still
    not what should be embedded — `yes`/`no` is what the surrounding English
    reads as.
    """
    if raw is None:
        return NOT_RECORDED
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    return str(raw)


def compose_claim_text(
    claim: Claim,
    employer: Employer,
    additional_injuries: Iterable[AdditionalInjury],
) -> str:
    """The embeddable summary of one claim. Pure, deterministic, no session.

    Takes its three inputs as arguments rather than loading them, so that the
    determinism property is a property of *this function* and can be tested
    without a database — and so that the refresh command can compose a batch of
    twenty-five summaries from two queries rather than fifty.

    **Additional injuries are sorted here**, not trusted from the caller.
    `data/repositories/embeddings.py` also orders them by id, deliberately: the
    composer must not depend on its caller for the one property it exists to
    guarantee, and the repository must not hand out rows in whatever order the
    planner chose. Either alone would be enough; both is what makes a
    regression in one of them a failing test rather than a portfolio-wide
    re-embed nobody notices.
    """
    lines = [
        f"Injury: {_value(claim.injury_type)}",
        f"Cause: {_value(claim.cause)}",
        f"Body part: {_value(claim.body_part)}",
        f"Diagnosis: {_value(claim.icd)} {_value(claim.icd_desc)}",
        f"Severity score: {_value(claim.severity_score)}",
        f"Disability: {_value(claim.disability)}",
        f"Recovery window: {_value(claim.recovery)}",
        f"Surgery required: {_value(claim.surgery_required)}",
        f"Employer sector: {_value(employer.sector)}",
        f"Stage: {_value(claim.stage)}",
        f"Status: {_value(claim.status)}",
        f"Return to work: {_value(claim.return_status)}",
        f"Return to work recommended: {_value(claim.rtw_rec)}",
        f"Return to work actual: {_value(claim.actual_rtw)}",
    ]

    secondary = sorted(additional_injuries, key=lambda injury: injury.id)
    if secondary:
        lines.extend(
            f"Additional injury: {_value(injury.body_part)} — {_value(injury.injury_type)} "
            f"(severity {_value(injury.severity_score)})"
            for injury in secondary
        )
    else:
        # An explicit line rather than nothing, for `NOT_RECORDED`'s reason: "no
        # secondary injuries" is a fact that should make two such claims
        # similar, and the seed ships `additional_injury` empty, so this is the
        # branch every claim in the portfolio takes today.
        lines.append("Additional injury: none recorded")

    # A trailing newline is deliberately absent. It would be invisible in every
    # diff and would change every hash the day somebody removed it.
    return "\n".join(lines)


def text_hash(text: str) -> str:
    """sha256, hex. The value stored in `claim_embedding.source_text_hash`.

    A cryptographic digest rather than Python's `hash()`, which is salted per
    process and would therefore compare unequal across two api workers for
    identical text — a hash that marks every claim stale on every deploy.

    sha256 rather than a shorter digest not because collisions are a threat
    model here (there is no adversary choosing claim summaries) but because it
    is the digest this codebase has no reason to have two of, and a 64-character
    text column costs nothing at a hundred rows.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compose_and_hash(
    claim: Claim,
    employer: Employer,
    additional_injuries: Sequence[AdditionalInjury],
) -> tuple[str, str]:
    """`(text, hash)` in one call — the pairing every caller actually wants.

    Exists so that no caller can compose one text and hash a different one,
    which is the mistake that would store a `source_text_hash` describing
    something other than the vector beside it.
    """
    text = compose_claim_text(claim, employer, additional_injuries)
    return text, text_hash(text)
