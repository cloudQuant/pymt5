# Repository Guidelines

## Project Structure & Module Organization

`pymt5/` contains the library code. The main layers are `client.py` for the public async API, `transport.py` for WebSocket/session handling, `protocol.py` and `schemas.py` for binary encoding/parsing, and `constants.py` for MT5 command and enum values. `tests/` holds offline unit tests and shared setup in `tests/conftest.py`. `examples/` contains numbered usage samples, `scripts/` contains manual live-check helpers, and `docs/` contains the Sphinx source. `analysis/` stores reverse-engineering artifacts and is not part of the runtime package.

## Build, Test, and Development Commands

Install development dependencies with `pip install -e ".[dev]"`. Common commands:

- `make test` runs the full pytest suite with verbose output.
- `make lint` runs `ruff check pymt5/ tests/`.
- `make format` formats `pymt5/` and `tests/` with Ruff.
- `make typecheck` runs MyPy against `pymt5/`.
- `make docs` builds Sphinx docs under `docs/_build/html`.
- `python -m build && twine check dist/*` matches the CI package verification step.

## Coding Style & Naming Conventions

Target Python is 3.11+. Use 4-space indentation, type hints, and small, direct docstrings where needed. Ruff enforces imports and style with a 120-character line length; run `make format` before opening a PR. Follow existing naming patterns: `snake_case` for functions and modules, `UPPER_CASE` for MT5 constants, and `PascalCase` for dataclasses such as `TradeResult` and `AccountInfo`. Keep public API additions in `pymt5/client.py` consistent with the current async interface.

## Testing Guidelines

Tests use `pytest` with `pytest-asyncio` in auto mode. Add tests under `tests/` using `test_*.py` filenames and `test_*` function names. Prefer offline unit tests with mocked transport behavior; CI runs coverage collection, but there is no fixed coverage threshold in config. Reserve `scripts/live_test*.py` for manual broker validation, not automated test coverage.

## Commit & Pull Request Guidelines

Recent history favors short Conventional Commit-style subjects such as `fix: ...`, `feat: ...`, and `docs: ...`. Keep commit messages imperative and focused. For pull requests, include a brief description, note any protocol or API behavior changes, list the commands you ran (`make test`, `make lint`, `make typecheck`), and attach screenshots only when documentation output or rendered pages changed.

## Security & Configuration Tips

Do not commit broker credentials, session tokens, or live account data. Keep live-test secrets in local environment variables or untracked files, and avoid hardcoding server-specific values in examples or tests.
