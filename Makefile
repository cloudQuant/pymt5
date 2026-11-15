.PHONY: test lint format docs typecheck package-check check

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/ -v --tb=short

lint:
	ruff check pymt5/ tests/

format:
	ruff format pymt5/ tests/

docs:
	python docs/build_docs.py
	python docs/check_docs.py

typecheck:
	python -m mypy pymt5/ --ignore-missing-imports

package-check:
	python -c "import shutil; from pathlib import Path; [shutil.rmtree(path, ignore_errors=True) for path in (Path('build'), Path('dist'), Path('mt5_web_client.egg-info'))]"
	python -m build
	python -m twine check --strict dist/*

check: lint typecheck test docs package-check
