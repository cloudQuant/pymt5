"""Comprehensive tests for pymt5/_trading.py to cover all untested lines.

Covers: get_positions_and_orders, get_trade_history, get_positions, get_orders,
get_deals, positions_total, positions_get, orders_get, orders_total,
history_orders_get, history_orders_total, history_deals_get, history_deals_total,
order_calc_profit, order_calc_margin, trade_request, order_check, order_send,
_normalize_order_request, _validate_order_check_request, _estimate_order_margin,
_resolve_order_check_price, _parse_trade_response, _resolve_digits, _place_order,
buy_market, sell_market, buy_limit, sell_limit, buy_stop, sell_stop,
buy_stop_limit, sell_stop_limit, close_position, close_position_by,
modify_position_sltp, modify_pending_order, cancel_pending_order,
_detect_close_direction.
"""

import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import (
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_TRADE_HISTORY,
    CMD_TRADE_REQUEST,
    ORDER_FILLING_FOK,
    ORDER_TIME_SPECIFIED,
    ORDER_TIME_SPECIFIED_DAY,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_STOP_LIMIT,
    POSITION_TYPE_BUY,
    POSITION_TYPE_SELL,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID,
    TRADE_RETCODE_INVALID_EXPIRATION,
    TRADE_RETCODE_INVALID_FILL,
    TRADE_RETCODE_INVALID_ORDER,
    TRADE_RETCODE_INVALID_PRICE,
    TRADE_RETCODE_INVALID_STOPS,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_TRADE_DISABLED,
)
from pymt5.protocol import get_series_size
from pymt5.schemas import (
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import TRADE_RESPONSE_SCHEMA, SymbolInfo

# ---- Helpers ----


def _make_client() -> MT5WebClient:
    """Create a client with mocked transport ready for testing."""
    client = MT5WebClient()
    client.transport.is_ready = True
    return client


def _build_position_body(positions: list[dict]) -> bytes:
    """Build a binary body for positions (4-byte count + serialized records)."""
    pos_size = get_series_size(POSITION_SCHEMA)
    body = struct.pack("<I", len(positions))
    for pos in positions:
        values = []
        for i, _field in enumerate(POSITION_SCHEMA):
            name = POSITION_FIELD_NAMES[i]
            values.append(pos.get(name, 0))
        record_bytes = bytearray(pos_size)
        offset = 0
        for i, field in enumerate(POSITION_SCHEMA):
            name = POSITION_FIELD_NAMES[i]
            val = pos.get(name, 0)
            prop_type = field["propType"]
            prop_length = field.get("propLength")
            if prop_type == 17:  # I64
                record_bytes[offset : offset + 8] = int(val).to_bytes(8, "little", signed=True)
                offset += 8
            elif prop_type == 18:  # U64
                record_bytes[offset : offset + 8] = int(val).to_bytes(8, "little", signed=False)
                offset += 8
            elif prop_type == 6:  # U32
                struct.pack_into("<I", record_bytes, offset, int(val))
                offset += 4
            elif prop_type == 8:  # F64
                struct.pack_into("<d", record_bytes, offset, float(val))
                offset += 8
            elif prop_type == 11:  # FIXED_STRING
                from pymt5.helpers import encode_utf16le

                encoded = encode_utf16le(str(val), prop_length)
                record_bytes[offset : offset + prop_length] = encoded
                offset += prop_length
            elif prop_type == 3:  # I32
                struct.pack_into("<i", record_bytes, offset, int(val))
                offset += 4
        body += bytes(record_bytes)
    return body


def _build_order_body(orders: list[dict]) -> bytes:
    """Build a binary body for orders (4-byte count + serialized records)."""
    order_size = get_series_size(ORDER_SCHEMA)
    body = struct.pack("<I", len(orders))
    for order in orders:
        record_bytes = bytearray(order_size)
        offset = 0
        for i, field in enumerate(ORDER_SCHEMA):
            name = ORDER_FIELD_NAMES[i]
            val = order.get(name, 0)
            prop_type = field["propType"]
            prop_length = field.get("propLength")
            if prop_type == 17:  # I64
                record_bytes[offset : offset + 8] = int(val).to_bytes(8, "little", signed=True)
                offset += 8
            elif prop_type == 18:  # U64
                record_bytes[offset : offset + 8] = int(val).to_bytes(8, "little", signed=False)
                offset += 8
            elif prop_type == 6:  # U32
                struct.pack_into("<I", record_bytes, offset, int(val))
                offset += 4
            elif prop_type == 8:  # F64
                struct.pack_into("<d", record_bytes, offset, float(val))
                offset += 8
            elif prop_type == 11:  # FIXED_STRING
                from pymt5.helpers import encode_utf16le

                encoded = encode_utf16le(str(val), prop_length)
                record_bytes[offset : offset + prop_length] = encoded
                offset += prop_length
            elif prop_type == 3:  # I32
                struct.pack_into("<i", record_bytes, offset, int(val))
                offset += 4
        body += bytes(record_bytes)
    return body


def _build_deal_body(deals: list[dict]) -> bytes:
    """Build a binary body for deals (4-byte count + serialized records)."""
    deal_size = get_series_size(DEAL_SCHEMA)
    body = struct.pack("<I", len(deals))
    for deal in deals:
        record_bytes = bytearray(deal_size)
        offset = 0
        for i, field in enumerate(DEAL_SCHEMA):
            name = DEAL_FIELD_NAMES[i]
            val = deal.get(name, 0)
            prop_type = field["propType"]
            prop_length = field.get("propLength")
            if prop_type == 17:  # I64
                record_bytes[offset : offset + 8] = int(val).to_bytes(8, "little", signed=True)
                offset += 8
            elif prop_type == 18:  # U64
                record_bytes[offset : offset + 8] = int(val).to_bytes(8, "little", signed=False)
                offset += 8
            elif prop_type == 6:  # U32
                struct.pack_into("<I", record_bytes, offset, int(val))
                offset += 4
            elif prop_type == 8:  # F64
                struct.pack_into("<d", record_bytes, offset, float(val))
                offset += 8
            elif prop_type == 11:  # FIXED_STRING
                from pymt5.helpers import encode_utf16le

                encoded = encode_utf16le(str(val), prop_length)
                record_bytes[offset : offset + prop_length] = encoded
                offset += prop_length
            elif prop_type == 3:  # I32
                struct.pack_into("<i", record_bytes, offset, int(val))
                offset += 4
        body += bytes(record_bytes)
    return body


def _build_trade_response_body(
    retcode: int = TRADE_RETCODE_DONE,
    deal: int = 0,
    order: int = 0,
    volume: int = 0,
    price: float = 0.0,
    bid: float = 0.0,
    ask: float = 0.0,
    comment: str = "",
    request_id: int = 0,
) -> bytes:
    """Build a full trade response body matching TRADE_RESPONSE_SCHEMA."""
    from pymt5.helpers import encode_utf16le

    body = struct.pack("<I", retcode)
    body += int(deal).to_bytes(8, "little", signed=False)
    body += int(order).to_bytes(8, "little", signed=False)
    body += int(volume).to_bytes(8, "little", signed=False)
    body += struct.pack("<d", price)
    body += struct.pack("<d", bid)
    body += struct.pack("<d", ask)
    body += encode_utf16le(comment, 64)
    body += struct.pack("<I", request_id)
    return body


def _mock_account(
    balance: float = 10000.0,
    equity: float = 10000.0,
    profit: float = 0.0,
    margin: float = 0.0,
    margin_free: float = 10000.0,
    margin_level: float = 0.0,
    currency: str = "USD",
    leverage: int = 100,
) -> dict:
    return {
        "balance": balance,
        "equity": equity,
        "profit": profit,
        "margin": margin,
        "margin_free": margin_free,
        "margin_level": margin_level,
        "currency": currency,
        "leverage": leverage,
    }


def _mock_symbol_info_record(
    trade_symbol: str = "EURUSD",
    digits: int = 5,
    trade_calc_mode: int = 0,
    currency_profit: str = "USD",
    currency_margin: str = "EUR",
    contract_size: float = 100000.0,
    tick_size: float = 0.00001,
    tick_value: float = 1.0,
    point: float = 0.00001,
    trade_mode: int = 4,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    volume_step: float = 0.01,
    trade_stops_level: int = 0,
    filling_mode: int = 3,
) -> dict:
    return {
        "trade_symbol": trade_symbol,
        "digits": digits,
        "trade_calc_mode": trade_calc_mode,
        "currency_profit": currency_profit,
        "currency_margin": currency_margin,
        "contract_size": contract_size,
        "tick_size": tick_size,
        "tick_value": tick_value,
        "point": point,
        "trade_mode": trade_mode,
        "volume_min": volume_min,
        "volume_max": volume_max,
        "volume_step": volume_step,
        "trade_stops_level": trade_stops_level,
        "filling_mode": filling_mode,
    }


# =====================================================================
# Lines 117-124: get_positions_and_orders()
# =====================================================================


async def test_get_positions_and_orders_basic():
    """Test get_positions_and_orders with one position and one order."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {
                "position_id": 12345,
                "trade_symbol": "EURUSD",
                "trade_action": POSITION_TYPE_BUY,
                "price_open": 1.1234,
                "trade_volume": 100000000,
            }
        ]
    )
    order_body = _build_order_body(
        [
            {
                "trade_order": 99999,
                "trade_symbol": "GBPUSD",
                "order_type": ORDER_TYPE_BUY_LIMIT,
                "price_order": 1.2500,
            }
        ]
    )
    combined_body = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined_body)
    )
    result = await client.get_positions_and_orders()
    assert "positions" in result
    assert "orders" in result
    assert len(result["positions"]) == 1
    assert len(result["orders"]) == 1
    assert result["positions"][0]["position_id"] == 12345
    assert result["orders"][0]["trade_order"] == 99999


async def test_get_positions_and_orders_empty():
    """Test with zero positions and zero orders."""
    client = _make_client()
    body = struct.pack("<I", 0) + struct.pack("<I", 0)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=body)
    )
    result = await client.get_positions_and_orders()
    assert result["positions"] == []
    assert result["orders"] == []


# =====================================================================
# Lines 129-144: get_trade_history()
# =====================================================================


async def test_get_trade_history_basic():
    """Test get_trade_history with one deal and one order."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {
                "deal": 100,
                "trade_order": 200,
                "trade_symbol": "EURUSD",
                "time_create": 1700000,
                "time_update": 1700001,
                "time_create_ms": 500,
                "time_update_ms": 600,
            }
        ]
    )
    order_body = _build_order_body(
        [
            {
                "trade_order": 300,
                "trade_symbol": "GBPUSD",
            }
        ]
    )
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.get_trade_history(0, 0)
    assert "deals" in result
    assert "orders" in result
    assert len(result["deals"]) == 1
    assert len(result["orders"]) == 1
    deal = result["deals"][0]
    assert deal["deal"] == 100
    # time_create = original_seconds * 1000 + ms
    assert deal["time_create"] == 1700000 * 1000 + 500
    assert deal["time_update"] == 1700001 * 1000 + 600


