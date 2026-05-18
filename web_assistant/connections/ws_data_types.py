from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from web_assistant.connections.ws_connection import WSConnection


class WSRequest(ABC):
    is_auth_required: bool = False

    @abstractmethod
    async def send_with_connection(self, connection: WSConnection) -> None:
        raise NotImplementedError


@dataclass
class WSJSONRequest(WSRequest):
    payload: Mapping[str, Any]
    throttler_limit_id: str | None = None
    is_auth_required: bool = False

    async def send_with_connection(self, connection: WSConnection) -> None:
        await connection._send_json(payload=self.payload)


@dataclass
class WSPlainTextRequest(WSRequest):
    payload: str
    throttler_limit_id: str | None = None
    is_auth_required: bool = False

    async def send_with_connection(self, connection: WSConnection) -> None:
        await connection._send_plain_text(payload=self.payload)


@dataclass
class WSBinaryRequest(WSRequest):
    payload: bytes
    throttler_limit_id: str | None = None
    is_auth_required: bool = False

    async def send_with_connection(self, connection: WSConnection) -> None:
        await connection._send_binary(payload=self.payload)


@dataclass
class WSResponse:
    data: Any
