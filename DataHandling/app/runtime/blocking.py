"""Async boundary for synchronous database, SDK, and legacy model clients."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from typing import Any, TypeVar
from weakref import WeakKeyDictionary


ResultT = TypeVar("ResultT")
DEFAULT_BLOCKING_CONCURRENCY = 16


class BlockingExecutor:
    """Concurrency-limited adapter around asyncio's default thread pool."""

    def __init__(self, concurrency: int = DEFAULT_BLOCKING_CONCURRENCY):
        if concurrency <= 0:
            raise ValueError("blocking concurrency must be positive")
        self._concurrency = concurrency
        self._limiters: WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            asyncio.Semaphore,
        ] = WeakKeyDictionary()

    def _limiter_for_running_loop(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        limiter = self._limiters.get(loop)
        if limiter is None:
            limiter = asyncio.Semaphore(self._concurrency)
            self._limiters[loop] = limiter
        return limiter

    async def run(
        self,
        function: Callable[..., ResultT],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> ResultT:
        async with self._limiter_for_running_loop():
            return await asyncio.to_thread(function, *args, **kwargs)


def _configured_concurrency() -> int:
    raw_value = os.getenv(
        "MAX_BLOCKING_CONCURRENCY",
        str(DEFAULT_BLOCKING_CONCURRENCY),
    )
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("MAX_BLOCKING_CONCURRENCY must be an integer") from exc
    if value <= 0:
        raise ValueError("MAX_BLOCKING_CONCURRENCY must be positive")
    return value


_executor = BlockingExecutor(_configured_concurrency())


async def run_blocking(
    function: Callable[..., ResultT],
    /,
    *args: Any,
    **kwargs: Any,
) -> ResultT:
    """Run synchronous work behind the shared concurrency-limited boundary."""

    return await _executor.run(function, *args, **kwargs)
