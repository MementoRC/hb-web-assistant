import asyncio
import copy
import logging
import math
import sys
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

import pytest

from web_assistant.throttler.async_throttler import AsyncRequestContext, AsyncThrottler
from web_assistant.throttler.async_throttler_base import _raw_definition_equal
from web_assistant.throttler.data_types import LinkedLimitWeightPair, RateLimit, TaskLog

METRICS_LOG_LEVEL = 21

TEST_PATH_URL = "/hummingbot"
TEST_POOL_ID = "TEST"
TEST_WEIGHTED_POOL_ID = "TEST_WEIGHTED"
TEST_WEIGHTED_TASK_1_ID = "/weighted_task_1"
TEST_WEIGHTED_TASK_2_ID = "/weighted_task_2"

logging.basicConfig(level=METRICS_LOG_LEVEL)


class AsyncThrottlerUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ev_loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()

        cls.rate_limits: list[RateLimit] = [
            RateLimit(limit_id=TEST_POOL_ID, limit=1, time_interval=5.0),
            RateLimit(
                limit_id=TEST_PATH_URL,
                limit=1,
                time_interval=5.0,
                linked_limits=[LinkedLimitWeightPair(TEST_POOL_ID)],
            ),
            RateLimit(limit_id=TEST_WEIGHTED_POOL_ID, limit=10, time_interval=5.0),
            RateLimit(
                limit_id=TEST_WEIGHTED_TASK_1_ID,
                limit=1000,
                time_interval=5.0,
                linked_limits=[LinkedLimitWeightPair(TEST_WEIGHTED_POOL_ID, 5)],
            ),
            RateLimit(
                limit_id=TEST_WEIGHTED_TASK_2_ID,
                limit=1000,
                time_interval=5.0,
                linked_limits=[LinkedLimitWeightPair(TEST_WEIGHTED_POOL_ID, 1)],
            ),
        ]

    def setUp(self) -> None:
        super().setUp()
        self.throttler = AsyncThrottler(rate_limits=self.rate_limits)
        self._req_counters: dict[str, int] = {limit.limit_id: 0 for limit in self.rate_limits}

    async def execute_requests(self, no_request: int, limit_id: str, throttler: AsyncThrottler):
        for _ in range(no_request):
            async with throttler.execute_task(limit_id=limit_id):
                self._req_counters[limit_id] += 1

    def test_init_without_rate_limits_share_pct(self):
        self.assertEqual(0.1, self.throttler._retry_interval)
        self.assertEqual(5, len(self.throttler._rate_limits))
        self.assertEqual(1, self.throttler._id_to_limit_map[TEST_POOL_ID].limit)
        self.assertEqual(1, self.throttler._id_to_limit_map[TEST_PATH_URL].limit)

    def test_init_with_rate_limits_share_pct(self):
        rate_share_pct: Decimal = Decimal("55")
        self.throttler = AsyncThrottler(
            rate_limits=self.rate_limits, limits_share_percentage=rate_share_pct
        )

        rate_limits = self.rate_limits.copy()
        rate_limits.append(RateLimit(limit_id="ANOTHER_TEST", limit=10, time_interval=5))
        expected_limit = math.floor(Decimal("10") * rate_share_pct / Decimal("100"))

        throttler = AsyncThrottler(rate_limits=rate_limits, limits_share_percentage=rate_share_pct)
        self.assertEqual(0.1, throttler._retry_interval)
        self.assertEqual(6, len(throttler._rate_limits))
        self.assertEqual(Decimal("1"), throttler._id_to_limit_map[TEST_POOL_ID].limit)
        self.assertEqual(Decimal("1"), throttler._id_to_limit_map[TEST_PATH_URL].limit)
        self.assertEqual(expected_limit, throttler._id_to_limit_map["ANOTHER_TEST"].limit)

    def test_get_related_limits(self):
        self.assertEqual(5, len(self.throttler._rate_limits))

        rate_limit, related_limits = self.throttler.get_related_limits(TEST_POOL_ID)
        self.assertEqual(TEST_POOL_ID, rate_limit.limit_id)
        self.assertEqual(0, len(related_limits))

        rate_limit, related_limits = self.throttler.get_related_limits(TEST_PATH_URL)
        self.assertEqual(TEST_PATH_URL, rate_limit.limit_id)
        self.assertEqual(1, len(related_limits))

    def test_flush_empty_task_logs(self):
        # Test: No entries in task_logs to flush
        lock = asyncio.Lock()

        rate_limit = self.rate_limits[0]
        self.assertEqual(0, len(self.throttler._task_logs))
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[(rate_limit, rate_limit.weight)],
            lock=lock,
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        context.flush()
        self.assertEqual(0, len(self.throttler._task_logs))

    def test_flush_only_elapsed_tasks_are_flushed(self):
        lock = asyncio.Lock()
        rate_limit = self.rate_limits[0]
        self.throttler._task_logs = [
            TaskLog(timestamp=1.0, rate_limit=rate_limit, weight=rate_limit.weight),
            TaskLog(timestamp=time.time(), rate_limit=rate_limit, weight=rate_limit.weight),
        ]

        self.assertEqual(2, len(self.throttler._task_logs))
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[(rate_limit, rate_limit.weight)],
            lock=lock,
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        context.flush()
        self.assertEqual(1, len(self.throttler._task_logs))

    def test_within_capacity_singular_non_weighted_task_returns_false(self):
        rate_limit, _ = self.throttler.get_related_limits(limit_id=TEST_POOL_ID)
        self.throttler._task_logs.append(
            TaskLog(timestamp=time.time(), rate_limit=rate_limit, weight=rate_limit.weight)
        )

        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[(rate_limit, rate_limit.weight)],
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertFalse(context.within_capacity())

    def test_within_capacity_singular_non_weighted_task_returns_true(self):
        rate_limit, _ = self.throttler.get_related_limits(limit_id=TEST_POOL_ID)
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[(rate_limit, rate_limit.weight)],
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertTrue(context.within_capacity())

    def test_within_capacity_pool_non_weighted_task_returns_false(self):
        rate_limit, related_limits = self.throttler.get_related_limits(limit_id=TEST_PATH_URL)

        for linked_limit, weight in related_limits:
            self.throttler._task_logs.append(
                TaskLog(timestamp=time.time(), rate_limit=linked_limit, weight=weight)
            )

        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=related_limits,
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertFalse(context.within_capacity())

    def test_within_capacity_pool_non_weighted_task_returns_true(self):
        rate_limit, related_limits = self.throttler.get_related_limits(limit_id=TEST_PATH_URL)

        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=related_limits,
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertTrue(context.within_capacity())

    def test_within_capacity_pool_weighted_tasks(self):
        task_1, task_1_related_limits = self.throttler.get_related_limits(
            limit_id=TEST_WEIGHTED_TASK_1_ID
        )

        # Simulate Weighted Task 1 and Task 2 already in task logs, resulting in a used capacity of 6/10
        for linked_limit, weight in task_1_related_limits:
            self.throttler._task_logs.append(
                TaskLog(timestamp=time.time(), rate_limit=linked_limit, weight=weight)
            )
        task_2, task_2_related_limits = self.throttler.get_related_limits(
            limit_id=TEST_WEIGHTED_TASK_2_ID
        )
        for linked_limit, weight in task_2_related_limits:
            self.throttler._task_logs.append(
                TaskLog(timestamp=time.time(), rate_limit=linked_limit, weight=weight)
            )

        # Another Task 1(weight=5) will exceed the capacity(11/10)
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=task_1,
            related_limits=task_1_related_limits,
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertFalse(context.within_capacity())

        # However Task 2(weight=1) will not exceed the capacity(7/10)
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=task_2,
            related_limits=task_2_related_limits,
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertTrue(context.within_capacity())

    def test_within_capacity_returns_true(self):
        lock = asyncio.Lock()
        rate_limit = self.rate_limits[0]
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[(rate_limit, rate_limit.weight)],
            lock=lock,
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.assertTrue(context.within_capacity())

    def test_acquire_appends_to_task_logs(self):
        rate_limit = self.rate_limits[0]
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[],
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        self.ev_loop.run_until_complete(context.acquire())

        # We acquire()'d just one rate_limit, task log should have only one entry
        self.assertEqual(1, len(self.throttler._task_logs))

    def test_acquire_awaits_when_exceed_capacity(self):
        rate_limit = self.rate_limits[0]
        self.throttler._task_logs.append(
            TaskLog(timestamp=time.time(), rate_limit=rate_limit, weight=rate_limit.weight)
        )
        context = AsyncRequestContext(
            task_logs=self.throttler._task_logs,
            rate_limit=rate_limit,
            related_limits=[(rate_limit, rate_limit.weight)],
            lock=asyncio.Lock(),
            safety_margin_pct=self.throttler._safety_margin_pct,
        )
        with self.assertRaises(asyncio.exceptions.TimeoutError):
            self.ev_loop.run_until_complete(asyncio.wait_for(context.acquire(), 1.0))

    def test_concurrent_acquirers_cannot_overbook_a_limit(self):
        # A limit of N must admit exactly N of M > N concurrent acquirers: the capacity
        # check and the task-log append have to happen under the same lock hold. Lock
        # contention (all acquirers queued as waiters) is what exposes the race.
        rate_limit = RateLimit(limit_id="test_limit", limit=3, time_interval=60)
        throttler = AsyncThrottler(rate_limits=[rate_limit])
        acquired = 0

        async def acquirer():
            nonlocal acquired
            async with throttler.execute_task(limit_id=rate_limit.limit_id):
                acquired += 1

        async def run():
            # Hold the shared lock so every acquirer queues as a waiter before any proceeds
            async with throttler._lock:
                tasks = [asyncio.ensure_future(acquirer()) for _ in range(10)]
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        self.ev_loop.run_until_complete(run())

        self.assertEqual(3, acquired)
        self.assertEqual(3, len(throttler._task_logs))

    def test_within_capacity_returns_true_for_throttler_without_configured_limits(self):
        throttler = AsyncThrottler(rate_limits=[])
        context = throttler.execute_task(limit_id="test_limit_id")
        self.assertTrue(context.within_capacity())

    @patch("web_assistant.throttler.async_throttler.AsyncRequestContext._time")
    def test_within_capacity_for_limits_with_milliseconds_interval(self, time_mock):
        per_second_limit = RateLimit(limit_id="generic_per_second", limit=3, time_interval=1)
        per_millisecond_limit = RateLimit(
            limit_id="generic_per_millisecond", limit=2, time_interval=0.2
        )
        specific_limit = RateLimit(
            limit_id="specific_limit",
            limit=sys.maxsize,
            time_interval=1,
            linked_limits=[
                LinkedLimitWeightPair(per_second_limit.limit_id),
                LinkedLimitWeightPair(per_millisecond_limit.limit_id),
            ],
        )

        # Scenario where one specific task was executed at 0 milliseconds
        tasks_log = []
        tasks_log.append(
            TaskLog(timestamp=1640000000.0000, rate_limit=per_millisecond_limit, weight=1)
        )
        tasks_log.append(TaskLog(timestamp=1640000000.0000, rate_limit=per_second_limit, weight=1))

        context = AsyncRequestContext(
            task_logs=tasks_log,
            rate_limit=specific_limit,
            related_limits=[(per_millisecond_limit, 1), (per_second_limit, 1), (specific_limit, 1)],
            lock=asyncio.Lock(),
            safety_margin_pct=0,
        )

        time_mock.return_value = 1640000000.0100
        result = context.within_capacity()
        self.assertTrue(result)

        # Add one more occurrence of the same task but at millisecond 1
        tasks_log.append(
            TaskLog(timestamp=1640000000.1000, rate_limit=per_millisecond_limit, weight=1)
        )
        tasks_log.append(TaskLog(timestamp=1640000000.1000, rate_limit=per_second_limit, weight=1))

        time_mock.return_value = 1640000000.1000
        result = context.within_capacity()
        self.assertFalse(result)

        time_mock.return_value = 1640000000.1900
        result = context.within_capacity()
        self.assertFalse(result)

        time_mock.return_value = 1640000000.2000
        result = context.within_capacity()
        self.assertFalse(result)

        time_mock.return_value = 1640000000.2100
        result = context.within_capacity()
        self.assertTrue(result)


