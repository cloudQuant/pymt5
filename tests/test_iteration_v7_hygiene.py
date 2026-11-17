"""Regression tests for iteration 07 baseline logging and coverage isolation."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pymt5 import _logging


def test_unconfigured_structlog_bridges_stdlib_and_defaults_to_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S1-11 / 25.9: unconfigured structlog must route to stdlib logging at WARNING default."""
    bound_calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class FakeBoundLogger:
        def __init__(self, wrapped_logger: logging.Logger) -> None:
            self._wrapped = wrapped_logger

        def debug(self, msg: str, *args: object, **kwargs: object) -> None:
            bound_calls.append(("debug", args, kwargs))
            self._wrapped.debug(msg, *args, **kwargs)

        def info(self, msg: str, *args: object, **kwargs: object) -> None:
            bound_calls.append(("info", args, kwargs))
            self._wrapped.info(msg, *args, **kwargs)

        def warning(self, msg: str, *args: object, **kwargs: object) -> None:
            bound_calls.append(("warning", args, kwargs))
            self._wrapped.warning(msg, *args, **kwargs)

    fake_stdlib = types.SimpleNamespace(
        BoundLogger=FakeBoundLogger,
        PositionalArgumentsFormatter=lambda: "pos",
        render_to_log_kwargs="render",
    )
    fake_structlog = types.SimpleNamespace(
        is_configured=lambda: False,
        stdlib=fake_stdlib,
        wrap_logger=lambda wrapped, **_kw: FakeBoundLogger(wrapped),
        get_logger=lambda _name: pytest.fail("unconfigured structlog must not call global get_logger"),
        make_filtering_bound_logger=lambda level: level,
    )

    monkeypatch.setitem(sys.modules, "structlog", fake_structlog)
    monkeypatch.setattr(_logging, "_LOG_LEVEL", None)

    logger_name = "tests.iteration_v7.unconfigured_structlog"
    stdlib_logger = logging.getLogger(logger_name)
    stdlib_logger.setLevel(logging.NOTSET)

    logger = _logging.get_logger(logger_name)
    assert stdlib_logger.getEffectiveLevel() == logging.WARNING

    with caplog.at_level(logging.WARNING, logger=logger_name):
        logger.debug("hidden debug")
        logger.info("hidden info")
        logger.warning("visible warning: %s", "ok")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert [r.message for r in caplog.records if r.name == logger_name] == ["visible warning: ok"]
    assert any(level == "warning" for level, _, _ in bound_calls)


def test_real_structlog_if_installed_captures_in_caplog(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When real structlog is installed and unconfigured, stdlib caplog must still work."""
    if importlib.util.find_spec("structlog") is None:
        return

    import structlog  # type: ignore[import-untyped]

    monkeypatch.setattr(structlog, "is_configured", lambda: False)
    monkeypatch.setattr(_logging, "_LOG_LEVEL", None)
    logger_name = "tests.iteration_v7.real_structlog"
    logging.getLogger(logger_name).setLevel(logging.NOTSET)

    logger = _logging.get_logger(logger_name)
    with caplog.at_level(logging.WARNING, logger=logger_name):
        logger.debug("debug hidden")
        logger.info("info hidden")
        logger.warning("real warning: %s", 42)

    captured = capsys.readouterr()
    assert "debug hidden" not in captured.out
    assert "info hidden" not in captured.out
    assert [r.message for r in caplog.records if r.name == logger_name] == ["real warning: 42"]


def test_release_tag_subprocess_strips_coverage_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """B-01 / 29.7: run_guard must strip COV_CORE_* and COVERAGE_* env vars from subprocesses."""
    from tests import test_release_tag

    captured_envs: list[dict[str, str]] = []

    def fake_run(cmd, cwd=None, env=None, capture_output=None, text=None, check=None):
        captured_envs.append(dict(env or {}))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("COV_CORE_SOURCE", "pymt5")
    monkeypatch.setenv("COVERAGE_FILE", "/tmp/.coverage")
    monkeypatch.setattr(test_release_tag.subprocess, "run", fake_run)

    test_release_tag.run_guard(tmp_path, "refs/tags/v1.0.1")

    assert len(captured_envs) == 1
    assert "COV_CORE_SOURCE" not in captured_envs[0]
    assert "COVERAGE_FILE" not in captured_envs[0]
    assert captured_envs[0]["GITHUB_REF"] == "refs/tags/v1.0.1"
