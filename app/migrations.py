from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import spanner

_HISTORY_TABLE = "SchemaMigrations"
_CREATE_INDEX_RE = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+|NULL_FILTERED\s+)?INDEX\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\s+ON\b",
    re.IGNORECASE,
)
_CREATE_TABLE_RE = re.compile(
    r"^CREATE\s+TABLE\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\b",
    re.IGNORECASE,
)


def apply_migrations(project_id: str, instance_id: str, database_id: str) -> None:
    """Apply ordered additive schema files to the configured Spanner database."""

    database = spanner.Client(project=project_id).instance(instance_id).database(database_id)
    _ensure_history_table(database)
    applied = _applied_migrations(database)
    migrations_dir = Path(__file__).with_name("migrations")
    for migration in sorted(migrations_dir.glob("*.sql")):
        if migration.name in applied:
            print(f"Skipped {migration.name}")
            continue

        statements = _pending_statements(database, _split_statements(migration))
        if statements:
            operation = database.update_ddl(statements)
            operation.result()
        _record_migration(database, migration.name)
        print(f"Applied {migration.name}")


def _ensure_history_table(database: spanner.Database) -> None:
    if _table_exists(database, _HISTORY_TABLE):
        return
    operation = database.update_ddl(
        [
            f"""
            CREATE TABLE {_HISTORY_TABLE} (
              name STRING(256) NOT NULL,
              applied_at STRING(64) NOT NULL
            ) PRIMARY KEY (name)
            """
        ]
    )
    operation.result()


def _applied_migrations(database: spanner.Database) -> set[str]:
    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(f"SELECT name FROM {_HISTORY_TABLE}")
        return {row[0] for row in rows}


def _split_statements(migration: Path) -> list[str]:
    return [
        statement.strip()
        for statement in migration.read_text().split(";")
        if statement.strip()
    ]


def _pending_statements(database: spanner.Database, statements: list[str]) -> list[str]:
    pending = []
    for statement in statements:
        table_name = _created_table(statement)
        if table_name is not None and _table_exists(database, table_name):
            continue
        index_name = _created_index(statement)
        if index_name is not None and _index_exists(database, index_name):
            continue
        pending.append(statement)
    return pending


def _created_table(statement: str) -> str | None:
    match = _CREATE_TABLE_RE.match(_ddl_body(statement))
    return match.group(1) if match else None


def _created_index(statement: str) -> str | None:
    match = _CREATE_INDEX_RE.match(_ddl_body(statement))
    return match.group(1) if match else None


def _ddl_body(statement: str) -> str:
    return "\n".join(
        line for line in statement.splitlines() if not line.lstrip().startswith("--")
    ).lstrip()


def _table_exists(database: spanner.Database, table_name: str) -> bool:
    return _has_object(
        database,
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = @name",
        table_name,
    )


def _index_exists(database: spanner.Database, index_name: str) -> bool:
    return _has_object(
        database,
        "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.INDEXES WHERE INDEX_NAME = @name",
        index_name,
    )


def _has_object(database: spanner.Database, sql: str, name: str) -> bool:
    with database.snapshot() as snapshot:
        rows = snapshot.execute_sql(
            sql,
            params={"name": name},
            param_types={"name": spanner.param_types.STRING},
        )
        return next(iter(rows), None) is not None


def _record_migration(database: spanner.Database, migration_name: str) -> None:
    def record(transaction: spanner.Transaction) -> None:
        transaction.insert_or_update(
            _HISTORY_TABLE,
            ["name", "applied_at"],
            [[migration_name, datetime.now(UTC).isoformat()]],
        )

    database.run_in_transaction(record)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--instance", default="smp-prod-shared-spanner")
    parser.add_argument("--database", default="hiddentowerdefence")
    arguments = parser.parse_args()
    apply_migrations(arguments.project, arguments.instance, arguments.database)


if __name__ == "__main__":
    main()
