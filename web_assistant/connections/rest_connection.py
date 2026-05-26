import aiohttp

from web_assistant.connections.data_types import RESTRequest, RESTResponse


class RESTConnection:
    """Thin wrapper around an `aiohttp.ClientSession` that dispatches `RESTRequest` objects."""

    def __init__(self, aiohttp_client_session: aiohttp.ClientSession) -> None:
        """Initialise the connection with an existing aiohttp client session."""
        self._client_session = aiohttp_client_session

    async def call(self, request: RESTRequest) -> RESTResponse:
        """Execute the given request and return the wrapped response.

        Raises `ValueError` if `request.url` is not set.
        """
        if request.url is None:
            raise ValueError("RESTRequest.url must be set before calling.")
        aiohttp_resp = await self._client_session.request(
            method=request.method.value,
            url=request.url,
            params=request.params,
            data=request.data,
            headers=request.headers,
        )

        resp = await self._build_resp(aiohttp_resp)
        return resp

    @staticmethod
    async def _build_resp(aiohttp_resp: aiohttp.ClientResponse) -> RESTResponse:
        resp = RESTResponse(aiohttp_resp)
        return resp
