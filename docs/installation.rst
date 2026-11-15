Installation
============

Requirements
------------

- Python 3.11, 3.12, or 3.13
- Linux, macOS, or Windows
- An MT5 Web Terminal endpoint and an account you are authorized to use for
  live connection tests

The runtime dependencies are ``websockets`` and ``cryptography``. No local
MetaTrader terminal installation is required.

Install from PyPI
-----------------

.. code-block:: bash

   python -m pip install mt5-web-client

The distribution and import names differ intentionally:

.. code-block:: python

   from pymt5 import MT5WebClient

Optional integrations
---------------------

Install structured logging or pandas support only when needed:

.. code-block:: bash

   python -m pip install "mt5-web-client[structlog]"
   python -m pip install "mt5-web-client[pandas]"

Install for development
-----------------------

.. code-block:: bash

   git clone https://github.com/cloudQuant/pymt5.git
   cd pymt5
   python -m pip install -e ".[dev,docs]"

Verify the offline installation:

.. code-block:: bash

   python -c "import pymt5; print(pymt5.MT5WebClient.__name__)"
   python -m pytest tests/ -q

The default test suite is offline. Live integration tests remain disabled
unless ``PYMT5_INTEGRATION=1`` is set explicitly; see :doc:`development`.

Upgrade and compatibility
-------------------------

Review :doc:`changelog` before upgrading. Pin a tested version in production
and revalidate protocol-sensitive workflows against the exact server build you
use. A successful install or offline test run does not establish broker
compatibility.

Next steps
----------

Continue with :doc:`quickstart`, then review :doc:`configuration` and
:doc:`security` before enabling subscriptions or trading.
