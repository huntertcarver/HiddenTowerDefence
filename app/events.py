from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.models import TowerEvent
from app.repositories import SQLiteRepository


class EventHub:
    """Persists every event before broadcasting it to connected clients."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository
        self._subscribers: set[asyncio.Queue[TowerEvent]] = set()
        self._subscriber_lock = asyncio.Lock()

    async def publish(self, event: TowerEvent) -> TowerEvent:
        persisted = await self._repository.append_event(event)
        async with self._subscriber_lock:
            for queue in tuple(self._subscribers):
                if queue.full():
                    self._subscribers.discard(queue)
                    continue
                queue.put_nowait(persisted)
        return persisted

    async def replay(self, after_id: int = 0) -> list[TowerEvent]:
        return await self._repository.list_events(after_id=after_id)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[TowerEvent]]:
        queue: asyncio.Queue[TowerEvent] = asyncio.Queue(maxsize=256)
        async with self._subscriber_lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._subscriber_lock:
                self._subscribers.discard(queue)