async def test_unknown_limit_id_warns_once_and_runs_unthrottled():
    throttler = AsyncThrottler(rate_limits=[RateLimit("known", limit=1, time_interval=5.0)])

    with patch.object(AsyncThrottler, "logger") as logger_mock:
        for _ in range(3):
            async with throttler.execute_task("nope"):
                pass
        async with throttler.execute_task("other-nope"):
            pass

    assert throttler._task_logs == []
    assert logger_mock.return_value.warning.call_count == 2


def test_has_limit_and_get_limit_return_stored_scaled_limit():
    throttler = AsyncThrottler(
        rate_limits=[RateLimit("known", limit=10, time_interval=5.0)],
        limits_share_percentage=Decimal("55"),
    )

    assert throttler.has_limit("known") is True
    assert throttler.has_limit("nope") is False
    assert throttler.get_limit("nope") is None
    stored = throttler.get_limit("known")
    assert stored is throttler._id_to_limit_map["known"]
    assert stored is not None
    assert stored.limit == 5


def test_rate_limits_assignment_rebuilds_id_to_limit_map():
    throttler = AsyncThrottler(
        rate_limits=[RateLimit("old", limit=10, time_interval=5.0)],
        limits_share_percentage=Decimal("50"),
    )

    throttler._rate_limits = [RateLimit("new", limit=10, time_interval=5.0)]

    assert list(throttler._id_to_limit_map) == ["new"]
    assert [limit.limit_id for limit in throttler._rate_limits] == ["new"]
    assert throttler._id_to_limit_map["new"].limit == 5
    assert throttler._id_to_limit_map["new"] is throttler._rate_limits[0]


