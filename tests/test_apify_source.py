from pathlib import Path
from typing import Any

from app.config import Settings
from app.events import EventHub
from app.models import SourceItem, SourceRun, SourceRunStatus
from app.repositories import SQLiteRepository
from app.sources.apify_source import ApifySource


class FakeApifyClient:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses
        self.started: list[str] = []
        self.polled: list[str] = []

    async def start_actor(self, actor_id: str, limit: int) -> dict[str, Any]:
        del limit
        self.started.append(actor_id)
        return {"id": f"run-{len(self.started)}"}

    async def poll_run(self, run_id: str) -> dict[str, Any]:
        self.polled.append(run_id)
        status = self.statuses.get(run_id, "SUCCEEDED")
        return {
            "id": run_id,
            "status": status,
            "defaultDatasetId": f"dataset-{run_id}",
        }

    async def fetch_dataset(self, dataset_id: str, limit: int) -> list[dict[str, Any]]:
        del dataset_id, limit
        return [{"id": 1, "title": "Recovered item"}]

    @staticmethod
    def normalize(item: dict[str, Any]) -> SourceItem:
        return SourceItem(id=f"hn:{item['id']}", title=str(item["title"]))


async def test_apify_source_resumes_exact_persisted_run(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        await repository.store_source_run(
            SourceRun(
                id="persisted-run",
                actor_name="gentle_cloud/hacker-news-scraper",
                status=SourceRunStatus.RUNNING,
            )
        )
        client = FakeApifyClient({"persisted-run": "SUCCEEDED"})
        processed: list[str] = []

        async def process(item: SourceItem) -> bool:
            processed.append(item.id)
            return True

        source = ApifySource(
            Settings(environment="test", data_dir=tmp_path),
            repository,
            EventHub(repository),
            client,  # type: ignore[arg-type]
            process,
        )
        completed = await source.run_once()
        assert completed is not None
        assert completed.status == SourceRunStatus.SUCCEEDED
        assert client.started == []
        assert client.polled == ["persisted-run"]
        assert processed == ["hn:1"]
    finally:
        await repository.close()


async def test_apify_source_uses_fallback_after_primary_failure(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        client = FakeApifyClient({"run-1": "FAILED", "run-2": "SUCCEEDED"})

        async def process(item: SourceItem) -> bool:
            del item
            return True

        settings = Settings(environment="test", data_dir=tmp_path)
        source = ApifySource(
            settings,
            repository,
            EventHub(repository),
            client,  # type: ignore[arg-type]
            process,
        )
        completed = await source.run_once()
        assert completed is not None
        assert completed.actor_name == settings.apify_fallback_actor_id
        assert completed.fallback_for_run_id == "run-1"
        assert client.started == [
            settings.apify_actor_id,
            settings.apify_fallback_actor_id,
        ]
        assert client.polled == ["run-1", "run-2"]
    finally:
        await repository.close()
