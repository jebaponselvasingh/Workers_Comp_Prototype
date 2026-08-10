"""Stdlib loggers (uvicorn, alembic, …) must render through the JSON pipeline (AD-11)."""

import json
import logging

import pytest
import structlog

from logging_config import configure_logging


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
