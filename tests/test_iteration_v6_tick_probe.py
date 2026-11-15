"""Offline tests for the explicitly enabled, read-only demo tick probe."""

import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pymt5.constants import CMD_TICK_PUSH
from pymt5.exceptions import ProtocolError
from pymt5.transport import CommandResult
from pymt5.types import SymbolInfo
from scripts import demo_tick_probe


class FakeTransport:
    def __init__(self) -> None:
        self.listeners = {}

    def on(self, command, callback) -> None:
        self.listeners[command] = callback


class FakeClient:
    def __init__(self, *, body=None, connect_error=None, unsubscribe_error=None, bars_error=None) -> None:
        self.transport = FakeTransport()
        self.server_build = 6320
        self.body = body
        self.connect = AsyncMock(side_effect=connect_error)
        self.login = AsyncMock()
        self.load_symbols = AsyncMock(return_value={"EURUSD": SymbolInfo("EURUSD", 42, 5)})
        self.unsubscribe_ticks = AsyncMock(side_effect=unsubscribe_error)
        self.close = AsyncMock()
        self.get_rates = AsyncMock(
            side_effect=bars_error,
            return_value=[{"time": 1_700_000_000, "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2}],
        )

    async def subscribe_ticks(self, _ids) -> None:
        if self.body is not None:
            self.transport.listeners[CMD_TICK_PUSH](CommandResult(CMD_TICK_PUSH, 0, self.body))


async def collect(client, *, symbol="EURUSD"):
    return await demo_tick_probe.collect_sample(
        uri="wss://example.invalid/terminal",
        login=123,
        password="private-test-password",
        symbol=symbol,
        seconds=0.001,
        client_factory=lambda **_kwargs: client,
    )


def example_tick(symbol_id=42):
    return struct.pack("<IiIdddqIH", symbol_id, 1_700_000_000, 3, 1.125, 1.25, 0.0, 10, 123, 7)


async def test_no_sample_is_reported_and_connection_closed():
    client = FakeClient()
    report = await collect(client)
    assert report["status"] == "no_sample"
    assert report["frames_seen"] == 0
    assert report["unsubscribe"] == report["close"] == "ok"
    assert "sample" not in report
    client.get_rates.assert_not_awaited()
    client.unsubscribe_ticks.assert_awaited_once_with([42])
    client.close.assert_awaited_once()


async def test_wire_sample_and_nearby_bars_are_separate():
    # These are deliberately mocked bytes, not a live tick sample.
    client = FakeClient(body=example_tick())
    report = await collect(client)
    assert report["status"] == "sampled"
    assert report["digits"] == 5
    assert report["sample"]["source"] == "cmd8_wire"
    assert report["sample"]["body_bytes"] == 50
    assert report["sample"]["tick_time_unix_ms"] == 1_700_000_000_123
    assert report["sample"]["fields"] == 3
    assert report["sample"]["flags"] == 7
    assert report["sample"]["wire_prices"]["bid"] == pytest.approx(1.125)
    assert report["sample"]["scaled_candidates"]["bid"] == pytest.approx(1.125 / 100_000)
    assert report["bars_reference"]["source"] == "cmd11_m1"
    assert report["bars_reference"]["bars"][0]["close"] == pytest.approx(1.2)
    client.get_rates.assert_awaited_once_with("EURUSD", 1, 1_699_999_880, 1_700_000_060, bar_format="auto")
    client.close.assert_awaited_once()


async def test_unsubscribe_failure_is_reported_without_leaking_exception_text():
    client = FakeClient(body=example_tick(), unsubscribe_error=ProtocolError("code=1 private-test-password"))
    report = await collect(client)
    assert report["status"] == "sampled"
    assert report["unsubscribe"] == {"stage": "unsubscribe", "type": "ProtocolError", "server_code": 1}
    assert "private-test-password" not in json.dumps(report)
    client.close.assert_awaited_once()


async def test_connection_exception_is_sanitized_and_closes():
    client = FakeClient(connect_error=RuntimeError("private-test-password"))
    report = await collect(client)
    assert report["status"] == "error"
    assert report["error"] == {"stage": "connect", "type": "RuntimeError"}
    assert "private-test-password" not in json.dumps(report)
    client.close.assert_awaited_once()


async def test_malformed_tick_is_error_and_closes():
    client = FakeClient(body=b"\x00")
    report = await collect(client)
    assert report["status"] == "error"
    assert report["error"] == {"stage": "tick_parse", "type": "InvalidBodyLength", "body_bytes": 1}
    client.unsubscribe_ticks.assert_awaited_once_with([42])
    client.close.assert_awaited_once()


async def test_nonmatching_tick_is_not_a_sample():
    client = FakeClient(body=example_tick(symbol_id=43))
    report = await collect(client)
    assert report["status"] == "no_sample"
    assert report["frames_seen"] == 1
    assert "sample" not in report
    client.close.assert_awaited_once()


async def test_unknown_symbol_closes_without_subscription():
    client = FakeClient()
    report = await collect(client, symbol="UNKNOWN")
    assert report["status"] == "symbol_not_found"
    assert report["unsubscribe"] == "not_attempted"
    client.close.assert_awaited_once()


async def test_bar_failure_does_not_claim_reference_and_redacts_error():
    client = FakeClient(body=example_tick(), bars_error=RuntimeError("private-test-password"))
    report = await collect(client)
    assert report["status"] == "sampled"
    assert report["bars_reference"]["status"] == "error"
    assert "private-test-password" not in json.dumps(report)
    client.close.assert_awaited_once()


@pytest.mark.parametrize(("requested", "expected_id"), [("XAUUSD", 43), ("", 42)])
async def test_integration_symbol_environment_selects_matching_tick(monkeypatch, requested, expected_id):
    monkeypatch.setenv("PYMT5_LOGIN", "0")
    monkeypatch.setenv("PYMT5_PASSWORD", "")
    monkeypatch.setenv("PYMT5_SERVER", "wss://example.invalid")
    monkeypatch.setenv("PYMT5_TEST_SYMBOL", requested)
    path = Path(__file__).with_name("test_integration.py")
    spec = importlib.util.spec_from_file_location("demo_integration_probe", path)
    assert spec and spec.loader
    integration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(integration)

    class FakeIntegrationClient:
        def __init__(self):
            self.load_symbols = AsyncMock(
                return_value={"EURUSD": SymbolInfo("EURUSD", 42, 5), "XAUUSD": SymbolInfo("XAUUSD", 43, 2)}
            )
            self.unsubscribe_ticks = AsyncMock()
            self.subscribed = None
            self.callback = None

        def on_tick(self, callback):
            self.callback = callback

        async def subscribe_ticks(self, ids):
            self.subscribed = ids
            # An unrelated tick must not satisfy the selected-symbol check.
            self.callback([{"symbol_id": 999}])
            self.callback([{"symbol_id": ids[0]}])

    client = FakeIntegrationClient()
    await integration.test_tick_subscription(client)
    assert client.subscribed == [expected_id]
    client.unsubscribe_ticks.assert_awaited_once_with([expected_id])


def test_cli_requires_exact_symbol_and_emits_sanitized_no_sample(monkeypatch, capsys):
    monkeypatch.setenv("PYMT5_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PYMT5_SERVER", "wss://example.invalid")
    monkeypatch.setenv("PYMT5_LOGIN", "123")
    monkeypatch.setenv("PYMT5_PASSWORD", "private-test-password")
    monkeypatch.setattr(sys, "argv", ["demo_tick_probe.py", "--symbol", "XAUUSD", "--seconds", "1"])
    collect_mock = AsyncMock(return_value={"status": "no_sample", "unsubscribe": "ok", "close": "ok"})
    monkeypatch.setattr(demo_tick_probe, "collect_sample", collect_mock)
    assert demo_tick_probe.main() == 1
    assert collect_mock.await_args.kwargs["symbol"] == "XAUUSD"
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "no_sample"
    assert "private-test-password" not in output


def test_cli_sample_with_unsubscribe_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("PYMT5_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PYMT5_SERVER", "wss://example.invalid")
    monkeypatch.setenv("PYMT5_LOGIN", "123")
    monkeypatch.setenv("PYMT5_PASSWORD", "private-test-password")
    monkeypatch.setattr(sys, "argv", ["demo_tick_probe.py", "--symbol", "EURUSD"])
    monkeypatch.setattr(
        demo_tick_probe,
        "collect_sample",
        AsyncMock(
            return_value={"status": "sampled", "unsubscribe": {"type": "ProtocolError"}, "close": "ok"}
        ),
    )
    assert demo_tick_probe.main() == 1
    assert json.loads(capsys.readouterr().out)["unsubscribe"]["type"] == "ProtocolError"


@pytest.mark.parametrize("value", ["0", "301", "inf", "nan"])
def test_cli_sampling_duration_is_bounded(value):
    with pytest.raises(argparse.ArgumentTypeError):
        demo_tick_probe._seconds(value)
