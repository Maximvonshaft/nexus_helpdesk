from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import bindparam, create_engine, inspect, text

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import tenant_principal_preflight as preflight
import tenant_principal_resolution as resolution

SOURCE_SCHEMA_REVISION = "20260713_0059"
RECEIPT_SCHEMA = "nexus_tenant_backfill_receipt_v1"
ASSIGNMENT_SOURCE = "mapping_manifest"
MAX_BATCH_SIZE = 5_000
MAX_RECEIPT_BYTES = 512 * 1024
MAX_SIGNING_KEY_BYTES = 4 * 1024
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,119}$")
CORE_ORDER = resolution.CORE_ORDER
CORE_COLUMNS = {
    "markets": ("id", "code", "tenant_id", "tenant_assignment_source", "tenant_assignment_version"),
    "teams": ("id", "market_id", "tenant_id", "tenant_assignment_source", "tenant_assignment_version"),
    "users": ("id", "team_id", "tenant_id", "tenant_assignment_source", "tenant_assignment_version"),
    "channel_accounts": ("id", "market_id", "tenant_id", "tenant_assignment_source", "tenant_assignment_version"),
    "customers": ("id", "tenant_id", "tenant_assignment_source", "tenant_assignment_version"),
    "tickets": (
        "id",
        "customer_id",
        "market_id",
        "team_id",
        "channel_account_id",
        "assignee_id",
        "created_by",
        "tenant_id",
        "tenant_assignment_source",
        "tenant_assignment_version",
    ),
}


class TenantBackfillError(ValueError):
    pass


def _canonical_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "tenants": sorted(
            (
                {
                    "tenant_key": item["tenant_key"],
                    "display_name": item["display_name"],
                }
                for item in manifest["tenants"]
            ),
            key=lambda item: item["tenant_key"],
        ),
        **{
            section: dict(sorted(manifest[section].items()))
            for section in preflight._MAPPING_SECTIONS
        },
    }


