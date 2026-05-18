import json
from asyncio import wait_for
from copy import deepcopy
from typing import Any, cast

from web_assistant.auth import AuthBase
from web_assistant.connections.data_types import RESTMethod, RESTRequest, RESTResponse
from web_assistant.connections.rest_connection import RESTConnection
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

            if response.status >= 400 and not return_err:
                error_response = await response.text()
                error_text = "N/A" if "<html" in error_response else error_response
                raise OSError(
                    f"Error executing request {method.name} {url}. HTTP status is {response.status}. "
                    f"Error: {error_text}"
                )
            return response

    async def call(self, request: RESTRequest, timeout: float | None = None) -> RESTResponse:
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
