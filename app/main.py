from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.dispatcher import ToolDispatcher
from app.agent.tools import ControlledTools
from app.auth import COOKIE_NAME, OperatorSessionManager
from app.clients.apify import ApifyClient
from app.clients.hiddenlayer import HiddenLayerClient
from app.clients.nemotron import NemotronClient
from app.config import get_settings
from app.demo import DemoService
from app.events import EventHub
from app.heartbeat import Heartbeat
from app.intelligence import IntelligenceService
from app.models import EventType, TowerEvent, TrustState, Watchlist
from app.orchestrator import Orchestrator
from app.repositories import SpannerRepository, SQLiteRepository
from app.security import SecurityScanner
from app.sources.apify_source import ApifyScheduler, ApifySource

logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class IncidentResolutionRequest(BaseModel):
    resolution: str = Field(min_length=3, max_length=500)


class WatchlistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    search_terms: list[str] = Field(default_factory=list)
    companies_products: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    minimum_priority: str = "normal"
    sentiment: str | None = None
    minimum_engagement: int | None = Field(default=None, ge=0)


class InboxStateRequest(BaseModel):
    read: bool | None = None
    resolved: bool | None = None


def app_services(request: Request) -> dict[str, Any]:
    return request.app.state.services


Services = Annotated[dict[str, Any], Depends(app_services)]


def require_operator(request: Request, services: Services) -> None:
    services["auth"].require_mutation(request)


def require_operator_session(request: Request, services: Services) -> None:
    services["auth"].verify(request.cookies.get(COOKIE_NAME))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_backend == "spanner":
        if settings.spanner_project_id is None:
            raise RuntimeError("spanner_project_id is required when database_backend is spanner")
        repository = SpannerRepository(
            settings.spanner_project_id,
            settings.spanner_instance_id,
            settings.spanner_database_id,
        )
    else:
        repository = SQLiteRepository(settings.resolved_sqlite_path)
    await repository.connect()
    events = EventHub(repository)
    apify = ApifyClient(settings)
    hiddenlayer = HiddenLayerClient(settings)
    nemotron = NemotronClient(settings)
    scanner = SecurityScanner(repository, events, hiddenlayer)
    tools = ControlledTools(repository)
    dispatcher = ToolDispatcher(repository, events, scanner, tools)
    intelligence = IntelligenceService(repository, nemotron)
    orchestrator = Orchestrator(
        settings,
        repository,
        events,
        scanner,
        nemotron,
        dispatcher,
        intelligence,
    )
    apify_source = ApifySource(
        settings,
        repository,
        events,
        apify,
        orchestrator.process,
    )
    apify_scheduler = ApifyScheduler(settings, repository, apify_source)
    demo = DemoService(settings, repository, events, orchestrator)
    auth = OperatorSessionManager(settings)
    lease_owner = uuid4().hex

    async def heartbeat_tick() -> None:
        if not await repository.acquire_lease(
            "heartbeat", lease_owner, settings.heartbeat_lease_seconds
        ):
            return
        state = await repository.get_trust_state()
        await events.publish(
            TowerEvent(
                type=EventType.HEARTBEAT,
                trust_state=state,
                payload={"lease_owner": lease_owner[:8]},
            )
        )
        for pending_item in await repository.list_pending_source_items(limit=50):
            await orchestrator.process(pending_item)
        if settings.environment == "test":
            return
        if settings.apify_api_token is None:
            return
        try:
            await apify_scheduler.run_if_due()
        except Exception:
            logger.exception("Apify ingestion failed")

    heartbeat = Heartbeat(settings.heartbeat_interval_seconds, heartbeat_tick)
    app.state.services = {
        "settings": settings,
        "repository": repository,
        "events": events,
        "orchestrator": orchestrator,
        "heartbeat": heartbeat,
        "apify": apify,
        "apify_source": apify_source,
        "apify_scheduler": apify_scheduler,
        "scanner": scanner,
        "dispatcher": dispatcher,
        "intelligence": intelligence,
        "demo": demo,
        "auth": auth,
    }
    if await repository.acquire_lease(
        "pending_replay", lease_owner, settings.heartbeat_lease_seconds
    ):
        for pending_item in await repository.list_pending_source_items(limit=50):
            await orchestrator.process(pending_item)
    await heartbeat.start()
    try:
        yield
    finally:
        await demo.stop()
        await heartbeat.stop()
        await apify.close()
        await hiddenlayer.close()
        await repository.close()


