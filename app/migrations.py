from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path

from google.cloud import spanner

from app.config import Settings
from app.repositories import SQLiteRepository

INITIAL_TABLES = {
    "Approvals",
    "EventSequence",
    "Events",
    "Incidents",
    "RuntimeState",
    "SourceItems",
    "TriageResults",
}


def _statements(path: Path) -> list[str]:
    return [
        statement.strip()
        for statement in path.read_text().split(";")
        if statement.strip()
    ]


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table_names(database: spanner.Database) -> set[str]:
    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(
            """
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = ''
            """
        )
        return {str(row[0]) for row in rows}


def _applied_migrations(database: spanner.Database) -> dict[int, tuple[str, str]]:
    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(
            """
            SELECT version, name, checksum
            FROM SchemaMigrations ORDER BY version
            """
        )
        return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}


def _record_migration(
    database: spanner.Database, version: int, name: str, checksum: str
) -> None:
    def record(transaction: spanner.Transaction) -> None:
        transaction.insert(
            "SchemaMigrations",
            ["version", "name", "checksum", "applied_at"],
            [[version, name, checksum, spanner.COMMIT_TIMESTAMP]],
        )

    database.run_in_transaction(record)


def apply_migrations(project_id: str, instance_id: str, database_id: str) -> None:
    """Apply ordered additive Spanner migrations with safe legacy adoption."""
    database = spanner.Client(project=project_id).instance(instance_id).database(database_id)
    migrations_dir = Path(__file__).with_name("migrations")
    migrations = sorted(migrations_dir.glob("*.sql"))
    tables = _table_names(database)
    has_history = "SchemaMigrations" in tables

    if not has_history:
        initial_present = tables & INITIAL_TABLES
        if initial_present and initial_present != INITIAL_TABLES:
            missing = ", ".join(sorted(INITIAL_TABLES - initial_present))
            raise RuntimeError(f"Refusing to adopt partial initial schema; missing: {missing}")
        first = migrations[0]
        if not initial_present:
            operation = database.update_ddl(_statements(first))
            operation.result()
        second_statements = _statements(migrations[1])
        operation = database.update_ddl([second_statements[0]])
        operation.result()
        _record_migration(database, 1, first.stem, _checksum(first))
        print(f"Baselined {first.name}")

    applied = _applied_migrations(database)
    expected_versions = list(range(1, len(applied) + 1))
    if sorted(applied) != expected_versions:
        raise RuntimeError("Spanner migration history is out of order")

    for version, migration in enumerate(migrations, start=1):
        checksum = _checksum(migration)
        existing = applied.get(version)
        if existing:
            if existing != (migration.stem, checksum):
                raise RuntimeError(f"Spanner migration {version} checksum or name mismatch")
            continue
        statements = _statements(migration)
        if version == 2 and "SchemaMigrations" in _table_names(database):
            statements = statements[1:]
        operation = database.update_ddl(statements)
        operation.result()
        _record_migration(database, version, migration.stem, checksum)
        print(f"Applied {migration.name}")


async def apply_sqlite(path: Path) -> None:
    repository = SQLiteRepository(path)
    await repository.connect()
    await repository.close()
    print(f"SQLite migrations are current at {path}")


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend", choices=("sqlite", "spanner"), default=settings.database_backend
    )
    parser.add_argument("--project", default=settings.spanner_project_id)
    parser.add_argument("--instance", default=settings.spanner_instance_id)
    parser.add_argument("--database", default=settings.spanner_database_id)
    parser.add_argument("--sqlite-path", type=Path, default=settings.resolved_sqlite_path)
    arguments = parser.parse_args()
    if arguments.backend == "sqlite":
        asyncio.run(apply_sqlite(arguments.sqlite_path))
        return
    if not arguments.project:
        parser.error("--project or spanner_project_id is required for Spanner")
    apply_migrations(arguments.project, arguments.instance, arguments.database)


if __name__ == "__main__":
    main()