def test_direct_rate_limits_assignment_scales_once_and_leaves_source_untouched():
    tier_limits = [
        RateLimit(limit_id="A", limit=100, time_interval=60),
        RateLimit(
            limit_id="B",
            limit=10,
            time_interval=1,
            linked_limits=[LinkedLimitWeightPair("A", 2)],
        ),
    ]
    snapshot = copy.deepcopy(tier_limits)
    throttler = AsyncThrottler(rate_limits=[], limits_share_percentage=Decimal("50"))

    throttler._rate_limits = tier_limits
    throttler._rate_limits = tier_limits

    assert throttler.get_limit("A").limit == 50
    assert throttler.get_limit("B").limit == 5
    assert set(throttler._id_to_limit_map) == {"A", "B"}
    for stored in throttler._rate_limits:
        assert throttler._id_to_limit_map[stored.limit_id] is stored
        assert all(stored is not source for source in tier_limits)

    for source, before in zip(tier_limits, snapshot, strict=True):
        assert source.limit_id == before.limit_id
        assert source.limit == before.limit
        assert source.time_interval == before.time_interval
        assert source.weight == before.weight
        assert source.linked_limits == before.linked_limits


def test_rate_limits_getter_returns_live_list_and_deepcopy_works():
    throttler = AsyncThrottler(rate_limits=[RateLimit("a", limit=3, time_interval=1.0)])

    assert throttler._rate_limits is throttler._rate_limits
    throttler.add_rate_limits([RateLimit("b", limit=2, time_interval=1.0)])
    assert [limit.limit_id for limit in throttler._rate_limits] == ["a", "b"]

    copied = copy.deepcopy(throttler._rate_limits)
    assert [limit.limit_id for limit in copied] == ["a", "b"]
    assert copied[0] is not throttler._rate_limits[0]


