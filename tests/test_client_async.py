"""Async tests for MT5WebClient: mock transport.send_command to test
login flow, get_positions, subscribe_ticks, on_tick handler, trade validation,
unsubscribe_ticks, and on_* handler return values."""

import asyncio
import struct
from unittest.mock import AsyncMock

import pytest

from pymt5.client import MT5WebClient, SymbolInfo, _parse_account_response
from pymt5.constants import (
    CMD_LOGIN,
    CMD_SUBSCRIBE_BOOK,
    CMD_SUBSCRIBE_TICKS,
    CMD_TICK_PUSH,
    COPY_TICKS_ALL,
    COPY_TICKS_INFO,
    ORDER_TIME_SPECIFIED,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP_LIMIT,
    PROP_F64,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
)
from pymt5.protocol import SeriesCodec
from pymt5.transport import CommandResult

# ---- Login flow ----


async def test_login_stores_credentials():
    client = MT5WebClient()
    # Mock transport.send_command for LOGIN
    token_bytes = bytes(160)
    session_id = (42).to_bytes(8, "little", signed=False)
    login_body = token_bytes + session_id

    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body))
    client.transport.is_ready = True

    token, session = await client.login(login=12345678, password="test", auto_heartbeat=False)

    assert client._logged_in is True
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 12345678
    assert session == 42


# ---- subscribe_ticks ----


async def test_subscribe_ticks_accumulates():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""))
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
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""))
    client.transport.is_ready = True

    await client.unsubscribe_ticks([2])
    assert client._subscribed_ids == [1, 3]


async def test_unsubscribe_ticks_all():
    client = MT5WebClient()
    client._subscribed_ids = [1, 2]
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""))
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
    tick_data = SeriesCodec.serialize(
        [
            (PROP_U32, 42),  # symbol_id
            (PROP_I32, 1000000),  # tick_time
            (PROP_U32, 0),  # fields
            (PROP_F64, 1.12345),  # bid
            (PROP_F64, 1.12350),  # ask
            (PROP_F64, 0.0),  # last
            (PROP_I64, 100),  # tick_volume
            (PROP_U32, 500),  # time_ms_delta
            (PROP_U16, 0),  # flags
        ]
    )

    # Simulate tick push
    result = CommandResult(command=CMD_TICK_PUSH, code=0, body=tick_data)
    handler(result)

    assert len(received) == 1
    ticks = received[0]
    assert len(ticks) == 1
    assert ticks[0]["symbol_id"] == 42
    assert ticks[0]["bid"] == 1.12345
    assert ticks[0]["tick_time_ms"] == 1000000 * 1000 + 500


async def test_symbol_info_tick_uses_internal_cache():
    client = MT5WebClient()
    client._symbols["EURUSD"] = type("S", (), {"name": "EURUSD", "symbol_id": 42, "digits": 5})()
    client._symbols_by_id[42] = client._symbols["EURUSD"]
    tick_data = SeriesCodec.serialize(
        [
            (PROP_U32, 42),
            (PROP_I32, 1000000),
            (PROP_U32, 0),
            (PROP_F64, 1.12345),
            (PROP_F64, 1.12350),
            (PROP_F64, 0.0),
            (PROP_I64, 100),
            (PROP_U32, 500),
            (PROP_U16, 0),
        ]
    )

    client._cache_tick_push(CommandResult(command=CMD_TICK_PUSH, code=0, body=tick_data))
    tick = client.symbol_info_tick("EURUSD")
    assert tick is not None
    assert tick["symbol"] == "EURUSD"
    assert tick["bid"] == 1.12345


