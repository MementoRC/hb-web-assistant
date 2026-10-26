"""Re-export of the legacy-compatible rate-limit data types from `rate_limit.hb_compat`."""

from rate_limit.hb_compat.data_types import (
    DEFAULT_PATH,
    DEFAULT_WEIGHT,
    Limit,
    LinkedLimitWeightPair,
    RateLimit,
    RequestPath,
    RequestWeight,
    Seconds,
    TaskLog,
)

__all__ = [
    "DEFAULT_PATH",
    "DEFAULT_WEIGHT",
    "Limit",
    "LinkedLimitWeightPair",
    "RateLimit",
    "RequestPath",
    "RequestWeight",
    "Seconds",
    "TaskLog",
]
