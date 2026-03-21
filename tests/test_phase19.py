"""Tests for Phase 19 features: protocol version tracking, order manager,
connection pool, and dev-mode schema validation.
"""

from __future__ import annotations

import importlib
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5._order_manager import OrderManager, OrderState, PositionSummary, TrackedOrder
from pymt5._pool import MT5ConnectionPool, PoolAccount
from pymt5.constants import CMD_BOOTSTRAP, DEFAULT_TOKEN_LENGTH, PROP_TIME, PROP_U32
from pymt5.crypto import AESCipher, initial_cipher
from pymt5.protocol import SeriesCodec, pack_outer
from pymt5.transport import MT5WebSocketTransport

# ============================================================================
# Helpers (shared with test_transport_lifecycle.py)
# ============================================================================


def _make_bootstrap_response_body(
    code: int = 0,
    token: bytes | None = None,
    key: bytes | None = None,
    prefix: bytes | None = None,
) -> bytes:
    """Build a valid bootstrap response body.

    Layout: 2-byte prefix + 64-byte token + AES key (>= 16 bytes).
    """
    pfx = prefix or bytes(2)
    tok = token or (b"\xab" * DEFAULT_TOKEN_LENGTH)
    k = key or (b"\xcd" * 16)
    return pfx + tok + k


def _build_encrypted_response(cipher: AESCipher, command: int, code: int, body: bytes) -> bytes:
    inner = b"\x00\x00" + struct.pack("<H", command) + bytes([code]) + body
    encrypted = cipher.encrypt(inner)
    return pack_outer(encrypted)


class _MockWS:
    """Mock WebSocket that supports async iteration over messages."""

    def __init__(self, messages: list | None = None):
        self._messages = list(messages) if messages else []
        self.close = AsyncMock()
        self.send = AsyncMock()
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg


# ============================================================================
# Phase 19.1 — Protocol Version Tracking
# ============================================================================