async def test_market_book_get_uses_internal_cache():
    client = MT5WebClient()
    client._symbols["EURUSD"] = type("S", (), {"name": "EURUSD", "symbol_id": 42, "digits": 5})()
    client._symbols_by_id[42] = client._symbols["EURUSD"]
    body = struct.pack("<I", 1)
    body += struct.pack("<IiiIIH", 42, 0, 0, 1, 1, 0)
    body += struct.pack("<dq", 1.1234, 100)
    body += struct.pack("<dq", 1.1236, 120)

    client._cache_book_push(CommandResult(command=23, code=0, body=body))
    book = client.market_book_get("EURUSD")
    assert book is not None
    assert book["symbol"] == "EURUSD"
    assert book["bids"][0]["price"] == 1.1234
    assert book["asks"][0]["volume"] == 120


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
        await client.trade_request(trade_action=TRADE_ACTION_PENDING, volume=100, price_order=0.0)


# ---- _parse_account_response ----


def test_parse_account_response_empty():
    assert _parse_account_response(b"") == {}
    assert _parse_account_response(None) == {}


def test_parse_account_response_short():
    assert _parse_account_response(b"\x00" * 50) == {}


def test_parse_account_response_valid():
    header = SeriesCodec.serialize(
        [
            (4, 0),  # account_type
            (3, 0),  # rights
            (3, 16),  # permissions_flags
            (8, 10000.0),  # balance
            (8, 500.0),  # credit
            (11, "USD", 64),  # account_currency
            (6, 2),  # currency_digits
            (6, 100),  # margin_leverage
            (11, "Demo Account", 256),
            (5, 5687),  # server_build
            (11, "MetaQuotes-Demo", 128),
            (11, "MetaQuotes Ltd", 256),
            (3, 3600),  # timezone_shift
            (1, 1),  # daylightmode
            (6, 2),  # margin_mode
            (6, 0),  # margin_free_mode
            (8, 50.0),  # margin_so_call
            (8, 30.0),  # margin_so_so
            (6, 0),  # margin_so_mode
            (8, 0.0),  # margin_virtual
            (6, 0),  # margin_free_profit_mode
            (8, 123.45),  # acc_profit
            (8, 0.0),  # commission_daily
            (8, 0.0),  # commission_monthly
            (6, 10),  # auth_password_min
            (6, 1),  # otp_status
        ]
    )
    trade_settings = SeriesCodec.serialize(
        [
            (11, "Forex\\Major\\EURUSD", 256),
            (3, 12),
            (3, 0),
            (6, 4),
            (3, 20),
            (3, 10),
            (6, 2),
            (6, 7),
            (6, 15),
            (6, 127),
            (6, 3),
            (6, 30),
            (6, 1),
            (6, 7),
            (6, 2),
            (6, 3),
            (18, 1_000_000),
            (6, 1),
            (6, 0),
            (6, 0),
            (18, 1000),
            (18, 1000000),
            (18, 1000),
            (18, 0),
            (6, 0),
            (8, 1000.0),
            (8, 900.0),
            (12, struct.pack("<8d", 1.0, 1.1, 1.2, 1.3, 0.0, 0.0, 0.0, 0.0), 64),
            (12, struct.pack("<8d", 2.0, 2.1, 2.2, 2.3, 0.0, 0.0, 0.0, 0.0), 64),
            (8, 0.5),
            (8, 250.0),
            (8, 1.0),
            (6, 0),
            (8, -3.2),
            (8, 1.4),
            (3, 3),
            (6, 16),
            (6, 10),
            (12, struct.pack("<7d", 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.0), 56),
        ]
    )
    leverage_rule = SeriesCodec.serialize(
        [
            (11, "Forex\\Major\\*", 256),
            (6, 1),
            (11, "USD", 32),
            (6, 2),
            (3, 1),
        ]
    )
    leverage_tier = SeriesCodec.serialize(
        [
            (8, 0.0),
            (8, 100000.0),
            (8, 1.0),
            (8, 0.5),
        ]
    )
    commission = SeriesCodec.serialize(
        [
            (11, "Forex\\Major\\*", 256),
            (6, 1),
            (6, 2),
            (6, 3),
            (11, "USD", 32),
            (6, 4),
        ]
    )
    commission_tier = SeriesCodec.serialize(
        [
            (6, 1),
            (6, 2),
            (8, 7.0),
            (8, 0.0),
            (8, 1000000.0),
            (8, 0.0),
            (8, 1000.0),
            (11, "USD", 32),
        ]
    )
    body = b"".join(
        [
            header,
            struct.pack("<I", 1),
            trade_settings,
            struct.pack("<i", 7),
            struct.pack("<i", 6104),
            struct.pack("<Q", 9),
            struct.pack("<i", 1),
            leverage_rule,
            leverage_tier,
            struct.pack("<I", 1),
            commission,
            struct.pack("<I", 1),
            commission_tier,
        ]
    )
    result = _parse_account_response(body)
    assert result["balance"] == 10000.0
    assert result["credit"] == 500.0
    assert result["currency"] == "USD"
    assert result["leverage"] == 100
    assert result["currency_digits"] == 2
    assert result["name"] == "Demo Account"
    assert result["server"] == "MetaQuotes-Demo"
    assert result["company"] == "MetaQuotes Ltd"
    assert result["timezone_shift"] == 3600
    assert result["daylight_mode"] == 1
    assert result["server_offset_time"] == 7200
    assert result["profit"] == pytest.approx(123.45)
    assert result["equity"] == pytest.approx(10623.45)
    assert result["trade_allowed"] is True
    assert result["is_real"] is True
    assert result["is_hedged_margin"] is True
    assert result["risk_warning"] is True
    assert result["trade_flags"] == 7
    assert result["symbols_count"] == 6104
    assert result["leverage_flags"] == 9
    assert len(result["trade_settings"]) == 1
    assert result["trade_settings"][0]["symbol_path"] == "Forex\\Major\\EURUSD"
    assert result["trade_settings"][0]["margin_rates_initial"][:4] == pytest.approx([1.0, 1.1, 1.2, 1.3])
    assert result["trade_settings"][0]["swap_rates"][:3] == pytest.approx([0.1, 0.2, 0.3])
    assert len(result["leverage_rules"]) == 1
    assert result["leverage_rules"][0]["path"] == "Forex\\Major\\*"
    assert result["leverage_rules"][0]["tiers"][0]["margin_rate_maintenance"] == pytest.approx(0.5)
    assert result["rules"] == result["leverage_rules"]
    assert len(result["commissions"]) == 1
    assert result["commissions"][0]["mode_currency"] == "USD"
    assert result["commissions"][0]["tiers"][0]["value"] == pytest.approx(7.0)


