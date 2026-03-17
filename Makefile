.PHONY: test lint format docs typecheck package-check check

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/ -v --tb=short

lint:
	ruff check pymt5/ tests/

format:
	ruff format pymt5/ tests/

docs:
	cd docs && python -m sphinx . _build/html -W --keep-going

typecheck:
	python -m mypy pymt5/ --ignore-missing-imports --no-strict-optional

package-check:
	python -m build --no-isolation
	python -m twine check dist/*

check: lint typecheck test docs package-check
