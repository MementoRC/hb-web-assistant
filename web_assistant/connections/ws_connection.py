import time
from collections.abc import Mapping
from json import JSONDecodeError
from typing import Any

import aiohttp
from aiohttp import WebSocketError, WSCloseCode

from web_assistant.connections.ws_data_types import WSRequest, WSResponse


class WSConnection:
    """Low-level WebSocket connection wrapping a single `aiohttp` WebSocket session."""

    _MAX_MSG_SIZE = 4 * 1024 * 1024  # default aiohttp: 4 * 1024 * 1024

    def __init__(self, aiohttp_client_session: aiohttp.ClientSession) -> None:
        """Initialise the connection with an existing aiohttp client session."""
        self._client_session = aiohttp_client_session
        self._connection: aiohttp.ClientWebSocketResponse | None = None
        self._connected = False
        self._message_timeout: float | None = None
        self._last_recv_time: float = 0.0

    @property
    def last_recv_time(self) -> float:
        """Unix timestamp of the last successfully received message."""
        return self._last_recv_time

    @property
    def connected(self) -> bool:
        """True while the WebSocket handshake has completed and the connection is open."""
        return self._connected

    async def connect(
        self,
        ws_url: str,
        ping_timeout: float = 10,
        message_timeout: float | None = None,
        ws_headers: dict[str, str] | None = None,
        max_msg_size: int | None = None,
    ) -> None:
        """Open a WebSocket connection to the given URL.

        Raises `RuntimeError` if already connected.
        """
        self._ensure_not_connected()
        self._connection = await self._client_session.ws_connect(
            ws_url,
            headers=ws_headers or {},
            autoping=False,
            heartbeat=ping_timeout,
            max_msg_size=max_msg_size if max_msg_size is not None else self._MAX_MSG_SIZE,
        )
        self._message_timeout = message_timeout
        self._connected = True

    async def disconnect(self) -> None:
        """Close the WebSocket connection if it is currently open."""
        if self._connection is not None and not self._connection.closed:
            await self._connection.close()
        self._connection = None
        self._connected = False

    async def send(self, request: WSRequest) -> None:
        """Dispatch a `WSRequest` through the open connection.

        Raises `RuntimeError` if not connected.
        """
        self._ensure_connected()
        await request.send_with_connection(connection=self)

    async def ping(self) -> None:
        """Send a ping frame to keep the connection alive."""
        await self._connection.ping()  # type: ignore[union-attr]

    async def receive(self) -> WSResponse | None:
        """Block until the next application-level message arrives and return it.

        Returns `None` if the connection is closed while waiting.  Raises
        `RuntimeError` if not connected, `TimeoutError` if `message_timeout` is
        exceeded, or `ConnectionError` on unexpected close or oversized messages.
        """
        self._ensure_connected()
        response: WSResponse | None = None
        while self._connected:
            raw_msg = await self._read_message()
            processed = await self._process_message(raw_msg)
            if processed is not None:
                response = self._build_resp(processed)
                break
        return response

    def _ensure_not_connected(self) -> None:
        if self._connected:
            raise RuntimeError("WS is connected.")

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("WS is not connected.")

    async def _read_message(self) -> aiohttp.WSMessage:
        try:
            msg = await self._connection.receive(self._message_timeout)  # type: ignore[union-attr]
        except TimeoutError as err:
            raise TimeoutError("Message receive timed out.") from err
        return msg

    async def _process_message(self, msg: aiohttp.WSMessage) -> aiohttp.WSMessage | None:
        result = await self._check_msg_types(msg)
        self._update_last_recv_time(result)
        return result

    async def _check_msg_types(self, msg: aiohttp.WSMessage) -> aiohttp.WSMessage | None:
        result: aiohttp.WSMessage | None = await self._check_msg_too_big_type(msg)
        result = await self._check_msg_closed_type(result)
        result = await self._check_msg_ping_type(result)
        result = await self._check_msg_pong_type(result)
        return result

    async def _check_msg_too_big_type(
        self, msg: aiohttp.WSMessage | None
    ) -> aiohttp.WSMessage | None:
        if msg is not None and msg.type in [aiohttp.WSMsgType.ERROR]:
            if (
                isinstance(msg.data, WebSocketError)
                and msg.data.code == WSCloseCode.MESSAGE_TOO_BIG
            ):
                await self.disconnect()
                raise WebSocketError(
                    message=f"The WS message is too big: {msg.data}",
                    code=WSCloseCode.MESSAGE_TOO_BIG,
                )
            else:
                await self.disconnect()
                raise ConnectionError(f"WS error: {msg.data}")
        return msg

    async def _check_msg_closed_type(
        self, msg: aiohttp.WSMessage | None
    ) -> aiohttp.WSMessage | None:
        if msg is not None and msg.type in [aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE]:
            if self._connected:
                close_code = self._connection.close_code  # type: ignore[union-attr]
                await self.disconnect()
                raise ConnectionError(
                    f"The WS connection was closed unexpectedly. Close code = {close_code} msg data: {msg.data}"
                )
            return None
        return msg

    async def _check_msg_ping_type(self, msg: aiohttp.WSMessage | None) -> aiohttp.WSMessage | None:
        if msg is not None and msg.type == aiohttp.WSMsgType.PING:
            await self._connection.pong(msg.data)  # type: ignore[union-attr]
            return None
        return msg

    async def _check_msg_pong_type(self, msg: aiohttp.WSMessage | None) -> aiohttp.WSMessage | None:
        if msg is not None and msg.type == aiohttp.WSMsgType.PONG:
            return None
        return msg

    def _update_last_recv_time(self, _: aiohttp.WSMessage | None) -> None:
        self._last_recv_time = time.time()

    async def _send_json(self, payload: Mapping[str, Any]) -> None:
        await self._connection.send_json(payload)  # type: ignore[union-attr]

    async def _send_plain_text(self, payload: str) -> None:
        await self._connection.send_str(payload)  # type: ignore[union-attr]

    async def _send_binary(self, payload: bytes) -> None:
        await self._connection.send_bytes(payload)  # type: ignore[union-attr]

    @staticmethod
    def _build_resp(msg: aiohttp.WSMessage) -> WSResponse:
        if msg.type == aiohttp.WSMsgType.BINARY:
            data = msg.data
        else:
            try:
                data = msg.json()
            except JSONDecodeError:
                data = msg.data
        response = WSResponse(data)
        return response
