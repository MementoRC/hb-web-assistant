import pytest
from rate_limit import RateLimitPoolConflictError

from web_assistant.throttler.async_throttler import AsyncThrottler
from web_assistant.throttler.data_types import LinkedLimitWeightPair, RateLimit


def make(limit_id: str = "a", **overrides) -> RateLimit:
    fields = {"limit": 10, "time_interval": 5.0}
    fields.update(overrides)
    return RateLimit(limit_id, **fields)


def test_mismatched_readd_raises_pool_conflict_error():
    throttler = AsyncThrottler(rate_limits=[make("a")])

    with pytest.raises(RateLimitPoolConflictError) as excinfo:
        throttler.add_rate_limits([make("a", limit=99)])

    assert excinfo.value.pool == "a"
    assert excinfo.value.existing.limit == 10
    assert excinfo.value.requested.limit == 99


@pytest.mark.parametrize(
    "override",
    [
        {"limit": 11},
        {"time_interval": 6.0},
        {"weight": 2},
        {"linked_limits": [LinkedLimitWeightPair("p", 1)]},
    ],
)
def test_every_raw_field_difference_conflicts(override):
    throttler = AsyncThrottler(rate_limits=[make("a")])

    with pytest.raises(RateLimitPoolConflictError):
        throttler.add_rate_limits([make("a", **override)])


def test_conflict_is_detected_against_raw_not_scaled_definition():
    from decimal import Decimal

    throttler = AsyncThrottler(rate_limits=[make("a")], limits_share_percentage=Decimal("50"))

    throttler.add_rate_limits([make("a")])  # identical raw: no-op even though stored is scaled
    with pytest.raises(RateLimitPoolConflictError):
        throttler.add_rate_limits([make("a", limit=5)])  # equals the SCALED value, still a conflict


def test_conflict_inserts_nothing_from_the_batch():
    throttler = AsyncThrottler(rate_limits=[make("a")])

    with pytest.raises(RateLimitPoolConflictError):
        throttler.add_rate_limits([make("new"), make("a", limit=99), make("other")])

    assert not throttler.has_limit("new")
    assert not throttler.has_limit("other")
    assert [limit.limit_id for limit in throttler._rate_limits] == ["a"]
    assert list(throttler._raw_limits) == ["a"]


def test_conflicting_duplicates_inside_the_batch_raise_and_insert_nothing():
    throttler = AsyncThrottler(rate_limits=[])

    with pytest.raises(RateLimitPoolConflictError):
        throttler.add_rate_limits([make("x"), make("y"), make("x", limit=3)])

    assert throttler._rate_limits == []
    assert throttler._raw_limits == {}


def test_identical_duplicates_inside_the_batch_are_collapsed():
    throttler = AsyncThrottler(rate_limits=[])

    throttler.add_rate_limits([make("x"), make("x")])

    assert [limit.limit_id for limit in throttler._rate_limits] == ["x"]


def test_linked_limit_order_does_not_cause_a_conflict():
    linked = [LinkedLimitWeightPair("p", 1), LinkedLimitWeightPair("q", 2)]
    throttler = AsyncThrottler(rate_limits=[make("a", linked_limits=linked)])

    throttler.add_rate_limits([make("a", linked_limits=list(reversed(linked)))])

    assert len(throttler._rate_limits) == 1


def test_set_rate_limits_and_setter_never_raise():
    throttler = AsyncThrottler(rate_limits=[make("a")])

    throttler.set_rate_limits([make("a", limit=99)])
    assert throttler.get_limit("a").limit == 99

    throttler._rate_limits = [make("a", limit=7)]
    assert throttler.get_limit("a").limit == 7
