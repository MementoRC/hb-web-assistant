import rate_limit


def test_rate_limit_core_is_importable_and_in_supported_range() -> None:
    major, minor, *_ = rate_limit.__version__.split(".")
    assert int(major) == 0
    assert int(minor) == 4
    assert rate_limit.__file__ is not None
