from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import formatdate

import aiohttp
import pytest
import pytest_asyncio
import rate_limit
from rate_limit.testing import ManualClock

from tests._helpers.local_http_server import LocalHTTPServer
from web_assistant.connections.data_types import RESTMethod
from web_assistant.connections.rest_connection import RESTConnection
from web_assistant.rest_assistant import RateLimitedRequestError, RESTAssistant
from web_assistant.throttler.async_throttler import AsyncThrottler
from web_assistant.throttler.data_types import RateLimit

PATH = "/url"
LIMIT_ID = "lim"
WALL_NOW = 1_700_000_000.0


class Env:
    def __init__(
        self,
        assistant: RESTAssistant,
        throttler: AsyncThrottler,
        clock: ManualClock,
        url: str,
    ):
        self.assistant = assistant
        self.throttler = throttler
        self.clock = clock
        self.url = url

    async def get(self, limit_id: str = LIMIT_ID, **kwargs):
        return await self.assistant.execute_request(
            url=self.url, throttler_limit_id=limit_id, **kwargs
        )


@pytest.fixture
def mocked_api(local_server: LocalHTTPServer) -> LocalHTTPServer:
    return local_server


@pytest_asyncio.fixture
async def env(mocked_api: LocalHTTPServer) -> AsyncIterator[Env]:
    clock = ManualClock()
    throttler = AsyncThrottler(
        rate_limits=[RateLimit(LIMIT_ID, limit=100, time_interval=60.0)],
        retry_interval=0.005,
        clock=clock,
        wall_clock=lambda: WALL_NOW,
    )
    async with aiohttp.ClientSession() as session:
        assistant = RESTAssistant(RESTConnection(session), throttler=throttler)
        yield Env(assistant, throttler, clock, mocked_api.url(PATH))


def _golden(method: str, url: str, status: int, error: str) -> str:
    return f"Error executing request {method} {url}. HTTP status is {status}. Error: {error}"


async def test_429_golden_string_and_retry_after_seconds_pause(env, mocked_api):
    mocked_api.get(PATH, status=429, body="slow down", headers={"Retry-After": "7"})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    error = excinfo.value
    golden = _golden("GET", env.url, 429, "slow down")
    assert str(error) == golden
    assert error.args == (golden,)
    assert error.errno is None
    assert isinstance(error, OSError)
    assert isinstance(error, rate_limit.RateLimitExceededError)
    assert (error.status, error.retry_after, error.limit_id) == (429, 7.0, LIMIT_ID)
    assert env.throttler.paused_until(LIMIT_ID) == 7.0


async def test_429_with_http_date_retry_after_sets_the_pause(env, mocked_api):
    header = formatdate(WALL_NOW + 30, usegmt=True)
    mocked_api.get(PATH, status=429, body="x", headers={"Retry-After": header})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    assert excinfo.value.retry_after == pytest.approx(30.0, abs=1.0)
    assert env.throttler.paused_until(LIMIT_ID) == pytest.approx(30.0, abs=1.0)


async def test_418_golden_string_and_pause_all_with_default_ban(env, mocked_api):
    mocked_api.get(PATH, status=418, body="banned")

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    error = excinfo.value
    golden = _golden("GET", env.url, 418, "banned")
    assert str(error) == golden
    assert error.args == (golden,)
    assert error.errno is None
    assert (error.status, error.retry_after) == (418, None)
    assert env.throttler.paused_until("some_other_id") == 120.0


async def test_5xx_is_a_plain_oserror_with_the_legacy_text_and_no_pause(env, mocked_api):
    mocked_api.get(PATH, status=503, body="oops")

    with pytest.raises(OSError) as excinfo:
        await env.get()

    error = excinfo.value
    golden = _golden("GET", env.url, 503, "oops")
    assert type(error) is OSError
    assert not isinstance(error, rate_limit.RateLimitExceededError)
    assert str(error) == golden
    assert error.args == (golden,)
    assert error.errno is None
    assert env.throttler.paused_until(LIMIT_ID) is None


async def test_html_error_body_is_still_reported_as_na(env, mocked_api):
    mocked_api.get(PATH, status=429, body="<html>nope</html>")

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    assert str(excinfo.value).endswith("HTTP status is 429. Error: N/A")


