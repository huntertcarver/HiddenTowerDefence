import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import (
    Approval,
    ApprovalStatus,
    EventType,
    ProcessingStatus,
    SourceItem,
    TaintRecord,
    ToolRequest,
    ToolStatus,
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
        ingestion_at = datetime(2026, 7, 19, 12, tzinfo=UTC)
        source_claim = await repository.claim_source_item(
            source_id, "worker-a", 300, ingestion_at
        )
        assert source_claim is not None
        assert (
            await repository.claim_source_item(
                source_id,
                "worker-b",
                300,
                ingestion_at + timedelta(seconds=299),
            )
            is None
        )
        reclaimed = await repository.claim_source_item(
            source_id,
            "worker-b",
            300,
            ingestion_at + timedelta(seconds=301),
        )
        assert reclaimed is not None
        assert reclaimed.processing_owner == "worker-b"
        completed_source = await repository.update_source_status(
            source_id, ProcessingStatus.COMPLETED
        )
        assert completed_source is not None
        assert completed_source.processing_owner is None
        await repository.record_apify_success_at(ingestion_at)
        assert await repository.get_last_apify_success_at() == ingestion_at
        next_dispatch = ingestion_at + timedelta(seconds=17)
        await repository.record_next_source_dispatch_at(next_dispatch)
        assert await repository.get_next_source_dispatch_at() == next_dispatch
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

        tool_request, created = await repository.create_tool_request(
            ToolRequest(
                source_item_id=source_id,
                name="save_brief",
                idempotency_key=uuid4().hex,
            )
        )
        assert created
        tool_claim = await repository.claim_tool_request_execution(
            tool_request.id, ToolStatus.REQUESTED
        )
        assert tool_claim is not None
        assert tool_claim.status == ToolStatus.EXECUTING
        assert (
            await repository.claim_tool_request_execution(
                tool_request.id, ToolStatus.REQUESTED
            )
            is None
        )

        deferred_request, created = await repository.create_tool_request(
            ToolRequest(
                source_item_id=source_id,
                name="draft_alert",
                idempotency_key=uuid4().hex,
            )
        )
        assert created
        deferred_result = await repository.defer_tool_request_with_approval(
            deferred_request.id,
            Approval(
                id=deferred_request.id,
                source_item_id=source_id,
                action=deferred_request.name,
                idempotency_key=deferred_request.idempotency_key,
                tool_request_id=deferred_request.id,
            ),
        )
        assert deferred_result is not None
        assert deferred_result[0].status == ToolStatus.DEFERRED
        repeated_defer = await repository.defer_tool_request_with_approval(
            deferred_request.id, deferred_result[1]
        )
        assert repeated_defer is not None
        assert repeated_defer[1].id == deferred_request.id

        approval = await repository.create_approval(
            Approval(
                id=tool_request.id,
                source_item_id=source_id,
                action="save_brief",
                idempotency_key=uuid4().hex,
                tool_request_id=tool_request.id,
            )
        )
        assert (
            await repository.get_approval_for_tool_request(tool_request.id)
        ).id == approval.id
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
