"""Stdlib loggers (uvicorn, alembic, …) must render through the JSON pipeline (AD-11).

Story 8.2 adds the pipeline's other AD-11 job to this file: `_block_
unallowlisted`, the processor that blanks the value of any keyword
`LOG_KEY_ALLOWLIST` does not name. It is asserted here rather than in
`test_log_phi_lint.py` because the two are different claims about different
things — that file reads the source and this one reads the bytes the process
wrote, which is the only place the processor's behaviour is observable at all.
"""

import json
import logging

import pytest
import structlog

from logging_config import BLOCKED_VALUE, configure_logging


def _root_format(record: logging.LogRecord) -> str:
    handler = logging.getLogger().handlers[0]
    formatter = handler.formatter
    assert formatter is not None
    return formatter.format(record)


def test_stdlib_records_render_as_json() -> None:
    configure_logging("INFO")
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Application startup complete.",
        args=(),
        exc_info=None,
    )
    data = json.loads(_root_format(record))
    assert data["event"] == "Application startup complete."
    assert data["logger"] == "uvicorn.error"
    assert data["level"] == "info"
    assert "timestamp" in data


def test_uvicorn_loggers_propagate_without_own_handlers() -> None:
    configure_logging("INFO")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert lg.handlers == []
        assert lg.propagate is True


def test_structlog_records_are_json_on_stderr(capfd: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("test").info("app.start", env="dev")
    err = capfd.readouterr().err.strip().splitlines()[-1]
    data = json.loads(err)
    assert data["event"] == "app.start"
    assert data["env"] == "dev"
    assert data["level"] == "info"


def _last_line(capfd: pytest.CaptureFixture[str]) -> dict[str, object]:
    err = capfd.readouterr().err.strip().splitlines()[-1]
    parsed: dict[str, object] = json.loads(err)
    return parsed


def test_a_key_off_the_allowlist_has_its_value_blocked(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The Story 8.2 backstop, through the real pipeline (AD-11).

    `scripts/lint_log_phi.py` is the gate and would have refused this call at
    build time; this is what happens when one reaches the pipeline anyway — a
    keyword computed at run time, or a value arriving through the `**` unpack
    the lint exempts.

    **The key survives and the value does not**, which is the decision worth
    asserting rather than the blocking. Dropping the key would render a leaking
    call identically to a clean one, deleting the operational signal and the
    evidence in one move; keeping it means an operator who reads
    `<blocked:not-on-log-allowlist>` can grep the tree for the event name and
    find the call site. `event` and `level` are asserted alongside because a
    line stripped of its subject is a line nobody can act on.
    """
    configure_logging("INFO")
    structlog.get_logger("test").info("note.created", claim_id="WC-20017", body="lumbar strain")
    data = _last_line(capfd)

    assert data["event"] == "note.created"
    assert data["level"] == "info"
    assert data["claim_id"] == "WC-20017"
    assert data["body"] == BLOCKED_VALUE
    assert "lumbar strain" not in json.dumps(data)


def test_allowlisted_keys_and_the_event_name_survive_the_processor(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """…and the backstop is not simply blanking everything.

    The negative control the test above needs: a processor with an inverted
    condition, an empty allowlist or a typo in `_META_KEYS` would satisfy every
    assertion about `body` while destroying every log line in the build, and
    nothing would report it — a blocked value is still valid JSON on stderr.

    The keys chosen are the three shapes the allowlist's comment groups: an
    identifier, a closed vocabulary and a count. Their *values* are asserted
    too, because "the key is present" is also true of a blocked one.
    """
    configure_logging("INFO")
    structlog.get_logger("test").info(
        "audit.recorded",
        claim_id="WC-20017",
        action="update_claim_fields",
        fields=["injury_type"],
        rows_deleted=3,
    )
    data = _last_line(capfd)

    assert data["event"] == "audit.recorded"
    assert data["claim_id"] == "WC-20017"
    assert data["action"] == "update_claim_fields"
    assert data["fields"] == ["injury_type"]
    assert data["rows_deleted"] == 3
    assert BLOCKED_VALUE not in json.dumps(data)
    # The pipeline's own keys are not collateral damage: `_META_KEYS` is what
    # keeps `level`, `logger` and `timestamp` out of the allowlist's reach, and
    # they are added by processors that run *before* the block.
    assert data["level"] == "info"
    assert data["logger"] == "test"
    assert "timestamp" in data


def test_a_foreign_stdlib_record_still_renders_its_message(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The `foreign_pre_chain` half, which is where a mistake would be invisible.

    `_block_unallowlisted` is in `_shared_processors`, so it runs over uvicorn,
    alembic and sqlalchemy records as well as structlog ones — deliberately: a
    stdlib record is a leak surface too. But those records arrive carrying
    `ProcessorFormatter`'s own markers rather than anybody's keywords, and a
    `_META_KEYS` that had missed one of them would blank the machinery the
    formatter then tries to use. The symptom would be every uvicorn line
    turning to noise, in prod, with no test failing.
    """
    configure_logging("INFO")
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    data = _last_line(capfd)

    assert data["event"] == "Application startup complete."
    assert data["logger"] == "uvicorn.error"
    assert BLOCKED_VALUE not in json.dumps(data)
