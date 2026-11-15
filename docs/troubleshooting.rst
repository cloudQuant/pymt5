Troubleshooting
===============

Diagnose one layer at a time: Python environment, WebSocket bootstrap, login,
server response, then protocol parsing. A successful earlier layer does not
validate the next one. Keep initial tests read-only and use an authorized demo
account as described in :doc:`security`.

Installation and Python versions
--------------------------------

pymt5 requires Python 3.11 or newer; the package metadata and CI cover Python
3.11, 3.12, and 3.13 on Linux, macOS, and Windows. The PyPI distribution is
``mt5-web-client``, but the import package is ``pymt5``:

.. code-block:: bash

   python --version
   python -m pip install mt5-web-client
   python -c "import pymt5; print(pymt5.MT5WebClient.__name__)"

Use ``python -m pip`` so installation and import use the same interpreter. If
the import fails after installation, check the active virtual environment and
IDE interpreter rather than installing repeatedly into different environments.
For a source checkout, use ``python -m pip install -e ".[dev,docs]"`` from the
repository root.

Installation failures involving dependencies should be investigated with the
complete package-manager error, Python version, operating system, and
architecture. Do not disable TLS verification or accept an untrusted package as
a workaround. Runtime dependencies are ``websockets`` and ``cryptography``;
Sphinx and test tools are optional extras.

Connection and bootstrap failures
---------------------------------

The normal sequence is ``connect()`` followed by ``login()``. ``connect()``
opens the WebSocket and performs the encrypted bootstrap/key exchange. It does
not authenticate the account.

Check the following when ``MT5ConnectionError``, ``MT5TimeoutError``, an
``OSError``, or a bootstrap-related ``ProtocolError`` occurs:

- Use the exact authorized ``wss://`` Web Terminal endpoint. The default is
  ``wss://web.metatrader.app/terminal``; do not confuse it with a broker's
  ordinary website or desktop-terminal address.
- Confirm DNS, outbound WebSocket/TLS access, firewall rules, and any required
  corporate proxy. The examples set ``NO_PROXY`` and ``no_proxy`` to ``*``;
  review or remove that bypass if your network requires a proxy.
- Check that the timeout allows for network and bootstrap latency. Increasing a
  timeout may help a slow read-only handshake, but it does not make retrying a
  trade safe.
- Distinguish a rejected WebSocket connection from a bootstrap response or
  frame-format problem. Record ``client.server_build`` after a successful
  bootstrap; an empty or zero build is not evidence of a later login success.
- Create a fresh client after an invalidated or incompletely closed session.
  ``SessionError`` messages about close or reconnect cleanup indicate that a
  previous generation is still being retired, not that credentials are wrong.

See :doc:`quickstart` for the basic lifecycle and :doc:`configuration` for URI,
timeout, heartbeat, and reconnection settings.

Login errors and build-specific payloads
----------------------------------------

A nonzero cmd=28 login response raises ``AuthenticationError`` containing the
numeric response code. pymt5 currently reports that number; it does not define
a complete mapping from login response codes to business meanings. Do not
assume that a particular number means a bad password, missing OTP, unsupported
build, or any other specific cause without authoritative evidence for that
server.

Before changing credentials or payload options:

1. Verify the authorized endpoint, numeric login, password, account type, and
   whether the account requires an OTP. Never include those secrets in a bug
   report.
2. Record the bootstrap ``client.server_build`` and the installed pymt5 version.
3. Distinguish a nonzero response (``AuthenticationError``) from a success
   response with a truncated or invalid body (``ProtocolError``). The latter is
   a payload/parser compatibility problem, not evidence of valid authentication.
4. If the client says it is already logged in, call ``logout()`` before another
   login on the same client. A successful logout clears stored reconnect
   credentials; a rejected logout raises ``SessionError`` and retains session
   state.

The current login payload ends with an additional ``PROP_U64`` field observed
in Web Terminal build 6320. This is a build-specific compatibility observation,
not a universal rule and not an explanation for arbitrary login codes. Avoid
adding ``init_session()``, changing ``version`` or ``cid``, or removing fields
by guesswork. Compare the authorized terminal flow and report the observed
build, payload length, and sanitized result instead. Do not include a password,
CID, token, session ID, or raw authenticated frame.

The trade-specific ``trade_protocol="build_6090"`` profile is unrelated to
login authentication and must not be used as a login workaround. Release
compatibility notes are in :doc:`changelog`.

No ticks after subscribing
--------------------------

A successful subscription response only confirms that the server accepted the
request. It does not guarantee a tick during the observation window.

- Call ``load_symbols()`` first and use an exact name returned by that server.
  Broker suffixes and symbol availability differ.
- Check whether the selected market is open. Weekends, holidays, illiquid
  instruments, and closed sessions can legitimately produce no ticks.
- Register the callback before subscribing, allow enough time during an active
  session, and verify that the callback filters for the intended ``symbol_id``.
- Do not interpret an empty tick history as proof of a parser defect. The
  observed tick layout and price normalization still have server/build
  compatibility boundaries described in :doc:`protocol_reference`.

