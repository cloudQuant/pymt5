"""Iteration 07 regressions for Group 2: Market Data, Parsers, and Push Handlers.

Covers Phase 26 tasks:
- 26.1 (S3-01): Order-book subscribe acknowledgement and lock serialization
- 26.2 (S3-02): Order-book unsubscribe acknowledgement and cache preservation
- 26.3 (S3-03, S3-09): Account parsing count=0 offset & truncation guards
- 26.7 (S3-04): Push-handler callback error isolation
- 26.9 (S3-05): TradeResultEvent.order and retcode field extraction
- 26.10 (S3-06): Incremental tick batch rolling merge within a single frame
- 26.11 (S3-08): Async callback support across all push-handler wrappers
- 26.12 (S3-07, S3-10): Symbol cache invalidation completeness & market data errors
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from pymt5._market_data import _MarketDataMixin
from pymt5._parsers import (
    _parse_account_leverage_rules,
    _parse_account_response,
    _parse_account_trade_settings,
    _parse_tick_batch,
)
from pymt5.client import MT5WebClient
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_GET_FULL_SYMBOLS,
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_SPREADS,
    CMD_GET_SYMBOL_GROUPS,
    CMD_GET_SYMBOLS_GZIP,
    CMD_LOGIN_STATUS_PUSH,
    CMD_SUBSCRIBE_BOOK,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    COPY_TICKS_INFO,
    COPY_TICKS_TRADE,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
)
from pymt5.events import BookEvent, TickEvent, TradeResultEvent
from pymt5.exceptions import ProtocolError
from pymt5.protocol import ResponseFrame, SeriesCodec, get_series_size
from pymt5.schemas import (
    ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA,
    ACCOUNT_WEB_MAIN_SCHEMA,
    DEAL_SCHEMA,
    POSITION_SCHEMA,
    SYMBOL_DETAILS_SCHEMA,
    TRADE_RESULT_PUSH_SCHEMA,
    TRADE_RESULT_RESPONSE_SCHEMA,
    TRADE_UPDATE_BALANCE_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import SymbolInfo


def _book_response(code: int = 0) -> CommandResult:
    return CommandResult(command=CMD_SUBSCRIBE_BOOK, code=code, body=b"")


def _tick_bytes(
    *,
    symbol_id: int = 1,
    bid: float = 0.0,
    ask: float = 0.0,
    last: float = 0.0,
    tick_time: int = 1_700_000_000,
    time_ms_delta: int = 250,
    tick_volume: int = 10,
) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U32, symbol_id),
            (PROP_I32, tick_time),
            (PROP_U32, 0),
            (PROP_F64, bid),
            (PROP_F64, ask),
            (PROP_F64, last),
            (PROP_I64, tick_volume),
            (PROP_U32, time_ms_delta),
            (PROP_U16, 0),
        ]
    )


def _book_push_bytes(symbol_id: int = 42, bid_price: float = 1.10, ask_price: float = 1.11) -> bytes:
    header = SeriesCodec.serialize(
        [
            (PROP_U32, symbol_id),
            (PROP_I32, 0),
            (PROP_I32, 0),
            (PROP_U32, 1),
            (PROP_U32, 1),
            (PROP_U16, 0),
        ]
    )
    bid_level = SeriesCodec.serialize([(PROP_F64, bid_price), (PROP_I64, 100)])
    ask_level = SeriesCodec.serialize([(PROP_F64, ask_price), (PROP_I64, 200)])
    return struct.pack("<I", 1) + header + bid_level + ask_level


def _account_main_bytes() -> bytes:
    specs = []
    for field in ACCOUNT_WEB_MAIN_SCHEMA:
        entry = dict(field)
        entry["propValue"] = 0.0 if field["propType"] == PROP_F64 else 0
        specs.append(entry)
    return SeriesCodec.serialize(specs)


def _trade_result_bytes(
    *,
    action_result_code: int = 0,
    action_order: int = 0,
    resp_retcode: int | None = None,
    resp_order: int = 0,
    resp_volume: int = 0,
    resp_price: float = 0.0,
    resp_comment: str = "",
) -> bytes:
    action_fields = []
    for idx, field in enumerate(TRADE_RESULT_PUSH_SCHEMA):
        entry = dict(field)
        if idx == 0:
            entry["propValue"] = action_result_code
        elif idx == 6:
            entry["propValue"] = action_order
        elif field["propType"] == PROP_F64:
            entry["propValue"] = 0.0
        else:
            entry["propValue"] = 0
        action_fields.append(entry)
    payload = SeriesCodec.serialize(action_fields)
    if resp_retcode is None:
        return payload

    resp_fields = []
    for idx, field in enumerate(TRADE_RESULT_RESPONSE_SCHEMA):
        entry = dict(field)
        if idx == 0:
            entry["propValue"] = resp_retcode
        elif idx == 1:
            entry["propValue"] = resp_order
        elif idx == 2:
            entry["propValue"] = resp_volume
        elif idx == 3:
            entry["propValue"] = resp_price
        elif idx == 6:
            entry["propValue"] = resp_comment
        elif field["propType"] == PROP_F64:
            entry["propValue"] = 0.0
        else:
            entry["propValue"] = 0
        resp_fields.append(entry)
    return payload + SeriesCodec.serialize(resp_fields)


def _balance_bytes(balance: float = 5000.0) -> bytes:
    fields = []
    for idx, field in enumerate(TRADE_UPDATE_BALANCE_SCHEMA):
        entry = dict(field)
        entry["propValue"] = balance if idx == 0 else 0.0
        fields.append(entry)
    return SeriesCodec.serialize(fields)


async def _dispatch_and_drain(client: MT5WebClient, frame: ResponseFrame) -> None:
    await client.transport._dispatch(frame)
    listener_task = getattr(client.transport, "_listener_task", None)
    if listener_task is not None:
        await listener_task


# ---------------------------------------------------------------------------
# 26.1 (S3-01) & 26.2 (S3-02): Order-book subscribe/unsubscribe safety
# ---------------------------------------------------------------------------


async def test_subscribe_book_nonzero_code_raises_and_preserves_state() -> None:
    client = MT5WebClient()
    client._subscribed_book_ids = [10]
    client.transport.send_command = AsyncMock(return_value=_book_response(code=2))

    with pytest.raises(ProtocolError, match=r"subscribe_book failed with code=2"):
        await client.subscribe_book([20])

    assert client._subscribed_book_ids == [10]


async def test_subscribe_book_concurrent_calls_serialize_and_merge() -> None:
    client = MT5WebClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    sent: list[tuple[int, bytes]] = []

    async def send(command: int, payload: bytes) -> CommandResult:
        sent.append((command, payload))
        if len(sent) == 1:
            entered.set()
            await release.wait()
        return _book_response(code=0)

    client.transport.send_command = AsyncMock(side_effect=send)
    first = asyncio.create_task(client.subscribe_book([1]))
    await entered.wait()
    second = asyncio.create_task(client.subscribe_book([2]))
    await asyncio.sleep(0)
    assert len(sent) == 1
    release.set()
    await asyncio.gather(first, second)

    assert sent == [
        (CMD_SUBSCRIBE_BOOK, struct.pack("<II", 1, 1)),
        (CMD_SUBSCRIBE_BOOK, struct.pack("<III", 2, 1, 2)),
    ]
    assert client._subscribed_book_ids == [1, 2]


async def test_subscribe_book_lazily_initializes_lock_on_bare_mixin() -> None:
    mixin = object.__new__(_MarketDataMixin)
    mixin.transport = MagicMock()
    mixin.transport.send_command = AsyncMock(return_value=_book_response(code=0))
    mixin._subscribed_book_ids = []

    await mixin.subscribe_book([7])

    assert mixin._subscribed_book_ids == [7]
    assert isinstance(mixin._book_subscription_lock, asyncio.Lock)


async def test_unsubscribe_book_nonzero_code_raises_and_preserves_ids_and_cache() -> None:
    client = MT5WebClient()
    client._subscribed_book_ids = [1, 2]
    client._book_cache_by_id = {
        1: {"symbol_id": 1, "symbol": "EURUSD", "bids": [], "asks": []},
        2: {"symbol_id": 2, "symbol": "GBPUSD", "bids": [], "asks": []},
    }
    client._book_cache_by_name = {
        "EURUSD": client._book_cache_by_id[1],
        "GBPUSD": client._book_cache_by_id[2],
    }
    client.transport.send_command = AsyncMock(return_value=_book_response(code=5))

    with pytest.raises(ProtocolError, match=r"unsubscribe_book failed with code=5"):
        await client.unsubscribe_book([1])

    assert client._subscribed_book_ids == [1, 2]
    assert set(client._book_cache_by_id) == {1, 2}
    assert set(client._book_cache_by_name) == {"EURUSD", "GBPUSD"}


async def test_unsubscribe_book_success_evicts_only_removed_symbols() -> None:
    client = MT5WebClient()
    client._subscribed_book_ids = [1, 2]
    client._book_cache_by_id = {
        1: {"symbol_id": 1, "symbol": "EURUSD", "bids": [], "asks": []},
        2: {"symbol_id": 2, "symbol": "GBPUSD", "bids": [], "asks": []},
    }
    client._book_cache_by_name = {
        "EURUSD": client._book_cache_by_id[1],
        "GBPUSD": client._book_cache_by_id[2],
    }
    client.transport.send_command = AsyncMock(return_value=_book_response(code=0))

    await client.unsubscribe_book([1])

    assert client._subscribed_book_ids == [2]
    assert set(client._book_cache_by_id) == {2}
    assert set(client._book_cache_by_name) == {"GBPUSD"}


async def test_unsubscribe_book_serializes_with_subscribe_book() -> None:
    client = MT5WebClient()
    client._subscribed_book_ids = [1]
    entered = asyncio.Event()
    release = asyncio.Event()
    sent: list[bytes] = []

    async def send(_command: int, payload: bytes) -> CommandResult:
        sent.append(payload)
        if len(sent) == 1:
            entered.set()
            await release.wait()
        return _book_response(code=0)

    client.transport.send_command = AsyncMock(side_effect=send)
    sub_task = asyncio.create_task(client.subscribe_book([2]))
    await entered.wait()
    unsub_task = asyncio.create_task(client.unsubscribe_book([1]))
    await asyncio.sleep(0)
    assert len(sent) == 1
    release.set()
    await asyncio.gather(sub_task, unsub_task)

    assert sent == [
        struct.pack("<III", 2, 1, 2),
        struct.pack("<II", 1, 2),
    ]
    assert client._subscribed_book_ids == [2]


# ---------------------------------------------------------------------------
# 26.3 (S3-03, S3-09): Account parsing & trade transaction truncation guards
# ---------------------------------------------------------------------------


def test_parse_account_trade_settings_count_zero_advances_offset() -> None:
    body = struct.pack("<I", 0) + struct.pack("<i", 77)
    items, offset = _parse_account_trade_settings(body, 0)
    assert items == []
    assert offset == 4


def test_parse_account_response_trade_settings_count_zero_preserves_flags() -> None:
    body = b"".join(
        [
            _account_main_bytes(),
            struct.pack("<I", 0),  # trade_settings count=0
            struct.pack("<i", 7),  # trade_flags
            struct.pack("<i", 6104),  # symbols_count
            struct.pack("<Q", 9),  # leverage_flags
            struct.pack("<i", 0),  # leverage_rules count=0
            struct.pack("<I", 0),  # commissions count=0
        ]
    )
    parsed = _parse_account_response(body)
    assert parsed["trade_settings"] == []
    assert parsed["trade_flags"] == 7
    assert parsed["symbols_count"] == 6104
    assert parsed["leverage_flags"] == 9


def test_parse_account_response_truncated_trade_settings_prevents_flag_misparsing() -> None:
    # 20 bytes of non-zero truncated trade-settings payload: if offset were not
    # pushed to len(body), trade_flags/symbols_count/leverage_flags would decode non-zero.
    truncated_tail = struct.pack("<iiQ", 111, 222, 333) + b"\xaa\xbb\xcc\xdd"
    body = _account_main_bytes() + struct.pack("<I", 1) + truncated_tail
    parsed = _parse_account_response(body)
    assert parsed["trade_settings"] == []
    assert parsed["trade_flags"] == 0
    assert parsed["symbols_count"] == 0
    assert parsed["leverage_flags"] == 0


def test_parse_account_leverage_rules_truncated_rule_advances_offset_to_end() -> None:
    body = struct.pack("<i", 2) + b"\x01" * 20
    rules, offset = _parse_account_leverage_rules(body, 0)
    assert rules == []
    assert offset == len(body)


def test_parse_account_leverage_rules_truncated_tier_stops_and_advances_to_end() -> None:
    rule = SeriesCodec.serialize(
        [
            (PROP_FIXED_STRING, "Forex\\*", 256),
            (PROP_U32, 1),
            (PROP_FIXED_STRING, "USD", 32),
            (PROP_U32, 2),
            (PROP_I32, 2),  # tier_count = 2
        ]
    )
    tier = SeriesCodec.serialize(
        [
            (PROP_F64, 0.0),
            (PROP_F64, 100000.0),
            (PROP_F64, 1.0),
            (PROP_F64, 0.5),
        ]
    )
    trailing_garbage = b"\x01\x00\x00\x00" + b"\x55" * 16
    body = struct.pack("<i", 1) + rule + tier + trailing_garbage

    rules, offset = _parse_account_leverage_rules(body, 0)
    assert len(rules) == 1
    assert len(rules[0]["tiers"]) == 1
    assert offset == len(body)


def test_parse_account_response_truncated_leverage_tier_prevents_commission_misparsing() -> None:
    rule = SeriesCodec.serialize(
        [
            (PROP_FIXED_STRING, "Forex\\*", 256),
            (PROP_U32, 1),
            (PROP_FIXED_STRING, "USD", 32),
            (PROP_U32, 2),
            (PROP_I32, 2),  # tier_count = 2
        ]
    )
    tier_bytes = get_series_size(ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA)
    # Provide less than 1 tier (non-zero bytes that could otherwise look like commissions if large enough)
    partial_tier = b"\x01\x00\x00\x00" + b"\x00" * (tier_bytes - 5)
    body = b"".join(
        [
            _account_main_bytes(),
            struct.pack("<I", 0),  # trade_settings count=0
            struct.pack("<i", 7),  # trade_flags
            struct.pack("<i", 10),  # symbols_count
            struct.pack("<Q", 3),  # leverage_flags
            struct.pack("<i", 1),  # leverage_rules count=1
            rule,
            partial_tier,
        ]
    )
    parsed = _parse_account_response(body)
    assert len(parsed["leverage_rules"]) == 1
    assert parsed["leverage_rules"][0]["tiers"] == []
    assert parsed["commissions"] == []


@pytest.mark.parametrize(
    "tail_factory",
    [
        lambda: b"\x01\x00\x00\x00" + b"\x00" * 16,  # truncated balance (< balance_size)
        lambda: _balance_bytes() + b"\x01\x02",  # truncated deals header (< 4 bytes)
        lambda: _balance_bytes() + struct.pack("<I", 2) + b"\x00" * 10,  # truncated deals payload
        lambda: _balance_bytes() + struct.pack("<I", 0) + b"\x01\x02",  # truncated positions header
        lambda: _balance_bytes() + struct.pack("<I", 0) + struct.pack("<I", 1) + b"\x00" * 10,  # truncated positions
    ],
)
def test_on_trade_transaction_type2_truncated_segments_abort_without_callback(
    tail_factory, caplog: pytest.LogCaptureFixture
) -> None:
    client = MT5WebClient()
    received = []
    client.on_trade_transaction(received.append)
    handler = next(iter(client.transport._listeners[CMD_TRADE_UPDATE_PUSH]))
    caplog.set_level("ERROR", logger="pymt5.client")

    body = struct.pack("<I", 2) + tail_factory()
    handler(CommandResult(CMD_TRADE_UPDATE_PUSH, 0, body))

    assert received == []
    assert "trade transaction push parse error" in caplog.text


def test_on_trade_transaction_type2_balance_only_and_with_records() -> None:
    client = MT5WebClient()
    received = []
    client.on_trade_transaction(received.append)
    handler = next(iter(client.transport._listeners[CMD_TRADE_UPDATE_PUSH]))

    # Balance-only payload (no deals/positions trailers)
    handler(CommandResult(CMD_TRADE_UPDATE_PUSH, 0, struct.pack("<I", 2) + _balance_bytes(1234.5)))
    assert len(received) == 1
    assert received[0]["balance_info"]["balance"] == pytest.approx(1234.5)
    assert received[0]["deals"] == []
    assert received[0]["positions"] == []

    # Full payload with 1 deal and 1 position
    deal_size = get_series_size(DEAL_SCHEMA)
    pos_size = get_series_size(POSITION_SCHEMA)
    full_body = (
        struct.pack("<I", 2)
        + _balance_bytes(2345.0)
        + struct.pack("<I", 1)
        + b"\x00" * deal_size
        + struct.pack("<I", 1)
        + b"\x00" * pos_size
    )
    handler(CommandResult(CMD_TRADE_UPDATE_PUSH, 0, full_body))
    assert len(received) == 2
    assert len(received[1]["deals"]) == 1
    assert len(received[1]["positions"]) == 1


# ---------------------------------------------------------------------------
# 26.7 (S3-04): Push-handler callback error isolation across all 13 wrappers
# ---------------------------------------------------------------------------


def _register_and_frame(client: MT5WebClient, kind: str, callback) -> ResponseFrame:
    if kind == "on_tick":
        client.on_tick(callback)
        return ResponseFrame(CMD_TICK_PUSH, 0, _tick_bytes(symbol_id=1, bid=1.1, ask=1.2))
    if kind == "on_position_update":
        client.on_position_update(callback)
        return ResponseFrame(CMD_GET_POSITIONS_ORDERS, 0, struct.pack("<I", 0))
    if kind == "on_order_update":
        client.on_order_update(callback)
        return ResponseFrame(CMD_GET_POSITIONS_ORDERS, 0, struct.pack("<I", 0) + struct.pack("<I", 0))
    if kind == "on_trade_update":
        client.on_trade_update(callback)
        return ResponseFrame(CMD_GET_POSITIONS_ORDERS, 0, struct.pack("<I", 0) + struct.pack("<I", 0))
    if kind == "on_account_update":
        client.on_account_update(callback)
        return ResponseFrame(CMD_ACCOUNT_UPDATE_PUSH, 0, _account_main_bytes())
    if kind == "on_symbol_details":
        client.on_symbol_details(callback)
        return ResponseFrame(CMD_SYMBOL_DETAILS_PUSH, 0, b"\x00" * get_series_size(SYMBOL_DETAILS_SCHEMA))
    if kind == "on_trade_result":
        client.on_trade_result(callback)
        return ResponseFrame(CMD_TRADE_RESULT_PUSH, 0, _trade_result_bytes(resp_retcode=10009, resp_order=77))
    if kind == "on_trade_transaction":
        client.on_trade_transaction(callback)
        return ResponseFrame(CMD_TRADE_UPDATE_PUSH, 0, struct.pack("<I", 2) + _balance_bytes())
    if kind == "on_book_update":
        client.on_book_update(callback)
        return ResponseFrame(CMD_BOOK_PUSH, 0, _book_push_bytes(symbol_id=1))
    if kind == "on_tick_event":
        client.on_tick_event(callback)
        return ResponseFrame(CMD_TICK_PUSH, 0, _tick_bytes(symbol_id=1, bid=1.1, ask=1.2))
    if kind == "on_book_event":
        client.on_book_event(callback)
        return ResponseFrame(CMD_BOOK_PUSH, 0, _book_push_bytes(symbol_id=1))
    if kind == "on_trade_result_event":
        client.on_trade_result_event(callback)
        return ResponseFrame(CMD_TRADE_RESULT_PUSH, 0, _trade_result_bytes(resp_retcode=10009, resp_order=77))
    if kind == "on_account_event":
        client.on_account_event(callback)
        return ResponseFrame(CMD_ACCOUNT_UPDATE_PUSH, 0, _account_main_bytes())
    raise AssertionError(f"unknown kind {kind}")


@pytest.mark.parametrize(
    "kind",
    [
        "on_tick",
        "on_position_update",
        "on_order_update",
        "on_trade_update",
        "on_account_update",
        "on_symbol_details",
        "on_trade_result",
        "on_trade_transaction",
        "on_book_update",
        "on_tick_event",
        "on_book_event",
        "on_trade_result_event",
        "on_account_event",
    ],
)
@pytest.mark.parametrize("exc_cls", [KeyError, ValueError, TypeError, IndexError])
async def test_push_wrapper_user_callback_errors_reach_on_callback_error(kind: str, exc_cls: type[Exception]) -> None:
    client = MT5WebClient()
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    errors: list[tuple[Exception, object]] = []
    client.on_callback_error(lambda exc, cb: errors.append((exc, cb)))

    def bad_callback(_payload) -> None:
        raise exc_cls("user bug")

    frame = _register_and_frame(client, kind, bad_callback)
    await client.transport._dispatch(frame)

    assert len(errors) == 1
    assert isinstance(errors[0][0], exc_cls)


# ---------------------------------------------------------------------------
# 26.9 (S3-05): TradeResultEvent.order and retcode mapping
# ---------------------------------------------------------------------------


async def test_on_trade_result_event_extracts_trade_order_and_retcode() -> None:
    client = MT5WebClient()
    events: list[TradeResultEvent] = []
    client.on_trade_result_event(events.append)

    body = _trade_result_bytes(
        action_result_code=10008,
        action_order=111,
        resp_retcode=10009,
        resp_order=987654,
        resp_volume=250,
        resp_price=1.2345,
        resp_comment="filled",
    )
    await client.transport._dispatch(ResponseFrame(CMD_TRADE_RESULT_PUSH, 0, body))

    assert len(events) == 1
    assert events[0].retcode == 10009
    assert events[0].order == 987654
    assert events[0].volume == 250.0
    assert events[0].price == pytest.approx(1.2345)
    assert events[0].comment == "filled"


async def test_on_trade_result_event_falls_back_to_push_header_without_response_part() -> None:
    client = MT5WebClient()
    events: list[TradeResultEvent] = []
    client.on_trade_result_event(events.append)

    body = _trade_result_bytes(action_result_code=10004, action_order=555666, resp_retcode=None)
    await client.transport._dispatch(ResponseFrame(CMD_TRADE_RESULT_PUSH, 0, body))

    assert len(events) == 1
    assert events[0].retcode == 10004
    assert events[0].order == 555666


# ---------------------------------------------------------------------------
# 26.10 (S3-06): Incremental tick batch rolling merge
# ---------------------------------------------------------------------------


def test_parse_tick_batch_merges_multiple_partial_ticks_same_symbol_with_seed_cache() -> None:
    symbols_by_id = {1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}
    tick_cache = {1: {"bid": 1.0900, "ask": 1.0910, "last": 1.0905}}
    body = _tick_bytes(symbol_id=1, bid=1.1000, ask=0.0, last=0.0) + _tick_bytes(
        symbol_id=1, bid=0.0, ask=1.1005, last=0.0
    )

    ticks = _parse_tick_batch(body, symbols_by_id, tick_cache_by_id=tick_cache, price_mode="normalized")

    assert len(ticks) == 2
    assert ticks[0]["bid"] == pytest.approx(1.1000)
    assert ticks[0]["ask"] == pytest.approx(1.0910)
    assert ticks[1]["bid"] == pytest.approx(1.1000)
    assert ticks[1]["ask"] == pytest.approx(1.1005)
    assert ticks[1]["last"] == pytest.approx(1.0905)


def test_parse_tick_batch_merges_same_symbol_without_prior_cache() -> None:
    symbols_by_id = {
        1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5),
        2: SymbolInfo(name="GBPUSD", symbol_id=2, digits=5),
    }
    body = (
        _tick_bytes(symbol_id=1, bid=1.1000, ask=0.0)
        + _tick_bytes(symbol_id=2, bid=1.2500, ask=0.0)
        + _tick_bytes(symbol_id=1, bid=0.0, ask=1.1005)
        + _tick_bytes(symbol_id=2, bid=0.0, ask=1.2508)
    )

    ticks = _parse_tick_batch(body, symbols_by_id, tick_cache_by_id=None, price_mode="normalized")

    assert [t["symbol_id"] for t in ticks] == [1, 2, 1, 2]
    assert ticks[2]["bid"] == pytest.approx(1.1000)
    assert ticks[2]["ask"] == pytest.approx(1.1005)
    assert ticks[3]["bid"] == pytest.approx(1.2500)
    assert ticks[3]["ask"] == pytest.approx(1.2508)


async def test_cache_tick_push_preserves_rolling_merged_fields() -> None:
    client = MT5WebClient(tick_price_mode="normalized")
    client._symbols["EURUSD"] = client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._tick_cache_by_id[1] = {"bid": 1.0900, "ask": 1.0910, "last": 0.0}

    body = (
        _tick_bytes(symbol_id=1, tick_time=1000, bid=1.1000, ask=0.0, tick_volume=0)
        + _tick_bytes(symbol_id=1, tick_time=1001, bid=0.0, ask=1.1005, tick_volume=0)
        + _tick_bytes(symbol_id=1, tick_time=1002, bid=0.0, ask=0.0, last=1.1002, tick_volume=5)
    )
    await client.transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, body))

    assert client._tick_cache_by_id[1]["bid"] == pytest.approx(1.1000)
    assert client._tick_cache_by_id[1]["ask"] == pytest.approx(1.1005)
    assert client._tick_cache_by_id[1]["last"] == pytest.approx(1.1002)
    info_ticks = await client.copy_ticks_range("EURUSD", 1000, 1002, COPY_TICKS_INFO)
    trade_ticks = await client.copy_ticks_range("EURUSD", 1000, 1002, COPY_TICKS_TRADE)
    assert [t["time"] for t in info_ticks] == [1000, 1001]
    assert [t["time"] for t in trade_ticks] == [1002]


# ---------------------------------------------------------------------------
# 26.11 (S3-08): Async callback support across all push handlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "on_tick",
        "on_position_update",
        "on_order_update",
        "on_trade_update",
        "on_symbol_update",
        "on_account_update",
        "on_login_status",
        "on_symbol_details",
        "on_trade_result",
        "on_trade_transaction",
        "on_book_update",
        "on_tick_event",
        "on_book_event",
        "on_trade_result_event",
        "on_account_event",
    ],
)
async def test_all_push_handlers_support_async_def_callbacks(kind: str) -> None:
    client = MT5WebClient()
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    received: list[object] = []

    async def async_callback(payload) -> None:
        await asyncio.sleep(0)
        received.append(payload)

    if kind == "on_symbol_update":
        client.on_symbol_update(async_callback)
        frame = ResponseFrame(CMD_SYMBOL_UPDATE_PUSH, 0, b"abc")
    elif kind == "on_login_status":
        client.on_login_status(async_callback)
        frame = ResponseFrame(CMD_LOGIN_STATUS_PUSH, 0, b"xyz")
    else:
        frame = _register_and_frame(client, kind, async_callback)

    await _dispatch_and_drain(client, frame)
    assert len(received) == 1


async def test_on_tick_event_and_on_book_event_async_callbacks_preserve_order() -> None:
    client = MT5WebClient(tick_price_mode="normalized")
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)

    tick_events: list[TickEvent] = []
    book_events: list[BookEvent] = []

    async def on_tick_cb(event: TickEvent) -> None:
        await asyncio.sleep(0)
        tick_events.append(event)

    async def on_book_cb(event: BookEvent) -> None:
        await asyncio.sleep(0)
        book_events.append(event)

    client.on_tick_event(on_tick_cb)
    client.on_book_event(on_book_cb)

    tick_body = _tick_bytes(symbol_id=1, bid=1.10) + _tick_bytes(symbol_id=2, bid=1.25)
    await _dispatch_and_drain(client, ResponseFrame(CMD_TICK_PUSH, 0, tick_body))

    assert [e.symbol_id for e in tick_events] == [1, 2]

    # Build a 2-symbol book frame
    b1 = _book_push_bytes(symbol_id=1, bid_price=1.10, ask_price=1.11)[4:]
    b2 = _book_push_bytes(symbol_id=2, bid_price=1.25, ask_price=1.26)[4:]
    book_body = struct.pack("<I", 2) + b1 + b2
    await _dispatch_and_drain(client, ResponseFrame(CMD_BOOK_PUSH, 0, book_body))

    assert [e.symbol_id for e in book_events] == [1, 2]


async def test_on_tick_event_async_callback_error_has_no_unawaited_coroutine() -> None:
    client = MT5WebClient(tick_price_mode="normalized")
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)
    errors: list[Exception] = []
    client.on_callback_error(lambda exc, _cb: errors.append(exc))

    async def failing_cb(_event: TickEvent) -> None:
        await asyncio.sleep(0)
        raise ValueError("async tick failure")

    client.on_tick_event(failing_cb)
    tick_body = _tick_bytes(symbol_id=1, bid=1.10) + _tick_bytes(symbol_id=2, bid=1.25)
    await _dispatch_and_drain(client, ResponseFrame(CMD_TICK_PUSH, 0, tick_body))

    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


async def test_on_tick_event_hybrid_async_then_sync_callback() -> None:
    client = MT5WebClient(tick_price_mode="normalized")
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)
    seen: list[int] = []

    def hybrid_cb(event: TickEvent):
        if event.symbol_id == 1:

            async def _first() -> None:
                await asyncio.sleep(0)
                seen.append(event.symbol_id)

            return _first()
        seen.append(event.symbol_id)
        return None

    client.on_tick_event(hybrid_cb)
    tick_body = _tick_bytes(symbol_id=1, bid=1.10) + _tick_bytes(symbol_id=2, bid=1.25)
    await _dispatch_and_drain(client, ResponseFrame(CMD_TICK_PUSH, 0, tick_body))

    assert seen == [1, 2]


# ---------------------------------------------------------------------------
# 26.12 (S3-07, S3-10): Symbol cache invalidation & market data error checks
# ---------------------------------------------------------------------------


async def test_invalidate_symbol_cache_clears_full_symbols_and_forces_refetch() -> None:
    client = MT5WebClient(symbol_cache_ttl=60.0)
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client._symbols_by_id[1] = client._symbols["EURUSD"]
    client._symbols_loaded_at = 100.0
    client._cache_full_symbol("EURUSD", {"trade_symbol": "EURUSD", "symbol_id": 1, "digits": 5})

    client.invalidate_symbol_cache()

    assert client._symbols_loaded_at == 0.0
    assert client._full_symbols == {}
    assert client._full_symbols_loaded_at == {}

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=struct.pack("<I", 0))
    )
    info = await client.symbol_info("EURUSD")
    assert info is not None
    assert info["trade_symbol"] == "EURUSD"
    client.transport.send_command.assert_awaited_once()


async def test_get_symbols_gzip_corrupted_raises_protocol_error() -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(
            command=CMD_GET_SYMBOLS_GZIP,
            code=0,
            body=b"\x00\x00\x00\x00not-a-valid-zlib-stream",
        )
    )

    with pytest.raises(ProtocolError, match=r"failed to decompress symbol list"):
        await client.get_symbols(use_gzip=True)


async def test_get_symbol_groups_nonzero_code_raises_protocol_error() -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOL_GROUPS, code=4, body=b"")
    )

    with pytest.raises(ProtocolError, match=r"symbol groups command 9 failed: code=4"):
        await client.get_symbol_groups()


async def test_get_spreads_nonzero_code_raises_protocol_error() -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_GET_SPREADS, code=7, body=b""))

    with pytest.raises(ProtocolError, match=r"spreads command 20 failed: code=7"):
        await client.get_spreads([1, 2])


async def test_on_tick_event_async_callback_closed_before_execution_has_no_warning() -> None:
    """NEW-S3-01: closing transport before queued _SequentialAsyncCallbacks runs must not leak coroutines."""
    import gc

    client = MT5WebClient()
    seen: list[int] = []

    async def on_tick_cb(event) -> None:
        seen.append(event.symbol_id)

    client.on_tick_event(on_tick_cb)
    tick_body = _tick_bytes(symbol_id=1, bid=1.1, ask=1.2)
    await client.transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, tick_body))
    await client.transport.close()
    gc.collect()
    assert seen == []
