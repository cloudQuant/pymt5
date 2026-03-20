"""Input validation helpers for pymt5."""

from __future__ import annotations

from pymt5.exceptions import SessionError, ValidationError


def validate_volume(volume: float) -> None:
    """Validate that volume is positive."""
    if volume <= 0:
        raise ValidationError(f"volume must be > 0, got {volume}")


def validate_price(price: float, name: str = "price") -> None:
    """Validate that a price value is positive."""
    if price <= 0.0:
        raise ValidationError(f"{name} must be > 0, got {price}")


def validate_symbol_name(name: str) -> None:
    """Validate that a symbol name is non-empty and printable."""
    if not name or not name.strip():
        raise ValidationError("symbol name must be non-empty")
    if not name.isprintable():
        raise ValidationError(f"symbol name contains non-printable characters: {name!r}")


def validate_connection_state(is_ready: bool, logged_in: bool) -> None:
    """Validate that the client is connected and logged in."""
    if not is_ready:
        raise SessionError("transport is not connected")
    if not logged_in:
        raise SessionError("not logged in")
