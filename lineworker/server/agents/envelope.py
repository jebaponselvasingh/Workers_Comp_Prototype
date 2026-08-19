"""`{ok, data, display}` — the shape every deterministic figure arrives in.

AD-13's envelope, and the reason it exists before there is a tool registry to
put it in. Story 6.3 registers these wrappers as real tools with typed argument
schemas and a declared read/write kind; this story needs the *result* shape a
story earlier, because the whole of AD-2 rests on it: a narrative may quote
figures, and the only figures it may quote are ones a deterministic service
produced. An envelope makes "which numbers is this insight entitled to?" a
question with a data structure behind it rather than a convention.

## Why `display` is a separate field and not a formatting call at the edge

`display` holds the strings a reader sees — `"$48,000"`, `"115%"`, `"3 of 6"` —
produced once, by the service layer's own formatter
(`services/financials.format_dollars`), and carried alongside the raw value all
the way into the JSONB the card renders. Nothing downstream re-formats.

That is a rule with two failures behind it, one on each side of the wire.
Server-side, the prototype's copilot invented its own money strings and they
disagreed with the card beside them. Client-side, a browser that formatted
cents itself would be a second rounding of one figure — `web/src/lib/money.ts`
and `format_dollars` are already documented as twins for exactly that reason,
and an insight card is the worst place to discover they had drifted, because a
sentence and a table row would be quoting the same claim differently.

**This is the server's first money *display* envelope**, and it is scoped to
insight content on purpose. Every other payload in the build sends integer
cents and lets the browser format, which is right for a table whose columns the
reader compares. It is wrong here: the string is inside a narrative that a
model wrote around it, so it has to be fixed before generation, not after.

## `ok` and `error`, and why a failed gather is not an exception

A tool that could not answer returns `ok=False` with a short, content-free
reason rather than raising. The generator can then decide per kind — a fraud
narrative whose derivations failed to load is a kind that does not persist,
while the other three still do — which is the same per-kind independence
`services/rag/insights.py` arranges its savepoints around. Story 6.3's registry
needs identical behaviour for a different reason: a stack trace in a chat
transcript is both a bad answer and an information leak.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolResult[T]:
    """One deterministic gather: whether it worked, what it returned, how it reads.

    Generic in the payload so each wrapper keeps its service's own return type
    — `ReserveCheck`, `ClaimActions`, a sequence of `SimilarClaim` — rather than
    flattening everything to a dictionary at the boundary. The AD-2 equality
    test compares `data` against a second call to the same service, and it can
    only do that while `data` *is* what the service returned.

    `display` is keyed by field name (`"reserveCents" -> "$48,000"`) so a prompt
    can interpolate it and a content model can copy it without either of them
    holding a format string. Empty for a wrapper whose payload carries no money
    or date, which is honest rather than an omission: `next_actions` returns
    labels and urgencies, and there is nothing to format.

    `error` is a short sentence for an operator, never a vendor message and
    never a stack trace (AD-11). It is `None` exactly when `ok` is true.

    `unavailable` says the failure was a model server that did not answer,
    rather than a claim that is not there. It rides on the envelope instead of
    being recovered from `error`'s wording, because the caller turns it into a
    status code — a route that decided between 503 and 200 by matching a
    sentence would be one reworded string away from lying to a handler.
    """

    ok: bool
    data: T | None
    display: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None
    unavailable: bool = False

    @classmethod
    def succeeded(cls, data: T, *, display: Mapping[str, str] | None = None) -> "ToolResult[T]":
        return cls(ok=True, data=data, display=dict(display or {}))

    @classmethod
    def failed(cls, error: str, *, unavailable: bool = False) -> "ToolResult[T]":
        return cls(ok=False, data=None, display={}, error=error, unavailable=unavailable)

    def require(self) -> T:
        """The payload, or `ToolUnavailable` naming the wrapper that declined.

        Callers that cannot proceed without a figure say so with one call
        rather than with an `if result.data is None` whose failure branch is a
        second copy of the same sentence — and, more to the point, rather than
        with an `assert` that a reader could mistake for a type narrowing when
        it is in fact the AD-2 boundary being enforced.
        """
        if not self.ok or self.data is None:
            raise ToolUnavailable(
                self.error or "the deterministic source did not answer",
                unavailable=self.unavailable,
            )
        return self.data


class ToolUnavailable(RuntimeError):
    """A deterministic figure a kind's schema requires could not be gathered.

    Raised by `ToolResult.require` and caught in `agents/insights.py`, where it
    becomes an `InsightGenerationError` for that kind. It is deliberately *not*
    a model failure: the model was never asked, because asking it to narrate
    figures nobody could produce is exactly how a narrative acquires an invented
    one (AD-2).

    `unavailable` carries the envelope's flag through, so a gather that failed
    because the model server is down produces the same 503 an unanswerable
    completion does. The similar-case wrapper is the one that can raise it —
    embedding the query claim is a live call — and a story that added a second
    live gather would want the same treatment.
    """

    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable
