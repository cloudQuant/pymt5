"""Custom exception hierarchy for pymt5."""


class PyMT5Error(Exception):
    """Base exception for all pymt5 errors."""


class MT5ConnectionError(PyMT5Error):
    """Raised when connection to the MT5 server fails."""


class AuthenticationError(PyMT5Error):
    """Raised when login/authentication fails."""


class TradeError(PyMT5Error, ValueError):
    """Raised when a trade operation fails.

    Inherits from ValueError for backward compatibility with existing
    code that catches ValueError from trade validation.
    """


class ProtocolError(PyMT5Error):
    """Raised when the binary protocol parsing fails."""


class SymbolNotFoundError(PyMT5Error, KeyError):
    """Raised when a symbol is not found in the cache.

    Inherits from KeyError for backward compatibility.
    """


class ValidationError(PyMT5Error, ValueError):
    """Raised when input validation fails.

    Inherits from ValueError for backward compatibility with existing
    code that catches ValueError from input validation.
    """


class SessionError(PyMT5Error, RuntimeError):
    """Raised when an operation requires a different session state.

    Inherits from RuntimeError for backward compatibility with existing
    code that catches RuntimeError from session state checks.
    """


class MT5TimeoutError(PyMT5Error):
    """Raised when a command times out."""
