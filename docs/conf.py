"""Shared Sphinx configuration for the English and Chinese pymt5 sites."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DOCS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DOCS_DIR.parent))

project = "pymt5"
copyright = "2026, cloudQuant"
author = "cloudQuant"

try:
    from importlib.metadata import version as _pkg_version

    release = _pkg_version("mt5-web-client")
except Exception:
    release = "1.1.0"
version = release

_on_readthedocs = os.environ.get("READTHEDOCS", "").lower() == "true"
_requested_language = os.environ.get(
    "PYMT5_DOCS_LANGUAGE",
    os.environ.get("READTHEDOCS_LANGUAGE", "en"),
).lower()
_is_chinese = _requested_language in {"zh", "zh_cn", "zh-cn"}
language = "zh_CN" if _is_chinese else "en"
_language_directory = "zh-CN" if _is_chinese else "en"
root_doc = "index"
master_doc = "index"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.githubpages",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
]

if os.environ.get("PYMT5_ENABLE_INTERSPHINX") == "1":
    extensions.append("sphinx.ext.intersphinx")

intersphinx_mapping = {}
if "sphinx.ext.intersphinx" in extensions:
    intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    # Chinese sources are built as a separate site rooted at docs/zh_CN.
    "zh_CN",
    # Internal historical plans remain in the source tree, not the public site.
    "iteration_plan*.rst",
]
source_suffix = {".rst": "restructuredtext"}

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = [("author.js", {"defer": "defer"})]
html_title = "pymt5 中文文档" if _is_chinese else "pymt5 Documentation"
html_short_title = "pymt5"
html_baseurl = (
    os.environ.get("READTHEDOCS_CANONICAL_URL", "")
    if _on_readthedocs
    else f"https://cloudquant.github.io/pymt5/{_language_directory}/"
)
html_show_sphinx = False
html_show_sourcelink = True
html_copy_source = False
html_use_index = True
html_theme_options = {
    "analytics_id": "",
    "canonical_url": html_baseurl,
    "collapse_navigation": False,
    "navigation_depth": 3,
    "style_external_links": True,
    "titles_only": True,
}
html_context = {
    "docs_language": language,
    "pymt5_on_readthedocs": _on_readthedocs,
    "pymt5_rtd_switch_version": (
        "latest"
        if os.environ.get("READTHEDOCS_VERSION_TYPE") == "external"
        else os.environ.get("READTHEDOCS_VERSION", "latest")
    ),
    "display_github": True,
    "github_user": "cloudQuant",
    "github_repo": "pymt5",
    "github_version": "master",
    "conf_py_path": "/docs/zh_CN/" if _is_chinese else "/docs/",
}

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "member-order": "bysource",
    "show-inheritance": True,
}
napoleon_google_docstyle = True
napoleon_numpy_docstyle = False
