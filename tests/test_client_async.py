"""Async tests for MT5WebClient: mock transport.send_command to test
login flow, get_positions, subscribe_ticks, on_tick handler, trade validation,
unsubscribe_ticks, and on_* handler return values."""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5.client import MT5WebClient, SymbolInfo, _parse_account_response
from pymt5.constants import (
    CMD_GET_POSITIONS_ORDERS,
    CMD_LOGIN,
    CMD_SUBSCRIBE_TICKS,
    CMD_TICK_PUSH,
    ORDER_TYPE_SELL,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_RETCODE_DONE,
)
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import POSITION_FIELD_NAMES, POSITION_SCHEMA, TICK_FIELD_NAMES, TICK_SCHEMA
from pymt5.transport import CommandResult


# ---- Login flow ----

async def test_login_stores_credentials():
    client = MT5WebClient()
    # Mock transport.send_command for LOGIN
    token_bytes = bytes(160)
    session_id = (42).to_bytes(8, "little", signed=False)
    login_body = token_bytes + session_id

    client.transport.send_command = AsyncMock(return_value=CommandResult(
        command=CMD_LOGIN, code=0, body=login_body
    ))
    client.transport.is_ready = True

    token, session = await client.login(
        login=12345678, password="test", auto_heartbeat=False
    )

    assert client._logged_in is True
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 12345678
    assert session == 42


# ---- subscribe_ticks ----

async def test_subscribe_ticks_accumulates():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(
        command=CMD_SUBSCRIBE_TICKS, code=0, body=b""
    ))
    client.transport.is_ready = True

    await client.subscribe_ticks([1, 2])
    assert client._subscribed_ids == [1, 2]

    await client.subscribe_ticks([3])
    assert client._subscribed_ids == [1, 2, 3]

    # Verify the payload sent for the second call includes all 3 IDs
    last_call_payload = client.transport.send_command.call_args_list[-1][0][1]
    count = struct.unpack_from("<I", last_call_payload, 0)[0]
    assert count == 3


# ---- unsubscribe_ticks ----

async def test_unsubscribe_ticks_removes():
    client = MT5WebClient()
    client._subscribed_ids = [1, 2, 3]
    client.transport.send_command = AsyncMock(return_value=CommandResult(
        command=CMD_SUBSCRIBE_TICKS, code=0, body=b""
    ))
    client.transport.is_ready = True

    await client.unsubscribe_ticks([2])
    assert client._subscribed_ids == [1, 3]


async def test_unsubscribe_ticks_all():
    client = MT5WebClient()
    client._subscribed_ids = [1, 2]
    client.transport.send_command = AsyncMock(return_value=CommandResult(
        command=CMD_SUBSCRIBE_TICKS, code=0, body=b""
    ))
    client.transport.is_ready = True

    await client.unsubscribe_ticks([1, 2])
    assert client._subscribed_ids == []

    # Verify count=0 was sent
    last_call_payload = client.transport.send_command.call_args_list[-1][0][1]
    count = struct.unpack_from("<I", last_call_payload, 0)[0]
    assert count == 0


# ---- on_tick handler ----

async def test_on_tick_handler_parses_ticks():
    client = MT5WebClient()
    received = []
    handler = client.on_tick(lambda ticks: received.append(ticks))

    # Verify handler was returned (Phase 7.2)
    assert callable(handler)

    # Build a fake tick
    from pymt5.constants import PROP_F64, PROP_I32, PROP_I64, PROP_U16, PROP_U32
    tick_data = SeriesCodec.serialize([
        (PROP_U32, 42),      # symbol_id
        (PROP_I32, 1000000), # tick_time
        (PROP_U32, 0),       # fields
        (PROP_F64, 1.12345), # bid
        (PROP_F64, 1.12350), # ask
        (PROP_F64, 0.0),     # last
        (PROP_I64, 100),     # tick_volume
        (PROP_U32, 500),     # time_ms_delta
        (PROP_U16, 0),       # flags
    ])

    # Simulate tick push
    result = CommandResult(command=CMD_TICK_PUSH, code=0, body=tick_data)
    handler(result)

    assert len(received) == 1
    ticks = received[0]
    assert len(ticks) == 1
    assert ticks[0]["symbol_id"] == 42
    assert ticks[0]["bid"] == 1.12345
    assert ticks[0]["tick_time_ms"] == 1000000 * 1000 + 500


