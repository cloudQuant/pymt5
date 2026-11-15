"""Independent wire-layout checks for build 6320 cmd=17 quote details."""

import struct
from unittest.mock import Mock, patch

from pymt5.client import MT5WebClient
from pymt5.protocol import get_series_size
from pymt5.schemas import SYMBOL_DETAILS_6320_FIELD_NAMES, SYMBOL_DETAILS_6320_SCHEMA, SYMBOL_DETAILS_SCHEMA
from pymt5.transport import CommandResult, MT5WebSocketTransport
from pymt5.types import SymbolInfo

# The format is independently specified from the observed build-6320 client,
# rather than being produced through the Python schema under test.
_WIRE = struct.Struct("<IqiI9d3qIQ3IqIq6d8qH")


def _record(symbol_id: int, base: float, mask: int, flags: int) -> bytes:
    prices = [base + offset for offset in range(9)]
    return _WIRE.pack(
        symbol_id, mask, 1_700_000_000, 30,
        *prices,
        1300, 1400, 1500,
        1600, 1700, 1800, 1900, 2000, 2100, 2200, 2300,
        2400.5, 2500.5, 2600.5, 2700.5, 2800.5, 2900.5,
        3000, 3100, 3200, 3300, 3400, 3500, 3600, 3700,
        flags,
    )


def _result(body: bytes) -> CommandResult:
    return CommandResult(command=17, code=0, body=body)


def test_two_independent_6320_records_decode_raw_values_without_greeks():
    client = MT5WebClient(tick_price_mode="scaled")
    client.transport._server_build = 6320
    client._symbols_by_id[42] = SymbolInfo(name="XAUUSD", symbol_id=42, digits=2)
    callback = Mock()
    handler = client.on_symbol_details(callback)
    body = _record(42, 387_838.0, 0x123456789, 1) + _record(99, 110_225.0, 7, 2)

    assert _WIRE.size == get_series_size(SYMBOL_DETAILS_6320_SCHEMA) == 274
    assert len(SYMBOL_DETAILS_6320_SCHEMA) == len(SYMBOL_DETAILS_6320_FIELD_NAMES) == 39
    handler(_result(body))

    callback.assert_called_once()
    first, second = callback.call_args.args[0]
    assert first["symbol"] == "XAUUSD"
    assert first["symbol_id"] == 42
    assert first["fields_mask"] == 0x123456789
    assert first["tick_time"] == 1_700_000_000
    assert first["wire_field_3"] == 30
    assert first["bid"] == 387_838.0  # Raw even with tick_price_mode="scaled".
    assert first["bid_high"] == 387_839.0
    assert first["bid_low"] == 387_840.0
    assert first["ask"] == 387_841.0
    assert first["ask_high"] == 387_842.0
    assert first["ask_low"] == 387_843.0
    assert first["last"] == 387_844.0
    assert first["last_high"] == 387_845.0
    assert first["last_low"] == 387_846.0
    assert first["tick_volume"] == 1300
    assert first["price_open"] == 2400.5
    assert first["price_close"] == 2500.5
    assert first["wire_field_17"] == 1700
    assert first["wire_field_37"] == 3700
    assert first["flags"] == 1  # The observed web client uses bit 0 for delayed quotes.
    assert "delta" not in first and "gamma" not in first
    assert second["symbol_id"] == 99 and "symbol" not in second
    assert second["bid"] == 110_225.0
    assert second["flags"] == 2


def test_6320_empty_body_calls_back_with_empty_list():
    client = MT5WebClient()
    client.transport._server_build = 6320
    callback = Mock()
    client.on_symbol_details(callback)(_result(b""))
    callback.assert_called_once_with([])


def test_unmarked_zero_values_are_not_backfilled_from_tick_cache():
    client = MT5WebClient(tick_price_mode="auto")
    client.transport._server_build = 6320
    client._symbols_by_id[42] = SymbolInfo(name="XAUUSD", symbol_id=42, digits=2)
    client._tick_cache_by_id[42] = {"bid": 123.0, "ask": 124.0, "last": 125.0}
    values = list(_WIRE.unpack(_record(42, 100.0, 0, 0)))
    values[4:13] = [0.0] * 9
    callback = Mock()

    client.on_symbol_details(callback)(_result(_WIRE.pack(*values)))

    quote = callback.call_args.args[0][0]
    assert quote["fields_mask"] == 0
    assert (quote["bid"], quote["ask"], quote["last"]) == (0.0, 0.0, 0.0)


def test_6320_truncated_or_extra_bytes_are_not_partially_delivered():
    client = MT5WebClient()
    client.transport._server_build = 6320
    callback = Mock()
    handler = client.on_symbol_details(callback)
    with patch("pymt5._push_handlers.logger.error") as error:
        handler(_result(_record(42, 1.0, 1, 0) + b"\x00"))
        handler(_result(_record(42, 1.0, 1, 0)[:-1]))
    callback.assert_not_called()
    assert error.call_count == 2


def test_handler_uses_current_transport_build_after_replacement():
    client = MT5WebClient()
    callback = Mock()
    handler = client.on_symbol_details(callback)

    client.transport = MT5WebSocketTransport("ws://offline.invalid")
    client.transport._server_build = 6320
    handler(_result(_record(42, 100.0, 1, 0)))
    assert callback.call_args.args[0][0]["bid"] == 100.0
    assert "wire_field_3" in callback.call_args.args[0][0]

    client.transport = MT5WebSocketTransport("ws://offline.invalid")
    client.transport._server_build = 5687
    handler(_result(bytes(get_series_size(SYMBOL_DETAILS_SCHEMA)) + b"\x00"))
    legacy = callback.call_args.args[0][0]
    assert "delta" in legacy
    assert "wire_field_3" not in legacy
