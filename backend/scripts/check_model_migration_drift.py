#!/usr/bin/env python3
"""Check registered SQLAlchemy metadata against migrated PostgreSQL schema."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect

from app.db import Base
from app.model_registry import REPRESENTATIVE_TABLES, register_all_models
from app.settings import get_settings

REGISTERED_MODEL_MODULES = register_all_models()

IGNORED_TABLES_WITH_REASON = {
    "alembic_version": "Alembic bookkeeping table, not part of SQLAlchemy domain metadata.",
}

IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON = {
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

REQUIRED_CHECK_CONSTRAINTS: dict[str, set[str]] = {
    "case_contexts": {"ck_case_context_active_requires_identity"},
}

REQUIRED_INDEXES: dict[str, set[str]] = {
    "case_contexts": {
        "ix_case_contexts_is_active",
        "uq_case_context_active_conversation_only",
        "uq_case_context_active_ticket_only",
        "uq_case_context_active_conversation_ticket",
    },
}


@dataclass(frozen=True)
class Drift:
    kind: str
    name: str
    detail: str

    def render(self) -> str:
        return f"[{self.kind}] {self.name}: {self.detail}"


def _metadata_unique_constraints(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if isinstance(constraint, UniqueConstraint) and constraint.name}


def _metadata_check_constraints(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint) and constraint.name}


def metadata_registration_drift() -> list[Drift]:
    metadata_tables = set(Base.metadata.tables)
    registered = set(REGISTERED_MODEL_MODULES)
    drift: list[Drift] = []
    for module_name, table_name in REPRESENTATIVE_TABLES.items():
        if module_name not in registered:
            continue
        if table_name not in metadata_tables:
            drift.append(Drift("unregistered_model_table", module_name, f"expected representative table {table_name!r} in Base.metadata"))
    return drift


def collect_schema_drift(inspector) -> list[Drift]:
    db_tables = set(inspector.get_table_names())
    metadata_tables = set(Base.metadata.tables)
    drift = metadata_registration_drift()

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
            if constraint_name not in IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON:
                drift.append(Drift("missing_unique_constraint", f"{table_name}.{constraint_name}", "unique constraint exists in metadata but not in database"))

        required_checks = REQUIRED_CHECK_CONSTRAINTS.get(table_name, set())
        if required_checks:
            metadata_checks = _metadata_check_constraints(Base.metadata.tables[table_name])
            for constraint_name in sorted(required_checks - metadata_checks):
                drift.append(Drift("missing_metadata_check", f"{table_name}.{constraint_name}", "required check is absent from ORM metadata"))
            db_checks = {item.get("name") for item in inspector.get_check_constraints(table_name) if item.get("name")}
            for constraint_name in sorted(required_checks - db_checks):
                drift.append(Drift("missing_check_constraint", f"{table_name}.{constraint_name}", "required lifecycle check is absent from database"))

        required_indexes = REQUIRED_INDEXES.get(table_name, set())
        if required_indexes:
            metadata_indexes = {index.name for index in Base.metadata.tables[table_name].indexes if index.name}
            for index_name in sorted(required_indexes - metadata_indexes):
                drift.append(Drift("missing_metadata_index", f"{table_name}.{index_name}", "required index is absent from ORM metadata"))
            db_indexes = {item.get("name") for item in inspector.get_indexes(table_name) if item.get("name")}
            for index_name in sorted(required_indexes - db_indexes):
                drift.append(Drift("missing_index", f"{table_name}.{index_name}", "required lifecycle index is absent from database"))

    return drift


def main() -> int:
    settings = get_settings()
    if not settings.is_postgres:
        print("ERROR: check_model_migration_drift.py must run against PostgreSQL DATABASE_URL", file=sys.stderr)
        return 2

    engine = create_engine(settings.database_url, future=True)
    try:
        drift = collect_schema_drift(inspect(engine))
    finally:
        engine.dispose()

    if drift:
        print("Model / migration drift detected:", file=sys.stderr)
        for item in drift:
            print(" - " + item.render(), file=sys.stderr)
        return 1

    print("model migration drift check ok")
    print("registered model modules: " + ", ".join(REGISTERED_MODEL_MODULES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
