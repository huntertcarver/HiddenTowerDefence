from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

import aiosqlite

SQLITE_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "durable_product_model",
        (
            """
            CREATE TABLE IF NOT EXISTS source_items (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS source_comments (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              payload TEXT NOT NULL,
              FOREIGN KEY(source_item_id) REFERENCES source_items(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS runtime_state (
              state_key TEXT PRIMARY KEY,
              state_value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_type TEXT NOT NULL,
              source_item_id TEXT,
              trust_state TEXT,
              payload TEXT NOT NULL,
              occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              action TEXT NOT NULL,
              arguments TEXT NOT NULL,
              status TEXT NOT NULL,
              idempotency_key TEXT,
              tool_request_id TEXT,
              created_at TEXT NOT NULL,
              resolved_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS incidents (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              severity TEXT NOT NULL,
              summary TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              created_at TEXT NOT NULL,
              acknowledged_at TEXT,
              resolved_at TEXT,
              resolution TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS triage_results (
              source_item_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS source_runs (
              id TEXT PRIMARY KEY,
              actor_name TEXT NOT NULL,
              status TEXT NOT NULL,
              payload TEXT NOT NULL,
              started_at TEXT NOT NULL,
              completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS scans (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              boundary TEXT NOT NULL,
              detected INTEGER NOT NULL,
              threat_level TEXT NOT NULL,
              action TEXT NOT NULL,
              normalized_payload TEXT NOT NULL,
              raw_payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS trust_transitions (
              id TEXT PRIMARY KEY,
              source_item_id TEXT,
              from_state TEXT NOT NULL,
              to_state TEXT NOT NULL,
              reason TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS taints (
              source_item_id TEXT PRIMARY KEY,
              active INTEGER NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              resolved_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS tool_requests (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              name TEXT NOT NULL,
              idempotency_key TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS briefs (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS mock_outbox (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS quarantines (
              id TEXT PRIMARY KEY,
              source_item_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS watchlists (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS watchlist_matches (
              id TEXT PRIMARY KEY,
              watchlist_id TEXT NOT NULL,
              source_item_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(watchlist_id, source_item_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS triage_terms (
              source_item_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              value TEXT NOT NULL,
              PRIMARY KEY(source_item_id, kind, value)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS trend_snapshots (
              id TEXT PRIMARY KEY,
              topic TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS query_history (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS heartbeat_leases (
              name TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS demo_state (
              state_key TEXT PRIMARY KEY,
              state_value TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        2,
        "query_indexes",
        (
            "CREATE INDEX IF NOT EXISTS events_by_source ON events(source_item_id, id)",
            "CREATE INDEX IF NOT EXISTS approvals_by_status ON approvals(status, created_at)",
            (
                "CREATE INDEX IF NOT EXISTS incidents_by_source "
                "ON incidents(source_item_id, created_at)"
            ),
            "CREATE INDEX IF NOT EXISTS runs_by_status ON source_runs(status, started_at)",
            "CREATE INDEX IF NOT EXISTS scans_by_source ON scans(source_item_id, created_at)",
            "CREATE INDEX IF NOT EXISTS matches_by_watchlist ON watchlist_matches(watchlist_id)",
        ),
    ),
)


def migration_checksum(statements: Sequence[str]) -> str:
    content = "\n;\n".join(statement.strip() for statement in statements)
    return hashlib.sha256(content.encode()).hexdigest()


async def _upgrade_legacy_sqlite_tables(connection: aiosqlite.Connection) -> None:
    additions = {
        "approvals": (
            ("idempotency_key", "ALTER TABLE approvals ADD COLUMN idempotency_key TEXT"),
            ("tool_request_id", "ALTER TABLE approvals ADD COLUMN tool_request_id TEXT"),
        ),
        "incidents": (
            (
                "status",
                "ALTER TABLE incidents ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
            ),
            ("resolved_at", "ALTER TABLE incidents ADD COLUMN resolved_at TEXT"),
            ("resolution", "ALTER TABLE incidents ADD COLUMN resolution TEXT"),
        ),
    }
    for table, columns in additions.items():
        table_exists = await (
            await connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            )
        ).fetchone()
        if table_exists is None:
            continue
        cursor = await connection.execute(f"PRAGMA table_info({table})")
        existing = {str(row[1]) for row in await cursor.fetchall()}
        for column, statement in columns:
            if column not in existing:
                await connection.execute(statement)
    await connection.commit()


async def apply_sqlite_migrations(connection: aiosqlite.Connection) -> list[int]:
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA journal_mode = WAL")
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          checksum TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    await connection.commit()
    await _upgrade_legacy_sqlite_tables(connection)

    rows = await (
        await connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
    ).fetchall()
    applied = {int(row[0]): (str(row[1]), str(row[2])) for row in rows}
    expected_versions = [version for version, _, _ in SQLITE_MIGRATIONS]
    if sorted(applied) != expected_versions[: len(applied)]:
        raise RuntimeError("SQLite migration history is out of order")

    newly_applied: list[int] = []
    for version, name, statements in SQLITE_MIGRATIONS:
        checksum = migration_checksum(statements)
        existing = applied.get(version)
        if existing is not None:
            if existing != (name, checksum):
                raise RuntimeError(f"SQLite migration {version} checksum or name mismatch")
            continue
        await connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                await connection.execute(statement)
            await connection.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (version, name, checksum, datetime.now(UTC).isoformat()),
            )
        except BaseException:
            await connection.rollback()
            raise
        else:
            await connection.commit()
        newly_applied.append(version)
    return newly_applied
