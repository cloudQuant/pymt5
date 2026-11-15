Development Guide
=================

This guide covers a source checkout, offline validation, bilingual
documentation, and the automated delivery pipeline. Runtime behavior and public
interfaces are documented in :doc:`api` and :doc:`protocol_reference`; record
user-visible changes in :doc:`changelog`.

Set up an editable checkout
---------------------------

Python 3.11 or newer is required. CI tests Python 3.11, 3.12, and 3.13 on Linux,
macOS, and Windows. Create and activate a virtual environment using your
platform's normal workflow, then install the development and documentation
extras from the repository root:

.. code-block:: bash

   git clone https://github.com/cloudQuant/pymt5.git
   cd pymt5
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev,docs]"

The editable install keeps the ``pymt5`` import pointed at the checkout. The
``dev`` extra supplies pytest, pytest-asyncio, coverage, Hypothesis, mypy, Ruff,
build, and twine; ``docs`` supplies Sphinx, the Read the Docs theme, and autodoc
type-hint support. The distribution name remains ``mt5-web-client``.

Run the complete local check
----------------------------

From the repository root:

.. code-block:: bash

   make check

The target runs, in order:

1. ``lint`` — Ruff lint checks for ``pymt5/`` and ``tests/``;
2. ``typecheck`` — mypy checks for ``pymt5/``;
3. ``test`` — the offline pytest suite with the asyncio plugin explicitly loaded;
4. ``docs`` — the Sphinx documentation check; and
5. ``package-check`` — a clean, isolated wheel/sdist build followed by strict
   twine metadata validation.

Install both extras before using this target. ``make package-check`` removes
stale build metadata and artifacts before building fresh output under ``dist/``
and related build directories; inspect rather than commit generated output. If
one stage fails, fix that stage and rerun it individually before repeating the
complete check.

Individual quality commands
---------------------------

Use the same commands as the Makefile and workflows:

.. code-block:: bash

   ruff check pymt5/ tests/
   python -m mypy pymt5/ --ignore-missing-imports
   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/ -v --tb=short