For the optional live integration test, ``PYMT5_TEST_SYMBOL`` selects an exact
symbol from the loaded server list; otherwise the test selects the first symbol,
which may be inactive. This variable is used by the integration test, not by
the scripts in ``examples/``. The test skips after 30 seconds without a tick
because the market may be closed; a skip is not tick-format validation.

For a separately authorized demo investigation,
``scripts/demo_tick_probe.py`` collects a normalized wire tick and nearby M1
bars without placing trades. Its JSON distinguishes no sample, parsing errors,
bar lookup, unsubscribe, and close outcomes. Read :doc:`examples` before using
any live script.

Ambiguous historical rates
--------------------------

Observed rate records are 48 bytes (standard) or 56 bytes (extended, including
``real_volume``). In the default ``bar_format="auto"`` mode, a nonzero response,
a length divisible by both record sizes, or a length divisible by neither raises
``ProtocolError``. This is intentional: the client refuses to guess.

After establishing the format for the target server, either construct
``MT5WebClient(rate_bar_format="standard")`` or ``"extended"``, or pass
``bar_format=`` to ``get_rates()`` or a ``copy_rates_*()`` wrapper. Choosing a
format merely to make an exception disappear can silently decode incorrect
data. ``get_rates_raw()`` can support an authorized investigation, but length
alone cannot resolve an ambiguous layout and length checks cannot detect every
truncation. See :doc:`configuration` and :doc:`protocol_reference`.

Rejected full tick unsubscribe
------------------------------

The server replaces the tick subscription set with each cmd=7 request. Removing
the last locally known symbol therefore sends a four-byte zero count. On an
observed build 6320 session, full unsubscription was rejected with response
``code=1``. The business meaning of that code and the required alternative
payload have not been established; do not relabel it as a generic success,
permission error, or rate limit.

Current ``unsubscribe_ticks()`` behavior is deliberate:

- A nonzero response raises ``ProtocolError``.
- The last confirmed local subscription set is retained for a later retry or
  reconnect restoration.
- Cleanup must not be reported as successful when the request was rejected.

Check partial versus full removal separately and inspect the sanitized numeric
response. Do not ignore the exception or randomly switch between a zero-count
payload and an empty payload. If unsubscription cannot be confirmed, closing
the connection ends that client session's network stream, but it does not prove
which acknowledgement the server previously processed. Preserve the cleanup
error when reporting the issue.

Timeouts, connection isolation, and reconnect
---------------------------------------------

If sending may have started, a timeout, cancellation, or send error makes the
outcome uncertain. The transport then invalidates and isolates the connection,
fails other in-flight requests with ``SessionError``, and prevents subsequent
commands from reusing that generation. A late frame therefore cannot safely be
treated as the answer to a later request.

With the default ``auto_reconnect=False``, the application controls recovery.
With ``auto_reconnect=True`` and stored login credentials, an unexpected
disconnect starts bounded exponential backoff with jitter, creates a fresh
transport, logs in again, and restores confirmed tick/book subscription intent.
Reconnection does not replay the interrupted request or reveal its result.

For read-only queries, retry only after the new session is ready and consider
whether the previous response could have been ambiguous. For trades, never
blindly retry: reconcile positions, orders, and deal/order history first, as
explained in :doc:`security`. Repeated cleanup or generation ``SessionError``
messages should be investigated as lifecycle issues rather than handled with a
tight reconnect loop.

Logging and sanitized issue reports
-----------------------------------

Set ``PYMT5_LOG_LEVEL=DEBUG`` before starting the process or importing pymt5 to
capture library diagnostics. With the standard logging fallback, also configure
handlers and formatting, for example:

.. code-block:: python

   import logging
   import os

   os.environ["PYMT5_LOG_LEVEL"] = "DEBUG"  # before importing pymt5
   logging.basicConfig(level=logging.DEBUG)

   from pymt5 import MT5WebClient  # noqa: E402

Enable DEBUG only for a bounded, authorized reproduction. INFO login messages
currently include the numeric login and session ID, and URI, account, callback,
or application output can contain additional private data.

Before opening an issue at
`https://github.com/cloudQuant/pymt5/issues <https://github.com/cloudQuant/pymt5/issues>`_,
remove or redact:

- passwords, OTPs, tokens, session IDs, CIDs, login/account numbers, and cookies;
- account balances, positions, orders, deals, private symbol inventories, and
  raw authenticated frames;
- private Web Terminal JavaScript, generated analysis excerpts, local paths
  that reveal identity, and any third-party material you cannot redistribute.

A useful sanitized report includes the pymt5 and Python versions, operating
system, server build, public method and command ID, exception type, numeric
response/retcode, UTC timeline, configuration names with secrets removed, and a
minimal offline reproduction where possible. State whether the market was open,
whether reconnection was enabled, and whether the operation was read-only. Keep
unknown protocol meanings explicitly unknown. API signatures are in :doc:`api`;
release-specific behavior is in :doc:`changelog`.
