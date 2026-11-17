"""Regression tests for Iteration 07 Group 4: trading, validation, currency, OrderManager, types, and helpers."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from pymt5._order_manager import OrderManager, OrderState
from pymt5._trading import _TradingMixin
from pymt5.client import MT5WebClient
from pymt5.constants import (
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_TRADE_HISTORY,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_STOP_LIMIT,
    PROP_F64,
    PROP_FIXED_STRING,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_ERROR,
    TRADE_RETCODE_INVALID_PRICE,
    TRADE_RETCODE_INVALID_STOPS,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PLACED,
)
from pymt5.exceptions import ValidationError
from pymt5.helpers import pad_ascii, pad_bytes, truncate_ascii
from pymt5.protocol import SeriesCodec
from pymt5.schemas import (
    BOOK_LEVEL_FIELD_NAMES,
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
    SYMBOL_GROUP_FIELD_NAMES,
    TICK_FIELD_NAMES,
    TRADE_RESULT_RESPONSE_FIELD_NAMES,
    TRADE_RESULT_RESPONSE_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import (
    AccountDocument,
    AccountOpeningRequest,
    BookLevelRecord,
    DealRecord,
    DemoAccountRequest,
    OpenAccountResult,
    OrderRecord,
    PositionRecord,
    RealAccountRequest,
    SymbolGroupRecord,
    SymbolInfo,
    TickRecord,
    TradeResult,
)


def _make_client() -> MT5WebClient:
    client = MT5WebClient()
    client.transport.is_ready = True
    client.transport.send_command = AsyncMock()
    return client


def _serialize_from_schema(schema: list[dict[str, int]], field_names: list[str], values: dict[str, object]) -> bytes:
    specs = []
    for field_spec, name in zip(schema, field_names):
        entry = dict(field_spec)
        prop_type = entry["propType"]
        default: object
        if prop_type == PROP_FIXED_STRING:
            default = ""
        elif prop_type == PROP_F64:
            default = 0.0
        else:
            default = 0
        entry["propValue"] = values.get(name, default)
        specs.append(entry)
    return SeriesCodec.serialize(specs)


def _symbol_info(
    *,
    trade_stops_level: int = 20,
    point: float = 0.0001,
    trade_calc_mode: int = 0,
    contract_size: float = 100000.0,
    currency_margin: str = "EUR",
) -> dict[str, object]:
    return {
        "trade_symbol": "EURUSD",
        "digits": 5,
        "trade_calc_mode": trade_calc_mode,
        "currency_profit": "USD",
        "currency_margin": currency_margin,
        "contract_size": contract_size,
        "tick_size": 0.00001,
        "tick_value": 1.0,
        "point": point,
        "trade_mode": 4,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_stops_level": trade_stops_level,
        "filling_mode": 3,
    }


# =====================================================================
# 26.6 (S4-01): Finite positive validation
# =====================================================================


class TestFiniteValidation:
    @pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), 0, 0.0, -1.0, True, False])
    def test_validate_volume_rejects_non_finite_and_non_positive(self, bad_value: float | bool) -> None:
        from pymt5._validation import validate_volume

        with pytest.raises(ValidationError, match="volume must be a finite value > 0"):
            validate_volume(bad_value)

    @pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), 0, 0.0, -0.5, True, False])
    def test_validate_price_rejects_non_finite_and_non_positive(self, bad_value: float | bool) -> None:
        from pymt5._validation import validate_price

        with pytest.raises(ValidationError, match="stop_loss must be a finite value > 0"):
            validate_price(bad_value, name="stop_loss")


# =====================================================================
# 28.1 (S4-02): Stop-Limit independent reference price & stop checks
# =====================================================================


class TestStopLimitOrderCheck:
    async def test_resolve_reference_price_uses_market_quote_for_stop_limit(self) -> None:
        client = _make_client()
        info = _symbol_info()
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})

        buy_req = {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "type": ORDER_TYPE_BUY_STOP_LIMIT,
            "price_order": 1.1030,
            "price_trigger": 1.1050,
        }
        sell_req = {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "type": ORDER_TYPE_SELL_STOP_LIMIT,
            "price_order": 1.0970,
            "price_trigger": 1.0950,
        }
        assert await client._resolve_order_check_price(buy_req, info) == pytest.approx(1.1000)
        assert await client._resolve_order_check_price(sell_req, info) == pytest.approx(1.0998)

        client.symbol_info_tick = MagicMock(return_value=None)
        assert await client._resolve_order_check_price(buy_req, info) == pytest.approx(1.1030)
        assert await client._resolve_order_check_price(sell_req, info) == pytest.approx(1.0970)

    async def test_buy_and_sell_stop_limit_valid_with_stops_level(self) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info(trade_stops_level=20, point=0.0001))
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": 10000.0, "margin": 0.0})
        client.order_calc_margin = AsyncMock(return_value=100.0)

        # Notice sl=1.1005 is only 5 pips from ask=1.1000, but 25 pips (>= 20) from limit=1.1030.
        buy_res = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY_STOP_LIMIT,
                "volume": 0.1,
                "price": 1.1050,
                "stoplimit": 1.1030,
                "sl": 1.1005,
                "tp": 1.1060,
            }
        )
        assert buy_res["retcode"] == 0

        # Notice sl=1.0995 is only 3 pips from bid=1.0998, but 25 pips (>= 20) from limit=1.0970.
        sell_res = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_SELL_STOP_LIMIT,
                "volume": 0.1,
                "price": 1.0950,
                "stoplimit": 1.0970,
                "sl": 1.0995,
                "tp": 1.0940,
            }
        )
        assert sell_res["retcode"] == 0

    @pytest.mark.parametrize(
        ("order_type", "price", "stoplimit", "sl", "tp", "stops_level"),
        [
            # Buy stop-limit trigger too close / wrong side of ask (1.1000)
            (ORDER_TYPE_BUY_STOP_LIMIT, 1.1010, 1.1030, 0.0, 0.0, 20),
            (ORDER_TYPE_BUY_STOP_LIMIT, 1.0990, 1.1030, 0.0, 0.0, 20),
            # Buy stop-limit SL/TP too close or wrong direction relative to stoplimit (1.1030)
            (ORDER_TYPE_BUY_STOP_LIMIT, 1.1050, 1.1030, 1.1020, 0.0, 20),
            (ORDER_TYPE_BUY_STOP_LIMIT, 1.1050, 1.1030, 1.1040, 0.0, 0),
            (ORDER_TYPE_BUY_STOP_LIMIT, 1.1050, 1.1030, 0.0, 1.1040, 20),
            (ORDER_TYPE_BUY_STOP_LIMIT, 1.1050, 1.1030, 0.0, 1.1020, 0),
            # Sell stop-limit trigger too close / wrong side of bid (1.0998)
            (ORDER_TYPE_SELL_STOP_LIMIT, 1.0985, 1.0970, 0.0, 0.0, 20),
            (ORDER_TYPE_SELL_STOP_LIMIT, 1.1010, 1.0970, 0.0, 0.0, 20),
            # Sell stop-limit SL/TP too close or wrong direction relative to stoplimit (1.0970)
            (ORDER_TYPE_SELL_STOP_LIMIT, 1.0950, 1.0970, 1.0980, 0.0, 20),
            (ORDER_TYPE_SELL_STOP_LIMIT, 1.0950, 1.0970, 1.0960, 0.0, 0),
            (ORDER_TYPE_SELL_STOP_LIMIT, 1.0950, 1.0970, 0.0, 1.0960, 20),
            (ORDER_TYPE_SELL_STOP_LIMIT, 1.0950, 1.0970, 0.0, 1.0980, 0),
        ],
    )
    async def test_stop_limit_rejects_invalid_trigger_or_stops(
        self,
        order_type: int,
        price: float,
        stoplimit: float,
        sl: float,
        tp: float,
        stops_level: int,
    ) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info(trade_stops_level=stops_level, point=0.0001))
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": 10000.0, "margin": 0.0})
        client.order_calc_margin = AsyncMock(return_value=100.0)

        res = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": order_type,
                "volume": 0.1,
                "price": price,
                "stoplimit": stoplimit,
                "sl": sl,
                "tp": tp,
            }
        )
        assert res["retcode"] == TRADE_RETCODE_INVALID_STOPS

    async def test_stop_limit_missing_trigger_price_and_no_tick_fallback(self) -> None:
        client = _make_client()
        info = _symbol_info(trade_stops_level=20, point=0.0001)
        client.symbol_info = AsyncMock(return_value=info)
        client.symbol_info_tick = MagicMock(return_value=None)
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": 10000.0, "margin": 0.0})
        client.order_calc_margin = AsyncMock(return_value=100.0)

        missing_trigger = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY_STOP_LIMIT,
                "volume": 0.1,
                "price": 0.0,
                "stoplimit": 1.1030,
            }
        )
        assert missing_trigger["retcode"] == TRADE_RETCODE_INVALID_PRICE

        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        missing_limit = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY_STOP_LIMIT,
                "volume": 0.1,
                "price": 1.1050,
                "stoplimit": 0.0,
            }
        )
        assert missing_limit["retcode"] == TRADE_RETCODE_INVALID_PRICE
        client.symbol_info_tick = MagicMock(return_value=None)

        # Without tick in cache, sl/tp still validated against stoplimit and order succeeds.
        no_tick_valid = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY_STOP_LIMIT,
                "volume": 0.1,
                "price": 1.1050,
                "stoplimit": 1.1030,
                "sl": 1.1000,
                "tp": 1.1080,
            }
        )
        assert no_tick_valid["retcode"] == 0

        # Direct stop-limit helper fallback to price_trigger when price_order is absent.
        err = client._validate_order_stops(
            info,
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY_STOP_LIMIT,
                "price_order": 0.0,
                "price_trigger": 1.1050,
                "sl": 1.1040,
                "tp": 0.0,
            },
            1.1050,
        )
        assert err is not None


# =====================================================================
# 28.2 (S4-03): history_deals_get(ticket=...) filtering
# =====================================================================


class TestHistoryDealsTicketFilter:
    async def test_history_deals_get_filters_by_deal_ticket_not_trade_order(self) -> None:
        client = _make_client()
        client.get_deals = AsyncMock(
            return_value=[
                {"deal": 10, "trade_order": 200, "trade_symbol": "EURUSD"},
                {"deal": 20, "trade_order": 10, "trade_symbol": "GBPUSD"},
                {"ticket": 30, "trade_order": 40, "trade_symbol": "USDJPY"},
                {"trade_order": 50, "trade_symbol": "AUDUSD"},
            ]
        )

        matched = await client.history_deals_get(ticket=10)
        assert matched == [{"deal": 10, "trade_order": 200, "trade_symbol": "EURUSD"}]
        assert await client.history_deals_get(ticket=200) == []
        assert await client.history_deals_get(ticket=30) == [
            {"ticket": 30, "trade_order": 40, "trade_symbol": "USDJPY"}
        ]
        assert await client.history_deals_get(ticket=50) == []
        assert await client.history_deals_get(ticket=0) == []


# =====================================================================
# 28.3 (S4-04): Zero equity & zero margin_free handling in order_check
# =====================================================================


class TestOrderCheckZeroEquityAndMarginFree:
    async def test_order_check_preserves_zero_equity(self) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info(trade_stops_level=0))
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": 0.0, "margin": 0.0})
        client.order_calc_margin = AsyncMock(return_value=50.0)

        res = await client.order_check(
            {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 0.1, "price": 1.1}
        )
        assert res["equity"] == 0.0
        assert res["margin_free"] == -50.0
        assert res["retcode"] == TRADE_RETCODE_NO_MONEY

    async def test_order_check_falls_back_only_when_equity_is_none(self) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info(trade_stops_level=0))
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": None, "margin": 0.0})
        client.order_calc_margin = AsyncMock(return_value=50.0)

        res = await client.order_check(
            {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 0.1, "price": 1.1}
        )
        assert res["equity"] == 10000.0
        assert res["retcode"] == 0

    async def test_order_check_exception_path_preserves_zero_equity_and_margin_free(self) -> None:
        client = _make_client()
        client._normalize_order_request = MagicMock(side_effect=ValueError("boom"))
        client.get_account = AsyncMock(
            return_value={"balance": 10000.0, "equity": 0.0, "margin": 0.0, "margin_free": 0.0}
        )

        res = await client.order_check({"action": TRADE_ACTION_DEAL, "symbol": "EURUSD"})
        assert res["retcode"] == -99
        assert res["equity"] == 0.0
        assert res["margin_free"] == 0.0


# =====================================================================
# 28.4 (S4-05): Deterministic broker symbol matching in _currency.py
# =====================================================================


class TestCurrencyBrokerSymbolMatching:
    @pytest.mark.parametrize(
        ("symbols", "expected"),
        [
            (["EURUSD.pro"], "EURUSD.pro"),
            (["EURUSDm"], "EURUSDm"),
            (["mEURUSD"], "mEURUSD"),
            (["FX.EURUSD"], "FX.EURUSD"),
            (["EURUSD-ECN"], "EURUSD-ECN"),
            (["EUR_USD"], "EUR_USD"),
            (["EUR/USD"], "EUR/USD"),
            (["EUR#USD"], "EUR#USD"),
            (["EURUSD:raw"], "EURUSD:raw"),
            (["EUR.USD.pro"], "EUR.USD.pro"),
            (["FX.EUR.USD"], "FX.EUR.USD"),
            (["EURUSDmicro"], "EURUSDmicro"),
            (["EURUSDmini"], "EURUSDmini"),
            (["EURUSDstd"], "EURUSDstd"),
            (["EURUSDc"], "EURUSDc"),
            (["EURUSDi"], "EURUSDi"),
            (["EURUSDs"], "EURUSDs"),
            (["EURUSDz"], "EURUSDz"),
            (["fxEURUSD"], "fxEURUSD"),
            (["eurusd"], "eurusd"),
            (["EURUSDm.pro"], "EURUSDm.pro"),
            (["EURUSD", "EUR.USD", "EURUSD.pro"], "EURUSD"),
            (["EUR.USD", "EURUSD.pro"], "EUR.USD"),
            (["EURUSD.pro", "EURUSD-ECN"], "EURUSD-ECN"),
            (["EURUSDmicro", "EURUSDm"], "EURUSDm"),
            (["mEURUSD", "EURUSDm"], "EURUSDm"),
        ],
    )
    async def test_find_conversion_symbol_name_matches_broker_variants(self, symbols: list[str], expected: str) -> None:
        client = _make_client()
        client._symbols = {name: SymbolInfo(name=name, symbol_id=idx + 1, digits=5) for idx, name in enumerate(symbols)}

        assert await client._find_conversion_symbol_name("EUR", "USD") == expected

    @pytest.mark.parametrize(
        ("symbols", "base", "quote"),
        [
            (["EURUSDX", "MYEURUSD", "EURUSDFORD", "EUR_STOCK_USD", "AAPL", "MSFT", "..."], "EUR", "USD"),
            (["EUR_USD_FX"], "EUR", "FX"),
            (["USDEUR"], "EUR", "USD"),
        ],
    )
    async def test_find_conversion_symbol_name_rejects_false_positives(
        self, symbols: list[str], base: str, quote: str
    ) -> None:
        client = _make_client()
        client._symbols = {name: SymbolInfo(name=name, symbol_id=idx + 1, digits=5) for idx, name in enumerate(symbols)}

        assert await client._find_conversion_symbol_name(base, quote) is None


# =====================================================================
# 28.5 / 28.6 / 28.7 (S4-06, S4-07, S4-08): OrderManager fixes
# =====================================================================


class TestOrderManagerHardening:
    @pytest.mark.parametrize(
        ("terminal_state_code", "terminal_state"),
        [
            (4, OrderState.FILLED),
            (2, OrderState.CANCELED),
            (5, OrderState.REJECTED),
            (6, OrderState.EXPIRED),
        ],
    )
    def test_terminal_states_ignore_out_of_order_and_duplicate_pushes(
        self, terminal_state_code: int, terminal_state: OrderState
    ) -> None:
        mgr = OrderManager()
        events: list[tuple[int, OrderState, OrderState]] = []
        mgr.on_state_change(lambda o, old: events.append((o.order_id, old, o.state)))
        mgr.track_order(1, "EURUSD", ORDER_TYPE_BUY, 1.0, 1.1)

        mgr.update_from_push({"order": 1, "order_state": terminal_state_code})
        assert mgr.get_order(1).state == terminal_state
        assert len(events) == 1

        # Out-of-order PLACED/PARTIAL and duplicate terminal pushes must be ignored.
        mgr.update_from_push({"order": 1, "order_state": 1, "volume_current": 1.0})
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": 0.5})
        mgr.update_from_push({"order": 1, "order_state": terminal_state_code})
        assert mgr.get_order(1).state == terminal_state
        assert len(events) == 1

        # Trade result after terminal state also does not regress or re-fire.
        mgr.update_from_trade_result({"order": 1, "retcode": TRADE_RETCODE_PLACED})
        assert mgr.get_order(1).state == terminal_state
        assert len(events) == 1

    def test_volume_current_validation_clamping_monotonicity_and_filled_completion(self) -> None:
        mgr = OrderManager()
        order = mgr.track_order(1, "EURUSD", ORDER_TYPE_BUY, 1.0, 1.1)

        # Excess value (> 1.0) clamped to order.volume => filled=0.0
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": 5.0})
        assert order.filled_volume == pytest.approx(0.0)

        # Partial fill to 0.4 remaining => filled=0.6
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": 0.4})
        assert order.filled_volume == pytest.approx(0.6)

        # Out-of-order larger volume_current => filled stays monotonic at 0.6
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": 0.8})
        assert order.filled_volume == pytest.approx(0.6)

        # NaN / inf / None / bool / non-numeric string ignored safely
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": float("nan")})
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": float("inf")})
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": None})
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": True})
        mgr.update_from_push({"order": 1, "order_state": 3, "volume_current": "invalid"})
        assert order.filled_volume == pytest.approx(0.6)

        # Transition to FILLED without valid volume_current sets filled_volume = order.volume
        mgr.update_from_push({"order": 1, "order_state": 4, "volume_current": float("nan")})
        assert order.state == OrderState.FILLED
        assert order.filled_volume == pytest.approx(1.0)

        # Negative volume_current clamped to 0.0 => filled=volume
        order2 = mgr.track_order(2, "EURUSD", ORDER_TYPE_BUY, 2.0, 1.1)
        mgr.update_from_push({"order": 2, "order_state": 3, "volume_current": -1.0})
        assert order2.filled_volume == pytest.approx(2.0)

    def test_update_from_push_with_real_order_schema_dict(self) -> None:
        mgr = OrderManager()
        mgr.track_order(777, "EURUSD", ORDER_TYPE_BUY, 1.0, 1.1)

        raw_partial = _serialize_from_schema(
            ORDER_SCHEMA,
            ORDER_FIELD_NAMES,
            {
                "trade_order": 777,
                "trade_symbol": "EURUSD",
                "order_state": 3,
                "volume_initial": 100_000_000,
                "volume_current": 25_000_000,
            },
        )
        push_partial = dict(zip(ORDER_FIELD_NAMES, SeriesCodec.parse(raw_partial, ORDER_SCHEMA)))
        mgr.update_from_push(push_partial)

        order = mgr.get_order(777)
        assert order is not None
        assert order.state == OrderState.PARTIALLY_FILLED
        assert order.filled_volume == pytest.approx(0.75)

        raw_filled = _serialize_from_schema(
            ORDER_SCHEMA,
            ORDER_FIELD_NAMES,
            {
                "trade_order": 777,
                "trade_symbol": "EURUSD",
                "order_state": 4,
                "volume_initial": 100_000_000,
                "volume_current": 0,
            },
        )
        push_filled = dict(zip(ORDER_FIELD_NAMES, SeriesCodec.parse(raw_filled, ORDER_SCHEMA)))
        mgr.update_from_push(push_filled)
        assert order.state == OrderState.FILLED
        assert order.filled_volume == pytest.approx(1.0)

    def test_update_from_trade_result_accepts_dataclass_and_trade_order_dict(self) -> None:
        mgr = OrderManager()
        mgr.track_order(101, "EURUSD", ORDER_TYPE_BUY, 1.0, 1.1)
        tr = TradeResult(
            retcode=TRADE_RETCODE_DONE_PARTIAL,
            description="partial",
            success=True,
            order=101,
            volume=40_000_000,
            price=1.1025,
            comment="partial fill",
        )
        updated = mgr.update_from_trade_result(tr)
        assert updated is not None
        assert updated.state == OrderState.PARTIALLY_FILLED
        assert updated.filled_volume == pytest.approx(0.4)
        assert updated.fill_price == pytest.approx(1.1025)

        mgr.track_order(202, "GBPUSD", ORDER_TYPE_SELL, 0.5, 1.25)
        raw_resp = _serialize_from_schema(
            TRADE_RESULT_RESPONSE_SCHEMA,
            TRADE_RESULT_RESPONSE_FIELD_NAMES,
            {
                "retcode": TRADE_RETCODE_DONE,
                "trade_order": 202,
                "volume": 50_000_000,
                "price": 1.2505,
                "comment": "filled",
            },
        )
        decoded = dict(
            zip(TRADE_RESULT_RESPONSE_FIELD_NAMES, SeriesCodec.parse(raw_resp, TRADE_RESULT_RESPONSE_SCHEMA))
        )
        updated2 = mgr.update_from_trade_result(decoded)
        assert updated2 is not None
        assert updated2.state == OrderState.FILLED
        assert updated2.filled_volume == pytest.approx(0.5)
        assert updated2.fill_price == pytest.approx(1.2505)


# =====================================================================
# 28.8 (S4-09): Short body / truncation guards in _trading.py
# =====================================================================


class TestTradingShortBodyAndTruncation:
    @pytest.mark.parametrize("short_body", [b"", b"\x01", b"\x01\x00", b"\x01\x00\x00"])
    async def test_short_bodies_do_not_raise_struct_error(self, short_body: bytes) -> None:
        client = _make_client()

        client.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=short_body)
        )
        assert await client.get_positions_and_orders() == {"positions": [], "orders": []}

        client.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=short_body)
        )
        assert await client.get_trade_history() == {"deals": [], "orders": []}

        tr = client._parse_trade_response(short_body, "EURUSD", TRADE_ACTION_DEAL, 100)
        assert tr.retcode == -1
        assert tr.success is False

    async def test_truncated_positions_and_deals_do_not_raise_or_misparsed_orders(self) -> None:
        client = _make_client()

        pos_bytes = _serialize_from_schema(POSITION_SCHEMA, POSITION_FIELD_NAMES, {"position_id": 11})
        # Header claims 2 positions, but 2nd position is truncated (would look like order_count=999 if parsed).
        truncated_pos_body = struct.pack("<I", 2) + pos_bytes + struct.pack("<I", 999) + b"\x00" * 10
        client.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=truncated_pos_body)
        )
        pos_res = await client.get_positions_and_orders()
        assert len(pos_res["positions"]) == 1
        assert pos_res["orders"] == []

        deal_bytes = _serialize_from_schema(DEAL_SCHEMA, DEAL_FIELD_NAMES, {"deal": 22})
        truncated_deal_body = struct.pack("<I", 2) + deal_bytes + struct.pack("<I", 999) + b"\x00" * 10
        client.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_GET_TRADE_HISTORY, code=0, body=truncated_deal_body)
        )
        hist_res = await client.get_trade_history()
        assert len(hist_res["deals"]) == 1
        assert hist_res["orders"] == []


# =====================================================================
# 28.9 (S4-10): uint64 overflow & finite price checks
# =====================================================================


class TestOrderSendAndTradeRequestLimits:
    @pytest.mark.parametrize(
        "bad_volume",
        [-0.01, 0.0, float("nan"), float("inf"), float("-inf"), 1e12, 1e-12],
    )
    async def test_order_send_rejects_invalid_and_overflow_volumes(self, bad_volume: float) -> None:
        client = _make_client()
        with pytest.raises(ValidationError):
            await client.order_send({"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": bad_volume})
        client.transport.send_command.assert_not_awaited()

    async def test_order_send_allows_non_volume_actions_and_rejects_nonfinite_prices(self) -> None:
        client = _make_client()
        client.transport.send_command = AsyncMock(
            return_value=CommandResult(command=12, code=0, body=struct.pack("<I", TRADE_RETCODE_DONE))
        )
        res = await client.order_send({"action": TRADE_ACTION_REMOVE, "order": 12345})
        assert res.retcode == TRADE_RETCODE_DONE

        for bad_field in ("price", "sl", "tp"):
            req = {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.1, bad_field: float("nan")}
            with pytest.raises(ValidationError):
                await client.order_send(req)

        with pytest.raises(ValidationError):
            await client.order_send(
                {
                    "action": TRADE_ACTION_PENDING,
                    "symbol": "EURUSD",
                    "type": ORDER_TYPE_BUY_STOP_LIMIT,
                    "volume": 0.1,
                    "price": float("inf"),
                    "stoplimit": 1.1,
                }
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"trade_action": TRADE_ACTION_DEAL, "volume": -1},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 0xFFFFFFFFFFFFFFFF + 1},
            {"trade_action": TRADE_ACTION_DEAL, "volume": True},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 100, "price_order": float("nan")},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 100, "price_trigger": float("inf")},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 100, "price_sl": float("nan")},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 100, "price_tp": float("-inf")},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 100, "price_order": -1.0},
            {"trade_action": TRADE_ACTION_DEAL, "volume": 100, "price_trigger": -1.0},
            {"trade_action": TRADE_ACTION_MODIFY, "order": 123, "price_order": 0.0},
        ],
    )
    async def test_trade_request_rejects_uint64_overflow_and_bad_prices(self, kwargs: dict[str, object]) -> None:
        client = _make_client()
        with pytest.raises(ValidationError):
            await client.trade_request(**kwargs)  # type: ignore[arg-type]
        client.transport.send_command.assert_not_awaited()

    async def test_order_check_rejects_non_finite_volume_and_prices(self) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info())
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": 10000.0, "margin": 0.0})

        bad_vol = await client.order_check(
            {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": float("nan")}
        )
        assert bad_vol["retcode"] == TRADE_RETCODE_INVALID_VOLUME

        bad_price = await client.order_check(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY,
                "volume": 0.1,
                "price": float("inf"),
            }
        )
        assert bad_price["retcode"] == TRADE_RETCODE_INVALID_PRICE

        bad_stop = await client.order_check(
            {
                "action": TRADE_ACTION_DEAL,
                "symbol": "EURUSD",
                "type": ORDER_TYPE_BUY,
                "volume": 0.1,
                "price": 1.1,
                "sl": float("nan"),
            }
        )
        assert bad_stop["retcode"] == TRADE_RETCODE_INVALID_STOPS


# =====================================================================
# 28.10 (S4-11): Margin estimation failure propagation in order_check
# =====================================================================


class TestOrderCheckMarginFailurePropagation:
    async def test_order_check_propagates_real_margin_failure_and_preserves_last_error(self) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info(contract_size=0.0, trade_stops_level=0))
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        client.get_account = AsyncMock(
            return_value={"balance": 10000.0, "equity": 10000.0, "margin": 0.0, "currency": "USD", "leverage": 100}
        )

        res = await client.order_check(
            {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 0.1, "price": 1.1}
        )
        assert res["retcode"] == TRADE_RETCODE_ERROR
        assert client.last_error()[0] == -14
        assert "contract_size" in client.last_error()[1]
        assert "contract_size" in res["comment"]

    async def test_order_check_sets_error_when_mocked_calc_margin_returns_none(self) -> None:
        client = _make_client()
        client.symbol_info = AsyncMock(return_value=_symbol_info(trade_stops_level=0))
        client.symbol_info_tick = MagicMock(return_value={"ask": 1.1000, "bid": 1.0998})
        client.get_account = AsyncMock(return_value={"balance": 10000.0, "equity": 10000.0, "margin": 0.0})
        client.order_calc_margin = AsyncMock(return_value=None)

        res = await client.order_check(
            {"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "type": ORDER_TYPE_BUY, "volume": 0.1, "price": 1.1}
        )
        assert res["retcode"] == TRADE_RETCODE_ERROR
        assert client.last_error()[0] == TRADE_RETCODE_ERROR


# =====================================================================
# 28.11 (S4-12): Pre-validation in _order_helpers.py
# =====================================================================


class TestOrderHelpersPreValidation:
    async def test_close_position_validates_symbol_and_volume_before_querying_positions(self) -> None:
        client = _make_client()
        client.get_positions = AsyncMock()

        with pytest.raises(ValidationError):
            await client.close_position("", 123, 0.1)
        with pytest.raises(ValidationError):
            await client.close_position("EURUSD", 123, -1.0)
        with pytest.raises(ValidationError):
            await client.close_position("EURUSD", 123, float("nan"))

        client.get_positions.assert_not_awaited()
        client.transport.send_command.assert_not_awaited()

    async def test_other_order_helpers_validate_symbol_and_price_before_sending(self) -> None:
        client = _make_client()

        with pytest.raises(ValidationError):
            await client.close_position_by("", 1, 2)
        with pytest.raises(ValidationError):
            await client.modify_position_sltp("", 1, sl=1.1)
        with pytest.raises(ValidationError):
            await client.modify_pending_order("", 1, 1.1)
        with pytest.raises(ValidationError):
            await client.modify_pending_order("EURUSD", 1, 0.0)
        with pytest.raises(ValidationError):
            await client.modify_pending_order("EURUSD", 1, float("nan"))
        with pytest.raises(ValidationError):
            await client.buy_limit("EURUSD", 0.1, 0.0)
        with pytest.raises(ValidationError):
            await client.buy_limit("EURUSD", 0.1, float("inf"))

        client.transport.send_command.assert_not_awaited()


# =====================================================================
# 28.12 (S5-06, S4-13) & 29.1 part 2 (S5-03): types.py & helpers.py
# =====================================================================


class TestTypesAndHelpersAlignment:
    def test_typed_dicts_match_schemas_keys(self) -> None:
        assert set(PositionRecord.__annotations__) == set(POSITION_FIELD_NAMES)
        assert set(DealRecord.__annotations__) == set(DEAL_FIELD_NAMES)
        assert set(SymbolGroupRecord.__annotations__) == set(SYMBOL_GROUP_FIELD_NAMES)
        assert set(OrderRecord.__annotations__) == set(ORDER_FIELD_NAMES)
        assert set(BookLevelRecord.__annotations__) == set(BOOK_LEVEL_FIELD_NAMES)
        assert set(TICK_FIELD_NAMES).issubset(set(TickRecord.__annotations__))

        pos_raw = _serialize_from_schema(POSITION_SCHEMA, POSITION_FIELD_NAMES, {"position_id": 1})
        pos_dict = dict(zip(POSITION_FIELD_NAMES, SeriesCodec.parse(pos_raw, POSITION_SCHEMA)))
        assert set(pos_dict.keys()) == set(PositionRecord.__annotations__)

        deal_raw = _serialize_from_schema(DEAL_SCHEMA, DEAL_FIELD_NAMES, {"deal": 2})
        deal_dict = dict(zip(DEAL_FIELD_NAMES, SeriesCodec.parse(deal_raw, DEAL_SCHEMA)))
        assert set(deal_dict.keys()) == set(DealRecord.__annotations__)

    def test_sensitive_dataclass_fields_redacted_in_repr(self) -> None:
        open_res = OpenAccountResult(
            code=0, login=123456, password="SecretMainPass!", investor_password="SecretInvPass!"
        )
        assert "SecretMainPass!" not in repr(open_res)
        assert "SecretInvPass!" not in repr(open_res)
        assert "123456" in repr(open_res)
        assert open_res.password == "SecretMainPass!"

        demo_req = DemoAccountRequest(
            first_name="Alice",
            second_name="Wonder",
            email="alice@secret.example",
            phone="+15551234567",
            zipcode="90210-secret",
            address="742 Evergreen Terrace",
            phone_password="PhoneSecret!",
            email_confirm_code=987654,
            phone_confirm_code=654321,
        )
        assert isinstance(demo_req, AccountOpeningRequest)
        demo_repr = repr(demo_req)
        for secret in (
            "Alice",
            "Wonder",
            "alice@secret.example",
            "+15551234567",
            "90210-secret",
            "742 Evergreen Terrace",
            "PhoneSecret!",
            "987654",
            "654321",
        ):
            assert secret not in demo_repr

        doc = AccountDocument(
            data_type=1,
            document_type=2,
            front_name="front_id.png",
            front_buffer=b"SENSITIVE_PASSPORT_BYTES",
            back_name="back_id.png",
            back_buffer=b"SENSITIVE_BACK_BYTES",
        )
        assert "SENSITIVE_PASSPORT_BYTES" not in repr(doc)
        assert "SENSITIVE_BACK_BYTES" not in repr(doc)

        real_req = RealAccountRequest(
            first_name="Bob",
            second_name="Builder",
            middle_name="MiddleSecret",
            birth_date_ms=631152000000,
            citizenship="SecretNation",
            tax_id="TAX-999-888-777",
            documents=[doc],
        )
        real_repr = repr(real_req)
        for secret in (
            "Bob",
            "Builder",
            "MiddleSecret",
            "631152000000",
            "SecretNation",
            "TAX-999-888-777",
            "front_id.png",
            "SENSITIVE_PASSPORT_BYTES",
        ):
            assert secret not in real_repr

    @pytest.mark.parametrize("bad_len", [-10, -1, 0])
    def test_helpers_nonpositive_lengths_return_empty_bytes(self, bad_len: int) -> None:
        assert truncate_ascii("EURUSD", bad_len) == b""
        assert pad_ascii("EURUSD", bad_len) == b""
        assert pad_bytes(b"EURUSD", bad_len) == b""

    def test_trading_float_or_default_helper_exists(self) -> None:
        assert _TradingMixin._float_or_default(0.0, 10.0) == 0.0
        assert _TradingMixin._float_or_default(None, 10.0) == 10.0
