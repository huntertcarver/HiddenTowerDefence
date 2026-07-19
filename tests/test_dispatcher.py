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
