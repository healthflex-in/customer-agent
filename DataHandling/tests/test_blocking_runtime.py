import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.runtime.blocking import BlockingExecutor, run_blocking


class BlockingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.runtime.blocking.asyncio.to_thread", new_callable=AsyncMock)
    async def test_delegates_to_asyncio_thread_boundary(self, to_thread):
        def combine(left, right, *, separator):
            return f"{left}{separator}{right}"

        to_thread.return_value = "patient:form"
        result = await run_blocking(
            combine,
            "patient",
            "form",
            separator=":",
        )

        self.assertEqual(result, "patient:form")
        to_thread.assert_awaited_once_with(
            combine,
            "patient",
            "form",
            separator=":",
        )

    @patch("app.runtime.blocking.asyncio.to_thread", new_callable=AsyncMock)
    async def test_executor_limits_outstanding_sync_operations(self, to_thread):
        release = asyncio.Event()
        started = 0

        async def controlled_call(function, *args, **kwargs):
            nonlocal started
            started += 1
            await release.wait()
            return function(*args, **kwargs)

        to_thread.side_effect = controlled_call
        executor = BlockingExecutor(concurrency=1)
        first = asyncio.create_task(executor.run(lambda: "first"))
        second = asyncio.create_task(executor.run(lambda: "second"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(started, 1)
        release.set()
        self.assertEqual(await asyncio.gather(first, second), ["first", "second"])

    def test_rejects_non_positive_concurrency(self):
        with self.assertRaises(ValueError):
            BlockingExecutor(concurrency=0)


if __name__ == "__main__":
    unittest.main()
