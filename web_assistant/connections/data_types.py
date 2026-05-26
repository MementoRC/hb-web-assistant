from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    import aiohttp

from web_assistant.connections.ws_data_types import (
    WSBinaryRequest,
    WSJSONRequest,
    WSPlainTextRequest,
    WSRequest,
    WSResponse,
)

__all__ = [
    "RESTMethod",
    "RESTRequest",
    "EndpointRESTRequest",
    "RESTResponse",
    "WSRequest",
    "WSJSONRequest",
    "WSPlainTextRequest",
    "WSBinaryRequest",
    "WSResponse",
]


class RESTMethod(Enum):
    """HTTP method constants used when constructing REST requests."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"

    def __str__(self) -> str:
        obj_str = repr(self)
        return obj_str

    def __repr__(self) -> str:
        return self.value


@dataclass
class RESTRequest:
    """Describes a single outgoing REST request before it is dispatched.

    Pre-processors and authentication handlers receive and return instances of
    this dataclass to transform the request before it reaches the wire.
    """

    method: RESTMethod
    url: str | None = None
    endpoint_url: str | None = None
    params: Mapping[str, str] | None = None
    data: Any = None
    headers: Mapping[str, str] | None = None
    is_auth_required: bool = False
    throttler_limit_id: str | None = None


@dataclass
class EndpointRESTRequest(RESTRequest, ABC):
    """This request class enable the user to provide either a complete URL or simply an endpoint.

    The endpoint is concatenated with the return value of `base_url`. It can handle endpoints supplied both as
    `"endpoint"` and `"/endpoint"`. It also provides the necessary checks to ensure a valid URL can be constructed.
    """

    endpoint: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalise the request fields after dataclass initialisation."""
        self._ensure_url()
        self._ensure_params()
        self._ensure_data()

    @property
    @abstractmethod
    def base_url(self) -> str:
        """The root URL that is prepended to `endpoint` when no explicit `url` is given.

        Implementations must return a string without a trailing slash.
        """
        raise NotImplementedError

    def _ensure_url(self) -> None:
        if self.url is None and self.endpoint is None:
            raise ValueError("Either the full url or the endpoint must be specified.")
        if self.url is None:
            if self.endpoint.startswith("/"):  # type: ignore[union-attr]
                self.url = f"{self.base_url}{self.endpoint}"
            else:
                self.url = f"{self.base_url}/{self.endpoint}"

    def _ensure_params(self) -> None:
        if (
            self.method in [RESTMethod.POST, RESTMethod.PUT, RESTMethod.PATCH]
            and self.params is not None
        ):
            raise ValueError(
                f"{self.method.value} requests should not use `params`. Use `data` instead."
            )

    def _ensure_data(self) -> None:
        if self.method in [RESTMethod.POST, RESTMethod.PUT, RESTMethod.PATCH]:
            if self.data is not None:
                self.data = json.dumps(self.data)
        elif self.data is not None:
            raise ValueError(
                "The `data` field should be used only for POST, PUT, or PATCH requests. Use `params` instead."
            )


class RESTResponse:
    """Wraps an aiohttp.ClientResponse to provide a stable interface."""

    def __init__(self, aiohttp_response: aiohttp.ClientResponse) -> None:
        """Wrap an aiohttp response for consumption by post-processors and callers."""
        self._aiohttp_response = aiohttp_response

    @property
    def url(self) -> str:
        """The final URL of the response (after any redirects)."""
        url_str = str(self._aiohttp_response.url)
        return url_str

    @property
    def method(self) -> RESTMethod:
        """The HTTP method used by the original request."""
        method_ = RESTMethod[self._aiohttp_response.method.upper()]
        return method_

    @property
    def status(self) -> int:
        """The HTTP status code of the response."""
        status_ = int(self._aiohttp_response.status)
        return status_

    @property
    def headers(self) -> Mapping[str, str] | None:
        """The response headers as a mapping."""
        headers_ = self._aiohttp_response.headers
        return headers_

    async def json(self) -> Any:
        """Decode the response body as JSON, with fallback handling for text/plain and text/html content types."""
        if (
            self._aiohttp_response.content_type == "text/plain"
            or self._aiohttp_response.content_type == "text/html"
        ):
            # aiohttp does not support decoding of text/plain or text/html content types
            # so we need to read the response as bytes and decode it manually
            # https://docs.aiohttp.org/en/stable/client_reference.html#aiohttp.ClientResponse.json
            byte_string = await self._aiohttp_response.read()
            if isinstance(byte_string, bytes):
                decoded_string = byte_string.decode("utf-8")
                try:
                    json_ = json.loads(decoded_string)
                except json.JSONDecodeError:
                    json_ = decoded_string
            else:
                json_ = await self._aiohttp_response.json()
        else:
            json_ = await self._aiohttp_response.json()
        return json_

    async def text(self) -> str:
        """Decode the response body as a plain string."""
        text_ = await self._aiohttp_response.text()
        return text_

    def __repr__(self) -> str:
        return (
            f"RESTResponse(url='{self.url}', method={self.method}, "
            f"status={self.status}, headers={self._aiohttp_response.headers})"
        )
