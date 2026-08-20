"""The streaming chat model the copilot graph runs on — the build's **fourth**
reader of `OLLAMA_BASE_URL` (Story 6.3, AD-5).

`config.py` names it, `services/rag/client.py` embeds with it, `agents/client.py`
generates insights with it, and this module streams chat with it. Four files,
two endpoints, one URL each, and the exact-set-equality assertion in
`tests/test_ai_insights.py` is what keeps the list honest.

## Why a fourth reader is correct rather than a violation

`agents/client.py` states that its `ChatClient` Protocol is **structured-only by
design**: there is deliberately no `complete()` returning a string, because
insight generation persists validated structures rather than prose blobs, and a
free-text method would be the shape a later story reached for in a hurry. It
then names this story: "Story 6.3's streaming chat has genuinely different needs
and will own its own surface rather than widening this one."

This is that surface. The needs really are different — token streaming, tool
binding, a per-run wall-clock bound, `num_predict`, and a model object the
`create_agent` harness can be handed rather than a method a caller awaits — and
none of them fits behind `structured()`. Widening the Protocol would have made
one interface serve two shapes and would have put a streaming code path inside
the module whose whole claim is that it makes exactly one kind of request.

AD-5's real invariant is that **every reader lives in `agents/` or
`services/rag/`, and every one of them talks to the internal Ollama and nothing
else**. That survives a fourth file; it would not survive a cloud fallback, and
there is none — not behind a flag, not as a degraded mode. When the model server
is unreachable the run emits one `error` frame carrying a problem document and
stops. The prototype's `offlineAnswer` has no successor (AD-14).

## One model per process, built in `lifespan`

`agents/client.py` builds a client *per refresh run* and argues that a
long-lived one on a module global would outlive the event loop it was created
on. This one is built once, in `api/app.py`'s `lifespan`, and the difference is
that `lifespan` is the one place in the process with a live loop for the
process's whole life — which is precisely the shape that makes a shared client
safe rather than the shape that makes it dangerous.

That also discharges, honestly and by half, the deferral the Story 6.1 review
recorded: "the embedding client builds a fresh `httpx.AsyncClient` per call",
with this story named as the point to introduce a pooled client because "the
same base URL is hit per token". Chat traffic is now pooled by construction, and
chat traffic is the half this story creates. The embedding client's per-call
construction is unchanged and stays deferred: it is not on this story's hot path
and moving it would be scope this story cannot justify.

## AD-11

Nothing from this module reaches a log line — not a prompt, not a token, not a
completion, not a count of either. `api/routers/copilot.py` logs thread ids,
run ids, event names and durations, and that is the whole of what a copilot run
is allowed to say about itself.
"""

from langchain_ollama import ChatOllama

from config import Settings

#: Decoding temperature for chat. Zero, for `agents/client.py`'s reason and one
#: more.
#:
#: `CHAT_TEMPERATURE` there is a correctness knob because AD-15's done-gate must
#: produce the same result on two runs of one commit. The same applies here —
#: `e2e/stories/6-3-…spec.ts` asserts stream *structure*, but a sampled model
#: would still make "the turn completed" a probabilistic assertion — and on top
#: of it, a grounded-chat node whose job is to narrate figures a service
#: computed has nothing to gain from sampling. Creativity is not the product.
CHAT_TEMPERATURE = 0.0


def copilot_chat_model(settings: Settings) -> ChatOllama:
    """The streaming chat model, from configuration. The one construction site.

    Takes `Settings` rather than reading `get_settings()`, `chat_client`'s rule:
    `config.py` is the only module in the server allowed to touch the
    environment, and an agent that reached for the global would be a second one.

    **`num_predict` is the run bound in the dimension a clock does not cover.**
    A wall-clock timeout stops a model that is slow; it does not stop one that
    is fast and verbose, and a four-thousand-token answer about a claim is a
    misunderstood question rather than a thorough one.
    `copilot_max_output_tokens` is that ceiling. Story 6.6 surfaces hitting it
    as `ai_limit`; this story's job is that no run is ever unbounded in the
    meantime.

    **The timeout here is per HTTP request, not per run.** A `create_agent` loop
    may make several model calls, so `chat_request_timeout_seconds` bounds each
    hop and `copilot_run_timeout_seconds` — enforced by the runs endpoint around
    the whole stream — bounds the loop. Two bounds because there are two things
    that can hang, and one of them is the loop itself.

    The trailing slash is stripped for `OllamaChatClient`'s reason: the value
    comes from an operator's env file, and a URL that works depending on how it
    was typed is a support question waiting to happen.
    """
    return ChatOllama(
        base_url=settings.ollama_base_url.rstrip("/"),
        model=settings.chat_model,
        temperature=CHAT_TEMPERATURE,
        num_predict=settings.copilot_max_output_tokens,
        async_client_kwargs={"timeout": settings.chat_request_timeout_seconds},
    )


__all__ = ["CHAT_TEMPERATURE", "copilot_chat_model"]