app = FastAPI(title="Hidden Tower Defence", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
@app.get("/health")
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
async def get_events(
    services: Services, after_id: int = 0, limit: int = 200
) -> list[dict[str, Any]]:
    return [
        event.model_dump(mode="json")
        for event in await services["events"].replay(after_id, min(max(limit, 1), 500))
    ]


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket, after_id: int = 0) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    try:
        async with services["events"].subscribe() as queue:
            last_sent = max(after_id, 0)
            for event in await services["events"].replay(last_sent, 500):
                await websocket.send_json(event.model_dump(mode="json"))
                last_sent = event.id or last_sent
            while True:
                event = await queue.get()
                if event.id is not None and event.id <= last_sent:
                    continue
                await websocket.send_json(event.model_dump(mode="json"))
                last_sent = event.id or last_sent
    except WebSocketDisconnect:
        return


@app.get("/api/scene")
async def get_scene(services: Services) -> dict[str, Any]:
    state = await services["repository"].get_trust_state()
    approvals = await services["repository"].list_approvals()
    incidents = await services["repository"].list_incidents(active_only=True)
    active_items = await services["repository"].list_pending_source_items(limit=100)
    return {
        "schema_version": 1,
        "cursor": await services["repository"].latest_event_id(),
        "trust_state": state.value,
        "approvals": [approval.model_dump(mode="json") for approval in approvals],
        "incidents": [incident.model_dump(mode="json") for incident in incidents],
        "active_items": [item.model_dump(mode="json") for item in active_items],
        "demo": await services["demo"].state(),
    }


@app.get("/api/approvals")
async def list_approvals(services: Services) -> list[dict[str, Any]]:
    approvals = await services["repository"].list_approvals()
    return [approval.model_dump(mode="json") for approval in approvals]


@app.post("/api/operator/login")
async def operator_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    services: Services,
) -> dict[str, Any]:
    auth = services["auth"]
    auth.login_limiter.check(request.client.host if request.client else "unknown")
    cookie, session = auth.login(payload.token)
    auth.set_cookie(response, cookie)
    return {
        "authenticated": True,
        "expires_at": session.expires_at,
        "csrf_token": session.csrf_token,
    }


@app.get("/api/operator/session")
async def operator_session(request: Request, services: Services) -> dict[str, Any]:
    session = services["auth"].verify(request.cookies.get(COOKIE_NAME))
    return {
        "authenticated": True,
        "expires_at": session.expires_at,
        "csrf_token": session.csrf_token,
    }


@app.post("/api/operator/logout")
async def operator_logout(
    response: Response,
    _: None = Depends(require_operator),
) -> dict[str, bool]:
    OperatorSessionManager.clear_cookie(response)
    return {"authenticated": False}


