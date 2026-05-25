from tests._helpers.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase
from web_assistant.connections.connections_factory import ConnectionsFactory
from web_assistant.connections.rest_connection import RESTConnection
from web_assistant.connections.ws_connection import WSConnection


class ConnectionsFactoryTest(IsolatedAsyncioWrapperTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        ConnectionsFactory.reset()

    async def asyncTearDown(self) -> None:
        ConnectionsFactory.reset()
        await super().asyncTearDown()

    async def test_get_rest_connection(self):
        factory = ConnectionsFactory()

        rest_connection = await factory.get_rest_connection()

        self.assertIsInstance(rest_connection, RESTConnection)

    async def test_get_ws_connection(self):
        factory = ConnectionsFactory()

        rest_connection = await factory.get_ws_connection()

        self.assertIsInstance(rest_connection, WSConnection)

    async def test_close_resets_singleton_identity(self):
        factory = ConnectionsFactory()
        original_id = id(factory)

        await factory.close()

        new_factory = ConnectionsFactory()
        self.assertNotEqual(id(new_factory), original_id)

    async def test_post_close_creates_fresh_session(self):
        factory = ConnectionsFactory()
        await factory.get_rest_connection()
        old_session = factory._shared_client
        self.assertIsNotNone(old_session)

        await factory.close()

        new_factory = ConnectionsFactory()
        await new_factory.get_rest_connection()
        new_session = new_factory._shared_client

        self.assertIsNotNone(new_session)
        self.assertIsNot(new_session, old_session)
        self.assertFalse(new_session.closed)

    async def test_aexit_resets_singleton(self):
        async with ConnectionsFactory() as f1:
            id_inside = id(f1)

        f2 = ConnectionsFactory()
        self.assertNotEqual(id(f2), id_inside)

    async def test_explicit_reset_without_close(self):
        factory = ConnectionsFactory()
        original_id = id(factory)

        ConnectionsFactory.reset()

        new_factory = ConnectionsFactory()
        self.assertNotEqual(id(new_factory), original_id)
