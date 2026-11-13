"""A real aiohttp server on 127.0.0.1 serving canned responses (replaces aioresponses)."""

import json
from collections import defaultdict, deque
from types import TracebackType
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer


class LocalHTTPServer:
    """Serve queued canned responses per (method, path).

    Each queued response is served once, FIFO. Unregistered or exhausted routes
    answer 404 so a wrong request fails loudly.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], deque[web.Response]] = defaultdict(deque)
        self.requests: list[tuple[str, str]] = []
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._server = TestServer(app, host="127.0.0.1")

    def url(self, path: str = "/url") -> str:
        return str(self._server.make_url(path))

    def add(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: str | bytes = b"",
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if payload is not None:
            body = json.dumps(payload)
        data = body.encode() if isinstance(body, str) else body
        response_headers = dict(headers or {})
        if "content-type" not in {key.lower() for key in response_headers}:
            response_headers["Content-Type"] = "application/json"
        self._routes[(method.upper(), path)].append(
            web.Response(status=status, body=data, headers=response_headers)
        )

    def get(self, path: str, **kwargs: Any) -> None:
        self.add("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> None:
        self.add("POST", path, **kwargs)

    async def _handle(self, request: web.Request) -> web.Response:
        key = (request.method, request.path)
        self.requests.append(key)
        queue = self._routes.get(key)
        if not queue:
            return web.Response(status=404, text=f"no canned response for {key}")
        canned = queue.popleft()
        return web.Response(status=canned.status, body=canned.body, headers=canned.headers)

    async def __aenter__(self) -> "LocalHTTPServer":
        await self._server.start_server()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._server.close()
