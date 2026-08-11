"""The ZEN tier's loader and evaluator — AD-8's JDM half, wired once.

AD-8 divides every rule in two: a versioned JDM document owns the
*parameters*, typed Python owns the *formula*. This module is the seam
between them, and it is deliberately the only place in the server that
imports `zen`.

**Why a document is evaluated once per request, not once per claim.**
Because it returns a parameter block, not a verdict. Story 2.1's scorer runs
over a hundred rows; asking ZEN a hundred questions would put a rules engine
on the hot path to answer the same thirteen constants each time, and would
make the scorer un-Hypothesis-testable (it would need an engine, a database
and a document to check that two weights add up). Loading the block once and
handing it to pure Python keeps the formula testable, keeps ZEN cold, and
makes "which document version produced this ordering?" a fact the response
can carry — see the queue cursor.

**Why the compile and the evaluation are cached but the load is not.** Both
halves of the ZEN work depend only on `(key, version)` and the context, and
`(key, version)` is immutable by construction: rule documents are inserted
by migration and the app role cannot update them (0008 grants SELECT only).
Caching only the compile left the cheaper-sounding half — a `json.dumps` of
the whole document plus a ZEN run — on every single request, including
`/stats/topbar`, which the shell polls. Neither is on the hot path now. The
*load* is a read of a handful of rows and is deliberately repeated, because
it is what makes an effective date mean anything — a version whose date
arrives tomorrow must be picked up tomorrow without a restart.

**Why a missing key raises.** A rule document that is absent — a migration
half-applied, a key renamed on one side only — has no safe default. Falling
back to zeros would score every claim identically and sort the queue by
claim id, quietly, with no error anywhere: a handler would work their
caseload in numerical order believing it was risk order. Refusing is the
only honest answer.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

import sqlalchemy as sa
import zen
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import RuleDocument


class RuleDocumentMissing(LookupError):
    """No version of a rule document is effective for the requested date."""


class RuleEvaluationFailed(RuntimeError):
    """The document exists but ZEN could not compile or evaluate it.

    Distinct from `RuleDocumentMissing` because the operator response
    differs: a missing key is a migration that did not run, a failed
    evaluation is a document that should never have been committed.
    """


@dataclass(frozen=True)
class LoadedDocument:
    """One resolved rule document: which rule, which version, what it says.

    `version` travels with `content` rather than being discarded after the
    lookup because it is the answer to "why is the queue in this order?" —
    the cursor records it, and a reader comparing two orderings needs to
    know whether they were cut from the same rules.
    """

    key: str
    version: int
    content: dict[str, Any]


def utc_today() -> date:
    """The default `as_of` for a rules lookup.

    UTC rather than the host's zone so that the API container, the test
    process and the e2e oracle agree on which day it is — three processes
    that in practice run in three different timezones.
    """
    return datetime.now(UTC).date()


async def load(db: AsyncSession, key: str, as_of: date | None = None) -> LoadedDocument:
    """The highest version of `key` whose effective date has arrived.

    Ordering on `version` rather than on `effective_from`: two versions may
    legitimately share an effective date (a same-day correction), and the
    later-authored one is the one that applies. `(key, version)` is unique,
    so this ordering is total and the answer cannot depend on the plan.
    """
    row = (
        await db.execute(
            sa.select(RuleDocument.version, RuleDocument.content)
            .where(RuleDocument.key == key, RuleDocument.effective_from <= (as_of or utc_today()))
            .order_by(RuleDocument.version.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise RuleDocumentMissing(
            f"no rule document {key!r} effective on {as_of or utc_today()} — "
            "a rules migration has not been applied"
        )
    return LoadedDocument(key=key, version=int(row.version), content=dict(row.content))


@lru_cache(maxsize=32)
def _decision(key: str, version: int, content_json: str) -> Any:
    """Compile a document, once per `(key, version, content)` per process.

    The content is passed as JSON text so the cache key is hashable — which
    also makes it part of the cache identity, and that is the honest
    behaviour to want: a `(key, version)` pair whose content differed
    between two rows would be a corrupted database, and serving the first
    body seen for the rest of the process's life would hide it. In practice
    the content never varies for a pair (0008's unique constraint plus
    SELECT-only grants), so the extra key component costs one comparison.
    """
    try:
        return zen.ZenEngine().create_decision(content_json)
    except Exception as exc:  # pragma: no cover - a committed document compiles
        raise RuleEvaluationFailed(f"{key} v{version} is not a valid JDM document: {exc}") from exc


@lru_cache(maxsize=64)
def _evaluated(key: str, version: int, content_json: str, context_json: str) -> str:
    """Run a compiled document and return its `result` as JSON text.

    Cached beside the compile because the answer is a pure function of the
    same immutable inputs. Story 2.1's documents answer with constants, so
    the top bar and the queue evaluate `(derivation_thresholds, v1, {})`
    over and over; without this, every request paid a `json.dumps` of the
    whole document plus a ZEN run to be told the same five numbers.

    JSON text out, not a dict, for the reason the input is text: the value
    has to be hashable to live in an LRU, and it must not be a mutable
    object a caller could edit inside the cache. `evaluate` parses it back,
    which is one small allocation in place of an engine invocation.

    The context is part of the key rather than assumed empty. Today both
    documents ignore theirs; a later decision-table document (the reserve
    bands, the Path A/B/C classification) will vary it per claim, and an LRU
    keyed only on the document would then answer the first claim's verdict
    for every claim.
    """
    decision = _decision(key, version, content_json)
    try:
        outcome = decision.evaluate(json.loads(context_json))
    except Exception as exc:
        raise RuleEvaluationFailed(f"{key} v{version} failed to evaluate: {exc}") from exc
    result = outcome.get("result")
    if not isinstance(result, dict):
        raise RuleEvaluationFailed(
            f"{key} v{version} produced {type(result).__name__}, not a parameter block"
        )
    return json.dumps(result)


def evaluate(document: LoadedDocument, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a document and return its `result` — the parameter block.

    `context` exists because JDM documents take an input node; Story 2.1's
    two documents ignore theirs (they answer with constants), and a later
    decision-table document — the reserve bands, the Path A/B/C
    classification — will populate it. Callers do not index the result
    directly: `rules/parameters.py` validates it into a typed block first,
    so a renamed key fails in one place with a readable message rather than
    as a `KeyError` deep in a scorer.
    """
    result: dict[str, Any] = json.loads(
        _evaluated(
            document.key,
            document.version,
            json.dumps(document.content, sort_keys=True),
            json.dumps(context or {}, sort_keys=True),
        )
    )
    return result
