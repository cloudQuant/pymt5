"""Currency conversion mixin for MT5WebClient.

Provides currency rate resolution, profit calculation, and margin
calculation methods. Extracted from _market_data.py to keep module
sizes within the 800-line guideline.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, TypeVar

from pymt5._logging import get_logger
from pymt5._parsers import _currencies_equal
from pymt5.constants import (
    BOND_CALC_MODES,
    CFD_CALC_MODES,
    COLLATERAL_CALC_MODE,
    FOREX_CALC_MODES,
    FUTURES_CALC_MODES,
)
from pymt5.types import Record, SymbolInfo

if TYPE_CHECKING:
    from pymt5.transport import MT5WebSocketTransport

_T = TypeVar("_T")

logger = get_logger("pymt5.client")


class _CurrencyMixin:
    """Mixin providing currency conversion and profit/margin calculations."""

    # Attributes provided by MT5WebClient.__init__
    transport: MT5WebSocketTransport
    _symbols: dict[str, SymbolInfo]
    _symbols_by_id: dict[int, SymbolInfo]
    _tick_cache_by_name: dict[str, Record]

    if TYPE_CHECKING:

        def _fail_last_error(self, code: int, message: str) -> _T | None: ...
        async def load_symbols(self, use_gzip: bool = True) -> dict[str, SymbolInfo]: ...

    async def _resolve_conversion_rates(
        self,
        *,
        source: str,
        target: str,
        current_symbol: str,
        fallback_rate: float,
    ) -> tuple[float, float] | None:
        """Resolve buy/sell conversion rates between two currencies.

        Tries direct conversion first, then triangulates via USD.
        """
        if not source or not target:
            return None
        if _currencies_equal(source, target):
            return 1.0, 1.0
        buy = await self._resolve_side_rate(
            source,
            target,
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell = await self._resolve_side_rate(
            source,
            target,
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        if buy > 0.0 and sell > 0.0:
            return buy, sell
        buy_usd = await self._resolve_side_rate(
            source,
            "USD",
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell_usd = await self._resolve_side_rate(
            source,
            "USD",
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        buy_target = await self._resolve_side_rate(
            "USD",
            target,
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell_target = await self._resolve_side_rate(
            "USD",
            target,
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        if buy_usd > 0.0 and sell_usd > 0.0 and buy_target > 0.0 and sell_target > 0.0:
            return buy_usd * buy_target, sell_usd * sell_target
        return None

    async def _resolve_side_rate(
        self,
        source: str,
        target: str,
        *,
        prefer_ask_when_direct: bool,
        current_symbol: str,
        fallback_rate: float,
    ) -> float:
        """Resolve a single-side (buy or sell) conversion rate."""
        direct = await self._find_conversion_symbol_name(source, target)
        if direct is not None:
            prices = self._get_conversion_prices(
                direct,
                current_symbol=current_symbol,
                fallback_rate=fallback_rate,
            )
            if prices is not None:
                bid, ask = prices
                return ask if prefer_ask_when_direct else bid
        inverse = await self._find_conversion_symbol_name(target, source)
        if inverse is not None:
            prices = self._get_conversion_prices(
                inverse,
                current_symbol=current_symbol,
                fallback_rate=fallback_rate,
            )
            if prices is not None:
                bid, ask = prices
                denominator = bid if prefer_ask_when_direct else ask
                if denominator > 0.0:
                    return 1.0 / denominator
        return 0.0

    async def _find_conversion_symbol_name(self, base: str, quote: str) -> str | None:
        """Find a symbol name that represents base/quote currency pair."""
        if not base or not quote:
            return None
        if not self._symbols:
            try:
                await self.load_symbols()
            except (RuntimeError, struct.error, KeyError, ValueError) as exc:
                logger.debug("load_symbols() failed in _find_conversion_symbol_name: %s", exc)
                return None
        direct = f"{base}{quote}"
        if direct in self._symbols:
            return direct
        matches = sorted(name for name in self._symbols if name.startswith(base) and name.endswith(quote))
        if matches:
            return matches[0]
        return None

    def _get_conversion_prices(
        self,
        symbol_name: str,
        *,
        current_symbol: str,
        fallback_rate: float,
    ) -> tuple[float, float] | None:
        """Get bid/ask prices for a conversion symbol from tick cache."""
        tick = self._tick_cache_by_name.get(symbol_name)
        bid = 0.0
        ask = 0.0
        if tick is not None:
            bid = float(tick.get("bid", 0.0) or 0.0)
            ask = float(tick.get("ask", 0.0) or 0.0)
        if symbol_name == current_symbol and fallback_rate > 0.0:
            if bid <= 0.0:
                bid = float(fallback_rate)
            if ask <= 0.0:
                ask = float(fallback_rate)
        if bid <= 0.0:
            bid = ask
        if ask <= 0.0:
            ask = bid
        if bid > 0.0 and ask > 0.0:
            return bid, ask
        return None

    def _calc_profit_raw(
        self,
        info: Record,
        is_buy: bool,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        """Calculate raw profit for a position without currency conversion."""
        mode = int(info.get("trade_calc_mode", 0) or 0)
        if mode in BOND_CALC_MODES:
            contract_size = float(info.get("contract_size", 0.0) or 0.0)
            face_value = float(info.get("face_value", 0.0) or 0.0)
            accrued_interest = float(info.get("accrued_interest", 0.0) or 0.0)
            if contract_size <= 0.0 or face_value <= 0.0:
                return self._fail_last_error(
                    -10,
                    f"trade_calc_mode={mode} requires contract_size and face_value for bond profit calculation",
                )
            direction = 1.0 if is_buy else -1.0
            open_value = (price_open / 100.0) * face_value
            close_value = (price_close / 100.0) * face_value + accrued_interest
            return direction * (close_value - open_value) * contract_size * float(volume)
        if mode == COLLATERAL_CALC_MODE:
            return 0.0
        direction = 1.0 if is_buy else -1.0
        contract_size = float(info.get("contract_size", 0.0) or 0.0)
        tick_size = float(info.get("tick_size", 0.0) or 0.0)
        tick_value = float(info.get("tick_value", 0.0) or 0.0)
        if mode in FOREX_CALC_MODES or mode in CFD_CALC_MODES:
            if contract_size <= 0.0:
                return self._fail_last_error(-11, "contract_size must be > 0 for profit calculation")
            return direction * (price_close - price_open) * contract_size * float(volume)
        if mode in {1, 33, 35, 36}:
            if tick_value <= 0.0:
                return self._fail_last_error(-12, "tick_value must be > 0 for futures/options profit calculation")
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return direction * (price_close - price_open) * float(volume) * point_value
        if mode == 34:
            if tick_value <= 0.0:
                return self._fail_last_error(-12, "tick_value must be > 0 for FORTS profit calculation")
            open_value = price_open * tick_value / tick_size if tick_size > 0.0 else price_open * tick_value
            close_value = price_close * tick_value / tick_size if tick_size > 0.0 else price_close * tick_value
            return direction * (close_value - open_value) * float(volume)
        if contract_size <= 0.0:
            return self._fail_last_error(-11, "contract_size must be > 0 for profit calculation")
        return direction * (price_close - price_open) * contract_size * float(volume)

    def _calc_margin_raw(
        self,
        info: Record,
        is_buy: bool,
        volume: float,
        price: float,
        leverage: int,
    ) -> float | None:
        """Calculate raw margin requirement without currency conversion."""
        del is_buy
        mode = int(info.get("trade_calc_mode", 0) or 0)
        contract_size = float(info.get("contract_size", 0.0) or 0.0)
        tick_size = float(info.get("tick_size", 0.0) or 0.0)
        tick_value = float(info.get("tick_value", 0.0) or 0.0)
        margin_initial = float(info.get("margin_initial", 0.0) or 0.0)
        lots = float(volume)
        units = lots * contract_size
        if mode in BOND_CALC_MODES:
            face_value = float(info.get("face_value", 0.0) or 0.0)
            if contract_size <= 0.0 or face_value <= 0.0:
                return self._fail_last_error(
                    -13,
                    f"trade_calc_mode={mode} requires contract_size and face_value for bond margin calculation",
                )
            return lots * contract_size * face_value * float(price) / 100.0
        if mode == COLLATERAL_CALC_MODE:
            return 0.0
        if mode == 5:
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            return units
        if mode == 0 or mode == 4:
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            if leverage <= 0:
                return self._fail_last_error(-15, "account leverage must be > 0 for leveraged margin calculation")
            base_margin = units / float(leverage)
            if mode == 4:
                base_margin *= float(price)
            return base_margin
        if mode in FUTURES_CALC_MODES:
            if margin_initial > 0.0:
                return lots * margin_initial
            if tick_value <= 0.0:
                return self._fail_last_error(
                    -16,
                    "margin_initial or tick_value is required for futures margin calculation",
                )
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return lots * float(price) * point_value
        if mode == 3:
            if contract_size <= 0.0 or tick_value <= 0.0:
                return self._fail_last_error(
                    -17,
                    "contract_size and tick_value are required for CFD index margin calculation",
                )
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return units * float(price) * point_value
        if mode in {2, 32, 35, 36, 38}:
            if margin_initial > 0.0 and mode in {32, 35, 36, 38}:
                return lots * margin_initial
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            return units * float(price)
        return self._fail_last_error(-18, f"trade_calc_mode={mode} is not supported yet")