async def test_get_trade_history_empty():
    """Test with zero deals and zero orders."""
    client = _make_client()
    body = struct.pack("<I", 0) + struct.pack("<I", 0)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=body)
    )
    result = await client.get_trade_history()
    assert result["deals"] == []
    assert result["orders"] == []


# =====================================================================
# Lines 153-154: get_orders() (calls get_positions_and_orders)
# =====================================================================


async def test_get_orders():
    """Test get_orders returns only orders part."""
    client = _make_client()
    pos_body = _build_position_body([])
    order_body = _build_order_body([{"trade_order": 555, "trade_symbol": "USDJPY"}])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    orders = await client.get_orders()
    assert len(orders) == 1
    assert orders[0]["trade_order"] == 555


# =====================================================================
# Lines 158-159: get_deals()
# =====================================================================


async def test_get_deals():
    """Test get_deals returns only deals part."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {
                "deal": 777,
                "trade_symbol": "EURUSD",
                "time_create": 1000,
                "time_update": 1001,
            }
        ]
    )
    order_body = _build_order_body([])
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    deals = await client.get_deals()
    assert len(deals) == 1
    assert deals[0]["deal"] == 777


# =====================================================================
# Line 163: positions_total()
# =====================================================================


async def test_positions_total():
    """Test positions_total returns count of positions."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {"position_id": 1, "trade_symbol": "EURUSD"},
            {"position_id": 2, "trade_symbol": "GBPUSD"},
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    total = await client.positions_total()
    assert total == 2


# =====================================================================
# Lines 174-179, 183: positions_get() filters
# =====================================================================


async def test_positions_get_by_symbol():
    """Test positions_get filtered by symbol."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {"position_id": 1, "trade_symbol": "EURUSD"},
            {"position_id": 2, "trade_symbol": "GBPUSD"},
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.positions_get(symbol="EURUSD")
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "EURUSD"


async def test_positions_get_by_group():
    """Test positions_get filtered by group mask."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {"position_id": 1, "trade_symbol": "EURUSD"},
            {"position_id": 2, "trade_symbol": "EURJPY"},
            {"position_id": 3, "trade_symbol": "GBPUSD"},
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.positions_get(group="EUR*")
    assert len(result) == 2


async def test_positions_get_by_ticket():
    """Test positions_get filtered by ticket."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {"position_id": 1001, "trade_symbol": "EURUSD"},
            {"position_id": 1002, "trade_symbol": "GBPUSD"},
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.positions_get(ticket=1002)
    assert len(result) == 1
    assert result[0]["position_id"] == 1002


async def test_positions_get_no_filter():
    """Test positions_get with no filter returns all."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {"position_id": 1, "trade_symbol": "EURUSD"},
            {"position_id": 2, "trade_symbol": "GBPUSD"},
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.positions_get()
    assert len(result) == 2


