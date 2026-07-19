from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.clients.apify import ApifyClient
from app.config import Settings
from app.events import EventHub
from app.models import EventType, SourceItem, SourceRun, SourceRunStatus, TowerEvent
from app.repositories import Repository

ProcessItem = Callable[[SourceItem], Awaitable[bool]]
logger = logging.getLogger(__name__)


class ApifySource:
    """Runs and recovers exact Apify Actor runs from durable state."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        events: EventHub,
        client: ApifyClient,
        process_item: ProcessItem,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._events = events
        self._client = client
        self._process_item = process_item

    async def run_once(self) -> SourceRun | None:
        active_runs = await self._repository.list_active_source_runs()
        if active_runs:
            logger.info(
                "apify_source_run %s",
                json.dumps(
                    {
                        "event": "resumed",
                        "actor": active_runs[0].actor_name,
                        "run_id": active_runs[0].id,
                    },
                    sort_keys=True,
                ),
            )
            return await self._complete(active_runs[0])
        return await self._start_and_complete(self._settings.apify_actor_id)

    async def _start_and_complete(
        self, actor_name: str, fallback_for_run_id: str | None = None
    ) -> SourceRun:
        run_payload = await self._client.start_actor(
            actor_name, self._settings.apify_batch_size
        )
        run = SourceRun(
            id=str(run_payload["id"]),
            actor_name=actor_name,
            status=SourceRunStatus.RUNNING,
            dataset_id=run_payload.get("defaultDatasetId"),
            fallback_for_run_id=fallback_for_run_id,
        )
        await self._repository.store_source_run(run)
        await self._events.publish(
            TowerEvent(
                type=EventType.SOURCE_RUN_STARTED,
                run_id=run.id,
                correlation_id=run.id,
                payload={
                    "actor": actor_name,
                    "run_id": run.id,
                    "fallback": fallback_for_run_id is not None,
                },
            )
        )
        logger.info(
            "apify_source_run %s",
            json.dumps(
                {
                    "event": "started",
                    "actor": actor_name,
                    "run_id": run.id,
                    "fallback": fallback_for_run_id is not None,
                },
                sort_keys=True,
            ),
        )
        return await self._complete(run)

    async def _complete(self, run: SourceRun) -> SourceRun:
        try:
            terminal = await self._client.poll_run(run.id)
        except TimeoutError:
            failed = await self._finish_run(
                run, SourceRunStatus.TIMED_OUT, failure_reason="poll_timeout"
            )
            return await self._fallback_if_needed(failed)
        except Exception as error:
            failed = await self._finish_run(
                run,
                SourceRunStatus.FAILED,
                failure_reason=f"{type(error).__name__}: provider_request_failed",
            )
            return await self._fallback_if_needed(failed)

        status = {
            "SUCCEEDED": SourceRunStatus.SUCCEEDED,
            "FAILED": SourceRunStatus.FAILED,
            "TIMED-OUT": SourceRunStatus.TIMED_OUT,
            "ABORTED": SourceRunStatus.ABORTED,
        }.get(str(terminal.get("status")), SourceRunStatus.FAILED)
        dataset_id = terminal.get("defaultDatasetId") or run.dataset_id
        if status != SourceRunStatus.SUCCEEDED or not dataset_id:
            failed = await self._finish_run(
                run,
                status,
                dataset_id=dataset_id,
                failure_reason=f"terminal_status:{terminal.get('status', 'unknown')}",
            )
            return await self._fallback_if_needed(failed)

        raw_items = await self._client.fetch_dataset(
            str(dataset_id), self._settings.apify_batch_size
        )
        accepted = 0
        for raw_item in raw_items:
            try:
                item = self._bounded(self._client.normalize(raw_item), run.id)
                if await self._process_item(item):
                    accepted += 1
            except (TypeError, ValueError):
                continue
        return await self._finish_run(
            run,
            SourceRunStatus.SUCCEEDED,
            dataset_id=str(dataset_id),
            item_count=accepted,
        )

    async def _fallback_if_needed(self, run: SourceRun) -> SourceRun:
        if (
            run.actor_name == self._settings.apify_actor_id
            and run.fallback_for_run_id is None
        ):
            return await self._start_and_complete(
                self._settings.apify_fallback_actor_id,
                fallback_for_run_id=run.id,
            )
        return run

    async def _finish_run(
        self,
        run: SourceRun,
        status: SourceRunStatus,
        *,
        dataset_id: str | None = None,
        item_count: int = 0,
        failure_reason: str | None = None,
    ) -> SourceRun:
        completed_at = datetime.now(UTC)
        updated = run.model_copy(
            update={
                "status": status,
                "dataset_id": dataset_id or run.dataset_id,
                "completed_at": completed_at,
                "duration_ms": int(
                    (completed_at - run.started_at).total_seconds() * 1000
                ),
                "item_count": item_count,
                "failure_reason": failure_reason,
            }
        )
        await self._repository.store_source_run(updated)
        await self._events.publish(
            TowerEvent(
                type=EventType.SOURCE_RUN_COMPLETED,
                run_id=run.id,
                correlation_id=run.id,
                payload={
                    "actor": run.actor_name,
                    "run_id": run.id,
                    "dataset_id": updated.dataset_id,
                    "status": status.value,
                    "duration_ms": updated.duration_ms,
                    "item_count": item_count,
                    "failure": failure_reason,
                },
            )
        )
        log = logger.info if status == SourceRunStatus.SUCCEEDED else logger.warning
        log(
            "apify_source_run %s",
            json.dumps(
                {
                    "event": "completed",
                    "actor": run.actor_name,
                    "run_id": run.id,
                    "dataset_id": updated.dataset_id,
                    "status": status.value,
                    "duration_ms": updated.duration_ms,
                    "item_count": item_count,
                    "failure": failure_reason,
                },
                sort_keys=True,
            ),
        )
        return updated

    def _bounded(self, item: SourceItem, run_id: str) -> SourceItem:
        comments = [
            comment[: self._settings.source_comment_limit]
            for comment in item.comments[: self._settings.apify_comment_limit]
        ]
        return item.model_copy(
            update={
                "title": item.title[: self._settings.source_title_limit],
                "text": item.text[: self._settings.source_text_limit],
                "comments": comments,
                "run_id": run_id,
            }
        )


class ApifyScheduler:
    """Coordinates source cadence from shared, success-only durable state."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        source: ApifySource,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._source = source

    async def run_if_due(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        last_success = await self._repository.get_last_apify_success_at()
        if (
            last_success is not None
            and (current - last_success).total_seconds()
            < self._settings.apify_interval_seconds
        ):
            return False
        return await self.run_now(current)

    async def run_now(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        run = await self._source.run_once()
        if run is None or run.status != SourceRunStatus.SUCCEEDED:
            return False
        await self._repository.record_apify_success_at(current)
        return True