On shells that do not support inline environment assignments, set
``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` in the process environment and run the same
``python -m pytest`` command. To select a test or file:

.. code-block:: bash

   python -m pytest tests/test_protocol_smoke.py -v --tb=short
   python -m pytest tests/ -k test_name -v --tb=short

For the CI coverage variant, explicitly load both plugins:

.. code-block:: bash

   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin -p pytest_cov.plugin tests/ -v --tb=short --cov=pymt5 --cov-report=term-missing

``make format`` runs ``ruff format pymt5/ tests/`` and rewrites files. Use it
only when formatting changes are intended; it is not a read-only check. Ruff's
configured target is Python 3.11 with a 120-character line limit. Do not broaden
a formatting or cleanup change beyond the task at hand.

Tests are offline by default
----------------------------

The default suite uses local fakes and binary fixtures; it needs no MT5 server
or credentials. Tests marked ``integration`` are skipped unless
``PYMT5_INTEGRATION`` is set. Default CI installs no live credentials and does
not run a live-server acceptance job.

A manual integration run requires a separately authorized demo environment and
``PYMT5_SERVER``, ``PYMT5_LOGIN``, and ``PYMT5_PASSWORD``.
``PYMT5_TEST_SYMBOL`` can select an exact server symbol for the tick test. No
ticks within 30 seconds causes a skip because the market may be closed, not a
pass that validates tick parsing. Cleanup errors, including rejected
unsubscribes, still matter.

Do not enable live tests in default CI, add real credentials to test files, or
use live trading as a substitute for offline tests. Review :doc:`security`
before any manual live run, keep it read-only unless a mutation is explicitly
authorized, and sanitize all output. Passing offline tests establishes local
regression coverage, not compatibility with every broker or server build.

Build English and Chinese documentation
---------------------------------------

Install the ``docs`` extra, then run the bilingual build from the repository
root:

.. code-block:: bash

   python docs/build_docs.py

The documentation build uses ``docs/`` as the English source root and
``docs/zh_CN/`` as the Simplified Chinese source root. Generated HTML is placed
at:

- ``docs/_build/html/en/`` for English; and
- ``docs/_build/html/zh-CN/`` for Simplified Chinese.

These are generated directories and should not be edited or committed. Open the
respective ``index.html`` files to inspect navigation, tables, code blocks,
cross-references, and API rendering. Run ``make docs`` from the repository root
to build both languages and validate the generated links and language switches,
or run the two scripts separately:

.. code-block:: bash

   python docs/build_docs.py
   python docs/check_docs.py

Sphinx warnings should be fixed at their source rather than hidden. When a page
references Python objects, verify signatures against :doc:`api` and current code;
when it describes bytes or command behavior, align it with
:doc:`protocol_reference` and identify observations that are build-specific.

Translation parity rule
-----------------------

English and Simplified Chinese pages are complete counterparts. Every new page,
section, warning, table row, command, and behavioral qualification must be
updated in both languages in the same change. Do not leave one language as a
stub, an outdated summary, or a machine-translated fragment.

Keep API names, class and method names, environment variables, command IDs,
literal configuration values, file paths, and shell commands identical unless a
language-specific convention requires otherwise. Translate prose, headings, and
explanatory labels while preserving warnings and uncertainty. In particular,
never soften statements about unknown trade outcomes, unofficial protocol
support, read-only scope, or unverified server compatibility.

Use each language tree's local ``:doc:`` references. The source directory name
``zh_CN`` intentionally differs from the published ``zh-CN`` path. Check both
outputs after documentation changes and review :doc:`changelog` when describing
release behavior.

CI and GitHub Pages
-------------------

The CI workflow has separate jobs for tests, lint/type checking, package build,
and documentation:

- Tests run on Ubuntu, macOS, and Windows with Python 3.11, 3.12, and 3.13.
- Ruff and mypy run on Ubuntu with Python 3.12.
- Package artifacts are built and checked with twine.
- Sphinx documentation is built with warnings treated as errors and
  ``--keep-going`` enabled.

No live integration credentials are part of the default matrix. A green CI run
therefore means the offline tests and static/package/documentation checks pass;
it is not live broker certification.

The GitHub Pages workflow runs on pushes to ``main`` or ``master`` and by manual
dispatch. It builds the documentation, configures Pages, uploads
``docs/_build/html`` as the Pages artifact, and deploys it through the
``github-pages`` environment. The bilingual site layout exposes English under
``/en/`` and Simplified Chinese under ``/zh-CN/``. Deployment permissions are
scoped to repository read, Pages write, and the OIDC identity token. A failed
build or upload should be fixed before examining deployment permissions.

Read the Docs
-------------

Read the Docs uses its native translation model: ``pymt5`` is the English parent
project and a separate Simplified Chinese project is linked as its translation.
Both projects use the same repository and ``.readthedocs.yaml``. The build
selects ``docs/`` or ``docs/zh_CN/`` from ``READTHEDOCS_LANGUAGE`` and writes one
language directly into ``$READTHEDOCS_OUTPUT/html``:

.. code-block:: bash

   python docs/build_docs.py --readthedocs

Canonical URLs come from ``READTHEDOCS_CANONICAL_URL`` rather than a hard-coded
host. Language switches use Read the Docs language/version paths, while GitHub
Pages keeps the combined ``/en/`` and ``/zh-CN/`` artifact. A project maintainer
must create and link the Chinese translation in the Read the Docs dashboard;
this cannot be done by the repository configuration alone.

Release pipeline
----------------

Pushing a ``v*.*.*`` tag first checks that the tag matches ``project.version``,
both changelog languages, and the source version fallbacks. Tests, lint/type
checks, bilingual documentation, and package verification must all pass before
publication. The package job builds a wheel and sdist, runs strict twine checks,
installs the wheel in a clean virtual environment outside the source tree, and
uploads the exact artifacts for later jobs.

The PyPI job downloads those artifacts and uses Trusted Publishing through the
protected ``pypi`` environment. Only that job receives OIDC permission. The
GitHub Release job downloads the same artifacts and attaches them to the release.
Do not upload distributions from a dirty local checkout. PyPI filenames are
immutable, so every release requires a new version and tag.

Contribution checklist
----------------------

- Keep changes focused and preserve public behavior unless the change explicitly
  requires otherwise.
- Add offline regression coverage for fixes, especially timeout, cancellation,
  protocol parsing, and connection-generation cases.
- Run the relevant test first, then ``make check`` before submitting.
- Update English and Chinese documentation together and verify both builds.
- Reference exact APIs and observed protocol evidence; do not invent numeric
  response-code meanings or claim cross-build validation that was not performed.
- Keep credentials, live account data, private terminal bundles, and generated
  analysis excerpts out of commits and reports, following :doc:`security`.
- Update :doc:`changelog` for user-visible changes and use :doc:`troubleshooting`
  for operational diagnostics rather than hiding them in development notes.