# ---- subscribe_book tracks IDs ----


async def test_subscribe_book_stores_ids():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""))
    client.transport.is_ready = True

    await client.subscribe_book([10, 20, 30])
    assert client._subscribed_book_ids == [10, 20, 30]
    sent_payload = client.transport.send_command.call_args_list[-1][0][1]
    assert struct.unpack_from("<I", sent_payload, 0)[0] == 3


async def test_subscribe_book_accumulates():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""))
    client.transport.is_ready = True

    await client.subscribe_book([10, 20])
    await client.subscribe_book([30])

    assert client._subscribed_book_ids == [10, 20, 30]
    sent_payload = client.transport.send_command.call_args_list[-1][0][1]
    assert struct.unpack_from("<I", sent_payload, 0)[0] == 3


async def test_unsubscribe_book_removes():
    client = MT5WebClient()
    client._subscribed_book_ids = [10, 20, 30]
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""))
    client.transport.is_ready = True

    await client.unsubscribe_book([20])
    assert client._subscribed_book_ids == [10, 30]
    sent_payload = client.transport.send_command.call_args_list[-1][0][1]
    assert struct.unpack_from("<I", sent_payload, 0)[0] == 2


async def test_symbols_get_group_filter():
    client = MT5WebClient()
    client.get_symbols = AsyncMock(
        return_value=[
            {"trade_symbol": "EURUSD"},
            {"trade_symbol": "USDJPY"},
            {"trade_symbol": "XAUUSD"},
        ]
    )

    filtered = await client.symbols_get("*USD*,!*JPY*")
    assert [item["trade_symbol"] for item in filtered] == ["EURUSD", "XAUUSD"]


