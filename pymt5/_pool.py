"""Connection pool for managing multiple MT5 client connections."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pymt5._logging import get_logger
from pymt5.client import MT5WebClient

logger = get_logger("pymt5.pool")


@dataclass
class PoolAccount:
    """Account credentials for a pool connection."""

    server: str
    login: int
    password: str = field(repr=False)
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
        """Configure a pool without opening any connections.

        Args:
            accounts: Account definitions used to create clients. Dictionary
                entries must contain fields accepted by :class:`PoolAccount`.
            **client_kwargs: Additional keyword arguments forwarded to every
                :class:`MT5WebClient` constructor.

        """
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
        """Connect and log in to all configured accounts concurrently.

        Raises:
            ValueError: If more than one configured account uses the same login.

        """
        seen: set[int] = set()
        duplicates: set[int] = set()
        for acct in self._accounts:
            if acct.login in seen:
                duplicates.add(acct.login)
            seen.add(acct.login)
        if duplicates:
            login_list = ", ".join(str(login) for login in sorted(duplicates))
            raise ValueError(f"Duplicate login key(s) configured for pool accounts: {login_list}")

        connect_tasks = [asyncio.create_task(self._connect_one(acct)) for acct in self._accounts]
        try:
            results = await asyncio.gather(*connect_tasks, return_exceptions=True)
        except BaseException:
            for task in connect_tasks:
                task.cancel()
            await asyncio.gather(*connect_tasks, return_exceptions=True)
            await self.close_all()
            raise
        for acct, result in zip(self._accounts, results):
            if isinstance(result, Exception):
                label = acct.label or str(acct.login)
                logger.error("failed to connect account %s: %s", label, result)

    async def _connect_one(self, acct: PoolAccount) -> None:
        """Connect and log in a single account without replacing an existing client."""
        if acct.login in self._clients:
            raise ValueError(f"Login key {acct.login} is already connected; refusing to overwrite its client")

        client = MT5WebClient(uri=acct.server, **self._client_kwargs)
        try:
            await client.connect()
            await client.login(login=acct.login, password=acct.password)
            # A concurrent _connect_one call may have installed this login while
            # this client was connecting. Never overwrite that existing client.
            if acct.login in self._clients:
                raise ValueError(f"Login key {acct.login} is already connected; refusing to overwrite its client")
        except BaseException:
            try:
                await client.close()
            except BaseException as cleanup_error:
                # Cleanup failures must not hide connect/login errors or cancellation.
                logger.error("pool: error closing login=%d after connection failure: %s", acct.login, cleanup_error)
            raise

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

    def _account_label(self, login: int) -> str:
        for acct in self._accounts:
            if acct.login == login and acct.label:
                return f"{acct.label} (login={login})"
        return str(login)

    def _log_broadcast_failures(self, operation: str, logins: list[int], results: list[Any]) -> None:
        for login, result in zip(logins, results):
            if isinstance(result, Exception):
                logger.warning("pool: %s failed for account %s: %s", operation, self._account_label(login), result)

    async def broadcast_subscribe_ticks(self, symbol_ids: list[int]) -> None:
        """Subscribe to ticks on all connected clients concurrently."""
        if not self._clients:
            return
        logins = list(self._clients.keys())
        tasks = [self._clients[login].subscribe_ticks(symbol_ids) for login in logins]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._log_broadcast_failures("subscribe_ticks", logins, results)

    async def broadcast_load_symbols(self) -> None:
        """Load symbols on all connected clients concurrently."""
        if not self._clients:
            return
        logins = list(self._clients.keys())
        tasks = [self._clients[login].load_symbols() for login in logins]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._log_broadcast_failures("load_symbols", logins, results)
