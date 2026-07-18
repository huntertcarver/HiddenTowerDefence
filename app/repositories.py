from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.models import (
    Approval,
    ApprovalStatus,
    Incident,
    SourceItem,
    TowerEvent,
    TrustState,
)


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
        await self._connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS source_items (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_state (
              state_key TEXT PRIMARY KEY,
              state_value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_type TEXT NOT NULL,
              source_item_id TEXT,
              trust_state TEXT,
              payload TEXT NOT NULL,
              occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              action TEXT NOT NULL,
              arguments TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS incidents (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              severity TEXT NOT NULL,
              summary TEXT NOT NULL,
              created_at TEXT NOT NULL,
              acknowledged_at TEXT
            );
            """
        )
        await self._connection.commit()

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
            return cursor.rowcount == 1

    async def get_source_item(self, source_item_id: str) -> SourceItem | None:
        cursor = await self.connection.execute(
            "SELECT payload FROM source_items WHERE id = ?", (source_item_id,)
        )
        row = await cursor.fetchone()
        return SourceItem.model_validate_json(row["payload"]) if row else None

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

    async def append_event(self, event: TowerEvent) -> TowerEvent:
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
                    json.dumps(event.payload, sort_keys=True),
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
            (after_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            TowerEvent(
                id=row["id"],
                type=row["event_type"],
                source_item_id=row["source_item_id"],
                trust_state=row["trust_state"],
                payload=json.loads(row["payload"]),
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        ]

    async def create_approval(self, approval: Approval) -> Approval:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO approvals
                (id, source_item_id, action, arguments, status, created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.source_item_id,
                    approval.action,
                    json.dumps(approval.arguments, sort_keys=True),
                    approval.status.value,
                    approval.created_at.isoformat(),
                    approval.resolved_at.isoformat() if approval.resolved_at else None,
                ),
            )
        return approval

    async def list_approvals(self) -> list[Approval]:
        cursor = await self.connection.execute(
            """
            SELECT id, source_item_id, action, arguments, status, created_at, resolved_at
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
                SELECT id, source_item_id, action, arguments, status, created_at, resolved_at
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
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]),
        )

    async def create_incident(self, incident: Incident) -> Incident:
        async with self.transaction():
            await self.connection.execute(
                """
                INSERT INTO incidents (id, source_item_id, severity, summary, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    incident.id,
                    incident.source_item_id,
                    incident.severity,
                    incident.summary,
                    incident.created_at.isoformat(),
                ),
            )
        return incident