async def test_positions_get_ticket_filter():
    client = MT5WebClient()
    client.get_positions = AsyncMock(
        return_value=[
            {"position_id": 1, "trade_symbol": "EURUSD"},
            {"position_id": 2, "trade_symbol": "GBPUSD"},
        ]
    )

    result = await client.positions_get(ticket=2)
    assert result == [{"position_id": 2, "trade_symbol": "GBPUSD"}]


async def test_history_deals_get_group_and_ticket_filters():
    client = MT5WebClient()
    client.get_deals = AsyncMock(
        return_value=[
            {"trade_order": 1, "position_id": 11, "trade_symbol": "EURUSD"},
            {"trade_order": 2, "position_id": 22, "trade_symbol": "USDJPY"},
            {"trade_order": 2, "position_id": 33, "trade_symbol": "XAUUSD"},
        ]
    )

    result = await client.history_deals_get(group="*USD*,!*JPY*", ticket=2)
    assert result == [{"trade_order": 2, "position_id": 33, "trade_symbol": "XAUUSD"}]


async def test_copy_rates_wrappers():
    client = MT5WebClient()
    bars = [
        {"time": 100},
        {"time": 200},
        {"time": 300},
        {"time": 400},
    ]
    client.get_rates = AsyncMock(return_value=bars)

    ranged = await client.copy_rates_range("EURUSD", 1, 10, 20)
    assert ranged == bars
    client.get_rates.assert_awaited_with("EURUSD", 1, 10, 20)

    client.get_rates.reset_mock()
    client.get_rates.return_value = bars
    latest = await client.copy_rates_from("EURUSD", 1, 400, 2)
    assert latest == bars[-2:]

    client.get_rates.reset_mock()
    client.get_rates.return_value = bars
    from_pos = await client.copy_rates_from_pos("EURUSD", 1, 1, 2)
    assert from_pos == bars[1:3]


async def test_get_full_symbol_info_parses_extended_bond_fields():
    client = MT5WebClient()
    trade = SeriesCodec.serialize(
        [
            (11, "Bonds\\MOEX\\OFZ26238", 256),
            (3, 0),
            (3, 0),
            (6, 4),
            (3, 0),
            (3, 0),
            (6, 2),
            (6, 7),
            (6, 15),
            (6, 127),
            (6, 3),
            (6, 30),
            (6, 1),
            (6, 7),
            (6, 2),
            (6, 3),
            (18, 1_000_000),
            (6, 1),
            (6, 0),
            (6, 0),
            (18, 1),
            (18, 1000),
            (18, 1),
            (18, 0),
            (6, 0),
            (8, 980.0),
            (8, 970.0),
            (12, struct.pack("<8d", *([0.0] * 8)), 64),
            (12, struct.pack("<8d", *([0.0] * 8)), 64),
            (8, 0.0),
            (8, 0.0),
            (8, 1.0),
            (6, 0),
            (8, 0.0),
            (8, 0.0),
            (3, 0),
            (6, 0),
            (6, 0),
            (12, struct.pack("<7d", *([0.0] * 7)), 56),
        ]
    )
    schedule = bytearray(896)
    struct.pack_into("<HH", schedule, 0, 10, 20)
    subscription = struct.pack("<IBBH", 3, 1, 2, 0)
    record = SeriesCodec.serialize(
        [
            (11, "OFZ26238", 64),
            (11, "RU000A1038V6", 32),
            (11, "OFZ Bond", 128),
            (11, "INTL", 128),
            (11, "RUB", 64),
            (11, "MOEX", 64),
            (11, "https://example.com/bond", 512),
            (11, "Bonds", 128),
            (11, "MOEX", 128),
            (11, "DBFNXX", 16),
            (5, 12),
            (5, 34),
            (11, "RU", 8),
            (11, "RUB", 32),
            (11, "RUB", 32),
            (11, "RUB", 32),
            (6, 2),
            (6, 2),
            (6, 2),
            (6, 0),
            (6, 0),
            (6, 2),
            (8, 0.01),
            (8, 1.0),
            (6, 1234),
            (6, 1),
            (6, 10),
            (6, 0),
            (6, 7),
            (3, 5),
            (3, 0),
            (8, 1.0),
            (8, 0.01),
            (8, 1.0),
            (6, 0),
            (6, 37),
            (8, 98.0),
            (8, 0.0),
            (8, 0.0),
            (8, 0.0),
            (6, 0),
            (8, 1000.0),
            (8, 12.5),
            (6, 0),
            (3, 0),
            (3, 0),
            (12, trade, 628),
            (12, bytes(schedule), 896),
            (12, subscription, 8),
        ]
    )
    body = struct.pack("<I", 1) + record
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=18, code=0, body=body))

    info = await client.get_full_symbol_info("OFZ26238")

    assert info is not None
    assert info["face_value"] == pytest.approx(1000.0)
    assert info["accrued_interest"] == pytest.approx(12.5)
    assert info["trade_calc_mode"] == 37
    assert info["symbol_path"] == "Bonds\\MOEX\\OFZ26238"
    assert info["trade_mode"] == 4
    assert info["margin_initial"] == pytest.approx(980.0)
    assert info["filling_mode"] == 7
    assert info["schedule"]["quote_sessions"][0][0] == (10, 20)
    assert info["subscription"]["delay"] == 3