# =====================================================================
# Line 183: orders_total()
# =====================================================================


async def test_orders_total():
    """Test orders_total returns count of pending orders."""
    client = _make_client()
    pos_body = _build_position_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 1, "trade_symbol": "EURUSD"},
        ]
    )
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    total = await client.orders_total()
    assert total == 1


# =====================================================================
# Lines 192-199: orders_get() filters
# =====================================================================


async def test_orders_get_by_symbol():
    """Test orders_get filtered by symbol."""
    client = _make_client()
    pos_body = _build_position_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 1, "trade_symbol": "EURUSD"},
            {"trade_order": 2, "trade_symbol": "GBPUSD"},
        ]
    )
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.orders_get(symbol="GBPUSD")
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "GBPUSD"


async def test_orders_get_by_group():
    """Test orders_get filtered by group mask."""
    client = _make_client()
    pos_body = _build_position_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 1, "trade_symbol": "EURUSD"},
            {"trade_order": 2, "trade_symbol": "EURJPY"},
            {"trade_order": 3, "trade_symbol": "GBPUSD"},
        ]
    )
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.orders_get(group="EUR*")
    assert len(result) == 2


async def test_orders_get_by_ticket():
    """Test orders_get filtered by ticket."""
    client = _make_client()
    pos_body = _build_position_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 5001, "trade_symbol": "EURUSD"},
            {"trade_order": 5002, "trade_symbol": "GBPUSD"},
        ]
    )
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.orders_get(ticket=5001)
    assert len(result) == 1
    assert result[0]["trade_order"] == 5001


async def test_orders_get_no_filter():
    """Test orders_get with no filter returns all."""
    client = _make_client()
    pos_body = _build_position_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 1, "trade_symbol": "EURUSD"},
            {"trade_order": 2, "trade_symbol": "GBPUSD"},
        ]
    )
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client.orders_get()
    assert len(result) == 2


# =====================================================================
# Lines 211-222, 230: history_orders_get / history_orders_total
# =====================================================================


async def test_history_orders_get_no_filter():
    """Test history_orders_get returns all historical orders."""
    client = _make_client()
    deal_body = _build_deal_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 8001, "trade_symbol": "EURUSD", "position_id": 100},
            {"trade_order": 8002, "trade_symbol": "GBPUSD", "position_id": 200},
        ]
    )
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_orders_get()
    assert len(result) == 2


async def test_history_orders_get_by_group():
    """Test history_orders_get filtered by group."""
    client = _make_client()
    deal_body = _build_deal_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 8001, "trade_symbol": "EURUSD"},
            {"trade_order": 8002, "trade_symbol": "GBPUSD"},
        ]
    )
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_orders_get(group="GBP*")
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "GBPUSD"


async def test_history_orders_get_by_ticket():
    """Test history_orders_get filtered by ticket."""
    client = _make_client()
    deal_body = _build_deal_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 8001, "trade_symbol": "EURUSD"},
            {"trade_order": 8002, "trade_symbol": "GBPUSD"},
        ]
    )
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_orders_get(ticket=8002)
    assert len(result) == 1
    assert result[0]["trade_order"] == 8002


async def test_history_orders_get_by_position():
    """Test history_orders_get filtered by position ID."""
    client = _make_client()
    deal_body = _build_deal_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 8001, "trade_symbol": "EURUSD", "position_id": 100},
            {"trade_order": 8002, "trade_symbol": "GBPUSD", "position_id": 200},
        ]
    )
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_orders_get(position=200)
    assert len(result) == 1
    assert result[0]["position_id"] == 200


async def test_history_orders_total():
    """Test history_orders_total returns count."""
    client = _make_client()
    deal_body = _build_deal_body([])
    order_body = _build_order_body(
        [
            {"trade_order": 1, "trade_symbol": "EURUSD"},
            {"trade_order": 2, "trade_symbol": "GBPUSD"},
            {"trade_order": 3, "trade_symbol": "USDJPY"},
        ]
    )
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    total = await client.history_orders_total()
    assert total == 3


# =====================================================================
# Lines 248, 257: history_deals_get / history_deals_total
# =====================================================================


async def test_history_deals_get_by_position():
    """Test history_deals_get filtered by position."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {
                "deal": 1,
                "trade_order": 100,
                "trade_symbol": "EURUSD",
                "position_id": 50,
                "time_create": 1000,
                "time_update": 1001,
            },
            {
                "deal": 2,
                "trade_order": 101,
                "trade_symbol": "GBPUSD",
                "position_id": 60,
                "time_create": 1000,
                "time_update": 1001,
            },
        ]
    )
    order_body = _build_order_body([])
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_deals_get(position=50)
    assert len(result) == 1
    assert result[0]["deal"] == 1


async def test_history_deals_get_by_group():
    """Test history_deals_get filtered by group."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {"deal": 1, "trade_order": 100, "trade_symbol": "EURUSD", "time_create": 1000, "time_update": 1001},
            {"deal": 2, "trade_order": 101, "trade_symbol": "GBPUSD", "time_create": 1000, "time_update": 1001},
        ]
    )
    order_body = _build_order_body([])
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_deals_get(group="GBP*")
    assert len(result) == 1


async def test_history_deals_get_by_ticket():
    """Test history_deals_get filtered by ticket."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {"deal": 1, "trade_order": 100, "trade_symbol": "EURUSD", "time_create": 1000, "time_update": 1001},
            {"deal": 2, "trade_order": 200, "trade_symbol": "GBPUSD", "time_create": 1000, "time_update": 1001},
        ]
    )
    order_body = _build_order_body([])
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_deals_get(ticket=200)
    assert len(result) == 1
    assert result[0]["trade_order"] == 200


async def test_history_deals_total():
    """Test history_deals_total returns count."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {"deal": 1, "trade_order": 100, "trade_symbol": "EURUSD", "time_create": 1000, "time_update": 1001},
        ]
    )
    order_body = _build_order_body([])
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    total = await client.history_deals_total()
    assert total == 1


# =====================================================================
# Lines 271-312: order_calc_profit()
# =====================================================================


async def test_order_calc_profit_unsupported_action():
    """Test order_calc_profit with unsupported action type returns None."""
    client = _make_client()
    result = await client.order_calc_profit(action=99, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2)
    assert result is None
    assert client._last_error[0] == -1


async def test_order_calc_profit_symbol_not_found():
    """Test order_calc_profit when symbol not found."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=None)
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="NOSYMBOL", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result is None
    assert client._last_error[0] == -2


async def test_order_calc_profit_no_account_currency():
    """Test order_calc_profit when account has no currency."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    client.get_account = AsyncMock(return_value={"currency": ""})
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result is None
    assert client._last_error[0] == -3


async def test_order_calc_profit_raw_returns_none():
    """Test order_calc_profit when _calc_profit_raw returns None."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    client.get_account = AsyncMock(return_value=_mock_account())
    client._calc_profit_raw = MagicMock(return_value=None)
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result is None


async def test_order_calc_profit_zero_profit():
    """Test order_calc_profit when profit is zero clears error."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    client.get_account = AsyncMock(return_value=_mock_account())
    client._calc_profit_raw = MagicMock(return_value=0.0)
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.1
    )
    assert result == 0.0
    assert client._last_error == (0, "")


