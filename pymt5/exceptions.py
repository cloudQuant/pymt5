"""Custom exception hierarchy for pymt5."""


class PyMT5Error(Exception):
    """Base exception for all pymt5 errors."""


class MT5ConnectionError(PyMT5Error, ConnectionError):
    """Raised when connection to the MT5 server fails.

    Inherits from ConnectionError for compatibility with stdlib
    connection error handling patterns.

    Attributes:
        server_uri: The URI of the server that was being connected to.

    """

    def __init__(self, message: str = "", *, server_uri: str = "") -> None:
        """Initialize a connection error with its target server.

        Args:
            message: Human-readable description of the connection failure.
            server_uri: URI of the server involved in the failure, if known.

        """
        super().__init__(message)
        self.server_uri = server_uri


class AuthenticationError(PyMT5Error):
    """Raised when login/authentication fails."""


class TradeError(PyMT5Error, ValueError):
    """Raised when a trade operation fails.

    Inherits from ValueError for backward compatibility with existing
    code that catches ValueError from trade validation.

    Attributes:
        retcode: MT5 return code.
        symbol: Symbol involved in the trade.
        action: Trade action that failed.

    """

    def __init__(
        self,
        message: str = "",
        *,
        retcode: int = 0,
        symbol: str = "",
        action: int = 0,
    ) -> None:
        """Initialize a trade error with server and operation context.

        Args:
            message: Human-readable description of the trade failure.
            retcode: MT5 return code associated with the failure.
            symbol: Symbol involved in the operation, if known.
            action: Numeric trade action that failed, if known.

        """
        super().__init__(message)
        self.retcode = retcode
        self.symbol = symbol
        self.action = action


class ProtocolError(PyMT5Error, ValueError):
    """Raised when the binary protocol parsing fails.

    Inherits from ValueError for backward compatibility with existing
    code that catches ValueError from protocol parsing.
    """


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


class MT5TimeoutError(PyMT5Error, TimeoutError):
    """Raised when a command times out.

    Inherits from TimeoutError for compatibility with stdlib
    timeout handling patterns (e.g., asyncio.wait_for).
    """