async def test_2xx_after_a_429_resets_the_backoff_streak(env, mocked_api):
    mocked_api.get(PATH, status=429, body="x")
    with pytest.raises(RateLimitedRequestError):
        await env.get()
    assert env.throttler.paused_until(LIMIT_ID) == 5.0

    env.clock.advance(5.0)
    mocked_api.get(PATH, status=200, payload={"ok": 1})
    assert await env.get() == {"ok": 1}

    mocked_api.get(PATH, status=429, body="x")
    with pytest.raises(RateLimitedRequestError):
        await env.get()
    assert env.throttler.paused_until(LIMIT_ID) == 10.0  # now 5.0 + a fresh 5s, not a doubled 10s


async def test_return_err_does_not_raise_but_still_reports_the_response(env, mocked_api):
    mocked_api.get(PATH, status=429, body="x", headers={"Retry-After": "4"})

    response = await env.assistant.execute_request_and_get_response(
        url=env.url, throttler_limit_id=LIMIT_ID, return_err=True
    )

    assert response.status == 429
    assert env.throttler.paused_until(LIMIT_ID) == 4.0


async def test_rate_limit_on_an_unregistered_id_still_raises_the_edge_error_and_pauses(
    env, mocked_api
):
    mocked_api.get(PATH, status=429, body="x", headers={"Retry-After": "2"})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get(limit_id="unregistered")

    assert excinfo.value.limit_id == "unregistered"
    assert env.throttler.paused_until("unregistered") == 2.0


async def test_without_a_limit_id_429_and_418_raise_the_edge_error_but_do_not_pause(
    env, mocked_api
):
    mocked_api.get(PATH, status=429, body="x", headers={"Retry-After": "9"})
    mocked_api.get(PATH, status=418, body="x")

    with pytest.raises(RateLimitedRequestError) as first:
        await env.get(limit_id="")
    with pytest.raises(RateLimitedRequestError) as second:
        await env.get(limit_id="")

    assert (first.value.status, first.value.retry_after, first.value.limit_id) == (429, 9.0, None)
    assert (second.value.status, second.value.retry_after) == (418, None)
    assert env.throttler.paused_until(LIMIT_ID) is None
    assert env.throttler.paused_until("") is None


async def test_without_a_limit_id_5xx_is_a_plain_oserror(env, mocked_api):
    mocked_api.get(PATH, status=500, body="boom")

    with pytest.raises(OSError) as excinfo:
        await env.get(limit_id="")

    assert type(excinfo.value) is OSError


class _DuckThrottler:
    """Minimal duck-typed throttler exposing only `execute_task`."""

    @asynccontextmanager
    async def execute_task(self, limit_id: str):
        yield


@pytest_asyncio.fixture
async def duck_assistant() -> AsyncIterator[RESTAssistant]:
    async with aiohttp.ClientSession() as session:
        yield RESTAssistant(RESTConnection(session), throttler=_DuckThrottler())  # type: ignore[arg-type]


async def test_duck_throttler_200_returns_normally(duck_assistant, mocked_api):
    mocked_api.get(PATH, status=200, payload={"ok": 1})

    result = await duck_assistant.execute_request(
        url=mocked_api.url(PATH), throttler_limit_id=LIMIT_ID
    )

    assert result == {"ok": 1}


async def test_duck_throttler_429_raises_rate_limited_with_header_retry_after(
    duck_assistant, mocked_api
):
    mocked_api.get(PATH, status=429, body="slow down", headers={"Retry-After": "7"})
    url = mocked_api.url(PATH)

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await duck_assistant.execute_request(url=url, throttler_limit_id=LIMIT_ID)

    assert str(excinfo.value) == _golden("GET", url, 429, "slow down")
    assert excinfo.value.retry_after == 7


async def test_duck_throttler_500_raises_plain_oserror(duck_assistant, mocked_api):
    mocked_api.get(PATH, status=500, body="boom")

    with pytest.raises(OSError) as excinfo:
        await duck_assistant.execute_request(url=mocked_api.url(PATH), throttler_limit_id=LIMIT_ID)

    assert type(excinfo.value) is OSError


async def test_method_name_in_legacy_message_uses_the_request_method(env, mocked_api):
    mocked_api.post(PATH, status=429, body="x")

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get(method=RESTMethod.POST)

    assert str(excinfo.value).startswith(f"Error executing request POST {env.url}.")
