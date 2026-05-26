from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping


class WSConnectionProtocol(Protocol):
    """Structural protocol satisfied by any object that can transmit WebSocket frames.

    `WSRequest` subtypes call the appropriate method via `send_with_connection` to
    decouple request serialisation from the underlying transport.
    """

    async def _send_json(self, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError

    async def _send_plain_text(self, payload: str) -> None:
        raise NotImplementedError

    async def _send_binary(self, payload: bytes) -> None:
        raise NotImplementedError


class WSRequest(ABC):
    """Abstract base class for all outgoing WebSocket messages.

    Concrete subclasses encode a specific wire format (JSON, plain text, or binary)
    and dispatch themselves to the connection via `send_with_connection`.
    """

    is_auth_required: bool = False

    @abstractmethod
    async def send_with_connection(self, connection: WSConnectionProtocol) -> None:
        """Dispatch this request through the given connection.

        Implementations must call the appropriate low-level send method on
        `connection` for their wire format.
        """
        raise NotImplementedError


@dataclass
class WSJSONRequest(WSRequest):
    """A WebSocket request whose payload is serialised as JSON."""

    payload: Mapping[str, Any]
    throttler_limit_id: str | None = None
    is_auth_required: bool = False

    async def send_with_connection(self, connection: WSConnectionProtocol) -> None:
        await connection._send_json(payload=self.payload)


@dataclass
class WSPlainTextRequest(WSRequest):
    """A WebSocket request whose payload is sent as a plain text frame."""

    payload: str
    throttler_limit_id: str | None = None
    is_auth_required: bool = False

    async def send_with_connection(self, connection: WSConnectionProtocol) -> None:
        await connection._send_plain_text(payload=self.payload)


@dataclass
class WSBinaryRequest(WSRequest):
    """A WebSocket request whose payload is sent as a binary frame."""

    payload: bytes
    throttler_limit_id: str | None = None
    is_auth_required: bool = False

    async def send_with_connection(self, connection: WSConnectionProtocol) -> None:
        await connection._send_binary(payload=self.payload)


@dataclass
class WSResponse:
    """Wraps a decoded WebSocket message received from the server."""

    data: Any