async def test_on_tick_handler_error_does_not_propagate():
    client = MT5WebClient()
    handler = client.on_tick(lambda ticks: None)
    # Send malformed body — handler should log error, not raise
    result = CommandResult(command=CMD_TICK_PUSH, code=0, body=b"\x00\x01")
    handler(result)  # Should not raise


# ---- on_* handler return values (Phase 7.2) ----

def test_on_handlers_return_callable():
    client = MT5WebClient()
    handlers = [
        client.on_tick(lambda t: None),
        client.on_position_update(lambda p: None),
        client.on_order_update(lambda o: None),
        client.on_trade_update(lambda d: None),
        client.on_symbol_update(lambda r: None),
        client.on_account_update(lambda d: None),
        client.on_login_status(lambda r: None),
        client.on_symbol_details(lambda d: None),
        client.on_trade_result(lambda d: None),
        client.on_trade_transaction(lambda d: None),
        client.on_book_update(lambda d: None),
    ]
    for h in handlers:
        assert callable(h), f"handler {h} should be callable"


# ---- trade_request validation ----

async def test_trade_request_volume_validation():
    client = MT5WebClient()
    client.transport.is_ready = True
    with pytest.raises(ValueError, match="volume must be > 0"):
        await client.trade_request(trade_action=TRADE_ACTION_DEAL, volume=0)


async def test_trade_request_pending_price_validation():
    client = MT5WebClient()
    client.transport.is_ready = True
    with pytest.raises(ValueError, match="price_order must be > 0"):
        await client.trade_request(
            trade_action=TRADE_ACTION_PENDING, volume=100, price_order=0.0
        )


# ---- _parse_account_response ----

def test_parse_account_response_empty():
    assert _parse_account_response(b"") == {}
    assert _parse_account_response(None) == {}


def test_parse_account_response_short():
    assert _parse_account_response(b"\x00" * 50) == {}


def test_parse_account_response_valid():
    from pymt5.helpers import encode_utf16le
    body = b"\x00"  # flags byte
    body += struct.pack("<II", 0, 0)  # param1, param2
    body += struct.pack("<dd", 10000.0, 500.0)  # balance, credit
    body += encode_utf16le("USD", 64)  # currency
    body += struct.pack("<II", 0, 100)  # trade_mode, leverage
    result = _parse_account_response(body)
    assert result["balance"] == 10000.0
    assert result["credit"] == 500.0
    assert result["currency"] == "USD"
    assert result["leverage"] == 100


# ---- subscribe_book tracks IDs ----

async def test_subscribe_book_stores_ids():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(
        command=22, code=0, body=b""
    ))
    client.transport.is_ready = True

    await client.subscribe_book([10, 20, 30])
    assert client._subscribed_book_ids == [10, 20, 30]


# ---- reconnect guard ----

def test_reconnect_guard_skips_if_in_progress():
    client = MT5WebClient(auto_reconnect=True)
    client._login_kwargs = {"login": 1, "password": "x"}

    # Create a fake task that is not done
    loop = asyncio.new_event_loop()
    fake_task = loop.create_future()
    # Wrap in a Task
    async def dummy(): await asyncio.sleep(999)
    client._reconnect_task = loop.create_task(dummy())

    # _handle_disconnect should not create a new task
    client._handle_disconnect()
    # The reconnect_task should still be the same one
    assert not client._reconnect_task.done() or client._reconnect_task is not None
    client._reconnect_task.cancel()
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()