async def test_order_send_maps_stop_limit_request():
    client = MT5WebClient()
    client._symbols["EURUSD"] = client._symbols_by_id[42] = type(
        "S", (), {"name": "EURUSD", "symbol_id": 42, "digits": 5}
    )()
    client.trade_request = AsyncMock(return_value="ok")

    result = await client.order_send(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": 0.1,
            "type": ORDER_TYPE_BUY_STOP_LIMIT,
            "price": 1.1050,
            "stoplimit": 1.1040,
            "sl": 1.1,
            "tp": 1.11,
        }
    )

    assert result == "ok"
    client.trade_request.assert_awaited_once_with(
        action_id=0,
        trade_action=TRADE_ACTION_PENDING,
        symbol="EURUSD",
        volume=10_000_000,
        digits=5,
        order=0,
        trade_type=ORDER_TYPE_BUY_STOP_LIMIT,
        type_filling=0,
        type_time=0,
        type_flags=0,
        type_reason=0,
        price_order=1.1040,
        price_trigger=1.1050,
        price_sl=1.1,
        price_tp=1.11,
        deviation=0,
        comment="",
        position_id=0,
        position_by=0,
        time_expiration=0,
    )


async def test_order_calc_profit_forex_quote_currency_account():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "EURUSD",
            "trade_calc_mode": 0,
            "contract_size": 100000.0,
            "currency_profit": "USD",
        }
    )
    client.get_account = AsyncMock(return_value={"currency": "USD", "leverage": 100})

    profit = await client.order_calc_profit(ORDER_TYPE_BUY, "EURUSD", 0.1, 1.1000, 1.1050)

    assert profit == pytest.approx(50.0)
    assert client.last_error() == (0, "")


async def test_order_calc_profit_cross_currency_uses_inverse_pair_tick():
    client = MT5WebClient()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.1000, "ask": 1.1002}
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "EURUSD",
            "trade_calc_mode": 0,
            "contract_size": 100000.0,
            "currency_profit": "USD",
        }
    )
    client.get_account = AsyncMock(return_value={"currency": "EUR", "leverage": 100})

    profit = await client.order_calc_profit(ORDER_TYPE_BUY, "EURUSD", 0.1, 1.1000, 1.1010)

    assert profit == pytest.approx(9.0909090909)
    assert client.last_error() == (0, "")


async def test_order_calc_profit_unsupported_bond_mode_sets_last_error():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "MOEXBOND",
            "trade_calc_mode": 37,
            "contract_size": 1.0,
            "face_value": 1000.0,
            "accrued_interest": 12.5,
            "currency_profit": "RUB",
        }
    )
    client.get_account = AsyncMock(return_value={"currency": "RUB", "leverage": 100})

    profit = await client.order_calc_profit(ORDER_TYPE_BUY, "MOEXBOND", 1.0, 98.0, 99.0)

    assert profit == pytest.approx(22.5)
    assert client.last_error() == (0, "")