async def test_order_calc_profit_missing_currency_profit():
    """Test order_calc_profit when symbol has no currency_profit."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account())
    client._calc_profit_raw = MagicMock(return_value=100.0)
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result is None
    assert client._last_error[0] == -4


async def test_order_calc_profit_same_currency():
    """Test order_calc_profit when profit currency matches account currency."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="USD")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_profit_raw = MagicMock(return_value=150.0)
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result == 150.0
    assert client._last_error == (0, "")


async def test_order_calc_profit_conversion_needed_forex():
    """Test order_calc_profit with currency conversion for forex mode."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="EUR", trade_calc_mode=0)
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_profit_raw = MagicMock(return_value=100.0)
    client._resolve_conversion_rates = AsyncMock(return_value=(1.1, 1.09))
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    # For forex mode, buy uses rate_buy: 100.0 * 1.1
    assert result == pytest.approx(110.0)
    assert client._last_error == (0, "")


async def test_order_calc_profit_conversion_needed_non_forex():
    """Test order_calc_profit with currency conversion for non-forex mode."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="EUR", trade_calc_mode=2)  # CFD mode
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_profit_raw = MagicMock(return_value=100.0)
    client._resolve_conversion_rates = AsyncMock(return_value=(1.1, 1.09))
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="XAUUSD", volume=1.0, price_open=1900.0, price_close=1910.0
    )
    # For non-forex mode, positive profit uses rate_sell: 100.0 * 1.09
    assert result == pytest.approx(109.0)


async def test_order_calc_profit_conversion_needed_non_forex_negative():
    """Test order_calc_profit non-forex mode with negative profit uses rate_buy."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="EUR", trade_calc_mode=2)
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_profit_raw = MagicMock(return_value=-100.0)
    client._resolve_conversion_rates = AsyncMock(return_value=(1.1, 1.09))
    result = await client.order_calc_profit(
        action=ORDER_TYPE_SELL, symbol="XAUUSD", volume=1.0, price_open=1910.0, price_close=1900.0
    )
    # For non-forex mode, negative profit uses rate_buy: -100.0 * 1.1
    assert result == pytest.approx(-110.0)


async def test_order_calc_profit_conversion_rates_none():
    """Test order_calc_profit when conversion rates unavailable."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="EUR")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="JPY"))
    client._calc_profit_raw = MagicMock(return_value=100.0)
    client._resolve_conversion_rates = AsyncMock(return_value=None)
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result is None
    assert client._last_error[0] == -5


async def test_order_calc_profit_exception_path():
    """Test order_calc_profit catches exceptions and returns error."""
    client = _make_client()
    client.symbol_info = AsyncMock(side_effect=ValueError("test error"))
    result = await client.order_calc_profit(
        action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price_open=1.1, price_close=1.2
    )
    assert result is None
    assert client._last_error[0] == -99


async def test_order_calc_profit_sell_side_forex():
    """Test order_calc_profit with sell action uses rate_sell for forex."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_profit="EUR", trade_calc_mode=0)
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_profit_raw = MagicMock(return_value=50.0)
    client._resolve_conversion_rates = AsyncMock(return_value=(1.1, 1.09))
    result = await client.order_calc_profit(
        action=ORDER_TYPE_SELL, symbol="EURUSD", volume=1.0, price_open=1.2, price_close=1.1
    )
    # For forex mode, sell uses rate_sell: 50.0 * 1.09
    assert result == pytest.approx(54.5)


# =====================================================================
# Lines 325-360: order_calc_margin()
# =====================================================================


async def test_order_calc_margin_unsupported_action():
    """Test order_calc_margin with unsupported action type."""
    client = _make_client()
    result = await client.order_calc_margin(action=99, symbol="EURUSD", volume=1.0, price=1.1)
    assert result is None
    assert client._last_error[0] == -1


async def test_order_calc_margin_symbol_not_found():
    """Test order_calc_margin when symbol not found."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=None)
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="NOSYMBOL", volume=1.0, price=1.1)
    assert result is None
    assert client._last_error[0] == -2


async def test_order_calc_margin_no_account_currency():
    """Test order_calc_margin when account has no currency."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    client.get_account = AsyncMock(return_value={"currency": ""})
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    assert result is None
    assert client._last_error[0] == -3


async def test_order_calc_margin_raw_returns_none():
    """Test order_calc_margin when _calc_margin_raw returns None."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    client.get_account = AsyncMock(return_value=_mock_account())
    client._calc_margin_raw = MagicMock(return_value=None)
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    assert result is None


async def test_order_calc_margin_zero_margin():
    """Test order_calc_margin when margin is zero clears error."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    client.get_account = AsyncMock(return_value=_mock_account())
    client._calc_margin_raw = MagicMock(return_value=0.0)
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    assert result == 0.0
    assert client._last_error == (0, "")


async def test_order_calc_margin_same_currency():
    """Test order_calc_margin when margin currency matches account currency."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_margin="USD")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_margin_raw = MagicMock(return_value=1000.0)
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    assert result == 1000.0
    assert client._last_error == (0, "")


async def test_order_calc_margin_conversion_needed():
    """Test order_calc_margin with currency conversion needed."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_margin="EUR")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_margin_raw = MagicMock(return_value=1000.0)
    client._resolve_conversion_rates = AsyncMock(return_value=(1.1, 1.09))
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    # BUY uses rate_buy: 1000.0 * 1.1
    assert result == pytest.approx(1100.0)
    assert client._last_error == (0, "")


async def test_order_calc_margin_conversion_sell():
    """Test order_calc_margin with sell uses rate_sell."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_margin="EUR")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_margin_raw = MagicMock(return_value=1000.0)
    client._resolve_conversion_rates = AsyncMock(return_value=(1.1, 1.09))
    result = await client.order_calc_margin(action=ORDER_TYPE_SELL, symbol="EURUSD", volume=1.0, price=1.1)
    # SELL uses rate_sell: 1000.0 * 1.09
    assert result == pytest.approx(1090.0)


