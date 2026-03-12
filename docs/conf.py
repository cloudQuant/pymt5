"""Sphinx configuration for pymt5 documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "pymt5"
copyright = "2026, cloudQuant"
author = "cloudQuant"
release = "0.5.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstyle = True
napoleon_numpy_docstyle = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
