from __future__ import annotations

from typing import TYPE_CHECKING

from rate_limit import RateLimiter

if TYPE_CHECKING:
    from collections.abc import Callable

    from web_assistant.throttler.async_throttler_base import AsyncThrottlerBase


class ThrottlerBackedLimiter(RateLimiter):
    """A core `RateLimiter` used only for its backoff and `report_response` machinery.

    It owns no limits. Its pause state is the owning throttler's: `pause`, `pause_all` and
    `paused_until` delegate to the throttler, so the throttler stays the single source of truth
    and the core's burst detection (`is_paused`) sees every pause, explicit or reported.
    """

    def __init__(self, throttler: AsyncThrottlerBase, wall_clock: Callable[[], float]) -> None:
        super().__init__(clock=throttler._clock, wall_clock=wall_clock, unknown="allow")
        self._throttler = throttler

    def pause(self, limit_id: str, seconds: float) -> None:
        self._throttler.pause(limit_id, seconds)

    def pause_all(self, seconds: float) -> None:
        self._throttler.pause_all(seconds)

    def paused_until(self, limit_id: str) -> float:
        deadline = self._throttler.paused_until(limit_id)
        return 0.0 if deadline is None else deadline
