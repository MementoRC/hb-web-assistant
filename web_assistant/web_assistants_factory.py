from typing import Any

from web_assistant.auth import AuthBase
from web_assistant.connections.connections_factory import ConnectionsFactory
from web_assistant.rest_assistant import RESTAssistant
from web_assistant.rest_post_processors import RESTPostProcessorBase
from web_assistant.rest_pre_processors import RESTPreProcessorBase
from web_assistant.throttler.async_throttler_base import AsyncThrottlerBase
from web_assistant.ws_assistant import WSAssistant
from web_assistant.ws_post_processors import WSPostProcessorBase
from web_assistant.ws_pre_processors import WSPreProcessorBase


class WebAssistantsFactory:
    """Creates `RESTAssistant` and `WSAssistant` objects.

    The purpose of the `web_assistant` layer is to abstract away all WebSocket and REST operations from the exchange
    logic. The assistant objects are designed to be injectable with additional logic via the pre- and post-processor
    lists. Consult the documentation of the relevant assistant and/or pre-/post-processor class for
    additional information.

    todo: integrate AsyncThrottler
    """

    def __init__(
        self,
        throttler: AsyncThrottlerBase,
        rest_pre_processors: list[RESTPreProcessorBase] | None = None,
        rest_post_processors: list[RESTPostProcessorBase] | None = None,
        ws_pre_processors: list[WSPreProcessorBase] | None = None,
        ws_post_processors: list[WSPostProcessorBase] | None = None,
        auth: AuthBase | None = None,
        connections_factory: ConnectionsFactory | None = None,
    ):
        """Configure the factory with shared throttler, processors, auth, and connection factory."""
        self._connections_factory = connections_factory or ConnectionsFactory()
        self._rest_pre_processors = rest_pre_processors or []
        self._rest_post_processors = rest_post_processors or []
        self._ws_pre_processors = ws_pre_processors or []
        self._ws_post_processors = ws_post_processors or []
        self._auth = auth
        self._throttler = throttler

    @property
    def throttler(self) -> AsyncThrottlerBase:
        """The throttler instance shared across all assistants created by this factory."""
        return self._throttler

    @property
    def auth(self) -> AuthBase | None:
        """The authentication provider, or `None` if unauthenticated requests are used."""
        return self._auth

    async def get_rest_assistant(self) -> RESTAssistant:
        """Create and return a new `RESTAssistant` backed by a fresh connection."""
        connection = await self._connections_factory.get_rest_connection()
        assistant = RESTAssistant(
            connection=connection,
            throttler=self._throttler,
            rest_pre_processors=self._rest_pre_processors,
            rest_post_processors=self._rest_post_processors,
            auth=self._auth,
        )
        return assistant

    async def get_ws_assistant(self) -> WSAssistant:
        """Create and return a new `WSAssistant` backed by a fresh connection."""
        connection = await self._connections_factory.get_ws_connection()
        assistant = WSAssistant(
            connection, self._ws_pre_processors, self._ws_post_processors, self._auth
        )
        return assistant

    async def close(self) -> None:
        """
        Close the underlying connections.
        """
        await self._connections_factory.close()

    async def __aenter__(self) -> "WebAssistantsFactory":
        """Enter the async context manager, initialising the connections factory if needed."""
        # If the underlying connections factory is a context manager, enter its context.
        if hasattr(self._connections_factory, "__aenter__"):
            await self._connections_factory.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context manager and close all underlying connections."""
        await self.close()
