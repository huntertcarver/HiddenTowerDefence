from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud import spanner


def apply_migrations(project_id: str, instance_id: str, database_id: str) -> None:
    """Apply ordered additive schema files to the configured Spanner database."""

    database = spanner.Client(project=project_id).instance(instance_id).database(database_id)
    migrations_dir = Path(__file__).with_name("migrations")
    for migration in sorted(migrations_dir.glob("*.sql")):
        statements = [
            statement.strip()
            for statement in migration.read_text().split(";")
            if statement.strip()
        ]
        operation = database.update_ddl(statements)
        operation.result()
        print(f"Applied {migration.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--instance", default="smp-prod-shared-spanner")
    parser.add_argument("--database", default="hiddentowerdefence")
    arguments = parser.parse_args()
    apply_migrations(arguments.project, arguments.instance, arguments.database)


if __name__ == "__main__":
    main()
