from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class Heartbeat:
    """Runs a callback periodically without overlapping executions."""

    def __init__(self, interval_seconds: int, callback: Callable[[], Awaitable[None]]) -> None:
        self._interval_seconds = interval_seconds
        self._callback = callback
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="hidden-tower-heartbeat")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def trigger(self) -> bool:
        if self._lock.locked():
            return False
        async with self._lock:
            await self._callback()
        return True

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self.trigger()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass
