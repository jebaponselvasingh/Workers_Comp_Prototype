"""Logging: one JSON pipeline for structlog AND stdlib loggers.

AD-11 PHI ban applies from the first line: log entries carry identifiers
and event names — never claim field values, prompt bodies, or model output.

Everything that logs — structlog calls, uvicorn access/error, alembic,
sqlalchemy — renders through the same ProcessorFormatter on the root
handler, so no plain-text side channel exists for PHI to leak into.

Story 8.2 turns that sentence into a mechanism. `LOG_KEY_ALLOWLIST` below is
the closed vocabulary of keyword names a log call may pass, and it has exactly
one definition: `scripts/lint_log_phi.py` imports it and fails the build on a
call that steps outside it, and `_block_unallowlisted` — the last processor in
the shared chain — blanks the value of anything that reaches the pipeline
anyway. Two lists would be two statements of one rule, and the weaker one is
always the one somebody ends up relying on.
"""

import logging
from typing import Final

import structlog

#: Every keyword name a log call in this build may pass, and nothing else.
#:
#: **The gate is `scripts/lint_log_phi.py`, not this constant.** A frozenset
#: cannot stop anybody writing `log.info("x", body=note.text)`; what it does is
#: give the lint something to compare against and give `_block_unallowlisted`
#: something to enforce at run time. Adding a name here is therefore a decision
#: about what the operational vocabulary *is*, and the question to ask of a
#: candidate is AD-11's: could the value be a claim field, a prompt body or a
#: model's answer? If it could, the answer is to log an id and read the value
#: from `audit_event`, which the Story 8.1 cascade can redact — a log line
#: cannot be redacted by anything this system owns.
#:
#: `tests/test_log_phi_lint.py::test_every_allowlisted_key_is_still_used_by_
#: some_log_call` holds the list to the tree in the other direction too: an
#: entry nothing emits is a permission nobody is exercising, and the next
#: reader takes it as precedent.
LOG_KEY_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # --- Identifiers -------------------------------------------------
        # Surrogate and business keys. Every one of these names a row, a
        # conversation or a request; none of them carries what is *in* it.
        # `user_id` reaches the pipeline only through `services/audit/purge
        # .py::_log_run(**subject)`, which is the tree's one `**` unpack into a
        # log call and the one entry in the lint's STAR_KWARG_EXEMPTIONS.
        "actor_id",
        "claim_id",
        "conversation_seq",
        "entity_id",
        "path",
        "prompt_key",
        "prompt_version",
        "thread_id",
        "tool_call_id",
        "user_id",
        # --- Enums, constants and closed vocabularies --------------------
        # Names this codebase chose: an AD-4 action, a table, a column, a
        # registered tool, a job, an HTTP method, a model tag, a US state
        # code, a short refusal sentence from a fixed set. A value here is
        # drawn from the source, never from a claim.
        "action",
        "by_entity",
        "entity",
        "env",
        "fields",
        "half",
        "job",
        "kind",
        "method",
        "model",
        "reason",
        "role",
        "scheduler",
        "state",
        "store",
        "tool",
        # --- Counts ------------------------------------------------------
        # Cardinalities and nothing else. A count of rows says how much
        # happened; it cannot say what any of it was.
        "answered",
        "attempt",
        "attempts",
        "blobs_deleted",
        "blobs_failed",
        "chunks",
        "chunks_failed",
        "claims",
        "expected",
        "failed",
        "inputs",
        "jobs",
        "max_attempts",
        "max_tool_calls",
        "returned",
        "rows",
        "rows_deleted",
        "rows_failed",
        "rows_paid",
        "rows_past_configured_floor",
        "rows_redacted",
        "threads_deleted",
        "threads_discarded",
        "threads_failed",
        "vectors",
        "written",
        # --- Durations and configured windows ----------------------------
        # Numbers that came from the clock or from `Settings`, so they are
        # deployment facts rather than anybody's data.
        "configured_years",
        "duration_ms",
        "retention_days",
        # --- Exception-class names and availability flags ----------------
        # `error=type(exc).__name__` is the house spelling and the reason this
        # group exists: an exception *message* can quote a value the statement
        # was carrying, so the class name is logged and the message is not.
        # `errors` is the plural of the same thing (a sorted set of class
        # names); `model_unavailable` is a bool.
        "error",
        "errors",
        "model_unavailable",
    }
)

