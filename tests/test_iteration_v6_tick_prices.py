"""Offline regressions for configurable tick price decoding (iteration v6 Q6)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_TICK_PUSH, PROP_F64, PROP_I32, PROP_I64, PROP_U16, PROP_U32
from pymt5.protocol import ResponseFrame, SeriesCodec
from pymt5.transport import CommandResult, MT5WebSocketTransport
from pymt5.types import SymbolInfo, TickPriceMode


def _tick_body(*, symbol_id: int = 1, bid: float, ask: float = 0.0, last: float = 0.0) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U32, symbol_id),
            (PROP_I32, 1_700_000_000),
            (PROP_U32, 0),
            (PROP_F64, bid),
            (PROP_F64, ask),
            (PROP_F64, last),
            (PROP_I64, 10),
            (PROP_U32, 500),
            (PROP_U16, 0),
        ]
    )


def _client(
    symbol: str,
    digits: int,
    mode: TickPriceMode = "auto",
    overrides: dict[str, TickPriceMode] | None = None,
) -> MT5WebClient:
    client = MT5WebClient(tick_price_mode=mode, tick_price_modes=overrides)
    client._symbols_by_id[1] = SymbolInfo(name=symbol, symbol_id=1, digits=digits)
    return client


@pytest.mark.parametrize(
    ("symbol", "digits", "wire", "scaled", "normalized"),
    [
        ("EURUSD", 5, 112345.0, 1.12345, 112345.0),
        ("XAUUSD", 2, 474267.0, 4742.67, 474267.0),
        ("BTCUSD", 2, 6_742_567.0, 67_425.67, 6_742_567.0),
        ("US500", 1, 601234.0, 60_123.4, 601234.0),
    ],
)
def test_explicit_tick_price_modes_are_deterministic(
    symbol: str, digits: int, wire: float, scaled: float, normalized: float
) -> None:
    for mode, expected in (("scaled", scaled), ("normalized", normalized)):
        client = _client(symbol, digits, mode)  # type: ignore[arg-type]
        ticks = client._parse_ticks(_tick_body(bid=wire))
        assert ticks[0]["bid"] == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["scaled", "normalized"])
@pytest.mark.parametrize("wire", [0.0, 1.0, -112345.0, 1e-12])
def test_explicit_modes_cover_zero_sign_and_small_boundaries(mode: TickPriceMode, wire: float) -> None:
    client = _client("EURUSD", 5, mode)
    parsed = client._parse_ticks(_tick_body(bid=wire))[0]["bid"]
    expected = wire / 100_000 if mode == "scaled" and wire else wire
    assert parsed == pytest.approx(expected)


def test_symbol_override_beats_global_mode_and_is_case_insensitive() -> None:
    client = _client("EURUSD", 5, "normalized", {"eurusd": "scaled"})
    client._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)

    body = _tick_body(bid=112345.0) + _tick_body(symbol_id=2, bid=125000.0)
    ticks = client._parse_ticks(body)
    assert ticks[0]["bid"] == pytest.approx(1.12345)
    assert ticks[1]["bid"] == 125000.0


def test_raw_typed_and_cache_paths_share_symbol_override() -> None:
    client = _client("XAUUSD", 2, "auto", {"XAUUSD": "normalized"})
    raw_ticks = []
    typed_ticks = []
    client.on_tick(raw_ticks.extend)
    client.on_tick_event(typed_ticks.append)
    result = CommandResult(CMD_TICK_PUSH, 0, _tick_body(bid=474267.0, ask=474268.0))

    for handler in list(client.transport._listeners[CMD_TICK_PUSH]):
        handler(result)

    assert raw_ticks[0]["bid"] == 474267.0
    assert typed_ticks[0].bid == 474267.0
    assert typed_ticks[0].raw["ask"] == 474268.0
    assert client._tick_cache_by_id[1]["bid"] == 474267.0
    assert client._tick_history_by_id[1][0]["ask"] == 474268.0


def test_auto_mode_preserves_legacy_examples() -> None:
    cases = [
        ("EURUSD", 5, 112345.0, 1.12345),
        ("EURGBP", 5, 87154.5, 0.871545),
        ("XAUUSD", 2, 474267.0, 4742.67),
        ("XAUUSD", 2, 2000.0, 20.0),  # Legacy behavior despite the old misleading comment.
        ("BTCUSD", 2, 67_425.67, 67_425.67),
        ("US500", 1, 601234.0, 60_123.4),
    ]
    for symbol, digits, wire, expected in cases:
        client = _client(symbol, digits)
        assert client._parse_ticks(_tick_body(bid=wire))[0]["bid"] == pytest.approx(expected)


def test_auto_diagnostic_is_emitted_once_per_symbol_and_mode(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client("XAUUSD", 2)
    client._symbols_by_id[2] = SymbolInfo(name="BTCUSD", symbol_id=2, digits=2)
    monkeypatch.setattr("pymt5._push_handlers.logger", logging.getLogger("tests.iteration_v6.tick_prices"))
    caplog.set_level(logging.WARNING, logger="tests.iteration_v6.tick_prices")

    for _ in range(100):
        client._parse_ticks(_tick_body(bid=474267.0, ask=474268.0, last=474269.0))
        client._parse_ticks(_tick_body(symbol_id=2, bid=6_742_567.0))

    messages = [record.message for record in caplog.records if "tick price auto mode" in record.message]
    assert len(messages) == 2
    assert sum("XAUUSD" in message for message in messages) == 1
    assert sum("BTCUSD" in message for message in messages) == 1


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"tick_price_mode": "invalid"}, "tick_price_mode"),
        ({"tick_price_modes": {"EURUSD": "invalid"}}, "EURUSD"),
        ({"tick_price_modes": {"": "scaled"}}, "non-empty"),
    ],
)
def test_invalid_tick_price_configuration_is_rejected(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        MT5WebClient(**kwargs)


async def _offline_transport(*args, **kwargs) -> MT5WebSocketTransport:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    transport.connect = AsyncMock()  # type: ignore[method-assign]
    transport.close = AsyncMock()  # type: ignore[method-assign]
    return transport


async def test_tick_price_configuration_survives_consecutive_reconnects() -> None:
    client = _client("XAUUSD", 2, "normalized", {"XAUUSD": "scaled"})
    client._login_kwargs = {"login": 1, "password": "secret"}
    client.login = AsyncMock(return_value=("token", 1))  # type: ignore[method-assign]
    client.transport.close = AsyncMock()  # type: ignore[method-assign]

    for _ in range(2):
        replacement = await _offline_transport()
        with patch("pymt5.client.MT5WebSocketTransport", return_value=replacement):
            await client._reconnect_loop()
        ticks = []
        client.on_tick(ticks.extend)
        await client.transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, _tick_body(bid=474267.0)))
        assert ticks[0]["bid"] == pytest.approx(4742.67)
        assert client._tick_price_mode == "normalized"
        assert client._tick_price_modes == {"XAUUSD": "scaled"}
