from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


def utc_now() -> datetime:
    return datetime.now(UTC)


class TrustState(StrEnum):
    NORMAL = "NORMAL"
    RESTRICTED = "RESTRICTED"
    LOCKED = "LOCKED"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ScanBoundary(StrEnum):
    INGEST = "ingest"
    PROMPT = "prompt"
    RESPONSE = "response"
    TOOL_ARGUMENTS = "tool_arguments"
    TOOL_RESULT = "tool_result"


class SourceRunStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


class EventType(StrEnum):
    HEARTBEAT = "heartbeat"
    SOURCE_RUN_STARTED = "source_run_started"
    SOURCE_RUN_COMPLETED = "source_run_completed"
    CONTENT_RECEIVED = "content_received"
    SCAN_STARTED = "scan_started"
    SCAN_COMPLETED = "scan_completed"
    DETECTION = "detection"
    STATE_CHANGED = "state_changed"
    MODEL_STARTED = "model_started"
    MODEL_COMPLETED = "model_completed"
    TOOL_REQUESTED = "tool_requested"
    TOOL_BLOCKED = "tool_blocked"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_CREATED = "approval_created"
    APPROVAL_RESOLVED = "approval_resolved"
    INCIDENT_CREATED = "incident_created"
    INCIDENT_ACKNOWLEDGED = "incident_acknowledged"
    INCIDENT_RESOLVED = "incident_resolved"
    DEMO_STATE_CHANGED = "demo_state_changed"
    PERSISTENCE_COMPLETED = "persistence_completed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    APPROVED = "approved"
    DENIED = "denied"
    FAILED = "failed"


class ToolStatus(StrEnum):
    REQUESTED = "requested"
    DEFERRED = "deferred"
    EXECUTING = "executing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    DENIED = "denied"
    FAILED = "failed"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class SourceItem(BaseModel):
    id: str
    title: str
    text: str = ""
    url: HttpUrl | None = None
    author: str | None = None
    score: int | None = None
    comment_count: int | None = None
    comments: list[str] = Field(default_factory=list)
    source: str = "hackernews"
    received_at: datetime = Field(default_factory=utc_now)
    simulated: bool = False
    run_id: str | None = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    failure_reason: str | None = None


class SourceRun(BaseModel):
    id: str
    actor_name: str
    status: SourceRunStatus = SourceRunStatus.STARTING
    dataset_id: str | None = None
    fallback_for_run_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    item_count: int = 0
    attempt: int = 1
    failure_reason: str | None = None


class ScanResult(BaseModel):
    boundary: ScanBoundary | str
    detected: bool = False
    threat_level: str = "None"
    action: str = "Allow"
    detectors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    provider_status: str = "completed"
    source_item_id: str | None = None
    parent_scan_id: str | None = None


class ScanRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    boundary: ScanBoundary
    detected: bool
    threat_level: str
    action: str
    detectors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    provider_status: str = "completed"
    parent_scan_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TriageResult(BaseModel):
    summary: str
    category: str
    priority: str
    sentiment: str
    topics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    repositories: list[str] = Field(default_factory=list)
    cves: list[str] = Field(default_factory=list)
    recommended_action: Literal[
        "save_brief",
        "draft_alert",
        "quarantine_item",
        "mock_web_fetch",
    ] = "save_brief"
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class TowerEvent(BaseModel):
    schema_version: int = 1
    id: int | None = None
    type: EventType
    source_item_id: str | None = None
    run_id: str | None = None
    entity_id: str | None = None
    correlation_id: str | None = None
    trust_state: TrustState | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    idempotency_key: str | None = None
    tool_request_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    severity: str
    summary: str
    status: IncidentStatus = IncidentStatus.OPEN
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TrustTransition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str | None = None
    from_state: TrustState
    to_state: TrustState
    reason: str
    created_at: datetime = Field(default_factory=utc_now)


class TaintRecord(BaseModel):
    source_item_id: str
    reason: str
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolution: str | None = None


class ToolRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    status: ToolStatus = ToolStatus.REQUESTED
    result: dict[str, Any] | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class Brief(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    title: str
    summary: str
    read: bool = False
    resolved: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class MockAlert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    subject: str
    body: str
    status: str = "draft"
    read: bool = False
    resolved: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class QuarantineRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    reason: str
    tool_request_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Watchlist(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    search_terms: list[str] = Field(default_factory=list)
    companies_products: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    minimum_priority: str = "normal"
    sentiment: str | None = None
    minimum_engagement: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WatchlistMatch(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    watchlist_id: str
    source_item_id: str
    evidence: list[str] = Field(default_factory=list)
    rationale: str
    created_at: datetime = Field(default_factory=utc_now)


class QueryHistory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    topic: str | None = None
    window_hours: int = 168
    evidence_count: int = 0
    response: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TrendSignal(BaseModel):
    topic: str
    current_mentions: int
    previous_mentions: int
    mention_delta: int
    current_engagement: int
    previous_engagement: int
    engagement_delta: int
    sentiment_delta: float
    evidence_count: int
    confidence: str
    citations: list[str] = Field(default_factory=list)
