.PHONY: test lint format docs typecheck

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check pymt5/ tests/

format:
	ruff format pymt5/ tests/

docs:
	cd docs && python -m sphinx . _build/html -W --keep-going

typecheck:
	python -m mypy pymt5/ --ignore-missing-imports --no-strict-optional