async def test_order_calc_margin_conversion_rates_none():
    """Test order_calc_margin when conversion rates unavailable."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_margin="EUR")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="JPY"))
    client._calc_margin_raw = MagicMock(return_value=1000.0)
    client._resolve_conversion_rates = AsyncMock(return_value=None)
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    assert result is None
    assert client._last_error[0] == -6


async def test_order_calc_margin_exception_path():
    """Test order_calc_margin catches exceptions and returns error."""
    client = _make_client()
    client.symbol_info = AsyncMock(side_effect=TypeError("test error"))
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    assert result is None
    assert client._last_error[0] == -99


async def test_order_calc_margin_empty_margin_currency_uses_account():
    """Test that empty margin currency falls back to account currency."""
    client = _make_client()
    info = _mock_symbol_info_record(currency_margin="")
    client.symbol_info = AsyncMock(return_value=info)
    client.get_account = AsyncMock(return_value=_mock_account(currency="USD"))
    client._calc_margin_raw = MagicMock(return_value=500.0)
    result = await client.order_calc_margin(action=ORDER_TYPE_BUY, symbol="EURUSD", volume=1.0, price=1.1)
    # margin_currency falls back to account_currency "USD", which matches, so no conversion
    assert result == 500.0


# =====================================================================
# Lines 390-413: trade_request()
# =====================================================================


async def test_trade_request_basic():
    """Test trade_request serializes and sends correctly."""
    client = _make_client()
    resp_body = _build_trade_response_body(
        retcode=TRADE_RETCODE_DONE,
        deal=123,
        order=456,
        volume=100000000,
        price=1.12345,
        bid=1.12340,
        ask=1.12350,
        comment="filled",
        request_id=1,
    )
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.trade_request(
        trade_action=TRADE_ACTION_DEAL,
        symbol="EURUSD",
        volume=100000000,
        trade_type=ORDER_TYPE_BUY,
        price_order=1.12345,
    )
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.success is True
    assert result.deal == 123
    assert result.order == 456


async def test_trade_request_volume_validation():
    """Test trade_request raises on zero volume for DEAL action."""
    client = _make_client()
    with pytest.raises(ValueError, match="volume must be > 0"):
        await client.trade_request(trade_action=TRADE_ACTION_DEAL, volume=0)


async def test_trade_request_pending_no_price():
    """Test trade_request raises on zero price for pending orders."""
    client = _make_client()
    with pytest.raises(ValueError, match="price_order must be > 0"):
        await client.trade_request(trade_action=TRADE_ACTION_PENDING, volume=100, price_order=0.0)


async def test_trade_request_pending_valid():
    """Test trade_request with valid pending order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.trade_request(
        trade_action=TRADE_ACTION_PENDING,
        symbol="EURUSD",
        volume=100000000,
        trade_type=ORDER_TYPE_BUY_LIMIT,
        price_order=1.1000,
    )
    assert result.retcode == TRADE_RETCODE_PLACED
    assert result.success is True


# =====================================================================
# Lines 488-498: order_check() exception path
# =====================================================================


async def test_order_check_exception_path():
    """Test order_check catches exception and returns error dict."""
    client = _make_client()
    # Make _normalize_order_request raise an exception
    client._normalize_order_request = MagicMock(side_effect=ValueError("bad request"))
    client.get_account = AsyncMock(return_value=_mock_account())
    result = await client.order_check({"symbol": "EURUSD", "action": 1})
    assert result["retcode"] == -99
    assert "bad request" in result["comment"]
    assert "balance" in result
    assert "equity" in result
    assert "request" in result
    assert client._last_error[0] == -99


# =====================================================================
# Lines 519-520: _normalize_order_request stop-limit handling
# =====================================================================


def test_normalize_order_request_stop_limit():
    """Test stop-limit price normalization in _normalize_order_request."""
    client = _make_client()
    request = {
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY_STOP_LIMIT,
        "price": 1.15,  # trigger price
        "stoplimit": 1.14,  # limit price
        "action": TRADE_ACTION_PENDING,
        "volume": 1.0,
    }
    normalized = client._normalize_order_request(request)
    # For stop-limit, price_order = stoplimit, price_trigger = price
    assert normalized["price_order"] == 1.14
    assert normalized["price_trigger"] == 1.15


def test_normalize_order_request_sell_stop_limit():
    """Test sell stop-limit price normalization."""
    client = _make_client()
    request = {
        "symbol": "EURUSD",
        "type": ORDER_TYPE_SELL_STOP_LIMIT,
        "price": 1.10,
        "stoplimit": 1.11,
        "action": TRADE_ACTION_PENDING,
        "volume": 1.0,
    }
    normalized = client._normalize_order_request(request)
    assert normalized["price_order"] == 1.11
    assert normalized["price_trigger"] == 1.10


def test_normalize_order_request_regular_order():
    """Test regular order (non stop-limit) normalization."""
    client = _make_client()
    request = {
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "price": 1.12,
        "action": TRADE_ACTION_DEAL,
        "volume": 1.0,
    }
    normalized = client._normalize_order_request(request)
    assert normalized["price_order"] == 1.12
    assert normalized["price_trigger"] == 0.0


# =====================================================================
# Lines 549-601: _validate_order_check_request() - many validation branches
# =====================================================================


async def test_validate_no_symbol():
    """Test validation fails on empty symbol for DEAL action."""
    client = _make_client()
    request = {"action": TRADE_ACTION_DEAL, "symbol": "", "type": ORDER_TYPE_BUY, "volume": 1.0}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID


async def test_validate_symbol_not_found():
    """Test validation fails when symbol not found."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=None)
    request = {"action": TRADE_ACTION_DEAL, "symbol": "NOSYM", "type": ORDER_TYPE_BUY, "volume": 1.0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID
    assert "symbol not found" in comment


async def test_validate_zero_volume():
    """Test validation fails on zero volume."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 0.0}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_VOLUME


async def test_validate_invalid_order_type():
    """Test validation fails on invalid order type (not buy or sell)."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": 99, "volume": 1.0}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_ORDER


async def test_validate_trade_disabled():
    """Test validation fails when trade mode is disabled."""
    client = _make_client()
    info = _mock_symbol_info_record(trade_mode=0)
    client.symbol_info = AsyncMock(return_value=info)
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 1.0}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_TRADE_DISABLED


async def test_validate_long_only_rejects_sell():
    """Test validation rejects sell for long-only symbols."""
    client = _make_client()
    info = _mock_symbol_info_record(trade_mode=1)  # long only
    client.symbol_info = AsyncMock(return_value=info)
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_SELL, "volume": 1.0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_ORDER
    assert "long-only" in comment


async def test_validate_short_only_rejects_buy():
    """Test validation rejects buy for short-only symbols."""
    client = _make_client()
    info = _mock_symbol_info_record(trade_mode=2)  # short only
    client.symbol_info = AsyncMock(return_value=info)
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 1.0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_ORDER
    assert "short-only" in comment


async def test_validate_close_only():
    """Test validation fails for close-only symbols."""
    client = _make_client()
    info = _mock_symbol_info_record(trade_mode=3)  # close only
    client.symbol_info = AsyncMock(return_value=info)
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 1.0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_TRADE_DISABLED
    assert "close-only" in comment


async def test_validate_expiration_required():
    """Test validation fails when expiration is required but not set."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "type_time": ORDER_TIME_SPECIFIED,
        "expiration": 0,
        "price_order": 1.1,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_EXPIRATION


async def test_validate_expiration_specified_day():
    """Test expiration required for ORDER_TIME_SPECIFIED_DAY."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "type_time": ORDER_TIME_SPECIFIED_DAY,
        "expiration": 0,
        "price_order": 1.1,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_EXPIRATION


async def test_validate_invalid_filling():
    """Test validation fails on invalid filling type."""
    client = _make_client()
    info = _mock_symbol_info_record(filling_mode=7)  # non-zero fill flags
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "type_filling": 99,
        "price_order": 1.1,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_FILL


async def test_validate_invalid_price():
    """Test validation fails when price resolves to zero."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value=None)
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "price_order": 0.0,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_PRICE


