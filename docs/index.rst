pymt5 Documentation
===================

**pymt5** is an asynchronous Python client for the MetaTrader 5 Web Terminal.
It connects over a reverse-engineered, encrypted WebSocket binary protocol and
provides account queries, market data, streaming callbacks, and trading helpers.

The PyPI distribution is named ``mt5-web-client``; the Python import is
``pymt5``. Python 3.11 or newer is required.

.. warning::

   This is an unofficial, experimental client. It is not affiliated with or
   endorsed by MetaQuotes. Protocol behavior varies by broker, server build,
   symbol, and market session. Start with an authorized demo account, validate
   every required workflow, and never treat a request timeout as proof that a
   trade was not executed.

Choose your path
----------------

.. list-table::
   :header-rows: 1
   :widths: 24 48 28

   * - I want to…
     - Start here
     - Then read
   * - Install and make a first read-only connection
     - :doc:`installation` and :doc:`quickstart`
     - :doc:`configuration`
   * - Retrieve bars or stream ticks and order-book updates
     - :doc:`market_data`
     - :doc:`push_notifications`
   * - Use trading helpers on an authorized demo account
     - :doc:`trading`
     - :doc:`security` and :doc:`reliability`
   * - Handle disconnects, timeouts, and server rejections
     - :doc:`reliability`
     - :doc:`troubleshooting`
   * - Look up a method, command, or compatibility helper
     - :doc:`api`
     - :doc:`protocol_reference` and :doc:`python_api_compat`
   * - Run tests or improve the documentation
     - :doc:`development`
     - :doc:`changelog`

Quick installation
------------------

.. code-block:: bash

   python -m pip install mt5-web-client

A first connection should load account and symbol data without placing an
order. See :doc:`quickstart` for a complete environment-based example.

Capabilities and boundaries
---------------------------

- Async WebSocket lifecycle, bootstrap key exchange, login, heartbeat, and
  optional automatic reconnection.
- Account, position, order, deal, symbol, historical-bar, tick, and order-book
  APIs.
- Raw and typed push callbacks, local caches, optional metrics, and structured
  logging.
- High-level trading helpers plus a lower-level ``trade_request()`` API.
- MetaTrader5-style compatibility helpers where the Web Terminal exposes an
  equivalent; local or cached helpers are identified as such.
- Explicit compatibility controls for tick prices, historical bar layouts, and
  trade request profiles. Observed support for one build is not a guarantee for
  another server.

Documentation map
-----------------

.. toctree::
   :maxdepth: 2
   :caption: Get started

   installation
   quickstart
   configuration
   examples

.. toctree::
   :maxdepth: 2
   :caption: User guides

   market_data
   trading
   push_notifications
   reliability
   security
   troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api
   protocol_reference
   command_coverage
   python_api_compat
   python_api_detailed_comparison

.. toctree::
   :maxdepth: 1
   :caption: Project

   development
   author
   changelog

Project links
-------------

- `GitHub repository <https://github.com/cloudQuant/pymt5>`_
- `Issue tracker <https://github.com/cloudQuant/pymt5/issues>`_
- :ref:`genindex`
- :ref:`search`
