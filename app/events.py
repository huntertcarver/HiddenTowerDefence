from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.models import TowerEvent
from app.redaction import sanitize_value
from app.repositories import Repository


class EventHub:
    """Persists every event before broadcasting it to connected clients."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository
        self._subscribers: set[asyncio.Queue[TowerEvent]] = set()
        self._subscriber_lock = asyncio.Lock()

    async def publish(self, event: TowerEvent) -> TowerEvent:
        sanitized = event.model_copy(update={"payload": sanitize_value(event.payload)})
        persisted = await self._repository.append_event(sanitized)
        async with self._subscriber_lock:
            for queue in tuple(self._subscribers):
                if queue.full():
                    self._subscribers.discard(queue)
                    continue
                queue.put_nowait(persisted)
        return persisted

    async def replay(self, after_id: int = 0, limit: int = 200) -> list[TowerEvent]:
        return await self._repository.list_events(after_id=after_id, limit=limit)

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