async def test_validate_pending_no_price_order():
    """Test pending order validation fails when price_order is zero."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    request = {
        "action": TRADE_ACTION_PENDING,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY_LIMIT,
        "volume": 1.0,
        "price_order": 0.0,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_PRICE


async def test_validate_stops_error():
    """Test validation fails when stops are too close."""
    client = _make_client()
    info = _mock_symbol_info_record(trade_stops_level=100, point=0.00001)
    client.symbol_info = AsyncMock(return_value=info)
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "price_order": 1.10000,
        "sl": 1.09999,  # sl too close (within 100*0.00001=0.001)
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID_STOPS


async def test_validate_sltp_action():
    """Test validation for SLTP action requires position."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_SLTP, "symbol": "EURUSD", "position": 0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID
    assert "position is required" in comment


async def test_validate_sltp_action_valid():
    """Test SLTP action passes with valid position."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_SLTP, "symbol": "EURUSD", "position": 12345}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == 0


async def test_validate_modify_action_no_order():
    """Test MODIFY action requires order."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_MODIFY, "symbol": "EURUSD", "order": 0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID
    assert "order is required" in comment


async def test_validate_remove_action_no_order():
    """Test REMOVE action requires order."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_REMOVE, "symbol": "EURUSD", "order": 0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID
    assert "order is required" in comment


async def test_validate_close_by_action():
    """Test CLOSE_BY action requires both position and position_by."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_CLOSE_BY, "symbol": "EURUSD", "position": 0, "position_by": 0}
    retcode, comment = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID
    assert "position and position_by" in comment


async def test_validate_close_by_action_valid():
    """Test CLOSE_BY passes when both positions provided."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {"action": TRADE_ACTION_CLOSE_BY, "symbol": "EURUSD", "position": 100, "position_by": 200}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == 0


async def test_validate_unknown_action():
    """Test unknown action returns INVALID."""
    client = _make_client()
    request = {"action": 999}
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == TRADE_RETCODE_INVALID


async def test_validate_deal_passes():
    """Test full valid DEAL request passes validation."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "price_order": 1.1,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == 0


# =====================================================================
# Lines 607-640: _estimate_order_margin / _resolve_order_check_price
# =====================================================================


async def test_estimate_order_margin_non_deal():
    """Test _estimate_order_margin returns 0.0 for non-deal actions."""
    client = _make_client()
    request = {"action": TRADE_ACTION_SLTP}
    result = await client._estimate_order_margin(request)
    assert result == 0.0


async def test_estimate_order_margin_zero_price():
    """Test _estimate_order_margin returns 0.0 when price resolves to zero."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=None)
    request = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 1.0}
    result = await client._estimate_order_margin(request)
    assert result == 0.0


async def test_estimate_order_margin_with_price():
    """Test _estimate_order_margin calls order_calc_margin."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.order_calc_margin = AsyncMock(return_value=1000.0)
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "price_order": 1.1,
    }
    result = await client._estimate_order_margin(request)
    assert result == 1000.0


async def test_resolve_order_check_price_no_symbol_info():
    """Test _resolve_order_check_price returns 0.0 when symbol_info is None."""
    client = _make_client()
    result = await client._resolve_order_check_price({"action": TRADE_ACTION_DEAL}, None)
    assert result == 0.0


async def test_resolve_order_check_price_pending():
    """Test _resolve_order_check_price for pending order returns price_order."""
    client = _make_client()
    info = _mock_symbol_info_record()
    request = {"action": TRADE_ACTION_PENDING, "type": ORDER_TYPE_BUY_LIMIT, "price_order": 1.09}
    result = await client._resolve_order_check_price(request, info)
    assert result == 1.09


async def test_resolve_order_check_price_pending_stop_limit():
    """Test _resolve_order_check_price for stop-limit pending returns price_trigger."""
    client = _make_client()
    info = _mock_symbol_info_record()
    request = {
        "action": TRADE_ACTION_PENDING,
        "type": ORDER_TYPE_BUY_STOP_LIMIT,
        "price_order": 1.09,
        "price_trigger": 1.10,
    }
    result = await client._resolve_order_check_price(request, info)
    assert result == 1.10


async def test_resolve_order_check_price_deal_with_price():
    """Test _resolve_order_check_price for deal with explicit price."""
    client = _make_client()
    info = _mock_symbol_info_record()
    request = {"action": TRADE_ACTION_DEAL, "type": ORDER_TYPE_BUY, "price_order": 1.12}
    result = await client._resolve_order_check_price(request, info)
    assert result == 1.12


async def test_resolve_order_check_price_deal_from_tick_buy():
    """Test _resolve_order_check_price for deal uses ask for buy from tick."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.11, "bid": 1.10})
    request = {"action": TRADE_ACTION_DEAL, "type": ORDER_TYPE_BUY, "price_order": 0.0, "symbol": "EURUSD"}
    result = await client._resolve_order_check_price(request, info)
    assert result == 1.11


async def test_resolve_order_check_price_deal_from_tick_sell():
    """Test _resolve_order_check_price for deal uses bid for sell from tick."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.11, "bid": 1.10})
    request = {"action": TRADE_ACTION_DEAL, "type": ORDER_TYPE_SELL, "price_order": 0.0, "symbol": "EURUSD"}
    result = await client._resolve_order_check_price(request, info)
    assert result == 1.10


