"""Connection pool for managing multiple MT5 client connections."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pymt5._logging import get_logger
from pymt5.client import MT5WebClient

logger = get_logger("pymt5.pool")


@dataclass
class PoolAccount:
    """Account credentials for a pool connection."""

    server: str
    login: int
    password: str
    label: str = ""  # optional human-readable label


class MT5ConnectionPool:
    """Manages multiple concurrent MT5 client connections.

    Usage::

        accounts = [
            PoolAccount(server="wss://broker1.example.com/ws", login=123, password="pw1"),
            PoolAccount(server="wss://broker2.example.com/ws", login=456, password="pw2"),
        ]
        async with MT5ConnectionPool(accounts) as pool:
            client = pool.get_client(123)
            await client.load_symbols()
    """

    def __init__(
        self,
        accounts: list[PoolAccount | dict[str, Any]],
        **client_kwargs: Any,
    ) -> None:
        self._accounts: list[PoolAccount] = []
        for acct in accounts:
            if isinstance(acct, dict):
                self._accounts.append(PoolAccount(**acct))
            else:
                self._accounts.append(acct)
        self._client_kwargs = client_kwargs
        self._clients: dict[int, MT5WebClient] = {}  # keyed by login

    async def __aenter__(self) -> MT5ConnectionPool:
        await self.connect_all()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close_all()

    async def connect_all(self) -> None:
        """Connect and log in to all configured accounts concurrently."""
        tasks = [self._connect_one(acct) for acct in self._accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for acct, result in zip(self._accounts, results):
            if isinstance(result, Exception):
                label = acct.label or str(acct.login)
                logger.error("failed to connect account %s: %s", label, result)

    async def _connect_one(self, acct: PoolAccount) -> None:
        """Connect and log in a single account."""
        client = MT5WebClient(uri=acct.server, **self._client_kwargs)
        await client.connect()
        await client.login(login=acct.login, password=acct.password)
        self._clients[acct.login] = client
        label = acct.label or str(acct.login)
        logger.info("pool: connected account %s", label)

    async def close_all(self) -> None:
        """Close all active client connections concurrently."""
        tasks = [self._close_one(login, client) for login, client in self._clients.items()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()
        logger.info("pool: all connections closed")

    async def _close_one(self, login: int, client: MT5WebClient) -> None:
        """Close a single client connection."""
        try:
            await client.close()
        except Exception as exc:
            logger.error("pool: error closing login=%d: %s", login, exc)

    def get_client(self, login: int) -> MT5WebClient | None:
        """Return the client for the given *login*, or ``None``."""
        return self._clients.get(login)

    @property
    def clients(self) -> list[MT5WebClient]:
        """Return all active client connections."""
        return list(self._clients.values())

    async def broadcast_subscribe_ticks(self, symbol_ids: list[int]) -> None:
        """Subscribe to ticks on all connected clients concurrently."""
        tasks = []
        for client in self._clients.values():
            tasks.append(client.subscribe_ticks(symbol_ids))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_load_symbols(self) -> None:
        """Load symbols on all connected clients concurrently."""
        tasks = []
        for client in self._clients.values():
            tasks.append(client.load_symbols())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
