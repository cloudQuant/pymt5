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
        """Called after a command is sent to the server."""
        ...

    def on_command_received(self, command: int, code: int) -> None:
        """Called when a response is received for a command."""
        ...

    def on_connect(self) -> None:
        """Called when the transport connects successfully."""
        ...

    def on_disconnect(self, reason: str) -> None:
        """Called when the transport disconnects."""
        ...

    def on_reconnect_attempt(self, attempt: int) -> None:
        """Called when a reconnection attempt begins."""
        ...

    def on_reconnect_success(self, attempt: int) -> None:
        """Called when reconnection succeeds."""
        ...
