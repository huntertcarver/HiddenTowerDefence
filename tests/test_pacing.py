from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.models import ProcessingStatus, SourceItem
from app.pacing import SourceDispatchQueue
from app.repositories import SQLiteRepository


async def test_source_dispatch_queue_releases_one_item_per_jittered_deadline(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        for index in range(3):
            await repository.store_source_item(
                SourceItem(id=f"hn:{index}", title=f"Item {index}")
            )
        processed: list[str] = []

        async def process(item: SourceItem) -> bool:
            processed.append(item.id)
            await repository.update_source_status(
                item.id, ProcessingStatus.COMPLETED
            )
            return True

        settings = Settings(
            environment="test",
            data_dir=tmp_path,
            source_dispatch_min_seconds=5,
            source_dispatch_max_seconds=9,
        )
        now = datetime(2026, 7, 19, 15, tzinfo=UTC)
        first_holder = SourceDispatchQueue(
            settings, repository, process, random_delay=lambda low, high: 7
        )
        assert await first_holder.dispatch_one_if_due(now)
        assert processed == ["hn:0"]
        assert await repository.get_next_source_dispatch_at() == now + timedelta(
            seconds=7
        )

        second_holder = SourceDispatchQueue(
            settings, repository, process, random_delay=lambda low, high: 6
        )
        assert not await second_holder.dispatch_one_if_due(
            now + timedelta(seconds=6)
        )
        assert processed == ["hn:0"]
        assert await second_holder.dispatch_one_if_due(
            now + timedelta(seconds=7)
        )
        assert processed == ["hn:0", "hn:1"]
    finally:
        await repository.close()