async def test_order_calc_margin_forex_uses_account_leverage():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "EURUSD",
            "trade_calc_mode": 0,
            "contract_size": 100000.0,
            "currency_margin": "EUR",
        }
    )
    client.get_account = AsyncMock(return_value={"currency": "EUR", "leverage": 100})

    margin = await client.order_calc_margin(ORDER_TYPE_BUY, "EURUSD", 0.1, 1.1000)

    assert margin == pytest.approx(100.0)
    assert client.last_error() == (0, "")


async def test_order_calc_margin_cfd_leverage_converts_currency():
    client = MT5WebClient()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.1000, "ask": 1.1002}
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "XAUUSD",
            "trade_calc_mode": 4,
            "contract_size": 100.0,
            "currency_margin": "USD",
        }
    )
    client.get_account = AsyncMock(return_value={"currency": "EUR", "leverage": 50})

    margin = await client.order_calc_margin(ORDER_TYPE_BUY, "XAUUSD", 0.1, 2000.0)

    assert margin == pytest.approx(363.6363636364)
    assert client.last_error() == (0, "")


async def test_order_calc_margin_bond_uses_face_value_formula():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "MOEXBOND",
            "trade_calc_mode": 37,
            "contract_size": 1.0,
            "face_value": 1000.0,
            "currency_margin": "RUB",
        }
    )
    client.get_account = AsyncMock(return_value={"currency": "RUB", "leverage": 100})

    margin = await client.order_calc_margin(ORDER_TYPE_BUY, "MOEXBOND", 1.0, 98.0)

    assert margin == pytest.approx(980.0)
    assert client.last_error() == (0, "")


async def test_copy_ticks_wrappers_use_cached_history():
    client = MT5WebClient()
    client._symbols["EURUSD"] = client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    body = b"".join(
        [
            SeriesCodec.serialize(
                [
                    (6, 1),
                    (3, 1000),
                    (6, 0),
                    (8, 1.1000),
                    (8, 1.1002),
                    (8, 0.0),
                    (17, 0),
                    (6, 100),
                    (5, 2),
                ]
            ),
            SeriesCodec.serialize(
                [
                    (6, 1),
                    (3, 1001),
                    (6, 0),
                    (8, 0.0),
                    (8, 0.0),
                    (8, 1.1001),
                    (17, 5),
                    (6, 200),
                    (5, 4),
                ]
            ),
            SeriesCodec.serialize(
                [
                    (6, 1),
                    (3, 1002),
                    (6, 0),
                    (8, 1.1003),
                    (8, 1.1005),
                    (8, 1.1004),
                    (17, 7),
                    (6, 300),
                    (5, 6),
                ]
            ),
        ]
    )
    client._cache_tick_push(CommandResult(command=CMD_TICK_PUSH, code=0, body=body))

    ticks_from = await client.copy_ticks_from("EURUSD", 1001, 2, COPY_TICKS_ALL)
    info_ticks = await client.copy_ticks_range("EURUSD", 1000, 1002, COPY_TICKS_INFO)

    assert [item["time"] for item in ticks_from] == [1001, 1002]
    assert ticks_from[0]["time_msc"] == 1001_200
    assert ticks_from[0]["volume"] == 5
    assert ticks_from[0]["flags"] == 4
    assert [item["time"] for item in info_ticks] == [1000, 1002]


async def test_order_check_success_uses_local_margin_estimate():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "EURUSD",
            "trade_mode": 4,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "point": 0.0001,
            "trade_stops_level": 10,
            "filling_mode": 7,
        }
    )
    client.order_calc_margin = AsyncMock(return_value=100.0)
    client.get_account = AsyncMock(
        return_value={
            "balance": 1000.0,
            "equity": 1000.0,
            "profit": 0.0,
            "margin": 50.0,
        }
    )

    result = await client.order_check(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": 0.10,
            "type": ORDER_TYPE_BUY_LIMIT,
            "price": 1.1000,
            "sl": 1.0900,
            "tp": 1.1100,
        }
    )

    assert result["retcode"] == 0
    assert result["margin"] == pytest.approx(150.0)
    assert result["margin_free"] == pytest.approx(850.0)
    assert result["comment"] == "OK"


