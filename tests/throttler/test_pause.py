import asyncio
import time

import pytest
from rate_limit.testing import ManualClock

from web_assistant.throttler.async_throttler import AsyncThrottler
from web_assistant.throttler.data_types import LinkedLimitWeightPair, RateLimit

POLL = 0.005


def build(clock: ManualClock, extra: list[RateLimit] | None = None) -> AsyncThrottler:
    limits = [
        RateLimit("pool", limit=100, time_interval=60.0),
        RateLimit(
            "endpoint",
            limit=100,
            time_interval=60.0,
            linked_limits=[LinkedLimitWeightPair("pool")],
        ),
        RateLimit("solo", limit=100, time_interval=60.0),
        *(extra or []),
    ]
    return AsyncThrottler(rate_limits=limits, retry_interval=POLL, clock=clock)


async def passes(throttler: AsyncThrottler, limit_id: str, timeout: float = 0.1) -> bool:
    """True if an acquire for `limit_id` completes within `timeout` real seconds."""
    try:
        await asyncio.wait_for(throttler.execute_task(limit_id).acquire(), timeout)
    except TimeoutError:
        return False
    return True


async def test_paused_until_is_none_when_never_paused():
    assert build(ManualClock()).paused_until("solo") is None


async def test_pause_blocks_direct_acquirer_until_deadline():
    clock = ManualClock(100.0)
    throttler = build(clock)

    throttler.pause("solo", 5.0)

    assert throttler.paused_until("solo") == 105.0
    assert not await passes(throttler, "solo")
    clock.advance(5.0)
    assert await passes(throttler, "solo")


async def test_pause_on_pool_blocks_linked_acquirer_but_not_unrelated_ids():
    clock = ManualClock()
    throttler = build(clock)

    throttler.pause("pool", 5.0)

    assert not await passes(throttler, "endpoint")
    assert await passes(throttler, "solo")
    assert throttler.paused_until("endpoint") == 5.0
    clock.advance(5.0)
    assert await passes(throttler, "endpoint")


async def test_pause_on_linked_endpoint_does_not_block_the_shared_pool():
    throttler = build(ManualClock())

    throttler.pause("endpoint", 5.0)

    assert await passes(throttler, "pool")
    assert not await passes(throttler, "endpoint")


async def test_pause_only_extends():
    throttler = build(ManualClock(0.0))

    throttler.pause("solo", 10.0)
    throttler.pause("solo", 2.0)
    assert throttler.paused_until("solo") == 10.0

    throttler.pause("solo", 20.0)
    assert throttler.paused_until("solo") == 20.0


async def test_pause_on_unknown_id_is_recorded_and_applies_after_add():
    clock = ManualClock()
    throttler = build(clock)

    throttler.pause("late", 5.0)
    assert throttler.paused_until("late") == 5.0

    throttler.add_rate_limits([RateLimit("late", limit=100, time_interval=60.0)])

    assert not await passes(throttler, "late")
    clock.advance(5.0)
    assert await passes(throttler, "late")


async def test_pause_all_blocks_every_acquirer_including_unknown_ids():
    clock = ManualClock()
    throttler = build(clock)

    throttler.pause_all(5.0)

    for limit_id in ("pool", "endpoint", "solo", "never_registered"):
        assert not await passes(throttler, limit_id), limit_id
    clock.advance(5.0)
    for limit_id in ("pool", "endpoint", "solo", "never_registered"):
        assert await passes(throttler, limit_id), limit_id


async def test_pause_all_only_extends_and_applies_to_ids_added_later():
    clock = ManualClock()
    throttler = build(clock)

    throttler.pause_all(10.0)
    throttler.pause_all(1.0)
    throttler.add_rate_limits([RateLimit("later", limit=1, time_interval=1.0)])

    assert throttler.paused_until("later") == 10.0
    assert not await passes(throttler, "later")


async def test_unknown_id_honours_per_id_pause_and_other_unknown_ids_do_not():
    clock = ManualClock()
    throttler = build(clock)

    throttler.pause("ghost", 5.0)

    assert not await passes(throttler, "ghost")
    assert await passes(throttler, "other_ghost")
    clock.advance(5.0)
    assert await passes(throttler, "ghost")
    assert throttler._task_logs == []


async def test_paused_acquirer_does_not_hold_the_lock_while_waiting():
    clock = ManualClock()
    throttler = build(clock)
    throttler.pause("solo", 5.0)

    waiter = asyncio.ensure_future(throttler.execute_task("solo").acquire())
    try:
        for _ in range(5):
            await asyncio.sleep(POLL * 3)
            assert not throttler._lock.locked()
        assert not waiter.done()
        assert await passes(throttler, "pool")  # an unpaused limit is not stuck behind the sleeper
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def test_concurrent_acquirers_behind_a_pause_cannot_overbook():
    clock = ManualClock()
    throttler = AsyncThrottler(
        rate_limits=[RateLimit("capped", limit=3, time_interval=60.0)],
        retry_interval=POLL,
        clock=clock,
    )
    throttler.pause("capped", 5.0)
    acquired = 0

    async def acquirer():
        nonlocal acquired
        async with throttler.execute_task("capped"):
            acquired += 1

    async with throttler._lock:  # queue every acquirer as a lock waiter first
        tasks = [asyncio.ensure_future(acquirer()) for _ in range(10)]
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    assert acquired == 0  # still paused
    clock.advance(5.0)
    await asyncio.sleep(0.3)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    assert acquired == 3
    assert len(throttler._task_logs) == 3


async def test_pause_rejects_non_finite_seconds():
    throttler = build(ManualClock())

    with pytest.raises(ValueError):
        throttler.pause("solo", float("inf"))
    with pytest.raises(ValueError):
        throttler.pause_all(float("nan"))


async def test_default_clock_is_monotonic():
    throttler = AsyncThrottler(rate_limits=[RateLimit("solo", limit=1, time_interval=1.0)])
    before = time.monotonic()

    throttler.pause("solo", 30.0)

    deadline = throttler.paused_until("solo")
    assert deadline is not None
    assert before + 30.0 <= deadline <= time.monotonic() + 30.0
