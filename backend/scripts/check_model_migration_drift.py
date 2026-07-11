#!/usr/bin/env python3
"""Check registered SQLAlchemy metadata against migrated PostgreSQL schema."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect

from app.db import Base
from app.model_registry import (
    REPRESENTATIVE_TABLES,
    declared_model_modules,
    register_all_models,
)
from app.settings import get_settings

DECLARED_MODEL_MODULES = declared_model_modules()
REGISTERED_MODEL_MODULES: tuple[str, ...] = ()

IGNORED_TABLES_WITH_REASON = {
    "alembic_version": "Alembic bookkeeping table, not part of SQLAlchemy domain metadata.",
}

IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON = {
    "uq_auth_throttle_entries_throttle_key": "Legacy equivalent unique index is accepted by column signature.",
    "uq_integration_clients_key_id": "Legacy equivalent unique index is accepted by column signature.",
    "uq_integration_clients_name": "Legacy equivalent unique index is accepted by column signature.",
    "uq_markets_code": "Legacy equivalent unique index is accepted by column signature.",
    "uq_markets_name": "Legacy equivalent unique index is accepted by column signature.",
    "uq_sla_policies_name": "Legacy equivalent unique index is accepted by column signature.",
    "uq_sla_policies_priority": "Legacy equivalent unique index is accepted by column signature.",
    "uq_tags_name": "Legacy equivalent unique index is accepted by column signature.",
    "uq_teams_name": "Legacy equivalent unique index is accepted by column signature.",
    "uq_tickets_ticket_no": "Legacy equivalent unique index is accepted by column signature.",
    "uq_users_email": "Legacy equivalent unique index is accepted by column signature.",
    "uq_users_username": "Legacy equivalent unique index is accepted by column signature.",
}

REQUIRED_CHECK_CONSTRAINTS: dict[str, set[str]] = {
    "case_contexts": {"ck_case_context_active_requires_identity"},
    "operations_dispatch_outbox": {
        "ck_operations_dispatch_outbox_status",
        "ck_operations_dispatch_outbox_attempt_count_nonnegative",
        "ck_operations_dispatch_outbox_max_attempts_positive",
        "ck_operations_dispatch_outbox_attempt_count_bounded",
        "ck_operations_dispatch_outbox_lease_state",
        "ck_operations_dispatch_outbox_retry_timestamp",
        "ck_operations_dispatch_outbox_dispatched_timestamp",
        "ck_operations_dispatch_outbox_cancelled_timestamp",
    },
}

REQUIRED_INDEXES: dict[str, set[str]] = {
    "case_contexts": {
        "ix_case_contexts_is_active",
        "uq_case_context_active_conversation_only",
        "uq_case_context_active_ticket_only",
        "uq_case_context_active_conversation_ticket",
    },
    "operations_dispatch_outbox": {
        "ix_operations_dispatch_outbox_ticket_id",
        "ix_operations_dispatch_outbox_routing_rule_id",
        "ix_operations_dispatch_outbox_status",
        "ix_operations_dispatch_outbox_next_retry_at",
        "ix_operations_dispatch_outbox_lease_owner",
        "ix_operations_dispatch_outbox_lease_expires_at",
        "ix_operations_dispatch_outbox_error_category",
        "ix_operations_dispatch_outbox_created_at",
        "ix_operations_dispatch_outbox_updated_at",
        "ix_operations_dispatch_outbox_dispatched_at",
        "ix_operations_dispatch_outbox_cancelled_at",
        "ix_operations_dispatch_outbox_scope",
        "ix_operations_dispatch_outbox_due",
        "ix_operations_dispatch_outbox_lease",
    },
    "operator_queue_scope_grants": {
        "ix_operator_queue_scope_grants_user_id",
        "ix_operator_queue_scope_grants_granted_by",
        "ix_operator_queue_scope_grants_user_enabled",
        "ix_operator_queue_scope_grants_scope",
    },
}


@dataclass(frozen=True)
class Drift:
    kind: str
    name: str
    detail: str

    def render(self) -> str:
        return f"[{self.kind}] {self.name}: {self.detail}"


def _register_model_metadata() -> tuple[str, ...]:
    """Register required models and expose evidence for this attempt only."""

    global REGISTERED_MODEL_MODULES
    REGISTERED_MODEL_MODULES = ()
    registered = tuple(register_all_models())
    REGISTERED_MODEL_MODULES = registered
    return registered


def _column_signature(column_names) -> frozenset[str]:
    return frozenset(str(name) for name in (column_names or []) if name)


def _metadata_unique_constraints(table) -> dict[str, frozenset[str]]:
    return {
        constraint.name: _column_signature(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }


def _is_partial_unique_index(index: dict) -> bool:
    options = index.get("dialect_options") or {}
    return bool(
        options.get("postgresql_where")
        or options.get("sqlite_where")
        or index.get("where")
    )


def _database_unique_contracts(inspector, table_name: str) -> tuple[set[str], set[frozenset[str]]]:
    names: set[str] = set()
    signatures: set[frozenset[str]] = set()

    for item in inspector.get_unique_constraints(table_name):
        name = item.get("name")
        signature = _column_signature(item.get("column_names"))
        if name:
            names.add(str(name))
        if signature:
            signatures.add(signature)

    for item in inspector.get_indexes(table_name):
        if not item.get("unique") or _is_partial_unique_index(item):
            continue
        name = item.get("name")
        signature = _column_signature(item.get("column_names"))
        if name:
            names.add(str(name))
        if signature:
            signatures.add(signature)

    return names, signatures


def _metadata_check_constraints(table) -> set[str]:
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }


def metadata_registration_drift() -> list[Drift]:
    metadata_tables = set(Base.metadata.tables)
    declared = set(DECLARED_MODEL_MODULES)
    registered = set(REGISTERED_MODEL_MODULES)
    drift: list[Drift] = []

    for module_name in sorted(declared - registered):
        drift.append(Drift(
            "missing_registered_model_module",
            module_name,
            "declared model module was not imported into the runtime metadata contract",
        ))
    for module_name in sorted(registered - declared):
        drift.append(Drift(
            "unexpected_registered_model_module",
            module_name,
            "model module was imported without an active registry declaration",
        ))

    for module_name in sorted(declared):
        table_name = REPRESENTATIVE_TABLES.get(module_name)
        if not table_name:
            drift.append(Drift(
                "missing_representative_table_declaration",
                module_name,
                "declared model module has no representative table contract",
            ))
            continue
        if table_name not in metadata_tables:
            drift.append(Drift(
                "unregistered_model_table",
                module_name,
                f"expected representative table {table_name!r} in Base.metadata",
            ))
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

        db_unique_names, db_unique_signatures = _database_unique_contracts(inspector, table_name)
        metadata_unique = _metadata_unique_constraints(Base.metadata.tables[table_name])
        for constraint_name, signature in sorted(metadata_unique.items()):
            if constraint_name in db_unique_names or signature in db_unique_signatures:
                continue
            if constraint_name in IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON:
                continue
            columns = ",".join(sorted(signature)) or "unknown"
            drift.append(Drift(
                "missing_unique_constraint",
                f"{table_name}.{constraint_name}",
                f"no database unique constraint or non-partial unique index covers columns [{columns}]",
            ))

        required_checks = REQUIRED_CHECK_CONSTRAINTS.get(table_name, set())
        if required_checks:
            metadata_checks = _metadata_check_constraints(Base.metadata.tables[table_name])
            for constraint_name in sorted(required_checks - metadata_checks):
                drift.append(Drift("missing_metadata_check", f"{table_name}.{constraint_name}", "required check is absent from ORM metadata"))
            db_checks = {item.get("name") for item in inspector.get_check_constraints(table_name) if item.get("name")}
            for constraint_name in sorted(required_checks - db_checks):
                drift.append(Drift("missing_check_constraint", f"{table_name}.{constraint_name}", "required safety check is absent from database"))

        required_indexes = REQUIRED_INDEXES.get(table_name, set())
        if required_indexes:
            metadata_indexes = {index.name for index in Base.metadata.tables[table_name].indexes if index.name}
            for index_name in sorted(required_indexes - metadata_indexes):
                drift.append(Drift("missing_metadata_index", f"{table_name}.{index_name}", "required index is absent from ORM metadata"))
            db_indexes = {item.get("name") for item in inspector.get_indexes(table_name) if item.get("name")}
            for index_name in sorted(required_indexes - db_indexes):
                drift.append(Drift("missing_index", f"{table_name}.{index_name}", "required safety index is absent from database"))

    return drift


def _write_report(path: str | None, *, status: str, drift: list[Drift], error: str | None = None) -> None:
    if not path:
        return
    payload = {
        "schema": "nexus.model_migration_drift.v1",
        "status": status,
        "declared_model_modules": list(DECLARED_MODEL_MODULES),
        "registered_model_modules": list(REGISTERED_MODEL_MODULES),
        "drift_count": len(drift),
        "drift": [asdict(item) for item in drift],
        "error": error,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-json", default=None, help="Write a non-sensitive JSON drift report to this path.")
    return parser.parse_args(argv or [])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _write_report(args.report_json, status="started", drift=[])
    try:
        _register_model_metadata()
    except Exception as exc:
        error_type = type(exc).__name__[:80]
        _write_report(
            args.report_json,
            status="model_registry_error",
            drift=[],
            error=error_type,
        )
        print(f"ERROR: required model registration failed ({error_type})", file=sys.stderr)
        return 3

    try:
        settings = get_settings()
        if not settings.is_postgres:
            error = "check_model_migration_drift.py must run against PostgreSQL DATABASE_URL"
            _write_report(args.report_json, status="unsupported_database", drift=[], error=error)
            print("ERROR: " + error, file=sys.stderr)
            return 2

        engine = create_engine(settings.database_url, future=True)
        try:
            drift = collect_schema_drift(inspect(engine))
        finally:
            engine.dispose()
    except Exception as exc:
        error_type = type(exc).__name__[:80]
        _write_report(args.report_json, status="internal_error", drift=[], error=error_type)
        print(f"ERROR: model migration drift inspection failed ({error_type})", file=sys.stderr)
        return 3

    if drift:
        _write_report(args.report_json, status="drift_detected", drift=drift)
        print("Model / migration drift detected:", file=sys.stderr)
        for item in drift:
            print(" - " + item.render(), file=sys.stderr)
        return 1

    _write_report(args.report_json, status="ok", drift=[])
    print("model migration drift check ok")
    print("registered model modules: " + ", ".join(REGISTERED_MODEL_MODULES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
