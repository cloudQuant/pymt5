"""Offline checks for observed cmd=4 dispatch and build 6320 tick layout."""

import asyncio
import struct
from unittest.mock import AsyncMock

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_GET_POSITIONS_ORDERS, CMD_TICK_PUSH
from pymt5.protocol import ResponseFrame, get_series_size
from pymt5.schemas import TICK_SCHEMA
from pymt5.types import SymbolInfo


async def test_cmd4_query_response_and_no_pending_frame_reach_callbacks() -> None:
    client = MT5WebClient()
    socket = AsyncMock()
    sent = asyncio.Event()

    async def send(_frame: bytes) -> None:
        sent.set()

    socket.send.side_effect = send
    client.transport.ws = socket
    client.transport.is_ready = True
    positions = []
    orders = []
    combined = []
    client.on_position_update(positions.append)
    client.on_order_update(orders.append)
    client.on_trade_update(combined.append)

    # A real client query goes through the transport pending queue.  The two
    # zero counts represent an empty position list and an empty order list.
    request = asyncio.create_task(client.get_positions_and_orders())
    try:
        await asyncio.wait_for(sent.wait(), timeout=1)
        assert len(client.transport._pending[CMD_GET_POSITIONS_ORDERS]) == 1
        frame = ResponseFrame(CMD_GET_POSITIONS_ORDERS, 0, struct.pack("<II", 0, 0))
        await client.transport._dispatch(frame)
        assert await asyncio.wait_for(request, timeout=1) == {"positions": [], "orders": []}
        assert positions == [[]]
        assert orders == [[]]
        assert combined == [{"positions": [], "orders": []}]

        # Dispatching a fabricated no-pending frame exercises the same
        # callback contract; this does not establish a real server push.
        assert not client.transport._pending.get(CMD_GET_POSITIONS_ORDERS)
        await client.transport._dispatch(frame)
        assert positions == [[], []]
        assert orders == [[], []]
        assert combined == [
            {"positions": [], "orders": []},
            {"positions": [], "orders": []},
        ]
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        await client.close()


async def test_tick_dispatch_parses_consecutive_50_byte_records_without_count() -> None:
    client = MT5WebClient(tick_price_mode="normalized")
    client._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    client._symbols_by_id[43] = SymbolInfo(name="XAUUSD", symbol_id=43, digits=2)
    batches = []
    client.on_tick(batches.append)

    # Pack an independent, explicit wire example rather than serializing with
    # the schema being checked. Neither record has a leading count field.
    first = struct.pack("<IiIdddqIH", 42, 1_700_000_000, 3, 1.125, 1.25, 0.0, 10, 123, 1)
    second = struct.pack("<IiIdddqIH", 43, 1_700_000_001, 3, 2000.0, 2001.0, 0.0, 20, 456, 2)
    assert get_series_size(TICK_SCHEMA) == 50
    assert len(first) == len(second) == 50

    await client.transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, first + second))

    assert len(batches) == 1
    assert [tick["symbol"] for tick in batches[0]] == ["EURUSD", "XAUUSD"]
    assert [tick["bid"] for tick in batches[0]] == pytest.approx([1.125, 2000.0])
    assert [tick["ask"] for tick in batches[0]] == pytest.approx([1.25, 2001.0])
    assert [tick["tick_time_ms"] for tick in batches[0]] == [1_700_000_000_123, 1_700_000_001_456]