async def test_resolve_order_check_price_deal_no_tick():
    """Test _resolve_order_check_price returns 0.0 when no tick and no price."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info_tick = MagicMock(return_value=None)
    request = {"action": TRADE_ACTION_DEAL, "type": ORDER_TYPE_BUY, "price_order": 0.0, "symbol": "EURUSD"}
    result = await client._resolve_order_check_price(request, info)
    assert result == 0.0


# =====================================================================
# Lines 654-665: _parse_trade_response() - extended parse
# =====================================================================


def test_parse_trade_response_extended():
    """Test _parse_trade_response parses extended fields from full body."""
    client = _make_client()
    body = _build_trade_response_body(
        retcode=TRADE_RETCODE_DONE,
        deal=111,
        order=222,
        volume=100000000,
        price=1.12345,
        bid=1.12340,
        ask=1.12350,
        comment="ok",
        request_id=42,
    )
    result = client._parse_trade_response(body, "EURUSD", TRADE_ACTION_DEAL, 100000000)
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.success is True
    assert result.deal == 111
    assert result.order == 222
    assert result.price == 1.12345
    assert result.bid == 1.12340
    assert result.ask == 1.12350
    assert result.comment == "ok"
    assert result.request_id == 42


def test_parse_trade_response_extended_parse_failure():
    """Test _parse_trade_response falls back on extended parse error."""
    client = _make_client()
    # Build a body that is large enough but with corrupted data
    # Start with valid retcode, then garbage that is schema-sized
    resp_size = get_series_size(TRADE_RESPONSE_SCHEMA)
    body = struct.pack("<I", TRADE_RETCODE_DONE) + b"\xff" * (resp_size - 4)
    # This should parse the retcode from the first 4 bytes but might
    # fail on extended parse; the fallback catches the exception
    result = client._parse_trade_response(body, "EURUSD", TRADE_ACTION_DEAL, 100)
    # It should still get the retcode
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.success is True


def test_parse_trade_response_short_body():
    """Test _parse_trade_response with body shorter than schema size."""
    client = _make_client()
    # Only retcode, shorter than full schema
    body = struct.pack("<I", TRADE_RETCODE_DONE)
    result = client._parse_trade_response(body, "EURUSD", TRADE_ACTION_DEAL, 100)
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.success is True
    assert result.deal == 0  # not parsed from extended


# =====================================================================
# Lines 710-714: _resolve_digits()
# =====================================================================


def test_resolve_digits_from_symbols_cache():
    """Test _resolve_digits reads from _symbols cache."""
    client = _make_client()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    assert client._resolve_digits("EURUSD", None) == 5


def test_resolve_digits_explicit_overrides_cache():
    """Test _resolve_digits uses explicit digits over cache."""
    client = _make_client()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    assert client._resolve_digits("EURUSD", 3) == 3


def test_resolve_digits_unknown_symbol_default():
    """Test _resolve_digits defaults to 5 for unknown symbols."""
    client = _make_client()
    assert client._resolve_digits("UNKNOWN", None) == 5


# =====================================================================
# Lines 746+: High-level trading helpers
# =====================================================================


async def test_buy_market():
    """Test buy_market places a market buy order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.buy_market("EURUSD", 0.01)
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.success is True
    # Verify send_command was called
    client.transport.send_command.assert_awaited_once()


async def test_sell_market():
    """Test sell_market places a market sell order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.sell_market("EURUSD", 0.01)
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.success is True


async def test_buy_limit():
    """Test buy_limit places a buy limit order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.buy_limit("EURUSD", 0.01, 1.0900)
    assert result.retcode == TRADE_RETCODE_PLACED
    assert result.success is True


async def test_sell_limit():
    """Test sell_limit places a sell limit order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.sell_limit("EURUSD", 0.01, 1.1200)
    assert result.retcode == TRADE_RETCODE_PLACED
    assert result.success is True


async def test_buy_stop():
    """Test buy_stop places a buy stop order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.buy_stop("EURUSD", 0.01, 1.1200)
    assert result.retcode == TRADE_RETCODE_PLACED


async def test_sell_stop():
    """Test sell_stop places a sell stop order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.sell_stop("EURUSD", 0.01, 1.0800)
    assert result.retcode == TRADE_RETCODE_PLACED


async def test_buy_stop_limit():
    """Test buy_stop_limit places a buy stop limit order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.buy_stop_limit("EURUSD", 0.01, price=1.1200, stop_limit_price=1.1100)
    assert result.retcode == TRADE_RETCODE_PLACED


async def test_sell_stop_limit():
    """Test sell_stop_limit places a sell stop limit order."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_PLACED)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.sell_stop_limit("EURUSD", 0.01, price=1.0800, stop_limit_price=1.0900)
    assert result.retcode == TRADE_RETCODE_PLACED


# =====================================================================
# Lines 943-970: close_position / _detect_close_direction
# =====================================================================


async def test_close_position_with_explicit_order_type():
    """Test close_position with explicit order_type."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.close_position(
        "EURUSD",
        position_id=12345,
        volume=0.01,
        order_type=ORDER_TYPE_SELL,
    )
    assert result.retcode == TRADE_RETCODE_DONE


async def test_close_position_auto_detect_direction_buy():
    """Test close_position auto-detects direction for SELL position -> BUY."""
    client = _make_client()
    # Mock get_positions_and_orders to return a SELL position
    pos_body = _build_position_body(
        [
            {
                "position_id": 12345,
                "trade_symbol": "EURUSD",
                "trade_action": POSITION_TYPE_SELL,
            }
        ]
    )
    order_body = _build_order_body([])
    positions_response = pos_body + order_body

    # First call: get_positions_and_orders (for _detect_close_direction)
    # Second call: trade_request
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        side_effect=[
            CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=positions_response),
            CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body),
        ]
    )
    result = await client.close_position("EURUSD", position_id=12345, volume=0.01)
    assert result.retcode == TRADE_RETCODE_DONE


async def test_close_position_auto_detect_direction_sell():
    """Test close_position auto-detects direction for BUY position -> SELL."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {
                "position_id": 12345,
                "trade_symbol": "EURUSD",
                "trade_action": POSITION_TYPE_BUY,
            }
        ]
    )
    order_body = _build_order_body([])
    positions_response = pos_body + order_body

    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        side_effect=[
            CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=positions_response),
            CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body),
        ]
    )
    result = await client.close_position("EURUSD", position_id=12345, volume=0.01)
    assert result.retcode == TRADE_RETCODE_DONE


async def test_detect_close_direction_sell_position():
    """Test _detect_close_direction returns BUY for SELL position."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {
                "position_id": 999,
                "trade_symbol": "EURUSD",
                "trade_action": POSITION_TYPE_SELL,
            }
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client._detect_close_direction(999)
    assert result == ORDER_TYPE_BUY


async def test_detect_close_direction_buy_position():
    """Test _detect_close_direction returns SELL for BUY position."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {
                "position_id": 999,
                "trade_symbol": "EURUSD",
                "trade_action": POSITION_TYPE_BUY,
            }
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client._detect_close_direction(999)
    assert result == ORDER_TYPE_SELL


async def test_detect_close_direction_not_found():
    """Test _detect_close_direction defaults to SELL when position not found."""
    client = _make_client()
    pos_body = _build_position_body(
        [
            {
                "position_id": 111,
                "trade_symbol": "EURUSD",
                "trade_action": POSITION_TYPE_BUY,
            }
        ]
    )
    order_body = _build_order_body([])
    combined = pos_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=combined)
    )
    result = await client._detect_close_direction(999)
    assert result == ORDER_TYPE_SELL


# =====================================================================
# Lines 994-995: close_position_by()
# =====================================================================


async def test_close_position_by():
    """Test close_position_by sends a CLOSE_BY trade request."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.close_position_by("EURUSD", position_id=100, position_by=200)
    assert result.retcode == TRADE_RETCODE_DONE


# =====================================================================
# Line 1013: modify_position_sltp()
# =====================================================================


async def test_modify_position_sltp():
    """Test modify_position_sltp sends an SLTP trade request."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.modify_position_sltp("EURUSD", position_id=12345, sl=1.09, tp=1.15)
    assert result.retcode == TRADE_RETCODE_DONE


# =====================================================================
# Line 1033: modify_pending_order()
# =====================================================================


async def test_modify_pending_order():
    """Test modify_pending_order sends a MODIFY trade request."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.modify_pending_order("EURUSD", order=99999, price=1.09)
    assert result.retcode == TRADE_RETCODE_DONE


