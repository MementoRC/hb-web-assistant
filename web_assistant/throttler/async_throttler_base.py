import asyncio
import copy
import math
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from decimal import Decimal

from rate_limit import RateLimitPoolConflictError, RateLimitVerdict, coerce_limit, scale_limit

from web_assistant.hb_compat.common import HummingbotLogger, get_logger
from web_assistant.throttler._core_bridge import ThrottlerBackedLimiter
from web_assistant.throttler.async_request_context_base import AsyncRequestContextBase
from web_assistant.throttler.data_types import LinkedLimitWeightPair, RateLimit, TaskLog


def _raw_definition_equal(a: RateLimit, b: RateLimit) -> bool:
    """Compare two unscaled definitions; `linked_limits` compare as an unordered set of (id, weight)."""
    return (
        a.limit == b.limit
        and a.time_interval == b.time_interval
        and a.weight == b.weight
        and {(pair.limit_id, pair.weight) for pair in a.linked_limits}
        == {(pair.limit_id, pair.weight) for pair in b.linked_limits}
    )


class AsyncThrottlerBase(ABC):
    """
    The APIThrottlerBase is an abstract class meant to describe the functions necessary to handle the
    throttling of API requests through the usage of asynchronous context managers.
    """

    _default_config_map: dict[str, object] = {}
    _logger: HummingbotLogger | None = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        """Return the class-level logger, creating it on first access."""
        if cls._logger is None:
            cls._logger = get_logger(__name__)
        return cls._logger

    def __init__(
        self,
        rate_limits: list[RateLimit],
        retry_interval: float = 0.1,
        safety_margin_pct: float | None = 0.05,  # An extra safety margin, in percentage.
        limits_share_percentage: Decimal | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        """
        :param rate_limits: List of RateLimit(s).
        :param retry_interval: Time between every capacity check.
        :param safety_margin_pct: Percentage of limit to be added as a safety margin when calculating capacity to ensure
            calls are within the limit.
        :param limits_share_percentage: Percentage of the limits to be used by this instance (important when multiple
            bots operate with the same account)
        :param clock: Monotonic clock used for pause deadlines (injectable for tests).
        :param wall_clock: Epoch clock used only to parse an HTTP-date `Retry-After`.
        """
        self._clock: Callable[[], float] = clock
        self._wall_clock: Callable[[], float] = wall_clock
        # Core backoff/report machinery; its pause state is this throttler's (see ThrottlerBackedLimiter).
        self._backoff_core = ThrottlerBackedLimiter(self, wall_clock)
        # Pause deadlines (monotonic clock values). Recorded for any id, registered or not.
        self._pause_until: dict[str, float] = {}
        self._pause_all_until: float | None = None

        # If configured, users can define the percentage of rate limits to allocate to the throttler.
        share_percentage = limits_share_percentage or Decimal("100")
        self.limits_pct: Decimal = share_percentage / 100

        self.set_rate_limits(rate_limits)

        # Unknown limit ids already warned about (warn once per id per throttler instance).
        self._warned_unknown_ids: set[str] = set()

        # List of TaskLog used to determine the API requests within a set time window.
        self._task_logs: list[TaskLog] = []

        # Throttler Parameters
        self._retry_interval: float = retry_interval
        # Resolve None to a default of 0.0 so callers always have a plain float.
        self._safety_margin_pct: float = safety_margin_pct if safety_margin_pct is not None else 0.0

        # Shared asyncio.Lock instance to prevent multiple async ContextManager from accessing the _task_logs variable
        self._lock = asyncio.Lock()

    def set_rate_limits(self, rate_limits: list[RateLimit]) -> None:
        """Replace the active rate-limit set and rebuild the internal ID-to-limit index.

        The supplied limits are deep-copied and scaled by the configured `limits_pct`.
        """
        # Rate Limit Definitions
        scaled_limits: list[RateLimit] = copy.deepcopy(rate_limits)

        for rate_limit in scaled_limits:
            rate_limit.limit = scale_limit(rate_limit.limit, self.limits_pct)

        self._scaled_rate_limits = scaled_limits

        # Unscaled deep copies of the definitions, used for conflict detection in add_rate_limits.
        self._raw_limits: dict[str, RateLimit] = {
            limit.limit_id: copy.deepcopy(limit) for limit in rate_limits
        }

        # Dictionary of path_url to RateLimit
        self._id_to_limit_map: dict[str, RateLimit] = {
            limit.limit_id: limit for limit in scaled_limits
        }

    @property
    def _rate_limits(self) -> list[RateLimit]:
        """The live list of scaled `RateLimit` objects."""
        return self._scaled_rate_limits

    @_rate_limits.setter
    def _rate_limits(self, rate_limits: list[RateLimit]) -> None:
        """Direct assignment replaces the whole set via `set_rate_limits`, keeping the index in sync."""
        self.set_rate_limits(rate_limits)

    def add_rate_limits(self, rate_limits: list[RateLimit]) -> None:
        """
        Dynamically add new rate limits to the throttler.
        Useful when adding trading pairs at runtime that require pair-specific rate limits.

        An identical re-add (same raw definition) is a no-op.

        :param rate_limits: List of RateLimit(s) to add.
        :raises rate_limit.RateLimitPoolConflictError: if an id is already registered (or repeated in
            the batch) with a different raw definition. The whole batch is validated first, so
            nothing is inserted when it raises.
        """
        self._check_no_conflicts(rate_limits)
        for rate_limit in rate_limits:
            if rate_limit.limit_id in self._id_to_limit_map:
                # Identical re-add (conflicts were rejected above): no-op
                continue
            self._raw_limits[rate_limit.limit_id] = copy.deepcopy(rate_limit)
            # Apply the limits percentage
            new_limit = copy.deepcopy(rate_limit)
            new_limit.limit = scale_limit(new_limit.limit, self.limits_pct)
            self._rate_limits.append(new_limit)
            self._id_to_limit_map[new_limit.limit_id] = new_limit

    def _check_no_conflicts(self, rate_limits: list[RateLimit]) -> None:
        """Raise `RateLimitPoolConflictError` if any limit differs from its registered or earlier-in-batch raw definition."""
        seen: dict[str, RateLimit] = {}
        for requested in rate_limits:
            known = seen.get(requested.limit_id) or self._raw_limits.get(requested.limit_id)
            if known is None:
                seen[requested.limit_id] = requested
            elif not _raw_definition_equal(known, requested):
                raise RateLimitPoolConflictError(
                    requested.limit_id, coerce_limit(known), coerce_limit(requested)
                )

    def has_limit(self, limit_id: str) -> bool:
        """Return True if `limit_id` is a registered limit."""
        return limit_id in self._id_to_limit_map

    def get_limit(self, limit_id: str) -> RateLimit | None:
        """Return the live stored `RateLimit` (its `.limit` already scaled by `limits_pct`), or None.

        The returned object is not a copy; callers must not mutate it.
        """
        return self._id_to_limit_map.get(limit_id)

    def get_related_limits(
        self, limit_id: str
    ) -> tuple[RateLimit | None, list[tuple[RateLimit, int]]]:
        """Resolve a limit ID to its primary `RateLimit` and any linked limits with weights.

        Returns a 2-tuple of (primary_limit, [(linked_limit, weight), ...]).  If the ID
        is not registered, primary_limit is `None` and the linked list is empty.
        """
        rate_limit: RateLimit | None = self._id_to_limit_map.get(limit_id, None)
        linked_limits: list[LinkedLimitWeightPair] = (
            [] if rate_limit is None else rate_limit.linked_limits
        )

        related_limits = [
            (self._id_to_limit_map[limit_weight_pair.limit_id], limit_weight_pair.weight)
            for limit_weight_pair in linked_limits
            if limit_weight_pair.limit_id in self._id_to_limit_map
        ]

        return rate_limit, related_limits

    def _deadline_after(self, seconds: float) -> float:
        if not math.isfinite(seconds):
            raise ValueError(f"pause seconds must be finite, got {seconds!r}")
        return self._clock() + max(0.0, seconds)

    def pause(self, limit_id: str, seconds: float) -> None:
        """Block acquirers of `limit_id`, and of any id that links to it, for `seconds`.

        A pause never shortens an existing one. `limit_id` need not be registered yet.
        """
        until = self._deadline_after(seconds)
        self._pause_until[limit_id] = max(self._pause_until.get(limit_id, until), until)

    def pause_all(self, seconds: float) -> None:
        """Block every acquirer, including unregistered ids, for `seconds` (never shortens)."""
        until = self._deadline_after(seconds)
        self._pause_all_until = (
            until if self._pause_all_until is None else max(self._pause_all_until, until)
        )

    def paused_until(self, limit_id: str) -> float | None:
        """Monotonic deadline of the latest pause affecting `limit_id`, or None if never paused.

        Considers `limit_id`, the ids it links to, and `pause_all`. A past deadline is returned as is.
        """
        primary = self._id_to_limit_map.get(limit_id)
        ids = {limit_id}
        if primary is not None:
            ids.update(pair.limit_id for pair in primary.linked_limits)
        deadlines = [self._pause_until[i] for i in ids if i in self._pause_until]
        if self._pause_all_until is not None:
            deadlines.append(self._pause_all_until)
        return max(deadlines, default=None)

    def report_response(
        self, limit_id: str, status: int, headers: Mapping[str, str] | None = None
    ) -> RateLimitVerdict:
        """Feed an HTTP response outcome to the core backoff policy; never raises for HTTP statuses.

        2xx resets the streak of `limit_id`. 429 pauses `limit_id` for the Retry-After value or the
        backoff delay (doubling to a cap, per `limit_id`). 418 calls `pause_all`. Any other status
        changes nothing.
        """
        return self._backoff_core.report_response(limit_id, status, headers)

    def _pause_remaining(self, limit_id: str) -> float:
        deadline = self.paused_until(limit_id)
        return 0.0 if deadline is None else max(0.0, deadline - self._clock())

    def _validate_weights(self, limit_id: str, weights: Mapping[str, int]) -> None:
        """Raise ValueError unless every key is the primary or a registered linked id and every
        value is an int >= 1 (bool rejected)."""
        primary = self._id_to_limit_map.get(limit_id)
        allowed: set[str] = set()
        if primary is not None:
            allowed.add(limit_id)
            allowed.update(
                pair.limit_id
                for pair in primary.linked_limits
                if pair.limit_id in self._id_to_limit_map
            )
        for key, weight in weights.items():
            if key not in allowed:
                raise ValueError(
                    f"weights key '{key}' is neither '{limit_id}' nor a registered linked limit of it"
                )
            if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
                raise ValueError(f"weight for '{key}' must be an int >= 1, got {weight!r}")

    @abstractmethod
    def execute_task(
        self, limit_id: str, *, weights: Mapping[str, int] | None = None
    ) -> AsyncRequestContextBase:
        """Return an async context manager that gates entry on available capacity for `limit_id`.

        Use as `async with throttler.execute_task(limit_id): ...`.  Implementations must
        block (via `acquire()`) until the rate limit has room for the new task.

        :param weights: Optional per-call weight overrides keyed by the primary id and/or its
            registered linked ids; ids not present keep their declared weight.
        """
        raise NotImplementedError
