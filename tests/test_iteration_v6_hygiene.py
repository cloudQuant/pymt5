"""Regression tests for iteration v6 encoding, logging, and public hygiene."""

import logging
import sys
from pathlib import Path

import pytest

from pymt5 import _logging
from pymt5.helpers import decode_utf16le, encode_utf16le

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, ""),
        (1, ""),
        (2, "A"),
        (3, "A"),
        (4, "A"),
        (5, "A"),
        (6, "A😀"),
        (7, "A😀"),
        (8, "A😀B"),
        (9, "A😀B"),
    ],
)
def test_utf16le_fixed_buffers_never_split_surrogates(size: int, expected: str) -> None:
    encoded = encode_utf16le("A😀B", size)

    assert len(encoded) == size
    if size % 2:
        assert encoded[-1] == 0
    assert decode_utf16le(encoded) == expected


def test_utf16le_bmp_boundaries_and_null_termination() -> None:
    text = "\x00"
    assert encode_utf16le(text, 1) == b"\x00"

    encoded = encode_utf16le("A", 5)
    assert encoded == b"A\x00\x00\x00\x00"
    assert decode_utf16le(encoded + b"B\x00") == "A"

    for text in ("", "\u007f", "\u0080", "߿", "ࠀ", "￿"):
        assert decode_utf16le(encode_utf16le(text, 8)) == text


def test_utf16le_non_bmp_roundtrip_and_odd_decode_size() -> None:
    encoded = "MT5😀🚀".encode("utf-16-le")
    assert decode_utf16le(encoded + b"\x00") == "MT5😀🚀"
    assert decode_utf16le(encoded + b"\x00\x00ignored") == "MT5😀🚀"
    assert decode_utf16le(b"A\x00B") == "A"


@pytest.mark.parametrize("legacy", ["\ud800", "\udc00", "A\ud800B"])
def test_utf16le_preserves_unpaired_legacy_surrogates(legacy: str) -> None:
    assert decode_utf16le(encode_utf16le(legacy, 16)) == legacy


def test_stdlib_log_level_filters_events(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(_logging, "_LOG_LEVEL", "INFO")
    monkeypatch.setitem(sys.modules, "structlog", None)
    logger = _logging.get_logger("tests.iteration_v6.stdlib")

    caplog.set_level(logging.DEBUG)
    logger.debug("hidden")
    logger.info("visible")

    assert [record.message for record in caplog.records] == ["visible"]


def test_structlog_log_level_filters_without_global_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FilteringLogger:
        def __init__(self, level: int, name: str) -> None:
            self.level = level
            self.name = name

        def debug(self, event: str) -> None:
            if self.level <= logging.DEBUG:
                print(event)

        def info(self, event: str) -> None:
            if self.level <= logging.INFO:
                print(event)

    class FakeStructlog:
        @staticmethod
        def make_filtering_bound_logger(level: int) -> int:
            return level

        @staticmethod
        def wrap_logger(wrapped: object, wrapper_class: int, logger_factory_args: tuple[str]) -> FilteringLogger:
            assert wrapped is None
            return FilteringLogger(wrapper_class, logger_factory_args[0])

        @staticmethod
        def get_logger(name: str) -> None:
            pytest.fail("configured filtering must not use the global get_logger path")

    monkeypatch.setitem(sys.modules, "structlog", FakeStructlog)
    monkeypatch.setattr(_logging, "_LOG_LEVEL", "INFO")
    logger = _logging.get_logger("tests.iteration_v6.structlog")

    logger.debug("hidden")
    logger.info("visible")

    output = capsys.readouterr()
    assert "visible" in output.out
    assert "hidden" not in output.out


@pytest.mark.parametrize("path", [ROOT / "Makefile", ROOT / ".github/workflows/ci.yml"])
def test_mypy_strict_optional_is_not_disabled(path: Path) -> None:
    assert "--no-strict-optional" not in path.read_text(encoding="utf-8")


def test_iteration_plans_are_not_in_public_toctree() -> None:
    index = (ROOT / "docs/index.rst").read_text(encoding="utf-8")
    toctree = index.split(".. toctree::", 1)[1].split("Features", 1)[0]
    assert "iteration_plan" not in toctree
    config = (ROOT / "docs/conf.py").read_text(encoding="utf-8")
    assert '"iteration_plan*.rst"' in config
