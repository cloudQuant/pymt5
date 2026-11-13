"""Check that the import package uses the correct distribution metadata."""

import importlib
import importlib.metadata
from unittest.mock import patch

import pymt5


def test_version_uses_mt5_web_client_distribution():
    def lookup(name: str) -> str:
        return {"mt5-web-client": "1.2.3", "pymt5": "9.9.9"}[name]

    try:
        with patch("importlib.metadata.version", side_effect=lookup) as version:
            importlib.reload(pymt5)
            assert pymt5.__version__ == "1.2.3"
            version.assert_called_once_with("mt5-web-client")
    finally:
        importlib.reload(pymt5)


def test_version_falls_back_when_distribution_not_installed():
    try:
        with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            importlib.reload(pymt5)
            assert pymt5.__version__ == "1.0.4"
    finally:
        importlib.reload(pymt5)
