"""
Minimal NetworkMockingAssistant for use in web-assistant tests.

Extracted from hummingbot.connector.test_support.network_mocking_assistant.
Dropped: configure_web_assistants_factory() (depends on WebAssistantsFactory internals),
         HummingbotLogger type alias (replaced with logging.Logger).
API surface preserved: create_websocket_mock, add_websocket_aiohttp_message,
run_until_all_aiohttp_messages_delivered, json_messages_sent_through_websocket,
add_websocket_json_message, add_websocket_text_message,
text_messages_sent_through_websocket, run_until_all_text_messages_delivered,
run_until_all_json_messages_delivered, async_init, verify_async_init.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict, deque
from unittest.mock import AsyncMock

import aiohttp


def get_stable_key(ws: AsyncMock) -> uuid.UUID:
    """Recursively unwraps the websocket mock to retrieve the original stable key."""
    current = ws
    while True:
        if hasattr(current, "_stable_key"):
            return current._stable_key
        elif hasattr(current, "__wrapped__"):
            current = current.__wrapped__
        else:
            return id(current)


class NetworkMockingAssistant:
    _logger: logging.Logger | None = None

    @classmethod
    def logger(cls) -> logging.Logger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, deprecated_loop=None):
        super().__init__()
        self._response_text_queues: dict | None = None
        self._response_json_queues: dict | None = None
        self._response_status_queues: dict | None = None
        self._sent_http_requests: dict | None = None

        self._incoming_websocket_json_queues: dict | None = None
        self._all_incoming_websocket_json_delivered_event: dict | None = None
        self._incoming_websocket_text_queues: dict | None = None
        self._all_incoming_websocket_text_delivered_event: dict | None = None
        self._incoming_websocket_aiohttp_queues: dict | None = None
        self._all_incoming_websocket_aiohttp_delivered_event: dict | None = None
        self._sent_websocket_json_messages: dict | None = None
        self._sent_websocket_text_messages: dict | None = None

        try:
            self._loop_id = id(asyncio.get_running_loop())
            asyncio.create_task(self.async_init())
        except RuntimeError:
            self._loop_id = None

    async def async_init(self) -> None:
        self._response_text_queues = defaultdict(asyncio.Queue)
        self._response_json_queues = defaultdict(asyncio.Queue)
        self._response_status_queues = defaultdict(deque)
        self._sent_http_requests = defaultdict(asyncio.Queue)

        self._incoming_websocket_json_queues = defaultdict(asyncio.Queue)
        self._all_incoming_websocket_json_delivered_event = defaultdict(asyncio.Event)
        self._incoming_websocket_text_queues = defaultdict(asyncio.Queue)
        self._all_incoming_websocket_text_delivered_event = defaultdict(asyncio.Event)
        self._incoming_websocket_aiohttp_queues = defaultdict(asyncio.Queue)
        self._all_incoming_websocket_aiohttp_delivered_event = defaultdict(asyncio.Event)
        self._sent_websocket_json_messages = defaultdict(list)
        self._sent_websocket_text_messages = defaultdict(list)

    def verify_async_init(self) -> None:
        if any(
            attr is None
            for attr in [
                self._response_text_queues,
                self._response_json_queues,
                self._response_status_queues,
                self._sent_http_requests,
                self._incoming_websocket_json_queues,
                self._all_incoming_websocket_json_delivered_event,
                self._incoming_websocket_text_queues,
                self._all_incoming_websocket_text_delivered_event,
                self._incoming_websocket_aiohttp_queues,
                self._all_incoming_websocket_aiohttp_delivered_event,
                self._sent_websocket_json_messages,
                self._sent_websocket_text_messages,
            ]
        ):
            raise Exception(
                "NetworkMockingAssistant must be initialized in async context. Please call async_init() first."
            )

        with contextlib.suppress(RuntimeError):
            if self._loop_id != id(asyncio.get_running_loop()):
                raise Exception(
                    "NetworkMockingAssistant was initialized on a different event loop."
                )

    @staticmethod
    def async_partial(function, *args, **kwargs):
        async def partial_func(*_args, **_kwargs):
            return await function(*args, **kwargs)

        return partial_func

    async def _get_next_websocket_json_message(self, ws_key: uuid.UUID, *args, **kwargs):
        self.verify_async_init()
        queue = self._incoming_websocket_json_queues[ws_key]
        message = await queue.get()
        if queue.empty():
            self._all_incoming_websocket_json_delivered_event[ws_key].set()
        return message

    async def _get_next_websocket_aiohttp_message(self, ws_key: uuid.UUID, *args, **kwargs):
        self.verify_async_init()
        queue = self._incoming_websocket_aiohttp_queues[ws_key]
        message = await queue.get()
        if queue.empty():
            self._all_incoming_websocket_aiohttp_delivered_event[ws_key].set()
        if isinstance(message, (BaseException, Exception)):
            raise message
        return message

    async def _get_next_websocket_text_message(self, ws_key: uuid.UUID, *args, **kwargs):
        self.verify_async_init()
        queue = self._incoming_websocket_text_queues[ws_key]
        message = await queue.get()
        if queue.empty():
            self._all_incoming_websocket_text_delivered_event[ws_key].set()
        return message

    def create_websocket_mock(self) -> AsyncMock:
        self.verify_async_init()
        ws = AsyncMock()
        stable_key: uuid.UUID = uuid.uuid4()
        ws._stable_key = stable_key
        ws.__aenter__.side_effect = lambda: ws
        ws.__aexit__.return_value = None

        ws.send_json.side_effect = lambda sent_message: self._sent_websocket_json_messages[
            stable_key
        ].append(sent_message)
        ws.send.side_effect = lambda sent_message: self._sent_websocket_text_messages[
            stable_key
        ].append(sent_message)
        ws.send_str.side_effect = lambda sent_message: self._sent_websocket_text_messages[
            stable_key
        ].append(sent_message)
        ws.receive_json.side_effect = self.async_partial(
            self._get_next_websocket_json_message, stable_key
        )
        ws.receive_str.side_effect = self.async_partial(
            self._get_next_websocket_text_message, stable_key
        )
        ws.receive.side_effect = self.async_partial(
            self._get_next_websocket_aiohttp_message, stable_key
        )
        ws.recv.side_effect = self.async_partial(self._get_next_websocket_text_message, stable_key)
        return ws

    def add_websocket_json_message(self, websocket_mock: AsyncMock, message) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        self._incoming_websocket_json_queues[key].put_nowait(message)
        self._all_incoming_websocket_json_delivered_event[key].clear()

    def add_websocket_text_message(self, websocket_mock: AsyncMock, message) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        self._incoming_websocket_text_queues[key].put_nowait(message)
        self._all_incoming_websocket_text_delivered_event[key].clear()

    def add_websocket_aiohttp_message(
        self,
        websocket_mock: AsyncMock,
        message,
        message_type: aiohttp.WSMsgType = aiohttp.WSMsgType.TEXT,
    ) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        msg = aiohttp.WSMessage(message_type, message, extra=None)
        self._incoming_websocket_aiohttp_queues[key].put_nowait(msg)
        self._all_incoming_websocket_aiohttp_delivered_event[key].clear()

    def add_websocket_aiohttp_exception(
        self, websocket_mock: AsyncMock, exception: Exception | BaseException
    ) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        self._incoming_websocket_aiohttp_queues[key].put_nowait(exception)
        self._all_incoming_websocket_aiohttp_delivered_event[key].clear()

    def json_messages_sent_through_websocket(self, websocket_mock: AsyncMock) -> list:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        return self._sent_websocket_json_messages[key]

    def text_messages_sent_through_websocket(self, websocket_mock: AsyncMock) -> list:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        return self._sent_websocket_text_messages[key]

    async def run_until_all_text_messages_delivered(
        self, websocket_mock: AsyncMock, timeout: int = 1
    ) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        all_delivered = self._all_incoming_websocket_text_delivered_event[key]
        await asyncio.wait_for(all_delivered.wait(), timeout)

    async def run_until_all_json_messages_delivered(
        self, websocket_mock: AsyncMock, timeout: int = 1
    ) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        all_delivered = self._all_incoming_websocket_json_delivered_event[key]
        await asyncio.wait_for(all_delivered.wait(), timeout)

    async def run_until_all_aiohttp_messages_delivered(
        self, websocket_mock: AsyncMock, timeout: int = 1
    ) -> None:
        self.verify_async_init()
        key: uuid.UUID = get_stable_key(websocket_mock)
        all_delivered = self._all_incoming_websocket_aiohttp_delivered_event[key]
        await asyncio.wait_for(all_delivered.wait(), timeout)
