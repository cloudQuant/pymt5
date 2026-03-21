"""Integration tests for pymt5 -- requires a live MT5 demo server.

Gated by PYMT5_INTEGRATION=1 environment variable.
Credentials from: PYMT5_SERVER, PYMT5_LOGIN, PYMT5_PASSWORD
"""

import asyncio
import os

import pytest

from pymt5.client import MT5WebClient
from pymt5.events import HealthStatus
from pymt5.transport import TransportState

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("PYMT5_INTEGRATION"),
        reason="Integration tests disabled (set PYMT5_INTEGRATION=1)",
    ),
    pytest.mark.integration,
]

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

_SERVER = os.environ.get("PYMT5_SERVER", "wss://web.metatrader.app/terminal")
_LOGIN = int(os.environ.get("PYMT5_LOGIN", "0"))
_PASSWORD = os.environ.get("PYMT5_PASSWORD", "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    """Provide a connected (but not logged-in) MT5WebClient."""
    c = MT5WebClient(uri=_SERVER, timeout=30.0)
    await c.connect()
    yield c
    await c.close()


@pytest.fixture
async def logged_in_client():
    """Provide a connected AND logged-in MT5WebClient."""
    c = MT5WebClient(uri=_SERVER, timeout=30.0)
    await c.connect()
    await c.login(login=_LOGIN, password=_PASSWORD, auto_heartbeat=False)
    yield c
    await c.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_handshake(client: MT5WebClient):
    """Connect, verify transport is ready, disconnect."""
    assert client.transport.is_ready is True
    assert client.transport.state == TransportState.READY


@pytest.mark.asyncio
async def test_login_logout_cycle():
    """Connect, login, verify logged_in, logout, close."""
    c = MT5WebClient(uri=_SERVER, timeout=30.0)
    try:
        await c.connect()
        assert c.transport.is_ready is True

        token, session_id = await c.login(
            login=_LOGIN,
            password=_PASSWORD,
            auto_heartbeat=False,
        )
        assert c.is_connected is True
        assert isinstance(token, str)
        assert len(token) > 0
        assert isinstance(session_id, int)

        await c.logout()
        assert c._logged_in is False
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_symbol_load(logged_in_client: MT5WebClient):
    """Connect, login, load_symbols, verify at least 1 symbol."""
    symbols = await logged_in_client.load_symbols()
    assert isinstance(symbols, list)
    assert len(symbols) >= 1
    # Each symbol should have at minimum a trade_symbol field
    first = symbols[0]
    assert "trade_symbol" in first
    assert len(first["trade_symbol"]) > 0


@pytest.mark.asyncio
async def test_tick_subscription(logged_in_client: MT5WebClient):
    """Connect, login, load_symbols, subscribe first symbol, wait for tick, unsubscribe."""
    symbols = await logged_in_client.load_symbols()
    assert len(symbols) >= 1

    # Pick the first symbol's ID
    first_id = int(symbols[0]["symbol_id"])
    tick_received = asyncio.Event()
    received_ticks: list[dict] = []

    def on_tick(tick_data):
        received_ticks.append(tick_data)
        tick_received.set()

    logged_in_client.on_tick(on_tick)
    await logged_in_client.subscribe_ticks([first_id])

    # Wait up to 30 seconds for a tick
    try:
        await asyncio.wait_for(tick_received.wait(), timeout=30.0)
    except TimeoutError:
        pytest.skip("No tick received within 30s (market may be closed)")

    assert len(received_ticks) >= 1
    await logged_in_client.unsubscribe_ticks([first_id])


@pytest.mark.asyncio
async def test_heartbeat(logged_in_client: MT5WebClient):
    """Connect, login, send ping, verify response (no exception)."""
    # ping() should return without raising
    await logged_in_client.ping()


@pytest.mark.asyncio
async def test_health_check(logged_in_client: MT5WebClient):
    """Connect, login, call health_check(), verify HealthStatus fields."""
    status = await logged_in_client.health_check()
    assert isinstance(status, HealthStatus)
    assert status.state == TransportState.READY
    # ping_latency_ms should be a positive number when transport is ready
    assert status.ping_latency_ms is not None
    assert status.ping_latency_ms >= 0
    assert status.uptime_seconds >= 0
    assert status.reconnect_count >= 0
