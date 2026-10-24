from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import formatdate

import aiohttp
import pytest
import pytest_asyncio
import rate_limit
from aioresponses import aioresponses
from rate_limit.testing import ManualClock

from web_assistant.connections.data_types import RESTMethod
from web_assistant.connections.rest_connection import RESTConnection
from web_assistant.rest_assistant import RateLimitedRequestError, RESTAssistant
from web_assistant.throttler.async_throttler import AsyncThrottler
from web_assistant.throttler.data_types import RateLimit

URL = "https://www.test.com/url"
LIMIT_ID = "lim"
WALL_NOW = 1_700_000_000.0


class Env:
    def __init__(self, assistant: RESTAssistant, throttler: AsyncThrottler, clock: ManualClock):
        self.assistant = assistant
        self.throttler = throttler
        self.clock = clock

    async def get(self, limit_id: str = LIMIT_ID, **kwargs):
        return await self.assistant.execute_request(url=URL, throttler_limit_id=limit_id, **kwargs)


@pytest.fixture
def mocked_api():
    with aioresponses() as mocked:
        yield mocked


@pytest_asyncio.fixture
async def env() -> AsyncIterator[Env]:
    clock = ManualClock()
    throttler = AsyncThrottler(
        rate_limits=[RateLimit(LIMIT_ID, limit=100, time_interval=60.0)],
        retry_interval=0.005,
        clock=clock,
        wall_clock=lambda: WALL_NOW,
    )
    async with aiohttp.ClientSession() as session:
        assistant = RESTAssistant(RESTConnection(session), throttler=throttler)
        yield Env(assistant, throttler, clock)


async def test_429_golden_string_and_retry_after_seconds_pause(env, mocked_api):
    mocked_api.get(URL, status=429, body="slow down", headers={"Retry-After": "7"})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    error = excinfo.value
    golden = (
        "Error executing request GET https://www.test.com/url. HTTP status is 429. Error: slow down"
    )
    assert str(error) == golden
    assert error.args == (golden,)
    assert error.errno is None
    assert isinstance(error, OSError)
    assert isinstance(error, rate_limit.RateLimitExceededError)
    assert (error.status, error.retry_after, error.limit_id) == (429, 7.0, LIMIT_ID)
    assert env.throttler.paused_until(LIMIT_ID) == 7.0


async def test_429_with_http_date_retry_after_sets_the_pause(env, mocked_api):
    header = formatdate(WALL_NOW + 30, usegmt=True)
    mocked_api.get(URL, status=429, body="x", headers={"Retry-After": header})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    assert excinfo.value.retry_after == pytest.approx(30.0, abs=1.0)
    assert env.throttler.paused_until(LIMIT_ID) == pytest.approx(30.0, abs=1.0)


async def test_418_golden_string_and_pause_all_with_default_ban(env, mocked_api):
    mocked_api.get(URL, status=418, body="banned")

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    error = excinfo.value
    golden = (
        "Error executing request GET https://www.test.com/url. HTTP status is 418. Error: banned"
    )
    assert str(error) == golden
    assert error.args == (golden,)
    assert error.errno is None
    assert (error.status, error.retry_after) == (418, None)
    assert env.throttler.paused_until("some_other_id") == 120.0


async def test_5xx_is_a_plain_oserror_with_the_legacy_text_and_no_pause(env, mocked_api):
    mocked_api.get(URL, status=503, body="oops")

    with pytest.raises(OSError) as excinfo:
        await env.get()

    error = excinfo.value
    golden = "Error executing request GET https://www.test.com/url. HTTP status is 503. Error: oops"
    assert type(error) is OSError
    assert not isinstance(error, rate_limit.RateLimitExceededError)
    assert str(error) == golden
    assert error.args == (golden,)
    assert error.errno is None
    assert env.throttler.paused_until(LIMIT_ID) is None


