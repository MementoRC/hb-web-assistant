"""Smoke test: verify the package can be imported and reports the expected version."""

import web_assistant
from web_assistant import __version__


def test_import_succeeds() -> None:
    """web_assistant must be importable without errors."""
    assert web_assistant is not None


def test_version_is_correct() -> None:
    """Package version must be 0.1.0 for PR 1 scaffold."""
    assert __version__ == "0.1.0"


def test_version_attribute_on_module() -> None:
    """__version__ must be accessible directly on the top-level module."""
    assert hasattr(web_assistant, "__version__")
    assert web_assistant.__version__ == "0.1.0"
