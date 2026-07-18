from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.clients.hiddenlayer import HiddenLayerClient
from app.clients.nemotron import NemotronClient
from app.config import Settings, get_settings
from app.events import EventHub
from app.heartbeat import Heartbeat
from app.models import ApprovalStatus, EventType, ScanResult, SourceItem, TowerEvent, TrustState
from app.orchestrator import Orchestrator
from app.repositories import SQLiteRepository


class QueryRequest(BaseModel):
    query: str


def app_services(request: Request) -> dict[str, Any]:
    return request.app.state.services


Services = Annotated[dict[str, Any], Depends(app_services)]


def require_operator(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.requires_operator_token:
        return
    token = settings.operator_token
    if token is None or request.headers.get("x-operator-token") != token.get_secret_value():
        raise HTTPException(status_code=401, detail="An operator token is required")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    repository = SQLiteRepository(settings.resolved_sqlite_path)
    await repository.connect()
    events = EventHub(repository)
    hiddenlayer = HiddenLayerClient(settings)
    nemotron = NemotronClient(settings)
    orchestrator = Orchestrator(settings, repository, events, hiddenlayer, nemotron)

    async def heartbeat_tick() -> None:
        state = await repository.get_trust_state()
        await events.publish(TowerEvent(type=EventType.HEARTBEAT, trust_state=state))

    heartbeat = Heartbeat(settings.heartbeat_interval_seconds, heartbeat_tick)
    app.state.services = {
        "settings": settings,
        "repository": repository,
        "events": events,
        "orchestrator": orchestrator,
        "heartbeat": heartbeat,
    }
    await heartbeat.start()
    try:
        yield
    finally:
        await heartbeat.stop()
        await hiddenlayer.close()
        await repository.close()


app = FastAPI(title="Hidden Tower Defence", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(services: Services) -> dict[str, str]:
    await services["repository"].get_trust_state()
    return {"status": "ready", "database": "connected"}


@app.get("/api/state")
async def get_state(services: Services) -> dict[str, str]:
    state = await services["repository"].get_trust_state()
    return {"trust_state": state.value}


@app.get("/api/events")
async def get_events(services: Services, after_id: int = 0) -> list[dict[str, Any]]:
    return [event.model_dump(mode="json") for event in await services["events"].replay(after_id)]


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket, after_id: int = 0) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    for event in await services["events"].replay(after_id):
        await websocket.send_json(event.model_dump(mode="json"))
    try:
        async with services["events"].subscribe() as queue:
            while True:
                event = await queue.get()
                await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return


@app.get("/api/approvals")
async def list_approvals(services: Services) -> list[dict[str, Any]]:
    approvals = await services["repository"].list_approvals()
    return [approval.model_dump(mode="json") for approval in approvals]


@app.post("/api/approvals/{approval_id}/approve", dependencies=[Depends(require_operator)])
async def approve(approval_id: str, services: Services) -> dict[str, Any]:
    approval = await services["repository"].resolve_approval(approval_id, ApprovalStatus.APPROVED)
    if approval is None:
        raise HTTPException(status_code=404, detail="Pending approval not found")
    await services["events"].publish(
        TowerEvent(
            type=EventType.APPROVAL_RESOLVED,
            source_item_id=approval.source_item_id,
            payload={"approval_id": approval.id, "status": approval.status.value},
        )
    )
    return approval.model_dump(mode="json")


@app.post("/api/approvals/{approval_id}/deny", dependencies=[Depends(require_operator)])
async def deny(approval_id: str, services: Services) -> dict[str, Any]:
    approval = await services["repository"].resolve_approval(approval_id, ApprovalStatus.DENIED)
    if approval is None:
        raise HTTPException(status_code=404, detail="Pending approval not found")
    await services["events"].publish(
        TowerEvent(
            type=EventType.APPROVAL_RESOLVED,
            source_item_id=approval.source_item_id,
            payload={"approval_id": approval.id, "status": approval.status.value},
        )
    )
    return approval.model_dump(mode="json")


@app.post("/api/state/resume", dependencies=[Depends(require_operator)])
async def resume(services: Services) -> dict[str, str]:
    await services["repository"].set_trust_state(TrustState.NORMAL)
    await services["events"].publish(
        TowerEvent(type=EventType.STATE_CHANGED, trust_state=TrustState.NORMAL, payload={"reason": "operator_resume"})
    )
    return {"trust_state": TrustState.NORMAL.value}


@app.post("/api/heartbeat/run", dependencies=[Depends(require_operator)])
async def run_heartbeat(services: Services) -> dict[str, bool]:
    return {"started": await services["heartbeat"].trigger()}


def load_fixtures() -> list[dict[str, Any]]:
    path = Path(__file__).parents[1] / "fixtures" / "attack_feed.json"
    return json.loads(path.read_text())


@app.get("/api/demo/fixtures")
async def list_fixtures() -> list[dict[str, str]]:
    return [{"id": fixture["id"], "risk": fixture["risk"], "title": fixture["title"]} for fixture in load_fixtures()]


@app.post("/api/demo/fixtures/{fixture_id}/inject", dependencies=[Depends(require_operator)])
async def inject_fixture(fixture_id: str, services: Services) -> dict[str, bool]:
    fixture = next((item for item in load_fixtures() if item["id"] == fixture_id), None)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    item = SourceItem(
        id=f"fixture:{fixture['id']}",
        title=fixture["title"],
        text=fixture["text"],
        comments=fixture["comments"],
        source="fixture",
        simulated=True,
    )
    simulated_scan = {
        "clean": ScanResult(boundary="ingest"),
        "restricted": ScanResult(
            boundary="ingest",
            detected=True,
            threat_level="Medium",
            action="Alert",
            raw={"simulated": True},
        ),
        "locked": ScanResult(
            boundary="ingest",
            detected=True,
            threat_level="High",
            action="Block",
            raw={"simulated": True},
        ),
    }[fixture["risk"]]
    await services["orchestrator"].process(item, intake_override=simulated_scan)
    return {"accepted": True}


@app.post("/api/intelligence/query")
async def intelligence_query(payload: QueryRequest, services: Services) -> dict[str, Any]:
    events = await services["events"].replay()
    relevant = [
        event
        for event in events
        if event.type in {EventType.MODEL_COMPLETED, EventType.CONTENT_RECEIVED}
    ][-10:]
    return {
        "scope": "Hacker News developer-community signals",
        "query": payload.query,
        "evidence_count": len(relevant),
        "evidence": [event.model_dump(mode="json") for event in relevant],
        "answer": "Trend aggregation is awaiting sufficient enriched source history.",
    }


def run() -> None:
    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)
