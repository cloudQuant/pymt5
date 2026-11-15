"""Regression coverage for the counted ID-based cmd=18 protocol."""

import asyncio
import struct
from unittest.mock import AsyncMock, patch

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_GET_FULL_SYMBOLS, CMD_GET_SYMBOLS, CMD_GET_SYMBOLS_GZIP
from pymt5.exceptions import ProtocolError
from pymt5.helpers import encode_utf16le
from pymt5.protocol import get_series_size
from pymt5.schemas import FULL_SYMBOL_SCHEMA
from pymt5.transport import CommandResult
from pymt5.types import SymbolInfo

_ID = 0xF1234567


def _symbol(name: str = "EURUSD", symbol_id: int = _ID) -> SymbolInfo:
    return SymbolInfo(name=name, symbol_id=symbol_id, digits=5)


def _body(name: str = "EURUSD", symbol_id: int = _ID) -> bytes:
    record = bytearray(get_series_size(FULL_SYMBOL_SCHEMA))
    record[:64] = encode_utf16le(name, 64)
    struct.pack_into("<I", record, get_series_size(FULL_SYMBOL_SCHEMA[:21]), 5)
    struct.pack_into("<I", record, get_series_size(FULL_SYMBOL_SCHEMA[:24]), symbol_id)
    return struct.pack("<I", 1) + record


def _response(body: bytes, code: int = 0) -> CommandResult:
    return CommandResult(command=CMD_GET_FULL_SYMBOLS, code=code, body=body)


async def test_cached_symbol_uses_single_id_and_decodes_record():
    client = MT5WebClient()
    client._symbols["EURUSD"] = _symbol()
    client.load_symbols = AsyncMock()
    client.transport.send_command = AsyncMock(return_value=_response(_body()))

    info = await client.get_full_symbol_info("EURUSD")

    client.load_symbols.assert_not_awaited()
    client.transport.send_command.assert_awaited_once_with(CMD_GET_FULL_SYMBOLS, struct.pack("<II", 1, _ID))
    assert info is not None
    assert info["trade_symbol"] == "EURUSD"
    assert info["symbol_id"] == _ID
    assert info["digits"] == 5
    assert client._full_symbols["EURUSD"] == info


async def test_cold_lookup_loads_symbol_cache_before_request():
    client = MT5WebClient()

    async def load():
        client._symbols["EURUSD"] = _symbol()
        return dict(client._symbols)

    client.load_symbols = AsyncMock(side_effect=load)
    client.transport.send_command = AsyncMock(return_value=_response(_body()))
    assert (await client.get_full_symbol_info("EURUSD"))["symbol_id"] == _ID
    client.load_symbols.assert_awaited_once()
    client.transport.send_command.assert_awaited_once_with(CMD_GET_FULL_SYMBOLS, struct.pack("<II", 1, _ID))


async def test_unknown_symbol_does_not_send_full_symbol_request():
    client = MT5WebClient()
    client.load_symbols = AsyncMock(return_value={})
    client.transport.send_command = AsyncMock()
    assert await client.get_full_symbol_info("MISSING") is None
    client.load_symbols.assert_awaited_once()
    client.transport.send_command.assert_not_awaited()


async def test_symbol_list_load_failure_propagates():
    client = MT5WebClient()
    client.load_symbols = AsyncMock(side_effect=ConnectionError("offline"))
    client.transport.send_command = AsyncMock()
    with pytest.raises(ConnectionError, match="offline"):
        await client.get_full_symbol_info("EURUSD")
    client.transport.send_command.assert_not_awaited()


async def test_cold_full_symbol_lookup_rejects_failed_symbol_list_without_negative_cache():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=1, body=b""),
    )

    with pytest.raises(ProtocolError, match=r"command 34 failed: code=1"):
        await client.get_full_symbol_info("EURUSD")

    client.transport.send_command.assert_awaited_once_with(CMD_GET_SYMBOLS_GZIP)
    assert client._symbols == {}
    assert client._full_symbols == {}
    assert "EURUSD" not in client._unknown_symbol_misses


