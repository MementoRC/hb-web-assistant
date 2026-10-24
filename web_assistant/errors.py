import rate_limit


class RateLimitedRequestError(OSError, rate_limit.RateLimitExceededError):
    """An HTTP 429/418 surfaced at the REST edge.

    It is an `OSError` (so every legacy `except OSError` handler and message string-match keeps
    working) and also a core `rate_limit.RateLimitExceededError` (so consumers catching the core
    type handle it). `str(e)` and `e.args` are exactly the legacy message and `e.errno` is None;
    `status`, `retry_after` and `limit_id` are plain attributes.
    """

    status: int
    retry_after: float | None
    limit_id: str | None

    def __init__(
        self,
        legacy_message: str,
        *,
        status: int,
        retry_after: float | None,
        limit_id: str | None = None,
    ) -> None:
        # Deliberately bypass RateLimitExceededError.__init__: OSError must get exactly one
        # positional argument so that args == (legacy_message,) and errno stays None.
        OSError.__init__(self, legacy_message)
        self.status = status
        self.retry_after = retry_after
        self.limit_id = limit_id