async def test_html_error_body_is_still_reported_as_na(env, mocked_api):
    mocked_api.get(URL, status=429, body="<html>nope</html>")

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get()

    assert str(excinfo.value).endswith("HTTP status is 429. Error: N/A")


async def test_2xx_after_a_429_resets_the_backoff_streak(env, mocked_api):
    mocked_api.get(URL, status=429, body="x")
    with pytest.raises(RateLimitedRequestError):
        await env.get()
    assert env.throttler.paused_until(LIMIT_ID) == 5.0

    env.clock.advance(5.0)
    mocked_api.get(URL, status=200, payload={"ok": 1})
    assert await env.get() == {"ok": 1}

    mocked_api.get(URL, status=429, body="x")
    with pytest.raises(RateLimitedRequestError):
        await env.get()
    assert env.throttler.paused_until(LIMIT_ID) == 10.0  # now 5.0 + a fresh 5s, not a doubled 10s


async def test_return_err_does_not_raise_but_still_reports_the_response(env, mocked_api):
    mocked_api.get(URL, status=429, body="x", headers={"Retry-After": "4"})

    response = await env.assistant.execute_request_and_get_response(
        url=URL, throttler_limit_id=LIMIT_ID, return_err=True
    )

    assert response.status == 429
    assert env.throttler.paused_until(LIMIT_ID) == 4.0


async def test_rate_limit_on_an_unregistered_id_still_raises_the_edge_error_and_pauses(
    env, mocked_api
):
    mocked_api.get(URL, status=429, body="x", headers={"Retry-After": "2"})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get(limit_id="unregistered")

    assert excinfo.value.limit_id == "unregistered"
    assert env.throttler.paused_until("unregistered") == 2.0


async def test_without_a_limit_id_429_and_418_raise_the_edge_error_but_do_not_pause(
    env, mocked_api
):
    mocked_api.get(URL, status=429, body="x", headers={"Retry-After": "9"})
    mocked_api.get(URL, status=418, body="x")

    with pytest.raises(RateLimitedRequestError) as first:
        await env.get(limit_id="")
    with pytest.raises(RateLimitedRequestError) as second:
        await env.get(limit_id="")

    assert (first.value.status, first.value.retry_after, first.value.limit_id) == (429, 9.0, None)
    assert (second.value.status, second.value.retry_after) == (418, None)
    assert env.throttler.paused_until(LIMIT_ID) is None
    assert env.throttler.paused_until("") is None


async def test_without_a_limit_id_5xx_is_a_plain_oserror(env, mocked_api):
    mocked_api.get(URL, status=500, body="boom")

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
    mocked_api.get(URL, status=200, payload={"ok": 1})

    result = await duck_assistant.execute_request(url=URL, throttler_limit_id=LIMIT_ID)

    assert result == {"ok": 1}


async def test_duck_throttler_429_raises_rate_limited_with_header_retry_after(
    duck_assistant, mocked_api
):
    mocked_api.get(URL, status=429, body="slow down", headers={"Retry-After": "7"})

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await duck_assistant.execute_request(url=URL, throttler_limit_id=LIMIT_ID)

    golden = (
        "Error executing request GET https://www.test.com/url. HTTP status is 429. Error: slow down"
    )
    assert str(excinfo.value) == golden
    assert excinfo.value.retry_after == 7


async def test_duck_throttler_500_raises_plain_oserror(duck_assistant, mocked_api):
    mocked_api.get(URL, status=500, body="boom")

    with pytest.raises(OSError) as excinfo:
        await duck_assistant.execute_request(url=URL, throttler_limit_id=LIMIT_ID)

    assert type(excinfo.value) is OSError


async def test_method_name_in_legacy_message_uses_the_request_method(env, mocked_api):
    mocked_api.post(URL, status=429, body="x")

    with pytest.raises(RateLimitedRequestError) as excinfo:
        await env.get(method=RESTMethod.POST)

    assert str(excinfo.value).startswith("Error executing request POST https://www.test.com/url.")
