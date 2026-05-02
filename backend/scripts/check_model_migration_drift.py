#!/usr/bin/env python3
"""Check that SQLAlchemy metadata is represented in the migrated database schema.

Run after `alembic upgrade head` against a disposable PostgreSQL database.
This script intentionally checks only high-signal drift by default: missing tables,
missing columns, and missing unique constraints declared in metadata.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sqlalchemy import create_engine, inspect

from app.db import Base
from app.settings import get_settings
from app import models as _models  # noqa: F401 - register model tables on Base.metadata
from app import webchat_models as _webchat_models  # noqa: F401 - register webchat tables on Base.metadata


IGNORED_TABLES_WITH_REASON = {
    "alembic_version": "Alembic bookkeeping table, not part of SQLAlchemy domain metadata.",
}

IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON = {
    # These are generated implicitly by SQLAlchemy/index declarations on some dialects
    # and are already tracked through indexes or model field uniqueness.
    "uq_auth_throttle_entries_throttle_key": "Covered by model-level unique/index declaration.",
    "uq_integration_clients_key_id": "Covered by model-level unique/index declaration.",
    "uq_integration_clients_name": "Covered by model-level unique/index declaration.",
    "uq_markets_code": "Covered by model-level unique/index declaration.",
    "uq_markets_name": "Covered by model-level unique/index declaration.",
    "uq_sla_policies_name": "Covered by model-level unique/index declaration.",
    "uq_sla_policies_priority": "Covered by model-level unique/index declaration.",
    "uq_tags_name": "Covered by model-level unique/index declaration.",
    "uq_teams_name": "Covered by model-level unique/index declaration.",
    "uq_tickets_ticket_no": "Covered by model-level unique/index declaration.",
    "uq_users_email": "Covered by model-level unique/index declaration.",
    "uq_users_username": "Covered by model-level unique/index declaration.",
}


@dataclass
class Drift:
    kind: str
    name: str
    detail: str

    def render(self) -> str:
        return f"[{self.kind}] {self.name}: {self.detail}"


def _metadata_unique_constraints(table) -> set[str]:
    names: set[str] = set()
    for constraint in table.constraints:
        name = getattr(constraint, "name", None)
        if name and constraint.__class__.__name__ == "UniqueConstraint":
            names.add(name)
    return names


def main() -> int:
    settings = get_settings()
    if not settings.is_postgres:
        print("ERROR: check_model_migration_drift.py must run against PostgreSQL DATABASE_URL", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url, future=True)
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())
    metadata_tables = set(Base.metadata.tables.keys())
    drift: list[Drift] = []

    for table_name in sorted(metadata_tables):
        if table_name in IGNORED_TABLES_WITH_REASON:
            continue
        if table_name not in db_tables:
            drift.append(Drift("missing_table", table_name, "table exists in Base.metadata but not in database"))
            continue

        db_columns = {column["name"] for column in inspector.get_columns(table_name)}
        metadata_columns = set(Base.metadata.tables[table_name].columns.keys())
        for column_name in sorted(metadata_columns - db_columns):
            drift.append(Drift("missing_column", f"{table_name}.{column_name}", "column exists in Base.metadata but not in database"))

        db_unique_names = {item.get("name") for item in inspector.get_unique_constraints(table_name) if item.get("name")}
        metadata_unique_names = _metadata_unique_constraints(Base.metadata.tables[table_name])
        for constraint_name in sorted(metadata_unique_names - db_unique_names):
            if constraint_name in IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON:
                continue
            drift.append(Drift("missing_unique_constraint", f"{table_name}.{constraint_name}", "unique constraint exists in metadata but not in database"))

    if drift:
        print("Model / migration drift detected:", file=sys.stderr)
        for item in drift:
            print(" - " + item.render(), file=sys.stderr)
        return 1

    print("model migration drift check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