@pytest.mark.parametrize("use_gzip, command", [(True, CMD_GET_SYMBOLS_GZIP), (False, CMD_GET_SYMBOLS)])
async def test_failed_symbol_list_refresh_preserves_existing_caches(use_gzip: bool, command: int):
    client = MT5WebClient()
    symbol = _symbol()
    client._symbols["EURUSD"] = symbol
    client._symbols_by_id[_ID] = symbol
    full_info = {"trade_symbol": "EURUSD", "symbol_id": _ID}
    client._full_symbols["EURUSD"] = full_info
    client._full_symbols_loaded_at["EURUSD"] = 123.0
    client._symbols_loaded_at = 456.0
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=command, code=1, body=b""))

    with pytest.raises(ProtocolError, match=rf"command {command} failed: code=1"):
        await client.load_symbols(use_gzip=use_gzip)

    client.transport.send_command.assert_awaited_once_with(command)
    assert client._symbols == {"EURUSD": symbol}
    assert client._symbols_by_id == {_ID: symbol}
    assert client._full_symbols == {"EURUSD": full_info}
    assert client._full_symbols_loaded_at == {"EURUSD": 123.0}
    assert client._symbols_loaded_at == 456.0


@pytest.mark.parametrize("use_gzip, command", [(True, CMD_GET_SYMBOLS_GZIP), (False, CMD_GET_SYMBOLS)])
async def test_symbol_list_checks_rejection_before_parsing(use_gzip: bool, command: int):
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=command, code=7, body=b"bad!!"))

    with pytest.raises(ProtocolError, match=rf"command {command} failed: code=7"):
        await client.get_symbols(use_gzip=use_gzip)

    client.transport.send_command.assert_awaited_once_with(command)


async def test_symbol_refresh_during_full_request_cannot_cache_old_id():
    client = MT5WebClient()
    client._symbols["EURUSD"] = _symbol()
    client._symbols_by_id[_ID] = client._symbols["EURUSD"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def old_response(*_args):
        entered.set()
        await release.wait()
        return _response(_body())

    client.transport.send_command = AsyncMock(side_effect=old_response)
    client.get_symbols = AsyncMock(return_value=[{"trade_symbol": "EURUSD", "symbol_id": 42, "digits": 5}])
    task = asyncio.create_task(client.get_full_symbol_info("EURUSD"))
    await entered.wait()
    await client.load_symbols()
    release.set()
    with pytest.raises(ProtocolError, match="symbol mapping changed"):
        await task
    assert client._symbols["EURUSD"].symbol_id == 42
    assert client._full_symbols == {}
    assert client._full_symbols_loaded_at == {}


async def test_nonzero_response_is_not_misreported_as_unknown_symbol():
    client = MT5WebClient()
    client._symbols["EURUSD"] = _symbol()
    client.transport.send_command = AsyncMock(return_value=_response(b"", code=1))
    with pytest.raises(ProtocolError, match="code=1"):
        await client.get_full_symbol_info("EURUSD")
    assert client._full_symbols == {}


async def test_zero_count_is_not_found():
    client = MT5WebClient()
    client._symbols["EURUSD"] = _symbol()
    client.transport.send_command = AsyncMock(return_value=_response(struct.pack("<I", 0)))
    assert await client.get_full_symbol_info("EURUSD") is None
    assert client._full_symbols == {}


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\x01\x00\x00",
        struct.pack("<I", 1),
        _body()[:-1],
        _body() + b"\x00",
        struct.pack("<I", 0) + _body()[4:],
        struct.pack("<I", 2) + _body()[4:],
        _body("XAUUSD"),
        _body(symbol_id=123),
    ],
)
async def test_malformed_or_mismatched_responses_fail_closed(body: bytes):
    client = MT5WebClient()
    client._symbols["EURUSD"] = _symbol()
    client.transport.send_command = AsyncMock(return_value=_response(body))
    with pytest.raises(ProtocolError):
        await client.get_full_symbol_info("EURUSD")
    assert client._full_symbols == {}


async def test_full_symbol_cache_respects_ttl_with_id_requests():
    client = MT5WebClient(symbol_cache_ttl=5)
    client._symbols["EURUSD"] = _symbol()
    client.transport.send_command = AsyncMock(return_value=_response(_body()))
    with patch("pymt5._market_data.time.monotonic", side_effect=[100.0, 101.0, 106.0, 106.0]):
        first = await client.symbol_info("EURUSD")
        cached = await client.symbol_info("EURUSD")
        refreshed = await client.symbol_info("EURUSD")
    assert first == cached == refreshed
    assert client.transport.send_command.await_count == 2
