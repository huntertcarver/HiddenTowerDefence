from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import aiosqlite
from google.cloud import spanner

from app.database import apply_sqlite_migrations
from app.models import (
    Approval,
    ApprovalStatus,
    Brief,
    Incident,
    IncidentStatus,
    MockAlert,
    ProcessingStatus,
    QuarantineRecord,
    QueryHistory,
    ScanRecord,
    SourceItem,
    SourceRun,
    SourceRunStatus,
    TaintRecord,
    ToolRequest,
    ToolStatus,
    TowerEvent,
    TriageResult,
    TrustState,
    TrustTransition,
    Watchlist,
    WatchlistMatch,
)


class Repository(Protocol):
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def store_source_item(self, item: SourceItem) -> bool: ...

    async def get_source_item(self, source_item_id: str) -> SourceItem | None: ...

    async def get_trust_state(self) -> TrustState: ...

    async def set_trust_state(self, state: TrustState) -> None: ...

    async def append_event(self, event: TowerEvent) -> TowerEvent: ...

    async def list_events(self, after_id: int = 0, limit: int = 200) -> list[TowerEvent]: ...


class SQLiteRepository:
    """Local and test persistence compatible with the production repository contract."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        await apply_sqlite_migrations(self._connection)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Repository is not connected")
        return self._connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                yield
            except BaseException:
                await self.connection.rollback()
                raise
            else:
                await self.connection.commit()

    async def store_source_item(self, item: SourceItem) -> bool:
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                INSERT OR IGNORE INTO source_items (id, payload, created_at)
                VALUES (?, ?, ?)
                """,
                (item.id, item.model_dump_json(), item.received_at.isoformat()),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                for position, comment in enumerate(item.comments):
                    await self.connection.execute(
                        """
                        INSERT INTO source_comments (id, source_item_id, position, payload)
                        VALUES (?, ?, ?, ?)
                        """,
                        (f"{item.id}:comment:{position}", item.id, position, json.dumps(comment)),
                    )
            return inserted

    async def get_source_item(self, source_item_id: str) -> SourceItem | None:
        cursor = await self.connection.execute(
            "SELECT payload FROM source_items WHERE id = ?", (source_item_id,)
        )
        row = await cursor.fetchone()
        return SourceItem.model_validate_json(row["payload"]) if row else None

    async def update_source_status(
        self,
        source_item_id: str,
        status: ProcessingStatus,
        failure_reason: str | None = None,
    ) -> SourceItem | None:
        async with self.transaction():
            cursor = await self.connection.execute(
                "SELECT payload FROM source_items WHERE id = ?", (source_item_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            item = SourceItem.model_validate_json(row["payload"])
            updated = item.model_copy(
                update={"processing_status": status, "failure_reason": failure_reason}
            )
            await self.connection.execute(
                "UPDATE source_items SET payload = ? WHERE id = ?",
                (updated.model_dump_json(), source_item_id),
            )
            return updated

    async def list_pending_source_items(self, limit: int = 50) -> list[SourceItem]:
        cursor = await self.connection.execute(
            "SELECT payload FROM source_items ORDER BY created_at ASC"
        )
        items = [
            SourceItem.model_validate_json(row["payload"]) for row in await cursor.fetchall()
        ]
        return [
            item
            for item in items
            if item.processing_status.value in {"pending", "processing"}
        ][: min(max(limit, 1), 200)]

    async def set_trust_state(self, state: TrustState) -> None:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO runtime_state (state_key, state_value)
                VALUES ('trust_state', ?)
                ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
                """,
                (state.value,),
            )

    async def get_trust_state(self) -> TrustState:
        cursor = await self.connection.execute(
            "SELECT state_value FROM runtime_state WHERE state_key = 'trust_state'"
        )
        row = await cursor.fetchone()
        return TrustState(row["state_value"]) if row else TrustState.NORMAL

    async def transition_trust_state(
        self,
        to_state: TrustState,
        reason: str,
        source_item_id: str | None = None,
        *,
        allow_deescalation: bool = False,
    ) -> TrustTransition | None:
        ranks = {TrustState.NORMAL: 0, TrustState.RESTRICTED: 1, TrustState.LOCKED: 2}
        async with self.transaction():
            cursor = await self.connection.execute(
                "SELECT state_value FROM runtime_state WHERE state_key = 'trust_state'"
            )
            row = await cursor.fetchone()
            current = TrustState(row["state_value"]) if row else TrustState.NORMAL
            if current == to_state:
                return None
            if not allow_deescalation and ranks[to_state] < ranks[current]:
                return None
            transition = TrustTransition(
                source_item_id=source_item_id,
                from_state=current,
                to_state=to_state,
                reason=reason,
            )
            await self.connection.execute(
                """
                INSERT INTO runtime_state (state_key, state_value)
                VALUES ('trust_state', ?)
                ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
                """,
                (to_state.value,),
            )
            await self.connection.execute(
                """
                INSERT INTO trust_transitions
                (id, source_item_id, from_state, to_state, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.id,
                    transition.source_item_id,
                    transition.from_state.value,
                    transition.to_state.value,
                    transition.reason,
                    transition.created_at.isoformat(),
                ),
            )
            return transition

    async def create_taint(self, taint: TaintRecord) -> TaintRecord:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO taints
                (source_item_id, active, payload, created_at, resolved_at)
                VALUES (?, 1, ?, ?, NULL)
                ON CONFLICT(source_item_id) DO UPDATE SET
                  active = 1,
                  payload = excluded.payload,
                  resolved_at = NULL
                """,
                (
                    taint.source_item_id,
                    taint.model_dump_json(),
                    taint.created_at.isoformat(),
                ),
            )
        return taint

    async def is_tainted(self, source_item_id: str) -> bool:
        cursor = await self.connection.execute(
            "SELECT active FROM taints WHERE source_item_id = ?", (source_item_id,)
        )
        row = await cursor.fetchone()
        return bool(row["active"]) if row else False

    async def resolve_taint(self, source_item_id: str, resolution: str) -> bool:
        now = datetime.now(UTC)
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                SELECT payload FROM taints
                WHERE source_item_id = ? AND active = 1
                """,
                (source_item_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            taint = TaintRecord.model_validate_json(row["payload"]).model_copy(
                update={"active": False, "resolved_at": now, "resolution": resolution}
            )
            await self.connection.execute(
                """
                UPDATE taints SET active = 0, payload = ?, resolved_at = ?
                WHERE source_item_id = ?
                """,
                (taint.model_dump_json(), now.isoformat(), source_item_id),
            )
            return True

    async def has_active_taints(self) -> bool:
        cursor = await self.connection.execute(
            "SELECT 1 FROM taints WHERE active = 1 LIMIT 1"
        )
        return await cursor.fetchone() is not None

    async def acquire_lease(self, name: str, owner_id: str, ttl_seconds: int) -> bool:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=max(ttl_seconds, 1))
        async with self.transaction():
            cursor = await self.connection.execute(
                "SELECT owner_id, expires_at FROM heartbeat_leases WHERE name = ?", (name,)
            )
            row = await cursor.fetchone()
            if row and row["owner_id"] != owner_id:
                existing_expiry = datetime.fromisoformat(row["expires_at"])
                if existing_expiry > now:
                    return False
            await self.connection.execute(
                """
                INSERT INTO heartbeat_leases (name, owner_id, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  owner_id = excluded.owner_id,
                  expires_at = excluded.expires_at
                """,
                (name, owner_id, expires_at.isoformat()),
            )
            return True

    async def append_event(self, event: TowerEvent) -> TowerEvent:
        stored_payload = {
            **event.payload,
            "_schema_version": event.schema_version,
            "_run_id": event.run_id,
            "_entity_id": event.entity_id,
            "_correlation_id": event.correlation_id,
        }
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                INSERT INTO events (event_type, source_item_id, trust_state, payload, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.type.value,
                    event.source_item_id,
                    event.trust_state.value if event.trust_state else None,
                    json.dumps(stored_payload, sort_keys=True),
                    event.occurred_at.isoformat(),
                ),
            )
            return event.model_copy(update={"id": cursor.lastrowid})

    async def list_events(self, after_id: int = 0, limit: int = 200) -> list[TowerEvent]:
        cursor = await self.connection.execute(
            """
            SELECT id, event_type, source_item_id, trust_state, payload, occurred_at
            FROM events WHERE id > ? ORDER BY id ASC LIMIT ?
            """,
            (max(after_id, 0), min(max(limit, 1), 500)),
        )
        rows = await cursor.fetchall()
        events: list[TowerEvent] = []
        for row in rows:
            payload = json.loads(row["payload"])
            schema_version = int(payload.pop("_schema_version", 1))
            run_id = payload.pop("_run_id", None)
            entity_id = payload.pop("_entity_id", None)
            correlation_id = payload.pop("_correlation_id", None)
            events.append(
                TowerEvent(
                schema_version=schema_version,
                id=row["id"],
                type=row["event_type"],
                source_item_id=row["source_item_id"],
                run_id=run_id,
                entity_id=entity_id,
                correlation_id=correlation_id,
                trust_state=row["trust_state"],
                payload=payload,
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            )
        return events

    async def latest_event_id(self) -> int:
        cursor = await self.connection.execute("SELECT COALESCE(MAX(id), 0) AS id FROM events")
        row = await cursor.fetchone()
        return int(row["id"])

    async def store_source_run(self, run: SourceRun) -> SourceRun:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO source_runs
                (id, actor_name, status, payload, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  status = excluded.status,
                  payload = excluded.payload,
                  completed_at = excluded.completed_at
                """,
                (
                    run.id,
                    run.actor_name,
                    run.status.value,
                    run.model_dump_json(),
                    run.started_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                ),
            )
        return run

    async def get_source_run(self, run_id: str) -> SourceRun | None:
        cursor = await self.connection.execute(
            "SELECT payload FROM source_runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        return SourceRun.model_validate_json(row["payload"]) if row else None

    async def list_active_source_runs(self) -> list[SourceRun]:
        statuses = (
            SourceRunStatus.STARTING.value,
            SourceRunStatus.RUNNING.value,
        )
        cursor = await self.connection.execute(
            """
            SELECT payload FROM source_runs
            WHERE status IN (?, ?) ORDER BY started_at ASC
            """,
            statuses,
        )
        return [
            SourceRun.model_validate_json(row["payload"]) for row in await cursor.fetchall()
        ]

    async def store_scan(self, scan: ScanRecord) -> ScanRecord:
        normalized = scan.model_dump(exclude={"raw"}, mode="json")
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO scans
                (id, source_item_id, boundary, detected, threat_level, action,
                 normalized_payload, raw_payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan.id,
                    scan.source_item_id,
                    scan.boundary.value,
                    int(scan.detected),
                    scan.threat_level,
                    scan.action,
                    json.dumps(normalized, sort_keys=True),
                    json.dumps(scan.raw, sort_keys=True),
                    scan.created_at.isoformat(),
                ),
            )
        return scan

    async def list_scans(
        self, source_item_id: str, *, include_raw: bool = False
    ) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(
            """
            SELECT normalized_payload, raw_payload FROM scans
            WHERE source_item_id = ? ORDER BY created_at ASC
            """,
            (source_item_id,),
        )
        results: list[dict[str, Any]] = []
        for row in await cursor.fetchall():
            normalized = json.loads(row["normalized_payload"])
            if include_raw:
                normalized["raw"] = json.loads(row["raw_payload"])
            results.append(normalized)
        return results

    async def create_approval(self, approval: Approval) -> Approval:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO approvals
                (id, source_item_id, action, arguments, status, idempotency_key,
                 tool_request_id, created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.source_item_id,
                    approval.action,
                    json.dumps(approval.arguments, sort_keys=True),
                    approval.status.value,
                    approval.idempotency_key,
                    approval.tool_request_id,
                    approval.created_at.isoformat(),
                    approval.resolved_at.isoformat() if approval.resolved_at else None,
                ),
            )
        return approval

    async def list_approvals(self) -> list[Approval]:
        cursor = await self.connection.execute(
            """
            SELECT id, source_item_id, action, arguments, status, idempotency_key,
                   tool_request_id, created_at, resolved_at
            FROM approvals WHERE status = ? ORDER BY created_at ASC
            """,
            (ApprovalStatus.PENDING.value,),
        )
        rows = await cursor.fetchall()
        return [
            Approval(
                id=row["id"],
                source_item_id=row["source_item_id"],
                action=row["action"],
                arguments=json.loads(row["arguments"]),
                status=row["status"],
                idempotency_key=row["idempotency_key"],
                tool_request_id=row["tool_request_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                resolved_at=datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None,
            )
            for row in rows
        ]

    async def resolve_approval(self, approval_id: str, status: ApprovalStatus) -> Approval | None:
        now = datetime.now().astimezone().isoformat()
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                UPDATE approvals SET status = ?, resolved_at = ?
                WHERE id = ? AND status = ?
                """,
                (status.value, now, approval_id, ApprovalStatus.PENDING.value),
            )
            if cursor.rowcount != 1:
                return None
            cursor = await self.connection.execute(
                """
                SELECT id, source_item_id, action, arguments, status, idempotency_key,
                       tool_request_id, created_at, resolved_at
                FROM approvals WHERE id = ?
                """,
                (approval_id,),
            )
            row = await cursor.fetchone()
        return Approval(
            id=row["id"],
            source_item_id=row["source_item_id"],
            action=row["action"],
            arguments=json.loads(row["arguments"]),
            status=row["status"],
            idempotency_key=row["idempotency_key"],
            tool_request_id=row["tool_request_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]),
        )

    async def claim_approval_execution(self, approval_id: str) -> Approval | None:
        now = datetime.now(UTC)
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                UPDATE approvals SET status = ?, resolved_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ApprovalStatus.EXECUTING.value,
                    now.isoformat(),
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = await (
                await self.connection.execute(
                    """
                    SELECT id, source_item_id, action, arguments, status, idempotency_key,
                           tool_request_id, created_at, resolved_at
                    FROM approvals WHERE id = ?
                    """,
                    (approval_id,),
                )
            ).fetchone()
        return Approval(
            id=row["id"],
            source_item_id=row["source_item_id"],
            action=row["action"],
            arguments=json.loads(row["arguments"]),
            status=row["status"],
            idempotency_key=row["idempotency_key"],
            tool_request_id=row["tool_request_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]),
        )

    async def create_incident(self, incident: Incident) -> Incident:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO incidents
                (id, source_item_id, severity, summary, status, created_at,
                 acknowledged_at, resolved_at, resolution)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.id,
                    incident.source_item_id,
                    incident.severity,
                    incident.summary,
                    incident.status.value,
                    incident.created_at.isoformat(),
                    incident.acknowledged_at.isoformat()
                    if incident.acknowledged_at
                    else None,
                    incident.resolved_at.isoformat() if incident.resolved_at else None,
                    incident.resolution,
                ),
            )
        return incident

    async def list_incidents(self, active_only: bool = True) -> list[Incident]:
        where = "WHERE status != ?" if active_only else ""
        parameters: tuple[str, ...] = (IncidentStatus.RESOLVED.value,) if active_only else ()
        cursor = await self.connection.execute(
            f"""
            SELECT id, source_item_id, severity, summary, status, created_at,
                   acknowledged_at, resolved_at, resolution
            FROM incidents {where} ORDER BY created_at DESC
            """,
            parameters,
        )
        return [
            Incident(
                id=row["id"],
                source_item_id=row["source_item_id"],
                severity=row["severity"],
                summary=row["summary"],
                status=row["status"],
                created_at=datetime.fromisoformat(row["created_at"]),
                acknowledged_at=datetime.fromisoformat(row["acknowledged_at"])
                if row["acknowledged_at"]
                else None,
                resolved_at=datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None,
                resolution=row["resolution"],
            )
            for row in await cursor.fetchall()
        ]

    async def acknowledge_incident(self, incident_id: str) -> Incident | None:
        now = datetime.now(UTC)
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                UPDATE incidents SET status = ?, acknowledged_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    IncidentStatus.ACKNOWLEDGED.value,
                    now.isoformat(),
                    incident_id,
                    IncidentStatus.OPEN.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
        incidents = await self.list_incidents(active_only=True)
        return next((incident for incident in incidents if incident.id == incident_id), None)

    async def resolve_incident(self, incident_id: str, resolution: str) -> Incident | None:
        now = datetime.now(UTC)
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                SELECT source_item_id FROM incidents
                WHERE id = ? AND status != ?
                """,
                (incident_id, IncidentStatus.RESOLVED.value),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await self.connection.execute(
                """
                UPDATE incidents SET status = ?, resolved_at = ?, resolution = ?
                WHERE id = ?
                """,
                (
                    IncidentStatus.RESOLVED.value,
                    now.isoformat(),
                    resolution,
                    incident_id,
                ),
            )
            taint_cursor = await self.connection.execute(
                "SELECT payload FROM taints WHERE source_item_id = ? AND active = 1",
                (row["source_item_id"],),
            )
            taint_row = await taint_cursor.fetchone()
            if taint_row:
                taint = TaintRecord.model_validate_json(taint_row["payload"]).model_copy(
                    update={"active": False, "resolved_at": now, "resolution": resolution}
                )
                await self.connection.execute(
                    """
                    UPDATE taints SET active = 0, payload = ?, resolved_at = ?
                    WHERE source_item_id = ?
                    """,
                    (taint.model_dump_json(), now.isoformat(), row["source_item_id"]),
                )
        incidents = await self.list_incidents(active_only=False)
        return next((incident for incident in incidents if incident.id == incident_id), None)

    async def store_triage(self, source_item_id: str, triage: TriageResult) -> None:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO triage_results (source_item_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_item_id) DO UPDATE SET
                  payload = excluded.payload,
                  created_at = excluded.created_at
                """,
                (source_item_id, triage.model_dump_json(), datetime.now().astimezone().isoformat()),
            )
            await self.connection.execute(
                "DELETE FROM triage_terms WHERE source_item_id = ?", (source_item_id,)
            )
            term_groups = {
                "topic": triage.topics,
                "entity": triage.entities,
                "company": triage.companies,
                "product": triage.products,
                "technology": triage.technologies,
                "repository": triage.repositories,
                "cve": triage.cves,
            }
            for kind, values in term_groups.items():
                for value in {entry.strip() for entry in values if entry.strip()}:
                    await self.connection.execute(
                        """
                        INSERT INTO triage_terms (source_item_id, kind, value)
                        VALUES (?, ?, ?)
                        """,
                        (source_item_id, kind, value),
                    )

    async def list_triage(self, limit: int = 50) -> list[tuple[str, TriageResult]]:
        cursor = await self.connection.execute(
            """
            SELECT source_item_id, payload FROM triage_results
            ORDER BY created_at DESC LIMIT ?
            """,
            (min(max(limit, 1), 200),),
        )
        return [
            (row["source_item_id"], TriageResult.model_validate_json(row["payload"]))
            for row in await cursor.fetchall()
        ]

    async def list_triage_with_sources(
        self, limit: int = 200
    ) -> list[tuple[SourceItem, TriageResult]]:
        cursor = await self.connection.execute(
            """
            SELECT s.payload AS source_payload, t.payload AS triage_payload
            FROM triage_results t
            JOIN source_items s ON s.id = t.source_item_id
            ORDER BY t.created_at DESC LIMIT ?
            """,
            (min(max(limit, 1), 500),),
        )
        return [
            (
                SourceItem.model_validate_json(row["source_payload"]),
                TriageResult.model_validate_json(row["triage_payload"]),
            )
            for row in await cursor.fetchall()
        ]

    async def create_tool_request(self, request: ToolRequest) -> tuple[ToolRequest, bool]:
        async with self.transaction():
            existing = await (
                await self.connection.execute(
                    "SELECT payload FROM tool_requests WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                )
            ).fetchone()
            if existing:
                return ToolRequest.model_validate_json(existing["payload"]), False
            await self.connection.execute(
                """
                INSERT INTO tool_requests
                (id, source_item_id, name, idempotency_key, status, payload,
                 created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.id,
                    request.source_item_id,
                    request.name,
                    request.idempotency_key,
                    request.status.value,
                    request.model_dump_json(),
                    request.created_at.isoformat(),
                    request.completed_at.isoformat() if request.completed_at else None,
                ),
            )
            return request, True

    async def get_tool_request(self, request_id: str) -> ToolRequest | None:
        row = await (
            await self.connection.execute(
                "SELECT payload FROM tool_requests WHERE id = ?", (request_id,)
            )
        ).fetchone()
        return ToolRequest.model_validate_json(row["payload"]) if row else None

    async def update_tool_request(
        self,
        request_id: str,
        status: ToolStatus,
        *,
        result: dict[str, Any] | None = None,
        failure_reason: str | None = None,
    ) -> ToolRequest | None:
        async with self.transaction():
            row = await (
                await self.connection.execute(
                    "SELECT payload FROM tool_requests WHERE id = ?", (request_id,)
                )
            ).fetchone()
            if row is None:
                return None
            completed_at = (
                datetime.now(UTC)
                if status
                in {
                    ToolStatus.COMPLETED,
                    ToolStatus.BLOCKED,
                    ToolStatus.DENIED,
                    ToolStatus.FAILED,
                }
                else None
            )
            request = ToolRequest.model_validate_json(row["payload"]).model_copy(
                update={
                    "status": status,
                    "result": result,
                    "failure_reason": failure_reason,
                    "completed_at": completed_at,
                }
            )
            await self.connection.execute(
                """
                UPDATE tool_requests SET status = ?, payload = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    request.model_dump_json(),
                    completed_at.isoformat() if completed_at else None,
                    request_id,
                ),
            )
            return request

    async def finalize_approval(
        self, approval_id: str, status: ApprovalStatus
    ) -> Approval | None:
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.FAILED}:
            raise ValueError("Executing approvals can only be finalized as approved or failed")
        now = datetime.now(UTC)
        async with self.transaction():
            cursor = await self.connection.execute(
                """
                UPDATE approvals SET status = ?, resolved_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    now.isoformat(),
                    approval_id,
                    ApprovalStatus.EXECUTING.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = await (
                await self.connection.execute(
                    """
                    SELECT id, source_item_id, action, arguments, status, idempotency_key,
                           tool_request_id, created_at, resolved_at
                    FROM approvals WHERE id = ?
                    """,
                    (approval_id,),
                )
            ).fetchone()
        return Approval(
            id=row["id"],
            source_item_id=row["source_item_id"],
            action=row["action"],
            arguments=json.loads(row["arguments"]),
            status=row["status"],
            idempotency_key=row["idempotency_key"],
            tool_request_id=row["tool_request_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]),
        )

    async def store_brief(self, brief: Brief) -> Brief:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO briefs (id, source_item_id, payload, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (
                    brief.id,
                    brief.source_item_id,
                    brief.model_dump_json(),
                    brief.created_at.isoformat(),
                ),
            )
        return brief

    async def list_briefs(self) -> list[Brief]:
        cursor = await self.connection.execute(
            "SELECT payload FROM briefs ORDER BY created_at DESC"
        )
        return [Brief.model_validate_json(row["payload"]) for row in await cursor.fetchall()]

    async def store_mock_alert(self, alert: MockAlert) -> MockAlert:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO mock_outbox (id, source_item_id, payload, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (
                    alert.id,
                    alert.source_item_id,
                    alert.model_dump_json(),
                    alert.created_at.isoformat(),
                ),
            )
        return alert

    async def list_mock_alerts(self) -> list[MockAlert]:
        cursor = await self.connection.execute(
            "SELECT payload FROM mock_outbox ORDER BY created_at DESC"
        )
        return [
            MockAlert.model_validate_json(row["payload"]) for row in await cursor.fetchall()
        ]

    async def store_quarantine(self, quarantine: QuarantineRecord) -> QuarantineRecord:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO quarantines (id, source_item_id, payload, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (
                    quarantine.id,
                    quarantine.source_item_id,
                    quarantine.model_dump_json(),
                    quarantine.created_at.isoformat(),
                ),
            )
        return quarantine

    async def list_quarantines(self) -> list[QuarantineRecord]:
        cursor = await self.connection.execute(
            "SELECT payload FROM quarantines ORDER BY created_at DESC"
        )
        return [
            QuarantineRecord.model_validate_json(row["payload"])
            for row in await cursor.fetchall()
        ]

    async def upsert_watchlist(self, watchlist: Watchlist) -> Watchlist:
        updated = watchlist.model_copy(update={"updated_at": datetime.now(UTC)})
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO watchlists (id, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  payload = excluded.payload,
                  updated_at = excluded.updated_at
                """,
                (
                    updated.id,
                    updated.model_dump_json(),
                    updated.created_at.isoformat(),
                    updated.updated_at.isoformat(),
                ),
            )
        return updated

    async def list_watchlists(self) -> list[Watchlist]:
        cursor = await self.connection.execute(
            "SELECT payload FROM watchlists ORDER BY updated_at DESC"
        )
        return [
            Watchlist.model_validate_json(row["payload"]) for row in await cursor.fetchall()
        ]

    async def delete_watchlist(self, watchlist_id: str) -> bool:
        async with self.transaction():
            await self.connection.execute(
                "DELETE FROM watchlist_matches WHERE watchlist_id = ?", (watchlist_id,)
            )
            cursor = await self.connection.execute(
                "DELETE FROM watchlists WHERE id = ?", (watchlist_id,)
            )
            return cursor.rowcount == 1

    async def store_watchlist_match(self, match: WatchlistMatch) -> WatchlistMatch:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO watchlist_matches
                (id, watchlist_id, source_item_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(watchlist_id, source_item_id) DO UPDATE SET
                  payload = excluded.payload,
                  created_at = excluded.created_at
                """,
                (
                    match.id,
                    match.watchlist_id,
                    match.source_item_id,
                    match.model_dump_json(),
                    match.created_at.isoformat(),
                ),
            )
        return match

    async def list_watchlist_matches(
        self, watchlist_id: str | None = None
    ) -> list[WatchlistMatch]:
        if watchlist_id:
            cursor = await self.connection.execute(
                """
                SELECT payload FROM watchlist_matches
                WHERE watchlist_id = ? ORDER BY created_at DESC
                """,
                (watchlist_id,),
            )
        else:
            cursor = await self.connection.execute(
                "SELECT payload FROM watchlist_matches ORDER BY created_at DESC"
            )
        return [
            WatchlistMatch.model_validate_json(row["payload"])
            for row in await cursor.fetchall()
        ]

    async def store_query_history(self, query: QueryHistory) -> QueryHistory:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO query_history (id, payload, created_at)
                VALUES (?, ?, ?)
                """,
                (query.id, query.model_dump_json(), query.created_at.isoformat()),
            )
        return query

    async def list_query_history(self, limit: int = 50) -> list[QueryHistory]:
        cursor = await self.connection.execute(
            "SELECT payload FROM query_history ORDER BY created_at DESC LIMIT ?",
            (min(max(limit, 1), 200),),
        )
        return [
            QueryHistory.model_validate_json(row["payload"])
            for row in await cursor.fetchall()
        ]

    async def set_demo_state(self, state: dict[str, Any]) -> None:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO demo_state (state_key, state_value)
                VALUES ('demo', ?)
                ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
                """,
                (json.dumps(state, sort_keys=True),),
            )

    async def get_demo_state(self) -> dict[str, Any]:
        row = await (
            await self.connection.execute(
                "SELECT state_value FROM demo_state WHERE state_key = 'demo'"
            )
        ).fetchone()
        return json.loads(row["state_value"]) if row else {"running": False, "step": 0}


class SpannerRepository:
    """Production Cloud Spanner implementation using worker threads for blocking I/O."""

    def __init__(self, project_id: str, instance_id: str, database_id: str) -> None:
        self._project_id = project_id
        self._instance_id = instance_id
        self._database_id = database_id
        self._database: spanner.Database | None = None

    async def connect(self) -> None:
        client = await asyncio.to_thread(spanner.Client, project=self._project_id)
        instance = await asyncio.to_thread(client.instance, self._instance_id)
        self._database = await asyncio.to_thread(instance.database, self._database_id)

    async def close(self) -> None:
        return None

    @property
    def database(self) -> spanner.Database:
        if self._database is None:
            raise RuntimeError("Repository is not connected")
        return self._database

    async def _read_one(self, sql: str, params: dict[str, str]) -> tuple[Any, ...] | None:
        def read() -> tuple[Any, ...] | None:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    sql,
                    params=params,
                    param_types={key: spanner.param_types.STRING for key in params},
                )
                return next(iter(rows), None)

        return await asyncio.to_thread(read)

    async def store_source_run(self, run: SourceRun) -> SourceRun:
        await self._put_record(
            "source_run",
            run.id,
            run.model_dump_json(),
            status=run.status.value,
            created_at=run.started_at,
        )
        return run

    async def get_source_run(self, run_id: str) -> SourceRun | None:
        payload = await self._get_record("source_run", run_id)
        return SourceRun.model_validate_json(payload) if payload else None

    async def list_active_source_runs(self) -> list[SourceRun]:
        runs: list[SourceRun] = []
        for status in (SourceRunStatus.STARTING.value, SourceRunStatus.RUNNING.value):
            runs.extend(
                SourceRun.model_validate_json(payload)
                for payload in await self._list_records("source_run", status=status)
            )
        return sorted(runs, key=lambda run: run.started_at)

    async def store_scan(self, scan: ScanRecord) -> ScanRecord:
        await self._put_record(
            "scan",
            scan.id,
            scan.model_dump_json(),
            source_item_id=scan.source_item_id,
            status=scan.action,
            created_at=scan.created_at,
        )
        return scan

    async def list_scans(
        self, source_item_id: str, *, include_raw: bool = False
    ) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    """
                    SELECT payload FROM ProductRecords
                    WHERE record_type = 'scan' AND source_item_id = @source_item_id
                    ORDER BY created_at ASC
                    """,
                    params={"source_item_id": source_item_id},
                    param_types={"source_item_id": spanner.param_types.STRING},
                )
                results: list[dict[str, Any]] = []
                for row in rows:
                    scan = ScanRecord.model_validate_json(row[0])
                    payload = scan.model_dump(exclude={"raw"}, mode="json")
                    if include_raw:
                        payload["raw"] = scan.raw
                    results.append(payload)
                return results

        return await asyncio.to_thread(read)

    async def _mutation(
        self, operation: str, table: str, columns: list[str], values: list[list[Any]]
    ) -> None:
        def write() -> None:
            with self.database.batch() as batch:
                getattr(batch, operation)(table, columns, values)

        await asyncio.to_thread(write)

    async def _put_record(
        self,
        record_type: str,
        record_id: str,
        payload: str,
        *,
        source_item_id: str | None = None,
        status: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        now = datetime.now(UTC)
        await self._mutation(
            "insert_or_update",
            "ProductRecords",
            [
                "record_type",
                "record_id",
                "source_item_id",
                "status",
                "payload",
                "created_at",
                "updated_at",
            ],
            [[record_type, record_id, source_item_id, status, payload, created_at or now, now]],
        )

    async def _get_record(self, record_type: str, record_id: str) -> str | None:
        def read() -> str | None:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    """
                    SELECT payload FROM ProductRecords
                    WHERE record_type = @record_type AND record_id = @record_id
                    """,
                    params={"record_type": record_type, "record_id": record_id},
                    param_types={
                        "record_type": spanner.param_types.STRING,
                        "record_id": spanner.param_types.STRING,
                    },
                )
                row = next(iter(rows), None)
                return str(row[0]) if row else None

        return await asyncio.to_thread(read)

    async def _list_records(
        self, record_type: str, status: str | None = None, limit: int = 200
    ) -> list[str]:
        safe_limit = min(max(limit, 1), 500)

        def read() -> list[str]:
            with self.database.snapshot() as snapshot:
                sql = """
                    SELECT payload FROM ProductRecords
                    WHERE record_type = @record_type
                """
                params: dict[str, str] = {"record_type": record_type}
                param_types = {"record_type": spanner.param_types.STRING}
                if status is not None:
                    sql += " AND status = @status"
                    params["status"] = status
                    param_types["status"] = spanner.param_types.STRING
                sql += f" ORDER BY updated_at DESC LIMIT {safe_limit}"
                return [
                    str(row[0])
                    for row in snapshot.execute_sql(
                        sql, params=params, param_types=param_types
                    )
                ]

        return await asyncio.to_thread(read)

    async def store_source_item(self, item: SourceItem) -> bool:
        def store(transaction: spanner.Transaction) -> bool:
            rows = transaction.execute_sql(
                "SELECT id FROM SourceItems WHERE id = @id",
                params={"id": item.id},
                param_types={"id": spanner.param_types.STRING},
            )
            if next(iter(rows), None):
                return False
            transaction.insert(
                "SourceItems",
                ["id", "payload", "created_at"],
                [[item.id, item.model_dump_json(), item.received_at.isoformat()]],
            )
            return True

        return await asyncio.to_thread(self.database.run_in_transaction, store)

    async def get_source_item(self, source_item_id: str) -> SourceItem | None:
        row = await self._read_one(
            "SELECT payload FROM SourceItems WHERE id = @id",
            {"id": source_item_id},
        )
        return SourceItem.model_validate_json(row[0]) if row else None

    async def update_source_status(
        self,
        source_item_id: str,
        status: ProcessingStatus,
        failure_reason: str | None = None,
    ) -> SourceItem | None:
        item = await self.get_source_item(source_item_id)
        if item is None:
            return None
        updated = item.model_copy(
            update={"processing_status": status, "failure_reason": failure_reason}
        )
        await self._mutation(
            "update",
            "SourceItems",
            ["id", "payload", "created_at"],
            [[source_item_id, updated.model_dump_json(), updated.received_at.isoformat()]],
        )
        return updated

    async def list_pending_source_items(self, limit: int = 50) -> list[SourceItem]:
        safe_limit = min(max(limit, 1), 200)

        def read() -> list[SourceItem]:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    f"""
                    SELECT payload FROM SourceItems
                    ORDER BY created_at ASC LIMIT {safe_limit * 5}
                    """
                )
                items = [SourceItem.model_validate_json(row[0]) for row in rows]
                return [
                    item
                    for item in items
                    if item.processing_status in {
                        ProcessingStatus.PENDING,
                        ProcessingStatus.PROCESSING,
                    }
                ][:safe_limit]

        return await asyncio.to_thread(read)

    async def get_trust_state(self) -> TrustState:
        row = await self._read_one(
            "SELECT state_value FROM RuntimeState WHERE state_key = @key",
            {"key": "trust_state"},
        )
        return TrustState(row[0]) if row else TrustState.NORMAL

    async def set_trust_state(self, state: TrustState) -> None:
        await asyncio.to_thread(
            self.database.run_in_transaction,
            lambda transaction: transaction.insert_or_update(
                "RuntimeState",
                ["state_key", "state_value"],
                [["trust_state", state.value]],
            ),
        )

    async def transition_trust_state(
        self,
        to_state: TrustState,
        reason: str,
        source_item_id: str | None = None,
        *,
        allow_deescalation: bool = False,
    ) -> TrustTransition | None:
        ranks = {TrustState.NORMAL: 0, TrustState.RESTRICTED: 1, TrustState.LOCKED: 2}

        def transition(transaction: spanner.Transaction) -> str | None:
            rows = transaction.execute_sql(
                """
                SELECT state_value FROM RuntimeState
                WHERE state_key = 'trust_state'
                """
            )
            row = next(iter(rows), None)
            current = TrustState(row[0]) if row else TrustState.NORMAL
            if current == to_state:
                return None
            if not allow_deescalation and ranks[to_state] < ranks[current]:
                return None
            record = TrustTransition(
                source_item_id=source_item_id,
                from_state=current,
                to_state=to_state,
                reason=reason,
            )
            transaction.insert_or_update(
                "RuntimeState",
                ["state_key", "state_value"],
                [["trust_state", to_state.value]],
            )
            now = datetime.now(UTC)
            transaction.insert(
                "ProductRecords",
                [
                    "record_type",
                    "record_id",
                    "source_item_id",
                    "status",
                    "payload",
                    "created_at",
                    "updated_at",
                ],
                [[
                    "trust_transition",
                    record.id,
                    source_item_id,
                    to_state.value,
                    record.model_dump_json(),
                    now,
                    now,
                ]],
            )
            return record.model_dump_json()

        payload = await asyncio.to_thread(self.database.run_in_transaction, transition)
        return TrustTransition.model_validate_json(payload) if payload else None

    async def create_taint(self, taint: TaintRecord) -> TaintRecord:
        await self._put_record(
            "taint",
            taint.source_item_id,
            taint.model_dump_json(),
            source_item_id=taint.source_item_id,
            status="active",
            created_at=taint.created_at,
        )
        return taint

    async def is_tainted(self, source_item_id: str) -> bool:
        payload = await self._get_record("taint", source_item_id)
        return bool(payload and TaintRecord.model_validate_json(payload).active)

    async def resolve_taint(self, source_item_id: str, resolution: str) -> bool:
        payload = await self._get_record("taint", source_item_id)
        if payload is None:
            return False
        now = datetime.now(UTC)
        taint = TaintRecord.model_validate_json(payload)
        if not taint.active:
            return False
        resolved = taint.model_copy(
            update={"active": False, "resolved_at": now, "resolution": resolution}
        )
        await self._put_record(
            "taint",
            source_item_id,
            resolved.model_dump_json(),
            source_item_id=source_item_id,
            status="resolved",
            created_at=resolved.created_at,
        )
        return True

    async def has_active_taints(self) -> bool:
        return bool(await self._list_records("taint", status="active", limit=1))

    async def acquire_lease(self, name: str, owner_id: str, ttl_seconds: int) -> bool:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=max(ttl_seconds, 1))

        def acquire(transaction: spanner.Transaction) -> bool:
            rows = transaction.execute_sql(
                "SELECT owner_id, expires_at FROM HeartbeatLeases WHERE name = @name",
                params={"name": name},
                param_types={"name": spanner.param_types.STRING},
            )
            row = next(iter(rows), None)
            if row and row[0] != owner_id and row[1] > now:
                return False
            transaction.insert_or_update(
                "HeartbeatLeases",
                ["name", "owner_id", "expires_at"],
                [[name, owner_id, expires_at]],
            )
            return True

        return await asyncio.to_thread(self.database.run_in_transaction, acquire)

    async def append_event(self, event: TowerEvent) -> TowerEvent:
        stored_payload = {
            **event.payload,
            "_schema_version": event.schema_version,
            "_run_id": event.run_id,
            "_entity_id": event.entity_id,
            "_correlation_id": event.correlation_id,
        }

        def append(transaction: spanner.Transaction) -> int:
            updated = transaction.execute_update(
                "UPDATE EventSequence SET next_id = next_id + 1 WHERE name = 'events'"
            )
            if updated != 1:
                transaction.insert("EventSequence", ["name", "next_id"], [["events", 1]])
                event_id = 1
            else:
                row = next(
                    transaction.execute_sql(
                        "SELECT next_id FROM EventSequence WHERE name = 'events'"
                    )
                )
                event_id = int(row[0])
            transaction.insert(
                "Events",
                ["id", "event_type", "source_item_id", "trust_state", "payload", "occurred_at"],
                [[
                    event_id,
                    event.type.value,
                    event.source_item_id,
                    event.trust_state.value if event.trust_state else None,
                        json.dumps(stored_payload, sort_keys=True),
                    event.occurred_at.isoformat(),
                ]],
            )
            return event_id

        event_id = await asyncio.to_thread(self.database.run_in_transaction, append)
        return event.model_copy(update={"id": event_id})

    async def list_events(self, after_id: int = 0, limit: int = 200) -> list[TowerEvent]:
        safe_after_id = max(after_id, 0)
        safe_limit = min(max(limit, 1), 500)

        def read() -> list[TowerEvent]:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    f"""
                    SELECT id, event_type, source_item_id, trust_state, payload, occurred_at
                    FROM Events WHERE id > {safe_after_id} ORDER BY id ASC LIMIT {safe_limit}
                    """
                )
                return [
                    self._event_from_spanner_row(row)
                    for row in rows
                ]

        return await asyncio.to_thread(read)

    @staticmethod
    def _event_from_spanner_row(row: Sequence[Any]) -> TowerEvent:
        payload = json.loads(row[4])
        return TowerEvent(
            schema_version=int(payload.pop("_schema_version", 1)),
            id=row[0],
            type=row[1],
            source_item_id=row[2],
            run_id=payload.pop("_run_id", None),
            entity_id=payload.pop("_entity_id", None),
            correlation_id=payload.pop("_correlation_id", None),
            trust_state=row[3],
            payload=payload,
            occurred_at=datetime.fromisoformat(row[5]),
        )

    async def latest_event_id(self) -> int:
        def read() -> int:
            with self.database.snapshot() as snapshot:
                row = next(
                    iter(snapshot.execute_sql("SELECT COALESCE(MAX(id), 0) FROM Events")),
                    None,
                )
                return int(row[0]) if row else 0

        return await asyncio.to_thread(read)

    async def create_approval(self, approval: Approval) -> Approval:
        def create(transaction: spanner.Transaction) -> None:
            transaction.insert(
                "Approvals",
                [
                    "id",
                    "source_item_id",
                    "action",
                    "arguments",
                    "status",
                    "created_at",
                    "resolved_at",
                ],
                [[
                    approval.id,
                    approval.source_item_id,
                    approval.action,
                    json.dumps(approval.arguments, sort_keys=True),
                    approval.status.value,
                    approval.created_at.isoformat(),
                    None,
                ]],
            )
            transaction.insert(
                "ProductRecords",
                [
                    "record_type",
                    "record_id",
                    "source_item_id",
                    "status",
                    "payload",
                    "created_at",
                    "updated_at",
                ],
                [[
                    "approval",
                    approval.id,
                    approval.source_item_id,
                    approval.status.value,
                    approval.model_dump_json(),
                    approval.created_at,
                    approval.created_at,
                ]],
            )

        await asyncio.to_thread(self.database.run_in_transaction, create)
        return approval

    async def list_approvals(self) -> list[Approval]:
        payloads = await self._list_records(
            "approval", status=ApprovalStatus.PENDING.value
        )
        if payloads:
            return sorted(
                (Approval.model_validate_json(payload) for payload in payloads),
                key=lambda approval: approval.created_at,
            )

        def read() -> list[Approval]:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    """
                    SELECT id, source_item_id, action, arguments, status, created_at, resolved_at
                    FROM Approvals WHERE status = 'pending' ORDER BY created_at ASC
                    """
                )
                return [
                    Approval(
                        id=row[0],
                        source_item_id=row[1],
                        action=row[2],
                        arguments=json.loads(row[3]),
                        status=row[4],
                        created_at=datetime.fromisoformat(row[5]),
                        resolved_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    )
                    for row in rows
                ]

        return await asyncio.to_thread(read)

    async def resolve_approval(self, approval_id: str, status: ApprovalStatus) -> Approval | None:
        now = datetime.now(UTC)

        def resolve(transaction: spanner.Transaction) -> str | None:
            rows = transaction.execute_sql(
                """
                SELECT payload FROM ProductRecords
                WHERE record_type = 'approval' AND record_id = @id
                """,
                params={"id": approval_id},
                param_types={"id": spanner.param_types.STRING},
            )
            row = next(iter(rows), None)
            if row is None:
                return None
            approval = Approval.model_validate_json(row[0])
            if approval.status != ApprovalStatus.PENDING:
                return None
            updated = approval.model_copy(update={"status": status, "resolved_at": now})
            transaction.update(
                "Approvals",
                ["id", "status", "resolved_at"],
                [[approval_id, status.value, now.isoformat()]],
            )
            transaction.update(
                "ProductRecords",
                ["record_type", "record_id", "status", "payload", "updated_at"],
                [[
                    "approval",
                    approval_id,
                    status.value,
                    updated.model_dump_json(),
                    now,
                ]],
            )
            return updated.model_dump_json()

        payload = await asyncio.to_thread(self.database.run_in_transaction, resolve)
        return Approval.model_validate_json(payload) if payload else None

    async def claim_approval_execution(self, approval_id: str) -> Approval | None:
        return await self.resolve_approval(approval_id, ApprovalStatus.EXECUTING)

    async def finalize_approval(
        self, approval_id: str, status: ApprovalStatus
    ) -> Approval | None:
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.FAILED}:
            raise ValueError("Executing approvals can only be finalized as approved or failed")
        now = datetime.now(UTC)

        def finalize(transaction: spanner.Transaction) -> str | None:
            rows = transaction.execute_sql(
                """
                SELECT payload FROM ProductRecords
                WHERE record_type = 'approval' AND record_id = @id
                """,
                params={"id": approval_id},
                param_types={"id": spanner.param_types.STRING},
            )
            row = next(iter(rows), None)
            if row is None:
                return None
            approval = Approval.model_validate_json(row[0])
            if approval.status != ApprovalStatus.EXECUTING:
                return None
            updated = approval.model_copy(update={"status": status, "resolved_at": now})
            transaction.update(
                "Approvals",
                ["id", "status", "resolved_at"],
                [[approval_id, status.value, now.isoformat()]],
            )
            transaction.update(
                "ProductRecords",
                ["record_type", "record_id", "status", "payload", "updated_at"],
                [[
                    "approval",
                    approval_id,
                    status.value,
                    updated.model_dump_json(),
                    now,
                ]],
            )
            return updated.model_dump_json()

        payload = await asyncio.to_thread(self.database.run_in_transaction, finalize)
        return Approval.model_validate_json(payload) if payload else None

    async def create_incident(self, incident: Incident) -> Incident:
        await self._mutation(
            "insert",
            "Incidents",
            ["id", "source_item_id", "severity", "summary", "created_at", "acknowledged_at"],
            [[
                incident.id,
                incident.source_item_id,
                incident.severity,
                incident.summary,
                incident.created_at.isoformat(),
                None,
            ]],
        )
        await self._put_record(
            "incident",
            incident.id,
            incident.model_dump_json(),
            source_item_id=incident.source_item_id,
            status=incident.status.value,
            created_at=incident.created_at,
        )
        return incident

    async def list_incidents(self, active_only: bool = True) -> list[Incident]:
        payloads = await self._list_records("incident")
        incidents = [Incident.model_validate_json(payload) for payload in payloads]
        if active_only:
            incidents = [
                incident
                for incident in incidents
                if incident.status != IncidentStatus.RESOLVED
            ]
        return sorted(incidents, key=lambda incident: incident.created_at, reverse=True)

    async def acknowledge_incident(self, incident_id: str) -> Incident | None:
        payload = await self._get_record("incident", incident_id)
        if payload is None:
            return None
        incident = Incident.model_validate_json(payload)
        if incident.status != IncidentStatus.OPEN:
            return None
        now = datetime.now(UTC)
        updated = incident.model_copy(
            update={"status": IncidentStatus.ACKNOWLEDGED, "acknowledged_at": now}
        )
        await self._put_record(
            "incident",
            incident_id,
            updated.model_dump_json(),
            source_item_id=incident.source_item_id,
            status=updated.status.value,
            created_at=incident.created_at,
        )
        await self._mutation(
            "update",
            "Incidents",
            ["id", "acknowledged_at"],
            [[incident_id, now.isoformat()]],
        )
        return updated

    async def resolve_incident(self, incident_id: str, resolution: str) -> Incident | None:
        payload = await self._get_record("incident", incident_id)
        if payload is None:
            return None
        incident = Incident.model_validate_json(payload)
        if incident.status == IncidentStatus.RESOLVED:
            return None
        now = datetime.now(UTC)
        updated = incident.model_copy(
            update={
                "status": IncidentStatus.RESOLVED,
                "resolved_at": now,
                "resolution": resolution,
            }
        )
        await self._put_record(
            "incident",
            incident_id,
            updated.model_dump_json(),
            source_item_id=incident.source_item_id,
            status=updated.status.value,
            created_at=incident.created_at,
        )
        await self.resolve_taint(incident.source_item_id, resolution)
        return updated

    async def store_triage(self, source_item_id: str, triage: TriageResult) -> None:
        await self._mutation(
            "insert_or_update",
            "TriageResults",
            ["source_item_id", "payload", "created_at"],
            [[
                source_item_id,
                triage.model_dump_json(),
                datetime.now().astimezone().isoformat(),
            ]],
        )
        await self._put_record(
            "triage",
            source_item_id,
            triage.model_dump_json(),
            source_item_id=source_item_id,
            status=triage.priority,
        )

    async def list_triage(self, limit: int = 50) -> list[tuple[str, TriageResult]]:
        safe_limit = min(max(limit, 1), 200)

        def read() -> list[tuple[str, TriageResult]]:
            with self.database.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    f"""
                    SELECT source_item_id, payload FROM TriageResults
                    ORDER BY created_at DESC LIMIT {safe_limit}
                    """
                )
                return [(row[0], TriageResult.model_validate_json(row[1])) for row in rows]

        return await asyncio.to_thread(read)

    async def list_triage_with_sources(
        self, limit: int = 200
    ) -> list[tuple[SourceItem, TriageResult]]:
        triages = await self.list_triage(limit=limit)
        results: list[tuple[SourceItem, TriageResult]] = []
        for source_item_id, triage in triages:
            item = await self.get_source_item(source_item_id)
            if item:
                results.append((item, triage))
        return results

    async def create_tool_request(self, request: ToolRequest) -> tuple[ToolRequest, bool]:
        existing_payload = await self._get_record("tool_request_key", request.idempotency_key)
        if existing_payload:
            existing_id = json.loads(existing_payload)["request_id"]
            existing = await self.get_tool_request(existing_id)
            if existing is None:
                raise RuntimeError("Tool idempotency record is inconsistent")
            return existing, False

        def create(transaction: spanner.Transaction) -> None:
            now = datetime.now(UTC)
            rows = transaction.execute_sql(
                """
                SELECT payload FROM ProductRecords
                WHERE record_type = 'tool_request_key' AND record_id = @key
                """,
                params={"key": request.idempotency_key},
                param_types={"key": spanner.param_types.STRING},
            )
            if next(iter(rows), None):
                raise RuntimeError("Tool request idempotency key already exists")
            transaction.insert(
                "ProductRecords",
                [
                    "record_type",
                    "record_id",
                    "source_item_id",
                    "status",
                    "payload",
                    "created_at",
                    "updated_at",
                ],
                [
                    [
                        "tool_request",
                        request.id,
                        request.source_item_id,
                        request.status.value,
                        request.model_dump_json(),
                        request.created_at,
                        now,
                    ],
                    [
                        "tool_request_key",
                        request.idempotency_key,
                        request.source_item_id,
                        "active",
                        json.dumps({"request_id": request.id}),
                        request.created_at,
                        now,
                    ],
                ],
            )

        await asyncio.to_thread(self.database.run_in_transaction, create)
        return request, True

    async def get_tool_request(self, request_id: str) -> ToolRequest | None:
        payload = await self._get_record("tool_request", request_id)
        return ToolRequest.model_validate_json(payload) if payload else None

    async def update_tool_request(
        self,
        request_id: str,
        status: ToolStatus,
        *,
        result: dict[str, Any] | None = None,
        failure_reason: str | None = None,
    ) -> ToolRequest | None:
        request = await self.get_tool_request(request_id)
        if request is None:
            return None
        completed_at = (
            datetime.now(UTC)
            if status
            in {
                ToolStatus.COMPLETED,
                ToolStatus.BLOCKED,
                ToolStatus.DENIED,
                ToolStatus.FAILED,
            }
            else None
        )
        updated = request.model_copy(
            update={
                "status": status,
                "result": result,
                "failure_reason": failure_reason,
                "completed_at": completed_at,
            }
        )
        await self._put_record(
            "tool_request",
            request_id,
            updated.model_dump_json(),
            source_item_id=request.source_item_id,
            status=status.value,
            created_at=request.created_at,
        )
        return updated

    async def store_brief(self, brief: Brief) -> Brief:
        await self._put_record(
            "brief",
            brief.id,
            brief.model_dump_json(),
            source_item_id=brief.source_item_id,
            status="resolved" if brief.resolved else "active",
            created_at=brief.created_at,
        )
        return brief

    async def list_briefs(self) -> list[Brief]:
        return [
            Brief.model_validate_json(payload)
            for payload in await self._list_records("brief")
        ]

    async def store_mock_alert(self, alert: MockAlert) -> MockAlert:
        await self._put_record(
            "mock_alert",
            alert.id,
            alert.model_dump_json(),
            source_item_id=alert.source_item_id,
            status=alert.status,
            created_at=alert.created_at,
        )
        return alert

    async def list_mock_alerts(self) -> list[MockAlert]:
        return [
            MockAlert.model_validate_json(payload)
            for payload in await self._list_records("mock_alert")
        ]

    async def store_quarantine(self, quarantine: QuarantineRecord) -> QuarantineRecord:
        await self._put_record(
            "quarantine",
            quarantine.id,
            quarantine.model_dump_json(),
            source_item_id=quarantine.source_item_id,
            status="active",
            created_at=quarantine.created_at,
        )
        return quarantine

    async def list_quarantines(self) -> list[QuarantineRecord]:
        return [
            QuarantineRecord.model_validate_json(payload)
            for payload in await self._list_records("quarantine")
        ]

    async def upsert_watchlist(self, watchlist: Watchlist) -> Watchlist:
        updated = watchlist.model_copy(update={"updated_at": datetime.now(UTC)})
        await self._put_record(
            "watchlist",
            updated.id,
            updated.model_dump_json(),
            status="active",
            created_at=updated.created_at,
        )
        return updated

    async def list_watchlists(self) -> list[Watchlist]:
        return [
            Watchlist.model_validate_json(payload)
            for payload in await self._list_records("watchlist", status="active")
        ]

    async def delete_watchlist(self, watchlist_id: str) -> bool:
        payload = await self._get_record("watchlist", watchlist_id)
        if payload is None:
            return False
        watchlist = Watchlist.model_validate_json(payload)
        await self._put_record(
            "watchlist",
            watchlist_id,
            watchlist.model_dump_json(),
            status="deleted",
            created_at=watchlist.created_at,
        )
        return True

    async def store_watchlist_match(self, match: WatchlistMatch) -> WatchlistMatch:
        record_id = f"{match.watchlist_id}:{match.source_item_id}"
        await self._put_record(
            "watchlist_match",
            record_id,
            match.model_dump_json(),
            source_item_id=match.source_item_id,
            status=match.watchlist_id,
            created_at=match.created_at,
        )
        return match

    async def list_watchlist_matches(
        self, watchlist_id: str | None = None
    ) -> list[WatchlistMatch]:
        payloads = await self._list_records(
            "watchlist_match", status=watchlist_id if watchlist_id else None
        )
        return [WatchlistMatch.model_validate_json(payload) for payload in payloads]

    async def store_query_history(self, query: QueryHistory) -> QueryHistory:
        await self._put_record(
            "query_history",
            query.id,
            query.model_dump_json(),
            status="complete",
            created_at=query.created_at,
        )
        return query

    async def list_query_history(self, limit: int = 50) -> list[QueryHistory]:
        return [
            QueryHistory.model_validate_json(payload)
            for payload in await self._list_records("query_history", limit=limit)
        ]

    async def set_demo_state(self, state: dict[str, Any]) -> None:
        await self._put_record(
            "demo_state",
            "demo",
            json.dumps(state, sort_keys=True),
            status="running" if state.get("running") else "stopped",
        )

    async def get_demo_state(self) -> dict[str, Any]:
        payload = await self._get_record("demo_state", "demo")
        return json.loads(payload) if payload else {"running": False, "step": 0}