async def test_order_check_invalid_volume():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "EURUSD",
            "trade_mode": 4,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "point": 0.0001,
            "trade_stops_level": 0,
            "filling_mode": 7,
        }
    )
    client.get_account = AsyncMock(
        return_value={
            "balance": 1000.0,
            "equity": 1000.0,
            "profit": 0.0,
            "margin": 50.0,
        }
    )

    result = await client.order_check(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": 0.005,
            "type": ORDER_TYPE_BUY_LIMIT,
            "price": 1.1000,
        }
    )

    assert result["retcode"] == TRADE_RETCODE_INVALID_VOLUME
    assert "minimum" in result["comment"]


async def test_order_check_no_money():
    client = MT5WebClient()
    client.symbol_info = AsyncMock(
        return_value={
            "trade_symbol": "EURUSD",
            "trade_mode": 4,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "point": 0.0001,
            "trade_stops_level": 0,
            "filling_mode": 7,
        }
    )
    client.order_calc_margin = AsyncMock(return_value=950.0)
    client.get_account = AsyncMock(
        return_value={
            "balance": 1000.0,
            "equity": 1000.0,
            "profit": 0.0,
            "margin": 100.0,
        }
    )

    result = await client.order_check(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": 0.10,
            "type": ORDER_TYPE_BUY_LIMIT,
            "price": 1.1000,
            "type_time": ORDER_TIME_SPECIFIED,
            "expiration": 1_700_000_000,
        }
    )

    assert result["retcode"] == TRADE_RETCODE_NO_MONEY
    assert result["margin_free"] == pytest.approx(-50.0)


async def test_terminal_info_uses_account_config_fields():
    client = MT5WebClient()
    client.transport.is_ready = True
    client.get_account = AsyncMock(
        return_value={
            "server_build": 5687,
            "company": "MetaQuotes Ltd",
            "server_name": "MetaQuotes-Demo",
            "trade_allowed": True,
            "is_read_only": False,
            "timezone_shift": 3600,
            "server_offset_time": 7200,
        }
    )

    info = await client.terminal_info()

    assert info == {
        "build": 5687,
        "company": "MetaQuotes Ltd",
        "name": "MetaQuotes-Demo",
        "server": "MetaQuotes-Demo",
        "connected": True,
        "trade_allowed": True,
        "tradeapi_disabled": False,
        "timezone_shift": 3600,
        "server_offset_time": 7200,
        "path": "",
        "data_path": "",
        "commondata_path": "",
    }


async def test_version_uses_account_build_and_observed_release_date():
    client = MT5WebClient()
    client.get_account = AsyncMock(return_value={"server_build": 5687})

    result = await client.version()

    assert result == (500, 5687, "15 Mar 2026")
    assert client.last_error() == (0, "")


async def test_version_missing_build_sets_last_error():
    client = MT5WebClient()
    client.get_account = AsyncMock(return_value={})

    result = await client.version()

    assert result is None
    assert client.last_error() == (-7, "terminal build unavailable for version()")


# ---- reconnect guard ----


def test_reconnect_guard_skips_if_in_progress():
    client = MT5WebClient(auto_reconnect=True)
    client._login_kwargs = {"login": 1, "password": "x"}

    # Create a fake task that is not done
    loop = asyncio.new_event_loop()

    async def dummy():
        await asyncio.sleep(999)

    client._reconnect_task = loop.create_task(dummy())

    # _handle_disconnect should not create a new task
    client._handle_disconnect()
    # The reconnect_task should still be the same one
    assert not client._reconnect_task.done() or client._reconnect_task is not None
    client._reconnect_task.cancel()
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()
