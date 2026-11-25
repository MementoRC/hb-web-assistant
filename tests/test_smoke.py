"""Smoke test: verify the package can be imported and reports the expected version."""

import re

import web_assistant
from web_assistant import __version__


def test_import_succeeds() -> None:
    """web_assistant must be importable without errors."""
    assert web_assistant is not None


def test_version_is_correct() -> None:
    """Package version must be a semantic version string."""
    assert re.match(r"^\d+\.\d+\.\d+", __version__)


def test_version_attribute_on_module() -> None:
    """__version__ must be accessible directly on the top-level module."""
    assert hasattr(web_assistant, "__version__")
    assert web_assistant.__version__ == __version__