#: Keys the pipeline itself puts in the event dict, which no allowlist governs.
#:
#: Separate from `LOG_KEY_ALLOWLIST` because they make a different claim: these
#: are not permissions anybody was granted, they are the shape of a rendered
#: line. `event`, `level`, `logger` and `timestamp` are added by the processors
#: above `_block_unallowlisted` in the chain; `_record`/`_from_structlog` are
#: `ProcessorFormatter`'s markers on a foreign stdlib record and are stripped
#: again by `remove_processors_meta` at render time; the exception trio is
#: consumed by `format_exc_info` and `StackInfoRenderer`.
#:
#: **`event` being here is the point of Design Note 5.** The event name is the
#: one thing a blocked line must keep, or an operator meets a JSON object with
#: no subject and cannot tell which call site leaked.
_META_KEYS: Final[frozenset[str]] = frozenset(
    {
        "_from_structlog",
        "_record",
        "event",
        "exc_info",
        "exception",
        "level",
        "logger",
        "logger_name",
        "positional_args",
        "stack",
        "stack_info",
        "timestamp",
    }
)

#: What a non-allowlisted value is replaced with. Distinctive on purpose: it is
#: what somebody greps for after reading one, and what
#: `tests/test_logging.py` asserts on.
BLOCKED_VALUE: Final[str] = "<blocked:not-on-log-allowlist>"


def _block_unallowlisted(
    logger: structlog.typing.WrappedLogger,
    method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Blank the value of any keyword the allowlist does not name (AD-11).

    **Depth, not the gate.** `scripts/lint_log_phi.py` is what actually stops a
    leaking call, because it fails the build before the code runs and names the
    file, the line and the keyword. This runs in the request path and catches
    the two things a static lint cannot see: a keyword name computed at run
    time, and a value that arrives through the `**` unpack the lint exempts.

    **The value is replaced and the key is not dropped**, which is Design Note
    5 and the only decision in this function. Dropping the key would make a
    leaking call render identically to a clean one — deleting the operational
    signal and the evidence in the same move — so the event name, the line and
    the offending key all survive and only the content goes. A backstop whose
    firing is invisible is a backstop nobody ever fixes.

    **It never raises**, which is why the iteration is over `list(event_dict)`
    rather than over the mapping being mutated and why there is no branch that
    can fail. A log call is not permitted to break the request it is
    describing; a line that is missing a value is an incident, and a 500 from
    the logging pipeline is an outage.
    """
    for key in list(event_dict):
        if key in _META_KEYS or key in LOG_KEY_ALLOWLIST:
            continue
        event_dict[key] = BLOCKED_VALUE
    return event_dict


_shared_processors: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    # Last, so that everything the chain above adds (`level`, `logger`,
    # `timestamp`) is already present and can be recognised as meta rather than
    # blanked as an unrecognised keyword. It is in the *shared* list rather
    # than in `configure_logging`'s structlog-only chain because a stdlib
    # record is a leak surface too: `foreign_pre_chain` is the only place a
    # uvicorn or sqlalchemy record passes through.
    _block_unallowlisted,
]


def configure_logging(log_level: str) -> None:
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            *_shared_processors,
            structlog.processors.StackInfoRenderer(),
            # Hand off to the root handler's ProcessorFormatter below.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs for records from stdlib loggers
        # (uvicorn, alembic, sqlalchemy, …) so they get the same shape.
        foreign_pre_chain=_shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own (colorized plain-text) handlers before the
    # app factory runs; strip them and let records propagate to root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