def test_raw_limits_hold_unscaled_copies_and_set_rate_limits_replaces_them():
    source = RateLimit("a", limit=10, time_interval=5.0)
    throttler = AsyncThrottler(rate_limits=[source], limits_share_percentage=Decimal("50"))

    assert throttler._raw_limits["a"].limit == 10
    assert throttler._raw_limits["a"] is not source
    assert throttler._id_to_limit_map["a"].limit == 5

    throttler.add_rate_limits([RateLimit("b", limit=8, time_interval=1.0)])
    assert throttler._raw_limits["b"].limit == 8
    assert throttler._id_to_limit_map["b"].limit == 4

    throttler.set_rate_limits([RateLimit("c", limit=6, time_interval=1.0)])
    assert list(throttler._raw_limits) == ["c"]


def test_add_rate_limits_identical_readd_is_noop_and_mismatch_keeps_existing_state_untouched():
    throttler = AsyncThrottler(rate_limits=[RateLimit("a", limit=10, time_interval=5.0)])
    stored = throttler._id_to_limit_map["a"]

    with patch.object(AsyncThrottler, "logger") as logger_mock:
        throttler.add_rate_limits([RateLimit("a", limit=10, time_interval=5.0)])
        assert logger_mock.return_value.warning.call_count == 0
        throttler.add_rate_limits([RateLimit("a", limit=99, time_interval=5.0)])
        assert logger_mock.return_value.warning.call_count == 1

    assert throttler._id_to_limit_map["a"] is stored
    assert stored.limit == 10
    assert throttler._raw_limits["a"].limit == 10
    assert len(throttler._rate_limits) == 1


