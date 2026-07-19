import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.demo import DemoService
from app.events import EventHub
from app.models import (
    Approval,
    ApprovalStatus,
    Brief,
    EventType,
    Incident,
    IncidentStatus,
    MockAlert,
    ProcessingStatus,
    ScanBoundary,
    ScanRecord,
    SourceItem,
    TaintRecord,
    ToolRequest,
    TowerEvent,
    TrustState,
    Watchlist,
)
from app.orchestrator import Orchestrator
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


async def test_repository_persists_security_and_product_state(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:security", title="Security story")
        assert await repository.store_source_item(item)
        updated = await repository.update_source_status(item.id, ProcessingStatus.PROCESSING)
        assert updated is not None
        assert updated.processing_status == ProcessingStatus.PROCESSING

        taint = await repository.create_taint(
            TaintRecord(source_item_id=item.id, reason="prompt injection")
        )
        assert taint.active
        assert await repository.is_tainted(item.id)

        transition = await repository.transition_trust_state(
            TrustState.LOCKED, "high finding", item.id
        )
        assert transition is not None
        assert transition.from_state == TrustState.NORMAL
        assert transition.to_state == TrustState.LOCKED
        assert (
            await repository.transition_trust_state(TrustState.NORMAL, "clean", item.id)
            is None
        )

        incident = await repository.create_incident(
            Incident(source_item_id=item.id, severity="High", summary="Blocked")
        )
        acknowledged = await repository.acknowledge_incident(incident.id)
        assert acknowledged is not None
        assert acknowledged.status == IncidentStatus.ACKNOWLEDGED
        resolved = await repository.resolve_incident(incident.id, "operator resolution")
        assert resolved is not None
        assert resolved.status == IncidentStatus.RESOLVED
        assert not await repository.is_tainted(item.id)

        request = ToolRequest(
            source_item_id=item.id,
            name="save_brief",
            arguments={"summary": "Safe"},
            idempotency_key="brief:security",
        )
        created, was_created = await repository.create_tool_request(request)
        assert was_created
        duplicate, was_created = await repository.create_tool_request(request)
        assert not was_created
        assert duplicate.id == created.id

        watchlist = await repository.upsert_watchlist(
            Watchlist(name="Agent security", search_terms=["prompt injection"])
        )
        assert [saved.id for saved in await repository.list_watchlists()] == [watchlist.id]
        assert await repository.delete_watchlist(watchlist.id)

        brief = await repository.store_brief(
            Brief(source_item_id=item.id, title="Brief", summary="Summary")
        )
        updated_brief = await repository.update_brief_state(
            brief.id, read=True, resolved=True
        )
        assert updated_brief is not None
        assert updated_brief.read and updated_brief.resolved

        alert = await repository.store_mock_alert(
            MockAlert(source_item_id=item.id, subject="Alert", body="Draft")
        )
        updated_alert = await repository.update_mock_alert_state(alert.id, read=True)
        assert updated_alert is not None
        assert updated_alert.read
    finally:
        await repository.close()


async def test_source_processing_claim_is_atomic_and_expiring(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:claim", title="Claim")
        await repository.store_source_item(item)
        now = datetime(2026, 7, 19, 12, tzinfo=UTC)
        first, second = await asyncio.gather(
            repository.claim_source_item(item.id, "worker-a", 300, now),
            repository.claim_source_item(item.id, "worker-b", 300, now),
        )
        winners = [claim for claim in (first, second) if claim is not None]
        assert len(winners) == 1
        assert winners[0].processing_owner in {"worker-a", "worker-b"}

        assert (
            await repository.claim_source_item(
                item.id, "worker-c", 300, now + timedelta(seconds=299)
            )
            is None
        )
        reclaimed = await repository.claim_source_item(
            item.id, "worker-c", 300, now + timedelta(seconds=301)
        )
        assert reclaimed is not None
        assert reclaimed.processing_owner == "worker-c"

        completed = await repository.update_source_status(
            item.id, ProcessingStatus.COMPLETED
        )
        assert completed is not None
        assert completed.processing_owner is None
        assert completed.processing_started_at is None
    finally:
        await repository.close()


async def test_demo_recovery_only_resolves_current_demo_incident(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        demo_item = SourceItem(
            id="fixture:locked:cycle",
            title="Demo item",
            source="fixture",
            simulated=True,
            run_id="demo:cycle",
        )
        real_item = SourceItem(id="hn:real", title="Real incident")
        await repository.store_source_item(demo_item)
        await repository.store_source_item(real_item)
        demo_incident = await repository.create_incident(
            Incident(source_item_id=demo_item.id, severity="High", summary="Demo")
        )
        real_incident = await repository.create_incident(
            Incident(source_item_id=real_item.id, severity="High", summary="Real")
        )
        await repository.create_taint(
            TaintRecord(source_item_id=demo_item.id, reason="demo")
        )
        await repository.create_taint(
            TaintRecord(source_item_id=real_item.id, reason="real")
        )
        await repository.set_trust_state(TrustState.LOCKED)

        service = DemoService(
            Settings(environment="test", data_dir=tmp_path),
            repository,
            EventHub(repository),
            object(),
        )
        service._last_source_item_id = demo_item.id

        await service._recover_for_next_step()

        incidents = await repository.list_incidents(active_only=False)
        by_id = {incident.id: incident for incident in incidents}
        assert by_id[demo_incident.id].status == IncidentStatus.RESOLVED
        assert by_id[real_incident.id].status == IncidentStatus.OPEN
        assert by_id[real_incident.id].resolution is None
        assert not await repository.is_tainted(demo_item.id)
        assert await repository.is_tainted(real_item.id)
        assert await repository.get_trust_state() == TrustState.LOCKED
    finally:
        await repository.close()


async def test_demo_recovery_resumes_when_only_demo_risk_remains(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        demo_item = SourceItem(
            id="fixture:locked:isolated",
            title="Demo item",
            source="fixture",
            simulated=True,
            run_id="demo:isolated",
        )
        await repository.store_source_item(demo_item)
        await repository.create_taint(
            TaintRecord(source_item_id=demo_item.id, reason="demo")
        )
        await repository.create_incident(
            Incident(source_item_id=demo_item.id, severity="High", summary="Demo")
        )
        await repository.set_trust_state(TrustState.LOCKED)
        service = DemoService(
            Settings(environment="test", data_dir=tmp_path),
            repository,
            EventHub(repository),
            object(),
        )
        service._last_source_item_id = demo_item.id

        await service._recover_for_next_step()

        assert not await repository.has_active_taints()
        assert await repository.list_incidents(active_only=True) == []
        assert await repository.get_trust_state() == TrustState.NORMAL
    finally:
        await repository.close()


async def test_replayed_lock_reuses_existing_incident(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:replayed-lock", title="Locked")
        await repository.store_source_item(item)
        events = EventHub(repository)
        orchestrator = Orchestrator(
            Settings(environment="test", data_dir=tmp_path),
            repository,
            events,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )
        scan = ScanRecord(
            source_item_id=item.id,
            boundary=ScanBoundary.INGEST,
            detected=True,
            threat_level="High",
            action="Block",
        )

        await orchestrator._lock_item(item.id, scan, "during first attempt")
        await orchestrator._lock_item(item.id, scan, "during recovery")

        incidents = await repository.list_incidents(active_only=True)
        assert len(incidents) == 1
        assert incidents[0].source_item_id == item.id
        incident_events = [
            event
            for event in await repository.list_events(limit=100)
            if event.type == EventType.INCIDENT_CREATED
        ]
        assert len(incident_events) == 1
    finally:
        await repository.close()


async def test_approval_claim_is_exactly_once(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:approval", title="Approval")
        await repository.store_source_item(item)
        approval = await repository.create_approval(
            Approval(
                source_item_id=item.id,
                action="save_brief",
                idempotency_key="approval:1",
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