# =====================================================================
# Line 1046: cancel_pending_order()
# =====================================================================


async def test_cancel_pending_order():
    """Test cancel_pending_order sends a REMOVE trade request."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.cancel_pending_order(order=99999)
    assert result.retcode == TRADE_RETCODE_DONE


# =====================================================================
# order_check() full integration - covers margin check + no_money path
# =====================================================================


async def test_order_check_valid_request():
    """Test order_check with a fully valid request returns success."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    client.get_account = AsyncMock(
        return_value=_mock_account(
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
        )
    )
    client.order_calc_margin = AsyncMock(return_value=100.0)
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 0.01,
        "price": 1.1,
    }
    result = await client.order_check(request)
    assert result["retcode"] == 0
    assert result["balance"] == 10000.0
    assert "request" in result


async def test_order_check_no_money():
    """Test order_check returns NO_MONEY when margin_free < 0."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.symbol_info_tick = MagicMock(return_value={"ask": 1.1, "bid": 1.09})
    client.get_account = AsyncMock(
        return_value=_mock_account(
            balance=100.0,
            equity=100.0,
            margin=50.0,
        )
    )
    client.order_calc_margin = AsyncMock(return_value=200.0)  # exceeds equity
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 10.0,
        "price": 1.1,
    }
    result = await client.order_check(request)
    assert result["retcode"] == TRADE_RETCODE_NO_MONEY


async def test_order_check_invalid_request_sets_last_error():
    """Test order_check with invalid request sets last_error."""
    client = _make_client()
    client.symbol_info = AsyncMock(return_value=None)
    client.get_account = AsyncMock(return_value=_mock_account())
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "NOSYM",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
    }
    result = await client.order_check(request)
    assert result["retcode"] == TRADE_RETCODE_INVALID
    assert client._last_error[0] != 0


# =====================================================================
# _place_order with volume validation
# =====================================================================


async def test_place_order_validates_volume():
    """Test _place_order validates volume for DEAL actions."""
    client = _make_client()
    with pytest.raises(ValueError):
        await client._place_order(
            trade_action=TRADE_ACTION_DEAL,
            symbol="EURUSD",
            volume=-1.0,
            trade_type=ORDER_TYPE_BUY,
        )


async def test_place_order_validates_symbol():
    """Test _place_order validates symbol name."""
    client = _make_client()
    with pytest.raises(ValueError):
        await client._place_order(
            trade_action=TRADE_ACTION_DEAL,
            symbol="",
            volume=0.01,
            trade_type=ORDER_TYPE_BUY,
        )


# =====================================================================
# Additional edge cases for comprehensive coverage
# =====================================================================


async def test_order_check_modify_action_valid():
    """Test order_check for MODIFY action with valid order."""
    client = _make_client()
    client.get_account = AsyncMock(return_value=_mock_account())
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {
        "action": TRADE_ACTION_MODIFY,
        "symbol": "EURUSD",
        "order": 12345,
        "type": ORDER_TYPE_BUY_LIMIT,
    }
    result = await client.order_check(request)
    assert result["retcode"] == 0


async def test_order_check_remove_action_valid():
    """Test order_check for REMOVE action with valid order."""
    client = _make_client()
    client.get_account = AsyncMock(return_value=_mock_account())
    client.symbol_info = AsyncMock(return_value=_mock_symbol_info_record())
    request = {
        "action": TRADE_ACTION_REMOVE,
        "symbol": "EURUSD",
        "order": 12345,
    }
    result = await client.order_check(request)
    assert result["retcode"] == 0


async def test_close_position_with_digits():
    """Test close_position with explicit digits."""
    client = _make_client()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.close_position(
        "EURUSD",
        position_id=12345,
        volume=0.01,
        order_type=ORDER_TYPE_SELL,
        digits=3,
    )
    assert result.retcode == TRADE_RETCODE_DONE


async def test_close_position_by_with_digits():
    """Test close_position_by resolves digits from cache."""
    client = _make_client()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.close_position_by(
        "EURUSD",
        position_id=100,
        position_by=200,
    )
    assert result.retcode == TRADE_RETCODE_DONE


async def test_buy_market_with_all_options():
    """Test buy_market with all optional parameters."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.buy_market(
        "EURUSD",
        0.1,
        sl=1.09,
        tp=1.15,
        deviation=10,
        comment="test buy",
        filling=ORDER_FILLING_FOK,
        digits=5,
        magic=42,
    )
    assert result.success is True


async def test_sell_market_with_all_options():
    """Test sell_market with all optional parameters."""
    client = _make_client()
    resp_body = _build_trade_response_body(retcode=TRADE_RETCODE_DONE)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=resp_body)
    )
    result = await client.sell_market(
        "EURUSD",
        0.1,
        sl=1.15,
        tp=1.09,
        deviation=10,
        comment="test sell",
        filling=ORDER_FILLING_FOK,
        digits=5,
        magic=42,
    )
    assert result.success is True


async def test_validate_pending_valid_with_price():
    """Test pending order validation passes with proper price."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    request = {
        "action": TRADE_ACTION_PENDING,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY_LIMIT,
        "volume": 1.0,
        "price_order": 1.09,
    }
    retcode, _ = await client._validate_order_check_request(request)
    assert retcode == 0


async def test_estimate_order_margin_calc_returns_none():
    """Test _estimate_order_margin returns 0.0 when calc returns None."""
    client = _make_client()
    info = _mock_symbol_info_record()
    client.symbol_info = AsyncMock(return_value=info)
    client.order_calc_margin = AsyncMock(return_value=None)
    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "EURUSD",
        "type": ORDER_TYPE_BUY,
        "volume": 1.0,
        "price_order": 1.1,
    }
    result = await client._estimate_order_margin(request)
    assert result == 0.0


async def test_resolve_order_check_price_pending_sell_stop_limit():
    """Test _resolve_order_check_price for sell stop-limit uses trigger."""
    client = _make_client()
    info = _mock_symbol_info_record()
    request = {
        "action": TRADE_ACTION_PENDING,
        "type": ORDER_TYPE_SELL_STOP_LIMIT,
        "price_order": 1.11,
        "price_trigger": 1.10,
    }
    result = await client._resolve_order_check_price(request, info)
    assert result == 1.10


async def test_history_deals_get_no_filter():
    """Test history_deals_get with no filters returns all deals."""
    client = _make_client()
    deal_body = _build_deal_body(
        [
            {"deal": 1, "trade_order": 100, "trade_symbol": "EURUSD", "time_create": 1000, "time_update": 1001},
            {"deal": 2, "trade_order": 200, "trade_symbol": "GBPUSD", "time_create": 1000, "time_update": 1001},
        ]
    )
    order_body = _build_order_body([])
    combined = deal_body + order_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=combined)
    )
    result = await client.history_deals_get()
    assert len(result) == 2
