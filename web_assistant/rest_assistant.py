import json
import time
from asyncio import wait_for
from copy import deepcopy
from typing import Any, cast

from rate_limit import RATE_LIMIT_STATUSES, RateLimitExceededError, RateLimitVerdict

from web_assistant.auth import AuthBase
from web_assistant.connections.data_types import RESTMethod, RESTRequest, RESTResponse
from web_assistant.connections.rest_connection import RESTConnection
from web_assistant.errors import RateLimitedRequestError as RateLimitedRequestError
from web_assistant.rest_post_processors import RESTPostProcessorBase
from web_assistant.rest_pre_processors import RESTPreProcessorBase
from web_assistant.throttler.async_throttler_base import AsyncThrottlerBase


class RESTAssistant:
    """A helper class to contain all REST-related logic.

    The class can be injected with additional functionality by passing a list of objects inheriting from
    the `RESTPreProcessorBase` and `RESTPostProcessorBase` classes. The pre-processors are applied to a request
    before it is sent out, while the post-processors are applied to a response before it is returned to the caller.
    """

    def __init__(
        self,
        connection: RESTConnection,
        throttler: AsyncThrottlerBase,
        rest_pre_processors: list[RESTPreProcessorBase] | None = None,
        rest_post_processors: list[RESTPostProcessorBase] | None = None,
        auth: AuthBase | None = None,
    ):
        """Construct a REST assistant wired to the given connection and throttler."""
        self._connection = connection
        self._rest_pre_processors = rest_pre_processors or []
        self._rest_post_processors = rest_post_processors or []
        self._auth = auth
        self._throttler = throttler

    async def execute_request(
        self,
        url: str,
        throttler_limit_id: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        method: RESTMethod = RESTMethod.GET,
        is_auth_required: bool = False,
        return_err: bool = False,
        timeout: float | None = None,
        headers: dict[str, Any] | None = None,
    ) -> str | dict[str, Any]:
        """Execute a throttled REST request and return the decoded JSON body.

        Raises `OSError` on HTTP 4xx/5xx unless `return_err` is `True`.
        """
        response = await self.execute_request_and_get_response(
            url=url,
            throttler_limit_id=throttler_limit_id,
            params=params,
            data=data,
            method=method,
            is_auth_required=is_auth_required,
            return_err=return_err,
            timeout=timeout,
            headers=headers,
        )
        response_json = await response.json()
        # json() returns Any; callers expect str | dict[str, Any] per this method's contract.
        return cast("str | dict[str, Any]", response_json)

    async def execute_request_and_get_response(
        self,
        url: str,
        throttler_limit_id: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        method: RESTMethod = RESTMethod.GET,
        is_auth_required: bool = False,
        return_err: bool = False,
        timeout: float | None = None,
        headers: dict[str, Any] | None = None,
    ) -> RESTResponse:
        """Execute a throttled REST request and return the raw `RESTResponse`.

        Unlike `execute_request`, this method does not decode the body — callers receive
        the full response object and can call `.json()` or `.text()` themselves.
        Raises `OSError` on HTTP 4xx/5xx unless `return_err` is `True`.
        """
        headers = headers or {}

        local_headers: dict[str, str] = {
            "Content-Type": (
                "application/json"
                if method != RESTMethod.GET
                else "application/x-www-form-urlencoded"
            )
        }

        local_headers.update(headers)

        data_str: str | None = json.dumps(data) if data is not None else None

        request = RESTRequest(
            method=method,
            url=url,
            params=params,
            data=data_str,
            headers=local_headers,
            is_auth_required=is_auth_required,
            throttler_limit_id=throttler_limit_id,
        )

        async with self._throttler.execute_task(limit_id=throttler_limit_id):
            response = await self.call(request=request, timeout=timeout)

            # Every status is reported (a 2xx resets the backoff streak); 429/418 pause the throttler.
            verdict: RateLimitVerdict | None = None
            report = getattr(self._throttler, "report_response", None)
            if throttler_limit_id and callable(report):
                verdict = report(throttler_limit_id, response.status, response.headers)

            if response.status >= 400 and not return_err:
                error_response = await response.text()
                error_text = "N/A" if "<html" in error_response else error_response
                legacy_message = (
                    f"Error executing request {method.name} {url}. HTTP status is {response.status}. "
                    f"Error: {error_text}"
                )
                raise self._error_for(legacy_message, response, throttler_limit_id or None, verdict)
            return response

    def _error_for(
        self,
        legacy_message: str,
        response: RESTResponse,
        limit_id: str | None,
        verdict: RateLimitVerdict | None,
    ) -> OSError:
        """Pick the edge exception: `RateLimitedRequestError` for 429/418, else a plain `OSError`."""
        status = response.status
        if verdict is not None:
            if verdict.rate_limited:
                return RateLimitedRequestError(
                    legacy_message,
                    status=status,
                    retry_after=verdict.retry_after,
                    limit_id=limit_id,
                )
        elif status in RATE_LIMIT_STATUSES:
            # No limit id to pause: still surface the rate limit, with Retry-After parsed from headers.
            retry_after = RateLimitExceededError.from_response(
                status, response.headers, now=getattr(self._throttler, "_wall_clock", time.time)
            ).retry_after
            return RateLimitedRequestError(
                legacy_message, status=status, retry_after=retry_after, limit_id=None
            )
        return OSError(legacy_message)

    async def call(self, request: RESTRequest, timeout: float | None = None) -> RESTResponse:
        """Apply pre-processors and auth, dispatch the request, then apply post-processors.

        This is the lowest-level public entry point; it does not interact with the throttler.
        """
        request = deepcopy(request)
        request = await self._pre_process_request(request)
        request = await self._authenticate(request)
        resp = await wait_for(self._connection.call(request), timeout)
        resp = await self._post_process_response(resp)
        return resp

    async def _pre_process_request(self, request: RESTRequest) -> RESTRequest:
        for pre_processor in self._rest_pre_processors:
            request = await pre_processor.pre_process(request)
        return request

    async def _authenticate(self, request: RESTRequest) -> RESTRequest:
        if self._auth is not None and request.is_auth_required:
            request = await self._auth.rest_authenticate(request)
        return request

    async def _post_process_response(self, response: RESTResponse) -> RESTResponse:
        for post_processor in self._rest_post_processors:
            response = await post_processor.post_process(response)
        return response
