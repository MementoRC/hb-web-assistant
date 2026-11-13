"""Shared pytest fixtures for web_assistant tests."""

from collections.abc import AsyncIterator

import pytest_asyncio

from tests._helpers.local_http_server import LocalHTTPServer


@pytest_asyncio.fixture
async def local_server() -> AsyncIterator[LocalHTTPServer]:
    async with LocalHTTPServer() as server:
        yield server
