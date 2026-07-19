import os
from uuid import uuid4

import pytest

from app.models import (
    Approval,
    ApprovalStatus,
    EventType,
    SourceItem,
    TaintRecord,
    TowerEvent,
    TrustState,
)
from app.repositories import SpannerRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("SPANNER_EMULATOR_HOST"),
    reason="Cloud Spanner emulator is not running",
)


async def test_spanner_repository_deduplication_and_event_ordering() -> None:
    repository = SpannerRepository(
        os.getenv("SPANNER_PROJECT_ID", "test-project"),
        os.getenv("SPANNER_INSTANCE_ID", "test-instance"),
        os.getenv("SPANNER_DATABASE_ID", "hiddentowerdefence"),
    )
    await repository.connect()
    try:
        source_id = f"hn:contract-{uuid4().hex}"
        item = SourceItem(id=source_id, title="Spanner contract")
        assert await repository.store_source_item(item)
        assert not await repository.store_source_item(item)
        first = await repository.append_event(
            TowerEvent(type=EventType.CONTENT_RECEIVED, source_item_id=source_id)
        )
        second = await repository.append_event(
            TowerEvent(type=EventType.SCAN_STARTED, source_item_id=source_id)
        )
        assert first.id is not None
        assert second.id == first.id + 1
        replay = await repository.list_events(after_id=first.id)
        assert any(event.id == second.id for event in replay)

        await repository.set_trust_state(TrustState.NORMAL)
        transition = await repository.transition_trust_state(
            TrustState.RESTRICTED, "contract", source_id
        )
        assert transition is not None
        await repository.create_taint(
            TaintRecord(source_item_id=source_id, reason="contract")
        )
        assert await repository.is_tainted(source_id)

        approval = await repository.create_approval(
            Approval(
                source_item_id=source_id,
                action="save_brief",
                idempotency_key=uuid4().hex,
            )
        )
        claimed = await repository.claim_approval_execution(approval.id)
        assert claimed is not None
        assert claimed.status == ApprovalStatus.EXECUTING
        assert await repository.claim_approval_execution(approval.id) is None
        finalized = await repository.finalize_approval(
            approval.id, ApprovalStatus.APPROVED
        )
        assert finalized is not None
        assert finalized.status == ApprovalStatus.APPROVED
    finally:
        await repository.close()