@app.post("/api/approvals/{approval_id}/approve", dependencies=[Depends(require_operator)])
async def approve(approval_id: str, services: Services) -> dict[str, Any]:
    approval = await services["dispatcher"].approve(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Pending approval not found")
    return approval.model_dump(mode="json")


@app.post("/api/approvals/{approval_id}/deny", dependencies=[Depends(require_operator)])
async def deny(approval_id: str, services: Services) -> dict[str, Any]:
    approval = await services["dispatcher"].deny(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Pending approval not found")
    return approval.model_dump(mode="json")


@app.post("/api/state/resume", dependencies=[Depends(require_operator)])
async def resume(services: Services) -> dict[str, str]:
    repository = services["repository"]
    current = await repository.get_trust_state()
    if current == TrustState.LOCKED:
        raise HTTPException(
            status_code=409,
            detail="Acknowledge the locked incident before resuming",
        )
    if await repository.has_active_taints():
        raise HTTPException(
            status_code=409,
            detail="Resolve active incidents and taint before resuming",
        )
    transition = await repository.transition_trust_state(
        TrustState.NORMAL,
        "operator_resume",
        allow_deescalation=True,
    )
    if transition is None and current != TrustState.NORMAL:
        raise HTTPException(status_code=409, detail="Trust state cannot be resumed")
    await services["events"].publish(
        TowerEvent(
            type=EventType.STATE_CHANGED,
            trust_state=TrustState.NORMAL,
            payload={
                "from": current.value,
                "to": TrustState.NORMAL.value,
                "reason": "operator_resume",
            },
        )
    )
    return {"trust_state": TrustState.NORMAL.value}


@app.get("/api/incidents")
async def list_incidents(services: Services) -> list[dict[str, Any]]:
    incidents = await services["repository"].list_incidents(active_only=False)
    return [incident.model_dump(mode="json") for incident in incidents]


@app.post(
    "/api/incidents/{incident_id}/acknowledge",
    dependencies=[Depends(require_operator)],
)
async def acknowledge_incident(incident_id: str, services: Services) -> dict[str, Any]:
    incident = await services["repository"].acknowledge_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Open incident not found")
    transition = await services["repository"].transition_trust_state(
        TrustState.RESTRICTED,
        "operator_acknowledge",
        incident.source_item_id,
        allow_deescalation=True,
    )
    await services["events"].publish(
        TowerEvent(
            type=EventType.INCIDENT_ACKNOWLEDGED,
            source_item_id=incident.source_item_id,
            entity_id=incident.source_item_id,
            trust_state=TrustState.RESTRICTED,
            payload={
                "incident_id": incident.id,
                "transition_id": transition.id if transition else None,
            },
        )
    )
    return incident.model_dump(mode="json")


@app.post(
    "/api/incidents/{incident_id}/resolve",
    dependencies=[Depends(require_operator)],
)
async def resolve_incident(
    incident_id: str,
    payload: IncidentResolutionRequest,
    services: Services,
) -> dict[str, Any]:
    incident = await services["repository"].resolve_incident(
        incident_id, payload.resolution
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Active incident not found")
    await services["events"].publish(
        TowerEvent(
            type=EventType.INCIDENT_RESOLVED,
            source_item_id=incident.source_item_id,
            entity_id=incident.source_item_id,
            payload={"incident_id": incident.id, "resolution": payload.resolution},
        )
    )
    return incident.model_dump(mode="json")


@app.post("/api/heartbeat/run", dependencies=[Depends(require_operator)])
async def run_heartbeat(services: Services) -> dict[str, bool]:
    return {"started": await services["heartbeat"].trigger()}


@app.get("/api/demo/fixtures")
async def list_fixtures(services: Services) -> list[dict[str, str]]:
    return services["demo"].list_fixtures()


@app.post("/api/demo/fixtures/{fixture_id}/inject", dependencies=[Depends(require_operator)])
async def inject_fixture(fixture_id: str, services: Services) -> dict[str, bool]:
    if not await services["demo"].inject(fixture_id):
        raise HTTPException(status_code=404, detail="Fixture not found")
    return {"accepted": True}


@app.get("/api/demo/state")
async def demo_state(services: Services) -> dict[str, Any]:
    return await services["demo"].state()


@app.post("/api/demo/start", dependencies=[Depends(require_operator)])
async def start_demo(services: Services) -> dict[str, Any]:
    return await services["demo"].start()


@app.post("/api/demo/stop", dependencies=[Depends(require_operator)])
async def stop_demo(services: Services) -> dict[str, Any]:
    return await services["demo"].stop()


@app.post("/api/demo/reset", dependencies=[Depends(require_operator)])
async def reset_demo(services: Services) -> dict[str, Any]:
    return await services["demo"].reset()


@app.post("/api/intelligence/query")
async def intelligence_query(payload: QueryRequest, services: Services) -> dict[str, Any]:
    return await services["intelligence"].query(payload.query)


@app.get("/api/query-history")
async def query_history(services: Services) -> list[dict[str, Any]]:
    history = await services["repository"].list_query_history()
    return [entry.model_dump(mode="json") for entry in history]


@app.get("/api/watchlists")
async def list_watchlists(services: Services) -> list[dict[str, Any]]:
    watchlists = await services["repository"].list_watchlists()
    return [watchlist.model_dump(mode="json") for watchlist in watchlists]


@app.post("/api/watchlists", dependencies=[Depends(require_operator)])
async def create_watchlist(
    payload: WatchlistRequest, services: Services
) -> dict[str, Any]:
    watchlist = await services["repository"].upsert_watchlist(
        Watchlist(**payload.model_dump())
    )
    return watchlist.model_dump(mode="json")


@app.put("/api/watchlists/{watchlist_id}", dependencies=[Depends(require_operator)])
async def update_watchlist(
    watchlist_id: str,
    payload: WatchlistRequest,
    services: Services,
) -> dict[str, Any]:
    existing = {
        watchlist.id: watchlist
        for watchlist in await services["repository"].list_watchlists()
    }.get(watchlist_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    watchlist = await services["repository"].upsert_watchlist(
        existing.model_copy(update=payload.model_dump())
    )
    return watchlist.model_dump(mode="json")


@app.delete("/api/watchlists/{watchlist_id}", dependencies=[Depends(require_operator)])
async def delete_watchlist(watchlist_id: str, services: Services) -> dict[str, bool]:
    if not await services["repository"].delete_watchlist(watchlist_id):
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {"deleted": True}


@app.get("/api/watchlist-matches")
async def watchlist_matches(
    services: Services, watchlist_id: str | None = None
) -> list[dict[str, Any]]:
    matches = await services["repository"].list_watchlist_matches(watchlist_id)
    return [match.model_dump(mode="json") for match in matches]


@app.get("/api/briefs")
async def list_briefs(services: Services) -> list[dict[str, Any]]:
    briefs = await services["repository"].list_briefs()
    return [brief.model_dump(mode="json") for brief in briefs]


@app.patch("/api/briefs/{brief_id}", dependencies=[Depends(require_operator)])
async def update_brief(
    brief_id: str, payload: InboxStateRequest, services: Services
) -> dict[str, Any]:
    brief = await services["repository"].update_brief_state(
        brief_id, read=payload.read, resolved=payload.resolved
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    return brief.model_dump(mode="json")


@app.get("/api/mock-alerts")
async def list_mock_alerts(services: Services) -> list[dict[str, Any]]:
    alerts = await services["repository"].list_mock_alerts()
    return [alert.model_dump(mode="json") for alert in alerts]


@app.patch("/api/mock-alerts/{alert_id}", dependencies=[Depends(require_operator)])
async def update_mock_alert(
    alert_id: str, payload: InboxStateRequest, services: Services
) -> dict[str, Any]:
    alert = await services["repository"].update_mock_alert_state(
        alert_id, read=payload.read, resolved=payload.resolved
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Mock alert not found")
    return alert.model_dump(mode="json")


@app.get("/api/quarantines")
async def list_quarantines(services: Services) -> list[dict[str, Any]]:
    records = await services["repository"].list_quarantines()
    return [record.model_dump(mode="json") for record in records]


@app.get("/api/trends")
async def trends(
    services: Services,
    query: str = "developer trends over the last 7 days",
) -> dict[str, Any]:
    return await services["intelligence"].query(query)


@app.get("/api/evidence/{source_item_id}")
async def source_evidence(source_item_id: str, services: Services) -> dict[str, Any]:
    item = await services["repository"].get_source_item(source_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Source evidence not found")
    scans = await services["repository"].list_scans(source_item_id, include_raw=False)
    return {
        "source": item.model_dump(mode="json"),
        "scans": scans,
        "scope": IntelligenceService.SCOPE,
    }


@app.get(
    "/api/evidence/{source_item_id}/raw-scans",
    dependencies=[Depends(require_operator_session)],
)
async def raw_scan_evidence(
    source_item_id: str, services: Services
) -> list[dict[str, Any]]:
    return await services["repository"].list_scans(source_item_id, include_raw=True)


def run() -> None:
    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)
