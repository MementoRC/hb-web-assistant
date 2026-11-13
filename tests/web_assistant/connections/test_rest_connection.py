import asyncio
import json

import aiohttp

from tests._helpers.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase
from tests._helpers.local_http_server import LocalHTTPServer
from web_assistant.connections.data_types import RESTMethod, RESTRequest, RESTResponse
from web_assistant.connections.rest_connection import RESTConnection


class RESTConnectionTest(IsolatedAsyncioWrapperTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ev_loop = asyncio.get_event_loop()

    async def test_rest_connection_call(self):
        server = LocalHTTPServer()
        await server.__aenter__()
        self.addAsyncCleanup(server.__aexit__, None, None, None)
        url = server.url("/url")
        resp = {"one": 1}
        server.get("/url", body=json.dumps(resp).encode())

        client_session = aiohttp.ClientSession()
        connection = RESTConnection(client_session)
        request = RESTRequest(method=RESTMethod.GET, url=url)

        ret = await connection.call(request)

        self.assertIsInstance(ret, RESTResponse)
        self.assertEqual(url, ret.url)
        self.assertEqual(200, ret.status)

        j = await ret.json()

        self.assertEqual(resp, j)
        await client_session.close()