def _manifest_digest(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_manifest(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


_table_names = resolution.table_names
_columns = resolution.columns
_foreign_keys = resolution.foreign_keys
_fetch_rows = resolution.fetch_rows


def _schema_revision(connection) -> str:
    tables = _table_names(inspect(connection))
    if "alembic_version" not in tables:
        raise TenantBackfillError("tenant_backfill_schema_revision_missing")
    rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if rows != [SOURCE_SCHEMA_REVISION]:
        raise TenantBackfillError("tenant_backfill_schema_revision_mismatch")
    return rows[0]


def _resolve_assignments(
    connection,
    inspector,
    manifest: dict[str, Any],
    findings: preflight.Findings,
    *,
    lock_rows: bool = False,
) -> tuple[dict[str, dict[int, str]], dict[str, int]]:
    assignments, record_counts, _used = resolution.resolve_assignments(
        connection,
        inspector,
        manifest,
        findings,
        lock_rows=lock_rows,
    )
    return assignments, record_counts


def _load_existing_principals(
    connection,
    manifest: dict[str, Any],
    findings: preflight.Findings,
) -> tuple[dict[str, int], set[str]]:
    rows = connection.execute(
        text("SELECT id, tenant_key, display_name, is_active FROM tenants")
    ).mappings().all()
    expected = {
        item["tenant_key"]: item["display_name"]
        for item in manifest["tenants"]
    }
    result: dict[str, int] = {}
    observed_expected_keys: set[str] = set()
    for row in rows:
        key = str(row["tenant_key"] or "")
        display = str(row["display_name"] or "")
        if key not in expected:
            findings.add(
                "tenant.principal_key_unknown",
                kind="tenants",
                record_id=row["id"],
            )
            continue
        observed_expected_keys.add(key)
        if display != expected[key]:
            findings.add(
                "tenant.principal_display_conflict",
                kind="tenants",
                record_id=row["id"],
            )
            continue
        if not bool(row.get("is_active")):
            findings.add(
                "tenant.principal_inactive",
                kind="tenants",
                record_id=row["id"],
            )
            continue
        result[key] = int(row["id"])
    return result, observed_expected_keys


def _is_relational_tenant_id(inspector, table_name: str, column_name: str) -> bool:
    if column_name != "tenant_id":
        return False
    for fk in _foreign_keys(inspector, table_name):
        if (
            fk.get("referred_table") == "tenants"
            and fk.get("referred_columns") == ["id"]
            and fk.get("constrained_columns") == [column_name]
        ):
            return True
    return False


def _validate_non_core_tenant_columns(
    connection,
    inspector,
    manifest: dict[str, Any],
    principal_ids: dict[str, int],
    findings: preflight.Findings,
) -> None:
    tenant_keys = set(manifest["tenant_keys"])
    core = set(CORE_ORDER) | {"tenants"}
    preparer = connection.dialect.identifier_preparer
    for table_name in sorted(_table_names(inspector) - core):
        columns = {item["name"] for item in _columns(inspector, table_name)}
        for column_name in sorted(columns & {"tenant_id", "tenant_key"}):
            table = preparer.quote(table_name)
            column = preparer.quote(column_name)
            rows = connection.execute(
                text(f"SELECT CAST({column} AS TEXT) AS value, count(*) AS count FROM {table} GROUP BY CAST({column} AS TEXT)")
            ).all()
            for row in rows:
                value = str(row.value or "").strip().lower()
                count = int(row.count)
                reason = None
                if not value:
                    reason = "tenant.existing_value_missing"
                elif value == "default":
                    reason = "tenant.existing_default_forbidden"
                elif _is_relational_tenant_id(inspector, table_name, column_name):
                    if not value.isdigit() or int(value) not in set(principal_ids.values()):
                        reason = "tenant.existing_principal_unknown"
                elif value not in tenant_keys:
                    reason = "tenant.existing_value_unknown"
                if reason:
                    for index in range(min(count, preflight.MAX_ISSUE_SAMPLES)):
                        findings.add(reason, kind=f"{table_name}.{column_name}", record_id=index)
                    if count > preflight.MAX_ISSUE_SAMPLES:
                        findings.counts[reason] += count - preflight.MAX_ISSUE_SAMPLES


def _lock_tenant_scope_tables(connection, inspector) -> None:
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    lock_tables: list[str] = []
    for table_name in sorted(resolution.table_names(inspector)):
        available = {item["name"] for item in resolution.columns(inspector, table_name)}
        if table_name in CORE_ORDER or table_name == "tenants" or available & {"tenant_id", "tenant_key"}:
            lock_tables.append(preparer.quote(table_name))
    if lock_tables:
        try:
            connection.execute(
                text(
                    "LOCK TABLE "
                    + ", ".join(lock_tables)
                    + " IN SHARE ROW EXCLUSIVE MODE NOWAIT"
                )
            )
        except sa.exc.OperationalError as exc:
            raise TenantBackfillError("tenant_backfill_scope_lock_unavailable") from exc


def _classify_core_state(
    rows_by_table: dict[str, list[dict[str, Any]]],
    assignments: dict[str, dict[int, str]],
    principal_ids: dict[str, int],
    digest: str,
    findings: preflight.Findings,
) -> tuple[dict[str, list[int]], dict[str, int]]:
    planned: dict[str, list[int]] = {table: [] for table in CORE_ORDER}
    already: dict[str, int] = {table: 0 for table in CORE_ORDER}
    for table_name in CORE_ORDER:
        expected = assignments[table_name]
        for row in rows_by_table[table_name]:
            record_id = int(row["id"])
            tenant_key = expected.get(record_id)
            if tenant_key is None:
                continue
            tenant_id = row.get("tenant_id")
            source = row.get("tenant_assignment_source")
            version = row.get("tenant_assignment_version")
            if tenant_id is None and source is None and version is None:
                planned[table_name].append(record_id)
                continue
            expected_id = principal_ids.get(tenant_key)
            if expected_id is not None and tenant_id == expected_id and source == ASSIGNMENT_SOURCE and version == digest:
                already[table_name] += 1
                continue
            findings.add("tenant.backfill_existing_assignment_conflict", kind=table_name, record_id=record_id)
    return planned, already


def _receipt_signature(payload: dict[str, Any], signing_key: bytes) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "hmac-sha256:" + hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()


def verify_receipt_signature(payload: dict[str, Any], signing_key: bytes) -> bool:
    signature = payload.get("receipt_signature")
    if not isinstance(signature, str):
        return False
    unsigned = dict(payload)
    unsigned.pop("receipt_signature", None)
    return hmac.compare_digest(signature, _receipt_signature(unsigned, signing_key))


def _render_receipt(
    payload: dict[str, Any],
    *,
    signing_key: bytes | None = None,
    signing_key_id: str | None = None,
) -> bytes:
    rendered = dict(payload)
    if signing_key is not None:
        rendered["receipt_signing_key_id"] = signing_key_id
        rendered["receipt_signature"] = _receipt_signature(rendered, signing_key)
    encoded = (json.dumps(rendered, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise TenantBackfillError("tenant_backfill_receipt_excessive")
    return encoded


def _prepare_receipt(
    output_path: Path,
    payload: dict[str, Any],
    *,
    signing_key: bytes | None = None,
    signing_key_id: str | None = None,
) -> Path:
    encoded = _render_receipt(
        payload,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path = output_path.with_name(output_path.name + ".pending")
    if output_path.exists() or pending_path.exists():
        raise TenantBackfillError("tenant_backfill_receipt_path_exists")
    try:
        with pending_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pending_path.unlink(missing_ok=True)
        raise
    return pending_path


def _confirm_pending_receipt(
    pending_path: Path,
    payload: dict[str, Any],
    *,
    signing_key: bytes | None = None,
    signing_key_id: str | None = None,
) -> None:
    encoded = _render_receipt(
        payload,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
    )
    confirmed_path = pending_path.with_name(pending_path.name + ".confirmed")
    if confirmed_path.exists():
        raise TenantBackfillError("tenant_backfill_receipt_confirmation_path_exists")
    try:
        with confirmed_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(confirmed_path, pending_path)
    except OSError as exc:
        confirmed_path.unlink(missing_ok=True)
        raise TenantBackfillError(
            f"tenant_backfill_receipt_confirmation_failed:{pending_path}"
        ) from exc


def _publish_receipt(pending_path: Path, output_path: Path) -> None:
    try:
        os.replace(pending_path, output_path)
    except OSError as exc:
        raise TenantBackfillError(
            f"tenant_backfill_receipt_publish_failed:{pending_path}"
        ) from exc
    try:
        directory_fd = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise TenantBackfillError(
            f"tenant_backfill_receipt_directory_sync_failed:{output_path}"
        ) from exc


def run_backfill(
    database_url: str,
    manifest_path: Path,
    output_path: Path,
    *,
    apply: bool = False,
    batch_size: int = 200,
    max_batches: int | None = None,
    expected_mapping_digest: str | None = None,
    receipt_signing_key: bytes | None = None,
    receipt_signing_key_id: str | None = None,
) -> int:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise TenantBackfillError("tenant_backfill_batch_size_invalid")
    if max_batches is not None and max_batches < 1:
        raise TenantBackfillError("tenant_backfill_max_batches_invalid")

    manifest = preflight._load_manifest(manifest_path)
    digest = _manifest_digest(manifest)
    if expected_mapping_digest is not None:
        if not _DIGEST_RE.fullmatch(expected_mapping_digest):
            raise TenantBackfillError("tenant_backfill_expected_digest_invalid")
        if not hmac.compare_digest(expected_mapping_digest, digest):
            raise TenantBackfillError("tenant_backfill_mapping_digest_mismatch")
    if apply:
        if expected_mapping_digest is None:
            raise TenantBackfillError("tenant_backfill_expected_digest_required")
        if receipt_signing_key is None or not 32 <= len(receipt_signing_key) <= MAX_SIGNING_KEY_BYTES:
            raise TenantBackfillError("tenant_backfill_receipt_signing_key_invalid")
        if receipt_signing_key_id is None or not _KEY_ID_RE.fullmatch(receipt_signing_key_id):
            raise TenantBackfillError("tenant_backfill_receipt_signing_key_id_invalid")
    elif receipt_signing_key is not None:
        if not 32 <= len(receipt_signing_key) <= MAX_SIGNING_KEY_BYTES:
            raise TenantBackfillError("tenant_backfill_receipt_signing_key_invalid")
        if receipt_signing_key_id is None or not _KEY_ID_RE.fullmatch(receipt_signing_key_id):
            raise TenantBackfillError("tenant_backfill_receipt_signing_key_id_invalid")
    findings = preflight.Findings()
    applied_counts = {table: 0 for table in CORE_ORDER}
    already_counts = {table: 0 for table in CORE_ORDER}
    planned_counts = {table: 0 for table in CORE_ORDER}
    remaining_counts = {table: 0 for table in CORE_ORDER}
    record_counts: dict[str, int] = {}
    principal_counts = {
        "declared": len(manifest["tenant_keys"]),
        "observed_existing": 0,
        "accepted_existing": 0,
        "planned_create": 0,
        "created": 0,
    }
    mutation_performed = False
    status = "fail"

    engine = create_engine(database_url, future=True)
    pending_receipt: Path | None = None
    payload: dict[str, Any] | None = None
    try:
        transaction_context = engine.begin()
        with transaction_context as connection:
            if connection.dialect.name == "postgresql" and not apply:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
            if apply and connection.dialect.name == "postgresql":
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
                lock_acquired = connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(7140059)")
                ).scalar_one()
                if not lock_acquired:
                    raise TenantBackfillError("tenant_backfill_lock_unavailable")
            _schema_revision(connection)
            inspector = inspect(connection)
            if apply:
                _lock_tenant_scope_tables(connection, inspector)
            assignments, record_counts = _resolve_assignments(
                connection,
                inspector,
                manifest,
                findings,
                lock_rows=apply,
            )
            (
                existing_principals,
                observed_principal_keys,
            ) = _load_existing_principals(connection, manifest, findings)
            declared_keys = sorted(manifest["tenant_keys"])
            principal_counts["observed_existing"] = len(observed_principal_keys)
            principal_counts["accepted_existing"] = len(existing_principals)
            principal_counts["planned_create"] = len(
                set(declared_keys) - observed_principal_keys
            )
            _validate_non_core_tenant_columns(
                connection,
                inspector,
                manifest,
                existing_principals,
                findings,
            )
            rows_by_table = {
                table: _fetch_rows(
                    connection,
                    inspector,
                    table,
                    CORE_COLUMNS[table],
                    lock_rows=apply,
                )
                for table in CORE_ORDER
            }
            planned, already_counts = _classify_core_state(
                rows_by_table,
                assignments,
                existing_principals,
                digest,
                findings,
            )
            planned_counts = {table: len(ids) for table, ids in planned.items()}
            remaining_counts = dict(planned_counts)

            if findings.as_dict()["issue_count"] == 0 and apply:
                display_names = {
                    item["tenant_key"]: item["display_name"]
                    for item in manifest["tenants"]
                }
                for tenant_key in declared_keys:
                    if tenant_key not in existing_principals:
                        result = connection.execute(
                            text(
                                "INSERT INTO tenants (tenant_key, display_name, is_active) "
                                "VALUES (:tenant_key, :display_name, true) RETURNING id"
                            ),
                            {"tenant_key": tenant_key, "display_name": display_names[tenant_key]},
                        )
                        existing_principals[tenant_key] = int(result.scalar_one())
                        principal_counts["created"] += 1
                        mutation_performed = True

                batches_used = 0
                for table_name in CORE_ORDER:
                    ids = planned[table_name]
                    for offset in range(0, len(ids), batch_size):
                        if max_batches is not None and batches_used >= max_batches:
                            break
                        batch = ids[offset : offset + batch_size]
                        by_tenant: dict[str, list[int]] = defaultdict(list)
                        for record_id in batch:
                            by_tenant[assignments[table_name][record_id]].append(record_id)
                        for tenant_key, tenant_ids in sorted(by_tenant.items()):
                            statement = text(
                                f"UPDATE {table_name} SET tenant_id=:tenant_id, "
                                "tenant_assignment_source=:source, tenant_assignment_version=:version "
                                "WHERE id IN :ids AND tenant_id IS NULL "
                                "AND tenant_assignment_source IS NULL AND tenant_assignment_version IS NULL"
                            ).bindparams(bindparam("ids", expanding=True))
                            result = connection.execute(
                                statement,
                                {
                                    "tenant_id": existing_principals[tenant_key],
                                    "source": ASSIGNMENT_SOURCE,
                                    "version": digest,
                                    "ids": tenant_ids,
                                },
                            )
                            if result.rowcount != len(tenant_ids):
                                raise TenantBackfillError("tenant_backfill_concurrent_drift")
                            applied_counts[table_name] += len(tenant_ids)
                            mutation_performed = True
                        batches_used += 1
                    remaining_counts[table_name] = planned_counts[table_name] - applied_counts[table_name]
                    if max_batches is not None and batches_used >= max_batches:
                        for later in CORE_ORDER[CORE_ORDER.index(table_name) + 1 :]:
                            remaining_counts[later] = planned_counts[later]
                        break

            issue_data = findings.as_dict()
            if issue_data["issue_count"]:
                status = "fail"
            elif any(remaining_counts.values()):
                status = "partial" if apply else "pass"
            else:
                status = "pass"

            payload = {
                "schema_version": RECEIPT_SCHEMA,
                "source_schema_revision": SOURCE_SCHEMA_REVISION,
                "mapping_digest": digest,
                "mode": "apply" if apply else "dry_run",
                "status": status,
                "batch_size": batch_size,
                "max_batches": max_batches,
                "tenant_count": len(manifest["tenant_keys"]),
                "principal_counts": principal_counts,
                "record_counts": record_counts,
                "planned_counts": planned_counts,
                "already_applied_counts": already_counts,
                "applied_counts": applied_counts,
                "remaining_counts": remaining_counts,
                "issues": issue_data,
                "production_mutation_performed": mutation_performed,
                "database_commit_state": (
                    "prepared_uncommitted" if apply else "not_applicable"
                ),
            }
            pending_receipt = _prepare_receipt(
                output_path,
                payload,
                signing_key=receipt_signing_key,
                signing_key_id=receipt_signing_key_id,
            )

        if pending_receipt is None or payload is None:
            raise TenantBackfillError("tenant_backfill_receipt_prepare_missing")
        if apply:
            payload["database_commit_state"] = "committed"
            _confirm_pending_receipt(
                pending_receipt,
                payload,
                signing_key=receipt_signing_key,
                signing_key_id=receipt_signing_key_id,
            )
        _publish_receipt(pending_receipt, output_path)
        return 0 if status in {"pass", "partial"} else 1
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--expected-mapping-digest")
    parser.add_argument("--receipt-hmac-key-file", type=Path)
    parser.add_argument("--receipt-key-id")
    args = parser.parse_args()
    try:
        signing_key = None
        if args.receipt_hmac_key_file is not None:
            if not args.receipt_hmac_key_file.is_file() or args.receipt_hmac_key_file.stat().st_size > MAX_SIGNING_KEY_BYTES:
                raise TenantBackfillError("tenant_backfill_receipt_signing_key_invalid")
            signing_key = args.receipt_hmac_key_file.read_bytes()
        return run_backfill(
            args.database_url,
            args.mapping,
            args.output,
            apply=args.apply,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            expected_mapping_digest=args.expected_mapping_digest,
            receipt_signing_key=signing_key,
            receipt_signing_key_id=args.receipt_key_id,
        )
    except (TenantBackfillError, preflight.TenantPreflightError, OSError, ValueError) as exc:
        print(f"tenant_backfill_error:{exc}", file=sys.stderr)
        return 2
    except sa.exc.SQLAlchemyError:
        print("tenant_backfill_error:tenant_backfill_database_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