def test_raw_definition_equal_compares_all_fields():
    def make(**overrides):
        fields = {"limit_id": "x", "limit": 5, "time_interval": 1.0, "weight": 1}
        fields.update(overrides)
        return RateLimit(**fields)

    assert _raw_definition_equal(make(), make())
    assert not _raw_definition_equal(make(), make(limit=6))
    assert not _raw_definition_equal(make(), make(time_interval=2.0))
    assert not _raw_definition_equal(make(), make(weight=2))
    assert not _raw_definition_equal(make(), make(linked_limits=[LinkedLimitWeightPair("p", 1)]))
    assert not _raw_definition_equal(
        make(linked_limits=[LinkedLimitWeightPair("p", 1)]),
        make(linked_limits=[LinkedLimitWeightPair("p", 2)]),
    )


def test_raw_definition_equal_linked_limits_are_order_insensitive():
    first = RateLimit(
        "x",
        limit=5,
        time_interval=1.0,
        linked_limits=[LinkedLimitWeightPair("p", 1), LinkedLimitWeightPair("q", 2)],
    )
    second = RateLimit(
        "x",
        limit=5,
        time_interval=1.0,
        linked_limits=[LinkedLimitWeightPair("q", 2), LinkedLimitWeightPair("p", 1)],
    )

    assert _raw_definition_equal(first, second)


def _weighted_throttler() -> AsyncThrottler:
    return AsyncThrottler(
        rate_limits=[
            RateLimit("pool", limit=10, time_interval=5.0),
            RateLimit("other", limit=10, time_interval=5.0),
            RateLimit(
                "ep",
                limit=100,
                time_interval=5.0,
                weight=2,
                linked_limits=[LinkedLimitWeightPair("pool", 1), LinkedLimitWeightPair("other", 1)],
            ),
        ]
    )


def _logged_weights(throttler: AsyncThrottler) -> dict[str, int]:
    return {log.rate_limit.limit_id: log.weight for log in throttler._task_logs}


async def test_weights_none_keeps_declared_weights():
    throttler = _weighted_throttler()
    async with throttler.execute_task("ep"):
        pass
    assert _logged_weights(throttler) == {"ep": 2, "pool": 1, "other": 1}


async def test_weights_primary_only_override():
    throttler = _weighted_throttler()
    async with throttler.execute_task("ep", weights={"ep": 7}):
        pass
    assert _logged_weights(throttler) == {"ep": 7, "pool": 1, "other": 1}


async def test_weights_linked_only_override():
    throttler = _weighted_throttler()
    async with throttler.execute_task("ep", weights={"pool": 6}):
        pass
    assert _logged_weights(throttler) == {"ep": 2, "pool": 6, "other": 1}


