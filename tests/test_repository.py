from pathlib import Path

from app.models import Approval, ApprovalStatus, EventType, SourceItem, TowerEvent, TrustState
from app.repositories import SQLiteRepository


async def test_repository_persists_events_and_approvals(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:1", title="A story")
        assert await repository.store_source_item(item)
        assert not await repository.store_source_item(item)

        event = await repository.append_event(
            TowerEvent(type=EventType.CONTENT_RECEIVED, source_item_id=item.id)
        )
        assert event.id == 1
        assert [saved.id for saved in await repository.list_events()] == [1]

        await repository.set_trust_state(TrustState.RESTRICTED)
        assert await repository.get_trust_state() == TrustState.RESTRICTED

        approval = await repository.create_approval(
            Approval(source_item_id=item.id, action="save_brief")
        )
        assert [pending.id for pending in await repository.list_approvals()] == [approval.id]
        resolved = await repository.resolve_approval(approval.id, ApprovalStatus.APPROVED)
        assert resolved is not None
        assert resolved.status == ApprovalStatus.APPROVED
        assert await repository.list_approvals() == []
    finally:
        await repository.close()
