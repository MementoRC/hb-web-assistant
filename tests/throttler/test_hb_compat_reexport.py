import importlib
from types import ModuleType

import pytest
from rate_limit import hb_compat as _hb_compat  # noqa: F401  (ensures the subpackage imports)

import web_assistant.throttler as throttler_pkg

MODULES = [
    "data_types",
    "async_throttler_base",
    "async_request_context_base",
    "async_throttler",
]


def _pair(name: str) -> tuple[ModuleType, ModuleType]:
    ours = importlib.import_module(f"web_assistant.throttler.{name}")
    theirs = importlib.import_module(f"rate_limit.hb_compat.{name}")
    return ours, theirs


@pytest.mark.parametrize("module_name", MODULES)
def test_module_all_is_nonempty_and_reexported_by_identity(module_name: str) -> None:
    ours, theirs = _pair(module_name)
    assert ours.__all__
    for name in ours.__all__:
        assert getattr(ours, name) is getattr(theirs, name), f"{module_name}.{name}"


@pytest.mark.parametrize("module_name", MODULES)
def test_module_all_has_no_duplicates(module_name: str) -> None:
    ours, _ = _pair(module_name)
    assert len(ours.__all__) == len(set(ours.__all__))


def test_expected_public_names_are_exported() -> None:
    expected = {
        "data_types": {
            "DEFAULT_PATH",
            "DEFAULT_WEIGHT",
            "Limit",
            "RequestPath",
            "RequestWeight",
            "Seconds",
            "LinkedLimitWeightPair",
            "RateLimit",
            "TaskLog",
        },
        "async_throttler_base": {"AsyncThrottlerBase"},
        "async_request_context_base": {
            "AsyncRequestContextBase",
            "MAX_CAPACITY_REACHED_WARNING_INTERVAL",
        },
        "async_throttler": {"AsyncRequestContext", "AsyncThrottler"},
    }
    for module_name, names in expected.items():
        ours, _ = _pair(module_name)
        assert set(ours.__all__) == names


def test_package_level_exports_are_identical_to_hb_compat() -> None:
    for name in throttler_pkg.__all__:
        assert getattr(throttler_pkg, name) is getattr(_hb_compat, name), name
