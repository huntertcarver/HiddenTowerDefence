from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from google.cloud import spanner

from app.models import (
    Approval,
    ApprovalStatus,
    Incident,
    SourceItem,
    TowerEvent,
    TriageResult,
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
            CREATE TABLE IF NOT EXISTS triage_results (
              source_item_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
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

    async def _mutation(
        self, operation: str, table: str, columns: list[str], values: list[list[Any]]
    ) -> None:
        def write() -> None:
            with self.database.batch() as batch:
                getattr(batch, operation)(table, columns, values)

        await asyncio.to_thread(write)

    async def store_source_item(self, item: SourceItem) -> bool:
        existing = await self._read_one(
            "SELECT id FROM SourceItems WHERE id = @id",
            {"id": item.id},
        )
        if existing:
            return False
        await self._mutation(
            "insert",
            "SourceItems",
            ["id", "payload", "created_at"],
            [[item.id, item.model_dump_json(), item.received_at.isoformat()]],
        )
        return True

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

    async def append_event(self, event: TowerEvent) -> TowerEvent:
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
                    json.dumps(event.payload, sort_keys=True),
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
                    TowerEvent(
                        id=row[0],
                        type=row[1],
                        source_item_id=row[2],
                        trust_state=row[3],
                        payload=json.loads(row[4]),
                        occurred_at=datetime.fromisoformat(row[5]),
                    )
                    for row in rows
                ]

        return await asyncio.to_thread(read)

    async def create_approval(self, approval: Approval) -> Approval:
        await self._mutation(
            "insert",
            "Approvals",
            ["id", "source_item_id", "action", "arguments", "status", "created_at", "resolved_at"],
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
        return approval

    async def list_approvals(self) -> list[Approval]:
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
        existing = await self._read_one(
            """
            SELECT id, source_item_id, action, arguments, status, created_at, resolved_at
            FROM Approvals WHERE id = @id
            """,
            {"id": approval_id},
        )
        if existing is None or existing[4] != ApprovalStatus.PENDING.value:
            return None
        resolved_at = datetime.now().astimezone().isoformat()
        await self._mutation(
            "update",
            "Approvals",
            ["id", "status", "resolved_at"],
            [[approval_id, status.value, resolved_at]],
        )
        return Approval(
            id=existing[0],
            source_item_id=existing[1],
            action=existing[2],
            arguments=json.loads(existing[3]),
            status=status,
            created_at=datetime.fromisoformat(existing[5]),
            resolved_at=datetime.fromisoformat(resolved_at),
        )

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
        return incident

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
