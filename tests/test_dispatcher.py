import asyncio
from pathlib import Path

from app.agent.dispatcher import ToolDispatcher
from app.agent.tools import ControlledTools
from app.events import EventHub
from app.models import (
    ScanBoundary,
    ScanResult,
    SourceItem,
    ToolRequest,
    ToolStatus,
    TrustState,
)
from app.repositories import SQLiteRepository
from app.security import SecurityScanner


class FailingHiddenLayerClient:
    async def scan(self, boundary: str, content: str) -> ScanResult:
        raise AssertionError("dispatcher test should use simulated scan results")


async def test_stale_tool_request_is_replayed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:stale", title="Stale tool request")
        await repository.store_source_item(item)
        arguments = {"title": item.title, "summary": "Recovered after restart"}
        idempotency_key = ToolDispatcher._idempotency_key(item.id, "save_brief", arguments)
        stale, created = await repository.create_tool_request(
            ToolRequest(
                source_item_id=item.id,
                name="save_brief",
                arguments=arguments,
                idempotency_key=idempotency_key,
            )
        )
        assert created
        assert stale.status == ToolStatus.REQUESTED

        events = EventHub(repository)
        scanner = SecurityScanner(repository, events, FailingHiddenLayerClient())
        dispatcher = ToolDispatcher(
            repository,
            events,
            scanner,
            ControlledTools(repository),
        )
        completed = await dispatcher.request(
            item.id,
            "save_brief",
            arguments,
            TrustState.NORMAL,
            argument_scan_override=ScanResult(boundary=ScanBoundary.TOOL_ARGUMENTS),
            result_scan_override=ScanResult(boundary=ScanBoundary.TOOL_RESULT),
        )

        assert completed.id == stale.id
        assert completed.status == ToolStatus.COMPLETED
        assert len(await repository.list_briefs()) == 1
    finally:
        await repository.close()


async def test_executing_tool_request_is_not_replayed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:executing", title="Executing tool request")
        await repository.store_source_item(item)
        arguments = {"title": item.title, "summary": "Already running"}
        idempotency_key = ToolDispatcher._idempotency_key(item.id, "save_brief", arguments)
        request, created = await repository.create_tool_request(
            ToolRequest(
                source_item_id=item.id,
                name="save_brief",
                arguments=arguments,
                idempotency_key=idempotency_key,
            )
        )
        assert created
        executing = await repository.update_tool_request(request.id, ToolStatus.EXECUTING)
        assert executing is not None

        events = EventHub(repository)
        scanner = SecurityScanner(repository, events, FailingHiddenLayerClient())
        dispatcher = ToolDispatcher(
            repository,
            events,
            scanner,
            ControlledTools(repository),
        )
        returned = await dispatcher.request(
            item.id,
            "save_brief",
            arguments,
            TrustState.NORMAL,
        )

        assert returned.id == request.id
        assert returned.status == ToolStatus.EXECUTING
        assert len(await repository.list_briefs()) == 0
    finally:
        await repository.close()


async def test_failed_tool_request_is_not_replayed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:failed", title="Failed tool request")
        await repository.store_source_item(item)
        arguments = {"title": item.title, "summary": "Do not retry"}
        request, _ = await repository.create_tool_request(
            ToolRequest(
                source_item_id=item.id,
                name="save_brief",
                arguments=arguments,
                idempotency_key=ToolDispatcher._idempotency_key(
                    item.id, "save_brief", arguments
                ),
            )
        )
        failed = await repository.update_tool_request(
            request.id, ToolStatus.FAILED, failure_reason="ambiguous side effect"
        )
        assert failed is not None
        dispatcher = ToolDispatcher(
            repository,
            EventHub(repository),
            SecurityScanner(
                repository, EventHub(repository), FailingHiddenLayerClient()
            ),
            ControlledTools(repository),
        )

        returned = await dispatcher.request(
            item.id, "save_brief", arguments, TrustState.NORMAL
        )

        assert returned.status == ToolStatus.FAILED
        assert await repository.list_briefs() == []
    finally:
        await repository.close()


async def test_tool_execution_claim_has_one_winner(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        request, _ = await repository.create_tool_request(
            ToolRequest(
                source_item_id="hn:claim-tool",
                name="save_brief",
                idempotency_key="claim-tool",
            )
        )
        first, second = await asyncio.gather(
            repository.claim_tool_request_execution(
                request.id, ToolStatus.REQUESTED
            ),
            repository.claim_tool_request_execution(
                request.id, ToolStatus.REQUESTED
            ),
        )
        assert len([claim for claim in (first, second) if claim is not None]) == 1
    finally:
        await repository.close()


async def test_deferred_retry_reuses_single_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        item = SourceItem(id="hn:defer", title="Deferred request")
        await repository.store_source_item(item)
        arguments = {"title": item.title, "summary": "Requires approval"}
        events = EventHub(repository)
        dispatcher = ToolDispatcher(
            repository,
            events,
            SecurityScanner(repository, events, FailingHiddenLayerClient()),
            ControlledTools(repository),
        )
        for _ in range(2):
            deferred = await dispatcher.request(
                item.id,
                "save_brief",
                arguments,
                TrustState.RESTRICTED,
                argument_scan_override=ScanResult(
                    boundary=ScanBoundary.TOOL_ARGUMENTS
                ),
            )
            assert deferred.status == ToolStatus.DEFERRED

        approvals = await repository.list_approvals()
        assert len(approvals) == 1
        assert approvals[0].id == deferred.id
        approval_events = [
            event
            for event in await repository.list_events(limit=100)
            if event.type.value == "approval_created"
        ]
        assert len(approval_events) == 1
    finally:
        await repository.close()


async def test_controlled_tool_side_effect_id_is_deterministic(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "tower.db")
    await repository.connect()
    try:
        tools = ControlledTools(repository)
        for _ in range(2):
            result = await tools.execute(
                "hn:deterministic",
                "save_brief",
                {"title": "Brief", "summary": "Same operation"},
                operation_id="tool-operation-id",
            )
            assert result["brief_id"] == "tool-operation-id"
        briefs = await repository.list_briefs()
        assert len(briefs) == 1
        assert briefs[0].id == "tool-operation-id"
    finally:
        await repository.close()
