import asyncio
import functools
import sys
import unittest
from collections.abc import Awaitable, Callable, Coroutine, Set
from typing import Any

# When pytest with asyncio_mode=auto drives the test suite, it already provides
# proper event-loop isolation.  The manual save/restore/assert dance in the
# wrapper classes was needed for the bare-unittest runner; under pytest it
# actively fights the framework and causes spurious failures.
_PYTEST_RUNNER = "pytest" in sys.modules


def async_to_sync[T](func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., T]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.run(func(*args, **kwargs))

        result: T = loop.run_until_complete(func(*args, **kwargs))
        return result

    return wrapper


class IsolatedAsyncioWrapperTestCase(unittest.IsolatedAsyncioTestCase):
    """
    Custom test case class that wraps `unittest.IsolatedAsyncioTestCase`.

    Saves and restores the "main" event loop around each test so that one
    test's loop destruction cannot cascade into subsequent tests.
    """

    main_event_loop = None

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.main_event_loop = asyncio.get_event_loop()
        except RuntimeError:
            cls.main_event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(cls.main_event_loop)
        assert cls.main_event_loop is not None
        super().setUpClass()

    def setUp(self) -> None:
        self.local_event_loop = asyncio.get_event_loop()
        if not _PYTEST_RUNNER:
            assert self.local_event_loop is not self.main_event_loop
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        if self.main_event_loop is not None and not self.main_event_loop.is_closed():
            asyncio.set_event_loop(self.main_event_loop)
            if not _PYTEST_RUNNER:
                assert asyncio.get_event_loop() is self.main_event_loop
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        if cls.main_event_loop is not None and not cls.main_event_loop.is_closed():
            asyncio.set_event_loop(cls.main_event_loop)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())
        assert asyncio.get_event_loop() is not None

    def run_async_with_timeout(self, coroutine: Awaitable, timeout: float = 1.0) -> Any:
        return self.local_event_loop.run_until_complete(
            asyncio.wait_for(coroutine, timeout=timeout)
        )

    @staticmethod
    async def await_task_completion(tasks_name: str | list[str] | None) -> None:
        def get_coro_func_name(task: asyncio.Task) -> str:
            coro = task.get_coro()
            return coro.cr_code.co_name  # type: ignore[union-attr]

        if tasks_name is None:
            return
        if isinstance(tasks_name, str):
            tasks_name = [tasks_name]
        tasks: Set[asyncio.Task] = asyncio.all_tasks()
        tasks = {
            task
            for task in tasks
            for task_name in tasks_name
            if task_name == get_coro_func_name(task)
        }

        if tasks:
            await asyncio.wait(tasks)
