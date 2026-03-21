pymt5 Documentation
====================

**pymt5** is a Python client for the MT5 Web Terminal via reverse-engineered WebSocket binary protocol.

It provides full access to account data, market data, real-time streaming, and trading operations
through a clean async Python API.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   quickstart
   api
   trading
   push_notifications
   protocol_reference
   command_coverage
   python_api_compat
   python_api_detailed_comparison
   iteration_plan
   iteration_plan_v2
   iteration_plan_v3
   iteration_plan_v4
   iteration_plan_v5
   changelog

Features
--------

- WebSocket transport to ``wss://web.metatrader.app/terminal``
- AES-CBC encryption with zero IV and PKCS7 padding
- Bootstrap handshake with automatic key exchange
- Login with UTF-16LE encoded fields
- Full account information (balance, equity, margin, leverage)
- Open positions, pending orders, and trade history
- Symbol cache with fast name-to-ID lookup
- Historical OHLCV bars (M1 to MN1)
- Real-time tick streaming with callbacks
- Order book (depth-of-market) subscription
- All 9 order types: market buy/sell, limit, stop, stop-limit
- Position close, modify SL/TP, cancel pending orders
- 9 push notification types for real-time updates
- Auto heartbeat and auto reconnect with exponential backoff
- Async context manager support

Requirements
------------

- Python 3.11+
- ``websockets >= 12.0``
- ``cryptography >= 42.0.0``

Installation
------------

.. code-block:: bash

   pip install pymt5

Or install from source:

.. code-block:: bash

   git clone https://github.com/cloudQuant/pymt5.git
   cd pymt5
   pip install -e .

Indices and Tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
