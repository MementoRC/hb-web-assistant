"""Re-export of `AsyncRequestContextBase` from `rate_limit.hb_compat`."""

from rate_limit.hb_compat.async_request_context_base import (
    MAX_CAPACITY_REACHED_WARNING_INTERVAL,
    AsyncRequestContextBase,
)

__all__ = ["MAX_CAPACITY_REACHED_WARNING_INTERVAL", "AsyncRequestContextBase"]
