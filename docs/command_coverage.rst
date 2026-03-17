MT5 Web Terminal Command Coverage
=================================

This page tracks command IDs seen in the MT5 Web Terminal frontend chunk
``analysis/D8ROuLSQ.js`` (captured on 2026-03-12) and compares them with the
public ``pymt5`` API as of 2026-03-16.

The live site was re-checked on 2026-03-16. The current official terminal entry
page reports build ``5687``, built on 2026-03-15, and still loads the same API
chunk name (``D8ROuLSQ.js``) with the same direct ``sendCommand(...)`` call set.

Observed Frontend Request Commands
----------------------------------

The current frontend snapshot contains direct ``sendCommand(...)`` call sites for:

- ``0`` bootstrap
- ``2`` logout
- ``3`` get account
- ``4`` get positions and orders
- ``5`` get trade history
- ``6`` get symbols
- ``7`` subscribe ticks
- ``9`` get symbol groups
- ``11`` get rates
- ``12`` trade request
- ``18`` get full symbol info
- ``20`` get spreads
- ``22`` subscribe book
- ``24`` change password
- ``27`` verify codes / verification flow
- ``28`` login
- ``29`` init
- ``30`` open demo account
- ``34`` get symbols (gzip)
- ``39`` open real account
- ``40`` send verification codes
- ``41`` trader params
- ``42`` notify
- ``43`` OTP setup / connect OTP
- ``44`` get corporate links
- ``51`` ping

Observed frontend event handlers include ``8``, ``10``, ``13``, ``14``, ``15``,
``17``, ``19``, and ``23`` for tick, trade, symbol, account, login, symbol-detail,
trade-result, and book pushes.

Implemented In pymt5
--------------------

``pymt5`` now covers the visible frontend command surface:

- Session/auth: ``0``, ``2``, ``24``, ``27``, ``28``, ``29``, ``30``, ``39``, ``40``,
  ``41``, ``42``, ``43``, ``51``
- Account/trading data: ``3``, ``4``, ``5``, ``12``
- Market data: ``6``, ``7``, ``9``, ``11``, ``18``, ``20``, ``22``, ``34``, ``44``
- Push handlers: ``8``, ``10``, ``13``, ``14``, ``15``, ``17``, ``19``, ``23``

Command Meanings
----------------

- ``cmd=27`` account-opening verification request:
  the current frontend sends ``int16 build + cid + base opening payload``.
  ``pymt5`` exposes this as ``request_opening_verification()`` and parses the
  two-byte response into ``VerificationStatus(email, phone)``.
- ``cmd=30`` demo account opening:
  the current frontend sends the full registration payload, not the older
  init-like payload. ``pymt5`` now mirrors that via ``open_demo_account()``.
  The legacy ``open_demo()`` wrapper remains for compatibility.
- ``cmd=39`` real account opening:
  this extends the demo payload with identity/KYC fields, a ``propType=9``
  birth date, and zero or more uploaded documents. ``pymt5`` implements this
  as ``open_real_account()``.
- ``cmd=40`` verification code submission:
  this re-sends the base opening payload with populated email/phone code fields.
  ``pymt5`` exposes it as ``submit_opening_verification()`` and parses the same
  two-flag response shape as cmd=27.
- ``cmd=43`` OTP setup:
  the frontend uses one payload shape for both enabling and disabling TOTP.
  ``pymt5`` maps this to ``enable_otp()`` and ``disable_otp()``.

Response Shapes Confirmed From Frontend Usage
---------------------------------------------

- ``cmd=27`` and ``cmd=40`` return two ``u8`` flags.
  Inference: the flags represent email and phone verification state, because the
  frontend uses them to decide which confirmation fields to show and whether the
  submitted codes were accepted.
- ``cmd=30`` and ``cmd=39`` return
  ``[code, login, password, investor_password]``.
  This is confirmed by the frontend controller, which treats the first field as
  an error code and stores the remaining three values as new-account credentials.

Unknown Or Reserved IDs
-----------------------

The frontend transport allowlist also accepts these IDs:

``21``, ``25``, ``33``, ``37``, ``50``, ``52``, ``100``, ``101``, ``102``, ``103``,
``104``, ``105``, ``106``, ``107``, ``108``, ``109``, ``110``, ``111``, ``112``.

No direct ``sendCommand(...)`` call sites were found for them in the current frontend
snapshot or in the live 2026-03-16 bundle.

Live Probe Results
------------------

Minimal live probing against ``web.metatrader.app`` on 2026-03-16 produced these
repeatable results:

- ``21``, ``25``, ``33``, ``37``, ``50``, ``100``-``112``:
  accepted by the transport, but an empty payload produced no response before the
  client timeout, both before and after login. Inference: these commands likely
  require specialized payloads or are disabled on the MetaQuotes-Demo server.
- ``52``:
  returns ``code=0`` with an empty body only on a fresh bootstrap-only socket.
  After ``cmd=29`` or ``cmd=28``, the same command causes the server to drop the
  connection without a close frame. ``pymt5`` exposes this exact behavior as the
  experimental helper ``send_bootstrap_command_52()``.

These IDs should still be treated as reserved or unidentified until a newer bundle,
an older historical bundle, or a broker-specific capture reveals their payload
schemas and business meaning.

- [ ] Re-scan future Web Terminal bundles and map the unresolved IDs before adding
  named business APIs for them.
