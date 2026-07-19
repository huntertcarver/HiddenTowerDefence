from __future__ import annotations

import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.models import SourceItem
from app.repositories import Repository

ProcessSource = Callable[[SourceItem], Awaitable[bool]]
RandomDelay = Callable[[int, int], int]
logger = logging.getLogger(__name__)


class SourceDispatchQueue:
    """Releases at most one persisted source item at each jittered deadline."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        process_source: ProcessSource,
        random_delay: RandomDelay | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._process_source = process_source
        self._random_delay = random_delay or random.SystemRandom().randint

    async def dispatch_one_if_due(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        next_dispatch = await self._repository.get_next_source_dispatch_at()
        if next_dispatch is not None and current < next_dispatch:
            return False

        for item in await self._repository.list_pending_source_items(limit=50):
            if not await self._process_source(item):
                continue
            low = min(
                self._settings.source_dispatch_min_seconds,
                self._settings.source_dispatch_max_seconds,
            )
            high = max(
                self._settings.source_dispatch_min_seconds,
                self._settings.source_dispatch_max_seconds,
            )
            delay_seconds = self._random_delay(low, high)
            scheduled_at = current + timedelta(seconds=delay_seconds)
            await self._repository.record_next_source_dispatch_at(scheduled_at)
            logger.info(
                "source_dispatch_queue %s",
                json.dumps(
                    {
                        "source_item_id": item.id,
                        "delay_seconds": delay_seconds,
                        "next_dispatch_at": scheduled_at.isoformat(),
                    },
                    sort_keys=True,
                ),
            )
            return True
        return False
