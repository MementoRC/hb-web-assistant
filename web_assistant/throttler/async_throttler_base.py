import asyncio
import copy
import logging
import math
from abc import ABC, abstractmethod
from decimal import Decimal

from web_assistant.throttler.async_request_context_base import AsyncRequestContextBase
from web_assistant.throttler.data_types import LinkedLimitWeightPair, RateLimit, TaskLog


def _scaled_limit(raw_limit: int, pct: Decimal) -> int:
    """Return max(1, floor(raw_limit * pct)) as a plain int."""
    floored = math.floor(Decimal(str(raw_limit)) * pct)
    return max(1, int(floored))


class AsyncThrottlerBase(ABC):
    """
    The APIThrottlerBase is an abstract class meant to describe the functions necessary to handle the
    throttling of API requests through the usage of asynchronous context managers.
    """

    _default_config_map: dict[str, object] = {}
    _logger: logging.Logger | None = None

    @classmethod
    def logger(cls) -> logging.Logger:
        """Return the class-level logger, creating it on first access."""
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(
        self,
        rate_limits: list[RateLimit],
        retry_interval: float = 0.1,
        safety_margin_pct: float | None = 0.05,  # An extra safety margin, in percentage.
        limits_share_percentage: Decimal | None = None,
    ):
        """
        :param rate_limits: List of RateLimit(s).
        :param retry_interval: Time between every capacity check.
        :param safety_margin_pct: Percentage of limit to be added as a safety margin when calculating capacity to ensure
            calls are within the limit.
        :param limits_share_percentage: Percentage of the limits to be used by this instance (important when multiple
            bots operate with the same account)
        """
        # If configured, users can define the percentage of rate limits to allocate to the throttler.
        share_percentage = limits_share_percentage or Decimal("100")
        self.limits_pct: Decimal = share_percentage / 100

        self.set_rate_limits(rate_limits)

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
        self._rate_limits: list[RateLimit] = copy.deepcopy(rate_limits)

        for rate_limit in self._rate_limits:
            rate_limit.limit = _scaled_limit(rate_limit.limit, self.limits_pct)

        # Dictionary of path_url to RateLimit
        self._id_to_limit_map: dict[str, RateLimit] = {
            limit.limit_id: limit for limit in self._rate_limits
        }

    def add_rate_limits(self, rate_limits: list[RateLimit]) -> None:
        """
        Dynamically add new rate limits to the throttler.
        Useful when adding trading pairs at runtime that require pair-specific rate limits.

        :param rate_limits: List of RateLimit(s) to add.
        """
        for rate_limit in rate_limits:
            # Skip if already exists
            if rate_limit.limit_id in self._id_to_limit_map:
                continue
            # Apply the limits percentage
            new_limit = copy.deepcopy(rate_limit)
            new_limit.limit = _scaled_limit(new_limit.limit, self.limits_pct)
            self._rate_limits.append(new_limit)
            self._id_to_limit_map[new_limit.limit_id] = new_limit

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

    @abstractmethod
    def execute_task(self, limit_id: str) -> AsyncRequestContextBase:
        """Return an async context manager that gates entry on available capacity for `limit_id`.

        Use as `async with throttler.execute_task(limit_id): ...`.  Implementations must
        block (via `acquire()`) until the rate limit has room for the new task.
        """
        raise NotImplementedError