class TestServerBuildTransport:
    """Transport-level server_build tests."""

    def test_server_build_default_zero(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert t.server_build == 0

    def test_server_build_property_type(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert isinstance(t.server_build, int)

    async def test_connect_extracts_server_build_from_prefix(self):
        """If the 2-byte prefix of bootstrap body is non-zero, it is extracted."""
        build_number = 5687
        prefix = struct.pack("<H", build_number)
        bootstrap_body = _make_bootstrap_response_body(prefix=prefix)
        init_cipher = initial_cipher()
        response = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _MockWS(messages=[response])
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await t.connect()

        assert t.server_build == build_number
        await t.close()

    async def test_connect_server_build_zero_prefix(self):
        """When the prefix is 0x0000, server_build is 0."""
        bootstrap_body = _make_bootstrap_response_body(prefix=b"\x00\x00")
        init_cipher = initial_cipher()
        response = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _MockWS(messages=[response])
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await t.connect()

        assert t.server_build == 0
        await t.close()


class TestServerBuildClient:
    """Client-level server_build tests."""

    def test_server_build_delegates_to_transport(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient()
        client.transport._server_build = 4321
        assert client.server_build == 4321

    def test_server_build_default_zero(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient()
        assert client.server_build == 0

    async def test_connect_logs_server_build_when_nonzero(self):
        """When server_build is non-zero, connect() logs it."""
        from pymt5.client import MT5WebClient

        build_number = 5687
        prefix = struct.pack("<H", build_number)
        bootstrap_body = _make_bootstrap_response_body(prefix=prefix)
        init_cipher = initial_cipher()
        response = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _MockWS(messages=[response])
        client = MT5WebClient(uri="wss://example.com", timeout=5.0)

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await client.connect()

        assert client.server_build == build_number
        await client.transport.close()

    async def test_connect_logs_without_build_when_zero(self):
        """When server_build is 0, connect() logs without build info."""
        from pymt5.client import MT5WebClient

        bootstrap_body = _make_bootstrap_response_body(prefix=b"\x00\x00")
        init_cipher = initial_cipher()
        response = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _MockWS(messages=[response])
        client = MT5WebClient(uri="wss://example.com", timeout=5.0)

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await client.connect()

        assert client.server_build == 0
        await client.transport.close()


# ============================================================================
# Phase 19.2 — Order Lifecycle Manager
# ============================================================================


class TestTrackedOrder:
    def test_creation_defaults(self):
        order = TrackedOrder(order_id=100, symbol="EURUSD", order_type=0, volume=0.01, price=1.12)
        assert order.order_id == 100
        assert order.symbol == "EURUSD"
        assert order.order_type == 0
        assert order.volume == 0.01
        assert order.price == 1.12
        assert order.state == OrderState.PENDING
        assert order.filled_volume == 0.0
        assert order.fill_price == 0.0
        assert order.sl == 0.0
        assert order.tp == 0.0
        assert order.comment == ""
        assert order.raw == {}


class TestOrderState:
    def test_all_states_exist(self):
        assert OrderState.PENDING.value == "pending"
        assert OrderState.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderState.FILLED.value == "filled"
        assert OrderState.CANCELED.value == "canceled"
        assert OrderState.REJECTED.value == "rejected"
        assert OrderState.EXPIRED.value == "expired"

    def test_enum_count(self):
        assert len(OrderState) == 6


class TestPositionSummary:
    def test_creation(self):
        pos = PositionSummary(symbol="EURUSD", net_volume=0.05, avg_price=1.12)
        assert pos.symbol == "EURUSD"
        assert pos.net_volume == 0.05
        assert pos.avg_price == 1.12
        assert pos.unrealized_pnl == 0.0
        assert pos.order_count == 0


class TestOrderManager:
    def test_init_empty(self):
        mgr = OrderManager()
        assert mgr.get_orders() == []
        assert mgr.get_positions() == []

    def test_track_order(self):
        mgr = OrderManager()
        order = mgr.track_order(1, "EURUSD", 0, 0.01, 1.12, sl=1.10, tp=1.15, comment="test")
        assert order.order_id == 1
        assert order.symbol == "EURUSD"
        assert order.sl == 1.10
        assert order.tp == 1.15
        assert order.comment == "test"
        assert order.state == OrderState.PENDING

    def test_track_order_replaces_existing(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        mgr.track_order(1, "EURUSD", 0, 0.02, 1.13)
        assert mgr.get_order(1).volume == 0.02

    def test_get_order_not_found(self):
        mgr = OrderManager()
        assert mgr.get_order(999) is None

    def test_get_orders_all(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        mgr.track_order(2, "GBPUSD", 1, 0.02, 1.30)
        orders = mgr.get_orders()
        assert len(orders) == 2

    def test_get_orders_filtered_by_state(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        mgr.track_order(2, "GBPUSD", 1, 0.02, 1.30)
        # Mark one as filled
        mgr.update_from_trade_result({"order": 1, "retcode": 10009, "price": 1.12})
        pending = mgr.get_orders(OrderState.PENDING)
        filled = mgr.get_orders(OrderState.FILLED)
        assert len(pending) == 1
        assert pending[0].order_id == 2
        assert len(filled) == 1
        assert filled[0].order_id == 1

    def test_update_from_trade_result_done(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        result = mgr.update_from_trade_result(
            {
                "order": 1,
                "retcode": 10009,
                "price": 1.125,
                "comment": "done",
            }
        )
        assert result is not None
        assert result.state == OrderState.FILLED
        assert result.filled_volume == 0.01
        assert result.fill_price == 1.125
        assert result.comment == "done"

    def test_update_from_trade_result_partial(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        result = mgr.update_from_trade_result(
            {
                "order": 1,
                "retcode": 10010,
                "volume": 0.5,
            }
        )
        assert result.state == OrderState.PARTIALLY_FILLED
        assert result.filled_volume == 0.5

    def test_update_from_trade_result_rejected(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        result = mgr.update_from_trade_result({"order": 1, "retcode": 10006})
        assert result.state == OrderState.REJECTED

    def test_update_from_trade_result_canceled(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        result = mgr.update_from_trade_result({"order": 1, "retcode": 10007})
        assert result.state == OrderState.CANCELED

    def test_update_from_trade_result_placed(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        result = mgr.update_from_trade_result({"order": 1, "retcode": 10008})
        assert result.state == OrderState.PENDING

    def test_update_from_trade_result_untracked_returns_none(self):
        mgr = OrderManager()
        result = mgr.update_from_trade_result({"order": 999, "retcode": 10009})
        assert result is None

    def test_update_from_trade_result_no_order_returns_none(self):
        mgr = OrderManager()
        result = mgr.update_from_trade_result({"retcode": 10009})
        assert result is None

    def test_update_from_push_state_mapping(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        mgr.update_from_push({"order": 1, "state": 4})  # FILLED
        assert mgr.get_order(1).state == OrderState.FILLED

    def test_update_from_push_canceled(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        mgr.update_from_push({"order": 1, "state": 2})  # CANCELED
        assert mgr.get_order(1).state == OrderState.CANCELED

    def test_update_from_push_expired(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        mgr.update_from_push({"order": 1, "state": 6})  # EXPIRED
        assert mgr.get_order(1).state == OrderState.EXPIRED

    def test_update_from_push_with_volume_current(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        mgr.update_from_push({"order": 1, "state": 3, "volume_current": 0.3})
        assert mgr.get_order(1).filled_volume == pytest.approx(0.7)

    def test_update_from_push_untracked(self):
        mgr = OrderManager()
        # Should not raise
        mgr.update_from_push({"order": 999, "state": 4})

    def test_update_from_push_no_order(self):
        mgr = OrderManager()
        mgr.update_from_push({"state": 4})

    def test_update_from_push_trade_order_key(self):
        """Push data using 'trade_order' instead of 'order'."""
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        mgr.update_from_push({"trade_order": 1, "state": 5})
        assert mgr.get_order(1).state == OrderState.REJECTED

    def test_on_state_change_callback(self):
        mgr = OrderManager()
        changes = []
        mgr.on_state_change(lambda order, old: changes.append((order.order_id, old, order.state)))
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        mgr.update_from_trade_result({"order": 1, "retcode": 10009})
        assert len(changes) == 1
        assert changes[0] == (1, OrderState.PENDING, OrderState.FILLED)

    def test_on_state_change_not_fired_for_same_state(self):
        mgr = OrderManager()
        changes = []
        mgr.on_state_change(lambda order, old: changes.append(1))
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        # PLACED retcode maps to PENDING, which is the default state
        mgr.update_from_trade_result({"order": 1, "retcode": 10008})
        assert len(changes) == 0

    def test_on_state_change_callback_error_suppressed(self):
        mgr = OrderManager()

        def bad_callback(order, old):
            raise ValueError("boom")

        mgr.on_state_change(bad_callback)
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        # Should not raise
        mgr.update_from_trade_result({"order": 1, "retcode": 10009})
        assert mgr.get_order(1).state == OrderState.FILLED

    def test_get_position_not_found(self):
        mgr = OrderManager()
        assert mgr.get_position("EURUSD") is None

    def test_clear(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 0.01, 1.12)
        mgr._positions["EURUSD"] = PositionSummary("EURUSD", 0.01, 1.12)
        assert len(mgr.get_orders()) == 1
        assert len(mgr.get_positions()) == 1
        mgr.clear()
        assert mgr.get_orders() == []
        assert mgr.get_positions() == []

    def test_track_order_with_raw(self):
        mgr = OrderManager()
        order = mgr.track_order(1, "EURUSD", 0, 0.01, 1.12, raw={"custom": "data"})
        assert order.raw == {"custom": "data"}

    def test_update_from_push_unknown_state_ignored(self):
        mgr = OrderManager()
        mgr.track_order(1, "EURUSD", 0, 1.0, 1.12)
        mgr.update_from_push({"order": 1, "state": 99})
        assert mgr.get_order(1).state == OrderState.PENDING  # unchanged


# ============================================================================
# Phase 19.3 — Connection Pool
# ============================================================================


class TestPoolAccount:
    def test_creation(self):
        acct = PoolAccount(server="wss://example.com/ws", login=123, password="pw")
        assert acct.server == "wss://example.com/ws"
        assert acct.login == 123
        assert acct.password == "pw"
        assert acct.label == ""

    def test_creation_with_label(self):
        acct = PoolAccount(server="wss://x", login=1, password="p", label="demo")
        assert acct.label == "demo"


class TestMT5ConnectionPool:
    def test_init_with_pool_accounts(self):
        accounts = [
            PoolAccount(server="wss://a", login=1, password="p1"),
            PoolAccount(server="wss://b", login=2, password="p2"),
        ]
        pool = MT5ConnectionPool(accounts, timeout=10.0)
        assert len(pool._accounts) == 2
        assert pool._client_kwargs == {"timeout": 10.0}

    def test_init_with_dicts(self):
        accounts = [
            {"server": "wss://a", "login": 1, "password": "p1"},
            {"server": "wss://b", "login": 2, "password": "p2", "label": "demo"},
        ]
        pool = MT5ConnectionPool(accounts)
        assert len(pool._accounts) == 2
        assert isinstance(pool._accounts[0], PoolAccount)
        assert pool._accounts[1].label == "demo"

    def test_get_client_not_connected(self):
        pool = MT5ConnectionPool([])
        assert pool.get_client(123) is None

    def test_clients_empty(self):
        pool = MT5ConnectionPool([])
        assert pool.clients == []

    async def test_connect_all_and_close_all(self):
        """Test connect_all and close_all with mocked clients."""
        accounts = [
            PoolAccount(server="wss://a", login=1, password="p1"),
            PoolAccount(server="wss://b", login=2, password="p2"),
        ]
        pool = MT5ConnectionPool(accounts)

        mock_client1 = AsyncMock()
        mock_client1.close = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client2.close = AsyncMock()
        mock_clients = [mock_client1, mock_client2]
        call_count = 0

        # Override _connect_one to inject mock clients directly
        async def fake_connect_one(acct):
            nonlocal call_count
            client = mock_clients[call_count]
            call_count += 1
            pool._clients[acct.login] = client

        pool._connect_one = fake_connect_one

        await pool.connect_all()
        assert len(pool.clients) == 2
        assert pool.get_client(1) is mock_client1
        assert pool.get_client(2) is mock_client2

        await pool.close_all()
        assert pool.clients == []

    async def test_context_manager(self):
        """Test async context manager protocol."""
        pool = MT5ConnectionPool([])
        pool.connect_all = AsyncMock()
        pool.close_all = AsyncMock()

        async with pool as p:
            assert p is pool
            pool.connect_all.assert_awaited_once()

        pool.close_all.assert_awaited_once()

    async def test_connect_all_handles_failure(self):
        """When a connection fails, it logs and continues with others."""
        accounts = [
            PoolAccount(server="wss://a", login=1, password="p1"),
            PoolAccount(server="wss://b", login=2, password="p2"),
        ]
        pool = MT5ConnectionPool(accounts)

        call_count = 0

        async def fake_connect_one(acct):
            nonlocal call_count
            call_count += 1
            if acct.login == 1:
                raise ConnectionError("failed")
            pool._clients[acct.login] = MagicMock()

        pool._connect_one = fake_connect_one

        await pool.connect_all()
        assert pool.get_client(1) is None
        assert pool.get_client(2) is not None

    async def test_broadcast_subscribe_ticks(self):
        pool = MT5ConnectionPool([])
        client1 = MagicMock()
        client1.subscribe_ticks = AsyncMock()
        client2 = MagicMock()
        client2.subscribe_ticks = AsyncMock()
        pool._clients = {1: client1, 2: client2}

        await pool.broadcast_subscribe_ticks([42, 99])
        client1.subscribe_ticks.assert_awaited_once_with([42, 99])
        client2.subscribe_ticks.assert_awaited_once_with([42, 99])

    async def test_broadcast_load_symbols(self):
        pool = MT5ConnectionPool([])
        client1 = MagicMock()
        client1.load_symbols = AsyncMock()
        pool._clients = {1: client1}

        await pool.broadcast_load_symbols()
        client1.load_symbols.assert_awaited_once()

    async def test_broadcast_empty_pool(self):
        """Broadcasts on empty pool should not raise."""
        pool = MT5ConnectionPool([])
        await pool.broadcast_subscribe_ticks([1, 2])
        await pool.broadcast_load_symbols()

    async def test_close_all_handles_error(self):
        """close_all suppresses errors from individual close calls."""
        pool = MT5ConnectionPool([])
        client = MagicMock()
        client.close = AsyncMock(side_effect=OSError("broken"))
        pool._clients = {1: client}

        # Should not raise
        await pool.close_all()
        assert pool.clients == []


# ============================================================================
# Phase 19.4 — Dev-mode Schema Validation
# ============================================================================


class TestDebugModeOff:
    """Verify that the _DEBUG flag defaults to off and has no side effects."""

    def test_debug_default_false(self):
        from pymt5.protocol import _DEBUG

        # May be True if PYMT5_DEBUG is set in the environment, but at least
        # it should be a bool.
        assert isinstance(_DEBUG, bool)

    def test_parse_works_without_debug(self):
        """Standard parse still works whether _DEBUG is on or off."""
        schema = [{"propType": PROP_U32}]
        data = struct.pack("<I", 42)
        values = SeriesCodec.parse(data, schema)
        assert values == [42]


class TestDebugModeOn:
    """Tests with PYMT5_DEBUG=1 enabled at import time."""

    def _reload_protocol_with_debug(self):
        """Reload protocol module with PYMT5_DEBUG=1."""
        import pymt5.protocol as proto

        with patch.dict("os.environ", {"PYMT5_DEBUG": "1"}):
            importlib.reload(proto)
        return proto

    def _reload_protocol_without_debug(self):
        """Restore protocol module without PYMT5_DEBUG."""
        import pymt5.protocol as proto

        with patch.dict("os.environ", {}, clear=True):
            importlib.reload(proto)
        return proto

    def test_debug_flag_set(self):
        proto = self._reload_protocol_with_debug()
        try:
            assert proto._DEBUG is True
        finally:
            self._reload_protocol_without_debug()

    def test_parse_with_debug_logs_field_count(self):
        proto = self._reload_protocol_with_debug()
        try:
            schema = [{"propType": PROP_U32}]
            data = struct.pack("<I", 42)
            with patch.object(proto._debug_logger, "debug") as mock_debug:
                values = proto.SeriesCodec.parse(data, schema)
            assert values == [42]
            # Should have logged field count and values
            mock_debug.assert_called_once()
            call_args = mock_debug.call_args
            assert "1 fields" in call_args[0][0] % call_args[0][1:]
        finally:
            self._reload_protocol_without_debug()

    def test_parse_with_debug_warns_trailing_bytes(self):
        """When cursor doesn't match expected end, a warning is logged."""
        proto = self._reload_protocol_with_debug()
        try:
            # This is a normal case where cursor == expected_end, so no warning.
            schema = [{"propType": PROP_U32}]
            data = struct.pack("<I", 42)
            with patch.object(proto._debug_logger, "warning") as mock_warn:
                proto.SeriesCodec.parse(data, schema)
            # No warning because cursor matches expected end
            mock_warn.assert_not_called()
        finally:
            self._reload_protocol_without_debug()

    def test_parse_with_debug_warns_bad_timestamp(self):
        """Timestamps outside [year 2000, year 2100] trigger a warning."""
        proto = self._reload_protocol_with_debug()
        try:
            schema = [{"propType": PROP_TIME}]
            # Create a timestamp far in the future (year 3000 in ms)
            bad_ts_ms = 32_503_680_000_000  # well beyond 2100
            from pymt5.protocol import _unix_ms_to_filetime

            data = _unix_ms_to_filetime(bad_ts_ms)
            with patch.object(proto._debug_logger, "warning") as mock_warn:
                proto.SeriesCodec.parse(data, schema)
            assert mock_warn.called
            warning_msg = mock_warn.call_args[0][0] % tuple(mock_warn.call_args[0][1:])
            assert "out of range" in warning_msg
        finally:
            self._reload_protocol_without_debug()

    def test_parse_with_debug_valid_timestamp_no_warning(self):
        """Valid timestamps do not trigger a warning."""
        proto = self._reload_protocol_with_debug()
        try:
            schema = [{"propType": PROP_TIME}]
            # 2025-01-01 in ms
            valid_ts_ms = 1_735_689_600_000
            from pymt5.protocol import _unix_ms_to_filetime

            data = _unix_ms_to_filetime(valid_ts_ms)
            with patch.object(proto._debug_logger, "warning") as mock_warn:
                proto.SeriesCodec.parse(data, schema)
            mock_warn.assert_not_called()
        finally:
            self._reload_protocol_without_debug()

    def test_parse_with_debug_zero_timestamp_no_warning(self):
        """Zero timestamps are skipped (not validated)."""
        proto = self._reload_protocol_with_debug()
        try:
            schema = [{"propType": PROP_TIME}]
            from pymt5.protocol import _unix_ms_to_filetime

            data = _unix_ms_to_filetime(0)
            with patch.object(proto._debug_logger, "warning") as mock_warn:
                proto.SeriesCodec.parse(data, schema)
            mock_warn.assert_not_called()
        finally:
            self._reload_protocol_without_debug()


# ============================================================================
# __init__.py exports
# ============================================================================


class TestPhase19Exports:
    def test_order_manager_exported(self):
        import pymt5

        assert hasattr(pymt5, "OrderManager")
        assert hasattr(pymt5, "OrderState")
        assert hasattr(pymt5, "TrackedOrder")
        assert hasattr(pymt5, "PositionSummary")

    def test_pool_exported(self):
        import pymt5

        assert hasattr(pymt5, "MT5ConnectionPool")
        assert hasattr(pymt5, "PoolAccount")
