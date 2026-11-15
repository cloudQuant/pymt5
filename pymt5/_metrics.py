"""Optional metrics collection for transport and client operations.

Implement the :class:`MetricsCollector` protocol and pass it to
:class:`MT5WebSocketTransport` or :class:`MT5WebClient` to receive
counters and timing data.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsCollector(Protocol):
    """Protocol for collecting pymt5 operational metrics."""

    def on_command_sent(self, command: int) -> None:
        """Record that a command was sent to the server."""
        ...

    def on_command_received(self, command: int, code: int) -> None:
        """Record a response received for a command."""
        ...

    def on_connect(self) -> None:
        """Record a successful transport connection."""
        ...

    def on_disconnect(self, reason: str) -> None:
        """Record a transport disconnection and its reason."""
        ...

    def on_reconnect_attempt(self, attempt: int) -> None:
        """Record the start of a reconnection attempt."""
        ...

    def on_reconnect_success(self, attempt: int) -> None:
        """Record a successful reconnection attempt."""
        ...
