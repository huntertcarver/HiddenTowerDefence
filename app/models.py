from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


def utc_now() -> datetime:
    return datetime.now(UTC)


class TrustState(StrEnum):
    NORMAL = "NORMAL"
    RESTRICTED = "RESTRICTED"
    LOCKED = "LOCKED"


class EventType(StrEnum):
    HEARTBEAT = "heartbeat"
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


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


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


class ScanResult(BaseModel):
    boundary: str
    detected: bool = False
    threat_level: str = "None"
    action: str = "Allow"
    detectors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class TriageResult(BaseModel):
    summary: str
    category: str
    priority: str
    sentiment: str
    topics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    recommended_action: str = "save_brief"
    rationale: str = ""


class TowerEvent(BaseModel):
    id: int | None = None
    type: EventType
    source_item_id: str | None = None
    trust_state: TrustState | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_item_id: str
    severity: str
    summary: str
    acknowledged_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
