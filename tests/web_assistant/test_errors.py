import rate_limit

from web_assistant import rest_assistant
from web_assistant.errors import RateLimitedRequestError

LEGACY = "Error executing request GET https://x/y. HTTP status is 429. Error: slow down"


def make(**overrides) -> RateLimitedRequestError:
    kwargs = {"status": 429, "retry_after": 7.5, "limit_id": "lim"}
    kwargs.update(overrides)
    return RateLimitedRequestError(LEGACY, **kwargs)


def test_dual_base_error_constructs_with_oserror_and_core_bases():
    error = make()

    assert isinstance(error, OSError)
    assert isinstance(error, rate_limit.RateLimitExceededError)


def test_message_args_and_errno_match_a_plain_oserror():
    error = make()
    plain = OSError(LEGACY)

    assert str(error) == LEGACY == str(plain)
    assert error.args == (LEGACY,) == plain.args
    assert error.errno is None
    assert error.strerror is None


def test_attributes_are_set():
    error = make()

    assert (error.status, error.retry_after, error.limit_id) == (429, 7.5, "lim")


def test_limit_id_defaults_to_none_and_retry_after_may_be_none():
    error = RateLimitedRequestError(LEGACY, status=418, retry_after=None)

    assert error.limit_id is None
    assert error.retry_after is None
    assert error.status == 418


def test_caught_by_both_except_clauses():
    for base in (OSError, rate_limit.RateLimitExceededError):
        try:
            raise make()
        except base as caught:
            assert str(caught) == LEGACY


def test_re_exported_from_rest_assistant():
    assert rest_assistant.RateLimitedRequestError is RateLimitedRequestError
