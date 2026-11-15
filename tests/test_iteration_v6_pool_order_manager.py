"""Iteration v6 tests for connection-pool cleanup and position API deprecation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5._order_manager import OrderManager, PositionSummary
from pymt5._pool import MT5ConnectionPool, PoolAccount


def _account(login: int = 123, *, server: str = "wss://example.com/ws") -> PoolAccount:
    return PoolAccount(server=server, login=login, password="secret", label=f"account-{login}")


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    return client


class TestPoolConnectionCleanup:
    async def test_connect_failure_closes_new_client_and_preserves_exception(self):
        pool = MT5ConnectionPool([])
        client = _mock_client()
        error = ConnectionError("connect failed")
        client.connect.side_effect = error

        with (
            patch("pymt5._pool.MT5WebClient", return_value=client),
            pytest.raises(ConnectionError) as exc_info,
        ):
            await pool._connect_one(_account())

        assert exc_info.value is error
        client.close.assert_awaited_once_with()
        client.login.assert_not_awaited()
        assert pool.clients == []

    async def test_login_failure_closes_connected_client_and_preserves_exception(self):
        pool = MT5ConnectionPool([])
        client = _mock_client()
        error = PermissionError("login failed")
        client.login.side_effect = error

        with (
            patch("pymt5._pool.MT5WebClient", return_value=client),
            pytest.raises(PermissionError) as exc_info,
        ):
            await pool._connect_one(_account())

        assert exc_info.value is error
        client.connect.assert_awaited_once_with()
        client.close.assert_awaited_once_with()
        assert pool.clients == []

    async def test_cancellation_closes_new_client_and_propagates(self):
        pool = MT5ConnectionPool([])
        client = _mock_client()
        client.connect.side_effect = asyncio.CancelledError("cancelled")

        with (
            patch("pymt5._pool.MT5WebClient", return_value=client),
            pytest.raises(asyncio.CancelledError, match="cancelled"),
        ):
            await pool._connect_one(_account())

        client.close.assert_awaited_once_with()
        assert pool.clients == []

    async def test_cleanup_failure_does_not_mask_login_failure(self):
        pool = MT5ConnectionPool([])
        client = _mock_client()
        error = PermissionError("bad credentials")
        client.login.side_effect = error
        client.close.side_effect = OSError("close failed")

        with (
            patch("pymt5._pool.MT5WebClient", return_value=client),
            patch("pymt5._pool.logger.error") as log_error,
            pytest.raises(PermissionError) as exc_info,
        ):
            await pool._connect_one(_account())

        assert exc_info.value is error
        client.close.assert_awaited_once_with()
        log_error.assert_called_once()
        assert "close failed" in str(log_error.call_args.args[-1])
        assert pool.clients == []

    async def test_cancellation_is_not_masked_by_cleanup_failure(self):
        pool = MT5ConnectionPool([])
        client = _mock_client()
        client.connect.side_effect = asyncio.CancelledError("cancelled")
        client.close.side_effect = OSError("close failed")

        with (
            patch("pymt5._pool.MT5WebClient", return_value=client),
            patch("pymt5._pool.logger.error"),
            pytest.raises(asyncio.CancelledError, match="cancelled"),
        ):
            await pool._connect_one(_account())

        client.close.assert_awaited_once_with()


class TestPoolDuplicateLogins:
    async def test_duplicate_config_raises_before_opening_any_client(self):
        pool = MT5ConnectionPool([_account(123), _account(123), _account(456), _account(456)])

        with (
            patch("pymt5._pool.MT5WebClient") as client_factory,
            pytest.raises(ValueError, match=r"Duplicate login key\(s\).*123, 456"),
        ):
            await pool.connect_all()

        client_factory.assert_not_called()
        assert pool.clients == []

    async def test_existing_login_collision_does_not_construct_or_replace_client(self):
        pool = MT5ConnectionPool([])
        existing = _mock_client()
        pool._clients[123] = existing

        with (
            patch("pymt5._pool.MT5WebClient") as client_factory,
            pytest.raises(ValueError, match="123.*already connected"),
        ):
            await pool._connect_one(_account(123))

        client_factory.assert_not_called()
        assert pool.get_client(123) is existing
        existing.close.assert_not_awaited()

    async def test_concurrent_collision_closes_new_client_without_replacing_existing(self):
        pool = MT5ConnectionPool([])
        existing = _mock_client()
        duplicate = _mock_client()

        async def install_existing_during_login(**kwargs):
            pool._clients[kwargs["login"]] = existing

        duplicate.login.side_effect = install_existing_during_login

        with (
            patch("pymt5._pool.MT5WebClient", return_value=duplicate),
            pytest.raises(ValueError, match="123.*already connected"),
        ):
            await pool._connect_one(_account(123))

        duplicate.close.assert_awaited_once_with()
        existing.close.assert_not_awaited()
        assert pool.get_client(123) is existing


class TestPoolSuccessfulBehavior:
    async def test_connect_all_stores_successful_clients_and_closes_failed_one(self):
        accounts = [_account(1, server="wss://one"), _account(2, server="wss://two")]
        pool = MT5ConnectionPool(accounts, timeout=3.0)
        clients = {account.server: _mock_client() for account in accounts}
        error = ConnectionError("unavailable")
        clients["wss://one"].connect.side_effect = error

        def client_factory(*, uri, **kwargs):
            assert kwargs == {"timeout": 3.0}
            return clients[uri]

        with (
            patch("pymt5._pool.MT5WebClient", side_effect=client_factory),
            patch("pymt5._pool.logger.error") as log_error,
        ):
            await pool.connect_all()

        assert pool.get_client(1) is None
        assert pool.get_client(2) is clients["wss://two"]
        clients["wss://one"].close.assert_awaited_once_with()
        clients["wss://two"].close.assert_not_awaited()
        clients["wss://two"].login.assert_awaited_once_with(login=2, password="secret")
        log_error.assert_called_once()
        assert log_error.call_args.args[2] is error

    async def test_connect_one_success_keeps_client_open(self):
        pool = MT5ConnectionPool([])
        client = _mock_client()

        with patch("pymt5._pool.MT5WebClient", return_value=client) as client_factory:
            await pool._connect_one(_account(123))

        client_factory.assert_called_once_with(uri="wss://example.com/ws")
        client.connect.assert_awaited_once_with()
        client.login.assert_awaited_once_with(login=123, password="secret")
        client.close.assert_not_awaited()
        assert pool.get_client(123) is client


class TestPositionSummaryDeprecation:
    @pytest.mark.parametrize("method_name", ["get_position", "get_positions"])
    def test_local_summary_queries_warn_and_retain_return_behavior(self, method_name):
        manager = OrderManager()
        summary = PositionSummary(symbol="EURUSD", net_volume=0.1, avg_price=1.1)
        manager._positions["EURUSD"] = summary

        with pytest.warns(DeprecationWarning) as recorded:
            if method_name == "get_position":
                result = manager.get_position("EURUSD")
                assert result is summary
            else:
                result = manager.get_positions()
                assert result == [summary]

        assert len(recorded) == 1
        warning = recorded[0]
        assert warning.filename == __file__  # stacklevel=2 points to the caller.
        message = str(warning.message)
        assert "never populated" in message
        assert "deprecated since 1.0.5" in message
        assert "removed in 2.0.0" in message
        assert "MT5WebClient.get_positions()" in message
        assert "authoritative positions" in message

    @pytest.mark.parametrize("method_name", ["get_position", "get_positions"])
    def test_docstrings_describe_deprecation_and_authoritative_replacement(self, method_name):
        docstring = getattr(OrderManager, method_name).__doc__
        assert docstring is not None
        assert "deprecated:: 1.0.5" in docstring
        assert "never populated" in docstring
        assert "removed in 2.0.0" in docstring
        assert "MT5WebClient.get_positions" in docstring
        assert "authoritative positions" in docstring

    def test_position_summary_remains_exported(self):
        import pymt5

        assert pymt5.PositionSummary is PositionSummary