async def test_weights_capacity_check_and_log_agree():
    throttler = _weighted_throttler()
    async with throttler.execute_task("ep", weights={"pool": 6}):
        pass

    # 6 used of 10: a further 5 would not fit, 4 exactly fits.
    assert throttler.execute_task("ep", weights={"pool": 5}).within_capacity() is False
    assert throttler.execute_task("ep", weights={"pool": 4}).within_capacity() is True
    # Without an override the declared weight (1) fits.
    assert throttler.execute_task("ep").within_capacity() is True

    async with throttler.execute_task("ep", weights={"pool": 4}):
        pass
    pool_used = sum(log.weight for log in throttler._task_logs if log.rate_limit.limit_id == "pool")
    assert pool_used == 10
    assert throttler.execute_task("ep").within_capacity() is False


async def test_weights_primary_override_is_used_by_capacity_check():
    throttler = _weighted_throttler()
    assert throttler.execute_task("ep", weights={"ep": 101}).within_capacity() is False
    assert throttler.execute_task("ep", weights={"ep": 100}).within_capacity() is True


@pytest.mark.parametrize("bad_key", ["nope", "unregistered"])
def test_weights_unknown_key_raises_value_error(bad_key):
    throttler = AsyncThrottler(
        rate_limits=[
            RateLimit(
                "ep",
                limit=10,
                time_interval=1.0,
                linked_limits=[LinkedLimitWeightPair("unregistered", 1)],
            ),
            RateLimit("unrelated", limit=10, time_interval=1.0),
        ]
    )
    with pytest.raises(ValueError):
        throttler.execute_task("ep", weights={bad_key: 1})
    with pytest.raises(ValueError):
        throttler.execute_task("ep", weights={"unrelated": 1})


def test_weights_for_unknown_primary_raises_value_error():
    throttler = AsyncThrottler(rate_limits=[])
    with pytest.raises(ValueError):
        throttler.execute_task("nope", weights={"nope": 1})


@pytest.mark.parametrize("bad_weight", [0, -1, True, False, 1.5, "2"])
def test_weights_invalid_values_raise_value_error(bad_weight):
    throttler = _weighted_throttler()
    with pytest.raises(ValueError):
        throttler.execute_task("ep", weights={"pool": bad_weight})


def _scaled_by_id(throttler: AsyncThrottler) -> dict[str, int]:
    return {limit_id: limit.limit for limit_id, limit in throttler._id_to_limit_map.items()}


def test_limits_share_percentage_zero_none_and_hundred_are_identical():
    rate_limits = [
        RateLimit("a", limit=10, time_interval=1.0),
        RateLimit("b", limit=1, time_interval=1.0),
    ]
    full = _scaled_by_id(AsyncThrottler(rate_limits, limits_share_percentage=Decimal("100")))

    assert full == {"a": 10, "b": 1}
    assert _scaled_by_id(AsyncThrottler(rate_limits, limits_share_percentage=None)) == full
    assert _scaled_by_id(AsyncThrottler(rate_limits, limits_share_percentage=Decimal("0"))) == full
    assert _scaled_by_id(AsyncThrottler(rate_limits)) == full


def test_limits_share_percentage_scaling_is_floored_with_minimum_of_one():
    rate_limits = [
        RateLimit("a", limit=10, time_interval=1.0),
        RateLimit("b", limit=1, time_interval=1.0),
        RateLimit("c", limit=7, time_interval=1.0),
    ]
    throttler = AsyncThrottler(rate_limits, limits_share_percentage=Decimal("55"))

    assert _scaled_by_id(throttler) == {"a": 5, "b": 1, "c": 3}


async def test_unknown_limit_id_warn_state_is_per_throttler_instance():
    first = AsyncThrottler(rate_limits=[])
    second = AsyncThrottler(rate_limits=[])

    with patch.object(AsyncThrottler, "logger") as logger_mock:
        async with first.execute_task("nope"):
            pass
        async with second.execute_task("nope"):
            pass

    assert logger_mock.return_value.warning.call_count == 2
