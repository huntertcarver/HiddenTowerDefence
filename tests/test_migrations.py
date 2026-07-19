from pathlib import Path

import aiosqlite
import pytest

from app.database import apply_sqlite_migrations


async def test_sqlite_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    async with aiosqlite.connect(path) as connection:
        assert await apply_sqlite_migrations(connection) == [1, 2]
        assert await apply_sqlite_migrations(connection) == []


async def test_sqlite_migration_checksum_mismatch_fails(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    async with aiosqlite.connect(path) as connection:
        await apply_sqlite_migrations(connection)
        await connection.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1"
        )
        await connection.commit()
        with pytest.raises(RuntimeError, match="checksum"):
            await apply_sqlite_migrations(connection)


async def test_sqlite_out_of_order_history_fails(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    async with aiosqlite.connect(path) as connection:
        await connection.execute(
            """
            CREATE TABLE schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              checksum TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        await connection.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (2, 'query_indexes', 'invalid', '2026-07-19T00:00:00Z')
            """
        )
        await connection.commit()
        with pytest.raises(RuntimeError, match="out of order"):
            await apply_sqlite_migrations(connection)
