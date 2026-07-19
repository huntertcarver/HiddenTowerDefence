from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

import app.migrations as spanner_migrations
from app.database import apply_sqlite_migrations


class FakeDdlOperation:
    def result(self) -> None:
        return None


class FakeSpannerDatabase:
    def __init__(self) -> None:
        self.ddl_calls: list[list[str]] = []

    def update_ddl(self, statements: list[str]) -> FakeDdlOperation:
        self.ddl_calls.append(statements)
        return FakeDdlOperation()


class FakeSpannerClient:
    def __init__(self, database: FakeSpannerDatabase) -> None:
        self._database = database

    def instance(self, instance_id: str) -> FakeSpannerClient:
        del instance_id
        return self

    def database(self, database_id: str) -> FakeSpannerDatabase:
        del database_id
        return self._database


def configure_spanner_migration_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tables: set[str],
) -> tuple[FakeSpannerDatabase, dict[int, tuple[str, str]]]:
    database = FakeSpannerDatabase()
    applied: dict[int, tuple[str, str]] = {}
    client = FakeSpannerClient(database)
    monkeypatch.setattr(
        spanner_migrations.spanner,
        "Client",
        lambda project: client,
    )
    monkeypatch.setattr(spanner_migrations, "_table_names", lambda _: set(tables))
    monkeypatch.setattr(
        spanner_migrations,
        "_applied_migrations",
        lambda _: dict(applied),
    )

    def record(
        _: FakeSpannerDatabase, version: int, name: str, checksum: str
    ) -> None:
        applied[version] = (name, checksum)

    monkeypatch.setattr(spanner_migrations, "_record_migration", record)
    return database, applied


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


def test_empty_spanner_history_baselines_complete_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = set(spanner_migrations.INITIAL_TABLES) | {"SchemaMigrations"}
    database, applied = configure_spanner_migration_fakes(monkeypatch, tables)

    spanner_migrations.apply_migrations("project", "instance", "database")

    assert sorted(applied) == [1, 2]
    assert all(
        "CREATE TABLE RuntimeState" not in statement
        for call in database.ddl_calls
        for statement in call
    )


def test_empty_spanner_history_applies_v1_to_empty_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, applied = configure_spanner_migration_fakes(
        monkeypatch, {"SchemaMigrations"}
    )

    spanner_migrations.apply_migrations("project", "instance", "database")

    assert sorted(applied) == [1, 2]
    assert any(
        "CREATE TABLE RuntimeState" in statement
        for call in database.ddl_calls
        for statement in call
    )


def test_empty_spanner_history_rejects_partial_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, applied = configure_spanner_migration_fakes(
        monkeypatch, {"SchemaMigrations", "RuntimeState"}
    )

    with pytest.raises(RuntimeError, match="partial initial schema"):
        spanner_migrations.apply_migrations("project", "instance", "database")

    assert applied == {}
    assert database.ddl_calls == []
