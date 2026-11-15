# Bilingual Sphinx documentation build.

PYTHON ?= python

.PHONY: help html check clean

help:
	@echo "make html   - build English and Chinese sites, then validate links"
	@echo "make check  - validate the generated bilingual artifact"
	@echo "make clean  - remove generated documentation"

html:
	$(PYTHON) build_docs.py
	$(PYTHON) check_docs.py

check:
	$(PYTHON) check_docs.py

clean:
	$(PYTHON) -c "import shutil; shutil.rmtree('_build', ignore_errors=True)"
