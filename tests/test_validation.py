"""Tests for pymt5._validation module."""

from __future__ import annotations

import pytest

from pymt5._validation import (
    validate_connection_state,
    validate_price,
    validate_symbol_name,
    validate_volume,
)
from pymt5.exceptions import SessionError, ValidationError

# ---------------------------------------------------------------------------
# validate_volume
# ---------------------------------------------------------------------------


class TestValidateVolume:
    """Tests for validate_volume."""

    def test_positive_integer_passes(self) -> None:
        validate_volume(1)

    def test_positive_float_passes(self) -> None:
        validate_volume(0.01)

    def test_large_volume_passes(self) -> None:
        validate_volume(100_000.0)

    def test_very_small_positive_passes(self) -> None:
        validate_volume(1e-10)

    def test_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume must be > 0"):
            validate_volume(0)

    def test_zero_float_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume must be > 0"):
            validate_volume(0.0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume must be > 0"):
            validate_volume(-1)

    def test_negative_float_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume must be > 0"):
            validate_volume(-0.5)

    def test_large_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume must be > 0"):
            validate_volume(-100_000.0)

    def test_error_message_includes_value(self) -> None:
        with pytest.raises(ValidationError, match="-42"):
            validate_volume(-42)

    def test_is_validation_error_subclass_of_value_error(self) -> None:
        with pytest.raises(ValueError):
            validate_volume(-1)


# ---------------------------------------------------------------------------
# validate_price
# ---------------------------------------------------------------------------


class TestValidatePrice:
    """Tests for validate_price."""

    def test_positive_integer_passes(self) -> None:
        validate_price(100)

    def test_positive_float_passes(self) -> None:
        validate_price(1.23456)

    def test_very_small_positive_passes(self) -> None:
        validate_price(0.00001)

    def test_large_price_passes(self) -> None:
        validate_price(99999.99)

    def test_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="price must be > 0"):
            validate_price(0.0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="price must be > 0"):
            validate_price(-1.5)

    def test_custom_name_in_error_message(self) -> None:
        with pytest.raises(ValidationError, match="stop_loss must be > 0"):
            validate_price(-1.0, name="stop_loss")

    def test_custom_name_take_profit(self) -> None:
        with pytest.raises(ValidationError, match="take_profit must be > 0"):
            validate_price(0.0, name="take_profit")

    def test_default_name_is_price(self) -> None:
        with pytest.raises(ValidationError, match="price must be > 0"):
            validate_price(-5.0)

    def test_error_message_includes_value(self) -> None:
        with pytest.raises(ValidationError, match="-99.5"):
            validate_price(-99.5)

    def test_is_validation_error_subclass_of_value_error(self) -> None:
        with pytest.raises(ValueError):
            validate_price(-1.0)


# ---------------------------------------------------------------------------
# validate_symbol_name
# ---------------------------------------------------------------------------


class TestValidateSymbolName:
    """Tests for validate_symbol_name."""

    def test_normal_symbol_passes(self) -> None:
        validate_symbol_name("EURUSD")

    def test_symbol_with_dot_passes(self) -> None:
        validate_symbol_name("EUR.USD")

    def test_symbol_with_underscore_passes(self) -> None:
        validate_symbol_name("BTC_USD")

    def test_symbol_with_digits_passes(self) -> None:
        validate_symbol_name("NQ100")

    def test_symbol_with_hash_passes(self) -> None:
        validate_symbol_name("#AAPL")

    def test_single_char_passes(self) -> None:
        validate_symbol_name("X")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValidationError, match="symbol name must be non-empty"):
            validate_symbol_name("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValidationError, match="symbol name must be non-empty"):
            validate_symbol_name("   ")

    def test_tab_only_raises(self) -> None:
        with pytest.raises(ValidationError, match="symbol name must be non-empty"):
            validate_symbol_name("\t")

    def test_newline_only_raises(self) -> None:
        with pytest.raises(ValidationError, match="symbol name must be non-empty"):
            validate_symbol_name("\n")

    def test_non_printable_control_char_raises(self) -> None:
        with pytest.raises(ValidationError, match="non-printable characters"):
            validate_symbol_name("EUR\x00USD")

    def test_non_printable_bell_raises(self) -> None:
        with pytest.raises(ValidationError, match="non-printable characters"):
            validate_symbol_name("ABC\x07")

    def test_non_printable_escape_raises(self) -> None:
        with pytest.raises(ValidationError, match="non-printable characters"):
            validate_symbol_name("\x1bEURUSD")

    def test_is_validation_error_subclass_of_value_error(self) -> None:
        with pytest.raises(ValueError):
            validate_symbol_name("")


# ---------------------------------------------------------------------------
# validate_connection_state
# ---------------------------------------------------------------------------


class TestValidateConnectionState:
    """Tests for validate_connection_state."""

    def test_both_true_passes(self) -> None:
        validate_connection_state(is_ready=True, logged_in=True)

    def test_not_ready_raises(self) -> None:
        with pytest.raises(SessionError, match="transport is not connected"):
            validate_connection_state(is_ready=False, logged_in=True)

    def test_not_logged_in_raises(self) -> None:
        with pytest.raises(SessionError, match="not logged in"):
            validate_connection_state(is_ready=True, logged_in=False)

    def test_neither_ready_nor_logged_in_raises_not_connected(self) -> None:
        """When both are False, is_ready is checked first."""
        with pytest.raises(SessionError, match="transport is not connected"):
            validate_connection_state(is_ready=False, logged_in=False)

    def test_is_session_error_subclass_of_runtime_error(self) -> None:
        with pytest.raises(RuntimeError):
            validate_connection_state(is_ready=False, logged_in=True)

    def test_is_session_error_subclass_of_pymt5_error(self) -> None:
        from pymt5.exceptions import PyMT5Error

        with pytest.raises(PyMT5Error):
            validate_connection_state(is_ready=True, logged_in=False)
