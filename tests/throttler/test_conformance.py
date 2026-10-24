import asyncio
import types

import pytest
from rate_limit.testing import ManualClock, assert_conforms_backoff, assert_conforms_pausable

from web_assistant.throttler import async_request_context_base
from web_assistant.throttler.async_throttler import AsyncThrottler
from web_assistant.throttler.data_types import RateLimit

START = 1000.0
LIMIT = 3
INTERVAL = 10.0


class _ThrottlerAsPausable:
    """Adapts one limit id of an AsyncThrottler to the core's pausable limiter protocol."""

    def __init__(self, throttler: AsyncThrottler, limit_id: str) -> None:
        self._throttler = throttler
        self._limit_id = limit_id

    async def acquire(self) -> None:
        async with self._throttler.execute_task(limit_id=self._limit_id):
            pass

    def pause(self, seconds: float) -> None:
        self._throttler.pause(self._limit_id, seconds)


@pytest.fixture
def route_waits_through(monkeypatch):
    """Send the context's asyncio.sleep to a ManualClock and make time.time() read it."""

    def install(clock: ManualClock, sleep=None) -> None:
        monkeypatch.setattr(
            async_request_context_base,
            "asyncio",
            types.SimpleNamespace(sleep=sleep or clock.sleep),
        )
        monkeypatch.setattr("time.time", clock)

    return install


def test_pausable_conformance_kit_passes(route_waits_through):
    def make(clock, sleep):
        route_waits_through(clock, sleep)
        # A huge retry_interval makes each pause wait a single sleep of exactly the remainder.
        throttler = AsyncThrottler(
            rate_limits=[RateLimit("x", limit=1000, time_interval=60.0)],
            retry_interval=10_000.0,
            clock=clock,
        )
        return _ThrottlerAsPausable(throttler, "x")

    assert_conforms_pausable(make)


def test_backoff_conformance_kit_passes_for_the_throttlers_policy():
    assert_conforms_backoff(lambda: AsyncThrottler(rate_limits=[])._backoff_core.backoff("x"))


def _fresh(clock: ManualClock) -> AsyncThrottler:
    return AsyncThrottler(
        rate_limits=[RateLimit("x", limit=LIMIT, time_interval=INTERVAL)],
        retry_interval=0.1,
        safety_margin_pct=0.0,
        clock=clock,
    )


async def _fill(throttler: AsyncThrottler) -> None:
    for _ in range(LIMIT):
        async with throttler.execute_task("x"):
            pass


def test_inclusive_window_boundary_mirrors_the_conformance_kit(route_waits_through):
    # Same scenario as rate_limit.testing's boundary check, run against the local window:
    # an entry at age == window still counts; one older than the window does not.
    async def at_exact_window_age() -> list[float]:
        clock = ManualClock(START)
        route_waits_through(clock)
        throttler = _fresh(clock)
        await _fill(throttler)
        clock.advance(INTERVAL)
        async with throttler.execute_task("x"):
            pass
        return clock.sleeps

    async def just_past_the_window() -> list[float]:
        clock = ManualClock(START)
        route_waits_through(clock)
        throttler = _fresh(clock)
        await _fill(throttler)
        clock.advance(INTERVAL * 1.001)
        async with throttler.execute_task("x"):
            pass
        return clock.sleeps

    assert len(asyncio.run(at_exact_window_age())) > 0
    assert asyncio.run(just_past_the_window()) == []


@pytest.mark.skip(reason="N/A per spec §4.6: poll-based acquisition does not guarantee FIFO")
def test_fifo_conformance_checks():
    raise AssertionError("never runs")
