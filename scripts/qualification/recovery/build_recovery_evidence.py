from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TABLES = 500
MAX_FOREIGN_KEYS = 1000
MAX_EVIDENCE_BYTES = 256 * 1024
_SAFE_REVISION = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class RecoveryEvidenceError(ValueError):
    pass


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryEvidenceError("timestamp_invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryEvidenceError(f"snapshot_invalid:{path.name}") from exc


def _write(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    if len(encoded.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise RecoveryEvidenceError("evidence_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def _validate_revision(value: str, *, reason: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_REVISION.fullmatch(normalized):
        raise RecoveryEvidenceError(reason)
    return normalized


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise RecoveryEvidenceError("backup_file_invalid")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def migration_plan(*, observed_heads: tuple[str, ...], expected_head: str, output: Path) -> int:
    expected = _validate_revision(expected_head, reason="expected_head_invalid")
    normalized = tuple(_validate_revision(item, reason="observed_head_invalid") for item in observed_heads)
    if not normalized:
        raise RecoveryEvidenceError("migration_head_missing")
    if len(normalized) > 1:
        raise RecoveryEvidenceError("migration_heads_multiple")
    if normalized[0] == expected:
        status = "current"
        action = "none"
        code = 0
    else:
        status = "repair_required"
        action = "alembic_upgrade_head"
        code = 1
    _write(
        output,
        {
            "schema_version": "nexus_migration_repair_plan_v1",
            "status": status,
            "action": action,
            "expected_head": expected,
            "observed_heads": list(normalized),
            "apply_authorized": False,
            "production_data_used": False,
            "production_mutation_performed": False,
        },
    )
    return code


def snapshot(database_url: str, output: Path, *, marker_code: str) -> int:
    try:
        from sqlalchemy import create_engine, inspect, text
    except ImportError as exc:
        raise RecoveryEvidenceError("sqlalchemy_unavailable") from exc

    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = sorted(name for name in inspector.get_table_names(schema="public") if name != "alembic_version")
        if len(tables) > MAX_TABLES:
            raise RecoveryEvidenceError("table_count_excessive")
        preparer = engine.dialect.identifier_preparer
        counts: dict[str, int] = {}
        with engine.connect() as connection:
            revision_rows = connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars().all()
            if len(revision_rows) != 1:
                raise RecoveryEvidenceError("alembic_head_invalid")
            revision = _validate_revision(str(revision_rows[0]), reason="alembic_head_invalid")
            for table_name in tables:
                quoted = preparer.quote(table_name)
                counts[table_name] = int(connection.execute(text(f"SELECT count(*) FROM {quoted}")).scalar_one())
            foreign_key_rows = connection.execute(
                text(
                    "SELECT n.nspname, r.relname, c.conname, pg_get_constraintdef(c.oid, true) "
                    "FROM pg_constraint AS c "
                    "JOIN pg_class AS r ON r.oid = c.conrelid "
                    "JOIN pg_namespace AS n ON n.oid = r.relnamespace "
                    "WHERE c.contype = 'f' AND n.nspname = 'public' "
                    "ORDER BY n.nspname, r.relname, c.conname, pg_get_constraintdef(c.oid, true)"
                )
            ).all()
            if len(foreign_key_rows) > MAX_FOREIGN_KEYS:
                raise RecoveryEvidenceError("foreign_key_count_excessive")
            foreign_key_signatures = [
                hashlib.sha256(
                    "\x00".join(str(value) for value in row).encode("utf-8")
                ).hexdigest()
                for row in foreign_key_rows
            ]
            invalid_fk_count = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE contype = 'f' AND connamespace = 'public'::regnamespace AND NOT convalidated"
                    )
                ).scalar_one()
            )
            marker_count = int(
                connection.execute(
                    text("SELECT count(*) FROM markets WHERE code = :code"),
                    {"code": marker_code},
                ).scalar_one()
            )
        _write(
            output,
            {
                "schema_version": "nexus_recovery_snapshot_v1",
                "alembic_head": revision,
                "table_count": len(tables),
                "tables": counts,
                "foreign_key_signature_count": len(foreign_key_signatures),
                "foreign_key_signatures": foreign_key_signatures,
                "invalid_foreign_key_count": invalid_fk_count,
                "synthetic_marker_count": marker_count,
            },
        )
        return 0
    finally:
        engine.dispose()


def compare(
    source_path: Path,
    restored_path: Path,
    output: Path,
    *,
    source_sha: str,
    backup_sha256: str,
    marker_committed_at: str,
    backup_completed_at: str,
    restore_started_at: str,
    restore_completed_at: str,
    rto_target_seconds: int,
    rpo_target_seconds: int,
) -> int:
    source = _load(source_path)
    restored = _load(restored_path)
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise RecoveryEvidenceError("source_sha_invalid")
    if not _SHA256.fullmatch(backup_sha256):
        raise RecoveryEvidenceError("backup_sha_invalid")
    if rto_target_seconds <= 0 or rpo_target_seconds <= 0:
        raise RecoveryEvidenceError("recovery_target_invalid")

    marker_time = _utc(marker_committed_at)
    backup_time = _utc(backup_completed_at)
    restore_start = _utc(restore_started_at)
    restore_end = _utc(restore_completed_at)
    raw_rpo_seconds = (backup_time - marker_time).total_seconds()
    raw_restore_seconds = (restore_end - restore_start).total_seconds()
    rpo_seconds = max(0.0, raw_rpo_seconds)
    restore_seconds = max(0.0, raw_restore_seconds)

    reasons: list[str] = []
    if raw_rpo_seconds < 0:
        reasons.append("recovery.rpo_timestamp_order_invalid")
    if raw_restore_seconds < 0:
        reasons.append("recovery.rto_timestamp_order_invalid")
    if source.get("schema_version") != "nexus_recovery_snapshot_v1" or restored.get("schema_version") != "nexus_recovery_snapshot_v1":
        reasons.append("recovery.snapshot_schema_invalid")
    if source.get("alembic_head") != restored.get("alembic_head"):
        reasons.append("recovery.alembic_head_mismatch")
    if source.get("tables") != restored.get("tables"):
        reasons.append("recovery.table_count_mismatch")
    if source.get("table_count") != restored.get("table_count"):
        reasons.append("recovery.table_set_mismatch")
    source_fk_signatures = source.get("foreign_key_signatures")
    restored_fk_signatures = restored.get("foreign_key_signatures")
    source_fk_count = source.get("foreign_key_signature_count")
    restored_fk_count = restored.get("foreign_key_signature_count")
    source_fk_valid = (
        isinstance(source_fk_signatures, list)
        and isinstance(source_fk_count, int)
        and not isinstance(source_fk_count, bool)
        and source_fk_count == len(source_fk_signatures)
        and source_fk_count <= MAX_FOREIGN_KEYS
        and all(isinstance(item, str) and _SHA256_HEX.fullmatch(item) for item in source_fk_signatures)
    )
    restored_fk_valid = (
        isinstance(restored_fk_signatures, list)
        and isinstance(restored_fk_count, int)
        and not isinstance(restored_fk_count, bool)
        and restored_fk_count == len(restored_fk_signatures)
        and restored_fk_count <= MAX_FOREIGN_KEYS
        and all(isinstance(item, str) and _SHA256_HEX.fullmatch(item) for item in restored_fk_signatures)
    )
    if not source_fk_valid or not restored_fk_valid:
        reasons.append("recovery.foreign_key_signature_invalid")
    elif source_fk_signatures != restored_fk_signatures:
        reasons.append("recovery.foreign_key_definition_mismatch")
    if source.get("invalid_foreign_key_count") != 0 or restored.get("invalid_foreign_key_count") != 0:
        reasons.append("recovery.foreign_key_not_validated")
    if source.get("synthetic_marker_count") != 1 or restored.get("synthetic_marker_count") != 1:
        reasons.append("recovery.synthetic_marker_missing")
    if restore_seconds > rto_target_seconds:
        reasons.append("recovery.rto_exceeded")
    if rpo_seconds > rpo_target_seconds:
        reasons.append("recovery.rpo_exceeded")

    payload = {
        "schema_version": "nexus_postgres_recovery_qualification_v1",
        "status": "pass" if not reasons else "fail",
        "source_sha": source_sha,
        "alembic_head": restored.get("alembic_head"),
        "backup_sha256": backup_sha256,
        "source_table_count": source.get("table_count"),
        "restored_table_count": restored.get("table_count"),
        "source_total_rows": sum(int(item) for item in (source.get("tables") or {}).values()),
        "restored_total_rows": sum(int(item) for item in (restored.get("tables") or {}).values()),
        "rto_target_seconds": int(rto_target_seconds),
        "rto_observed_seconds": round(restore_seconds, 3),
        "rpo_target_seconds": int(rpo_target_seconds),
        "rpo_observed_seconds": round(rpo_seconds, 3),
        "source_foreign_key_count": source_fk_count,
        "restored_foreign_key_count": restored_fk_count,
        "foreign_key_definitions_match": source_fk_valid and restored_fk_valid and source_fk_signatures == restored_fk_signatures,
        "foreign_keys_validated": source.get("invalid_foreign_key_count") == 0 and restored.get("invalid_foreign_key_count") == 0,
        "synthetic_marker_restored": restored.get("synthetic_marker_count") == 1,
        "reasons": sorted(set(reasons)),
        "production_data_used": False,
        "production_mutation_performed": False,
    }
    _write(output, payload)
    return 0 if not reasons else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = sub.add_parser("snapshot")
    snapshot_parser.add_argument("--database-url", required=True)
    snapshot_parser.add_argument("--output", type=Path, required=True)
    snapshot_parser.add_argument("--marker-code", required=True)

    digest_parser = sub.add_parser("digest")
    digest_parser.add_argument("--file", type=Path, required=True)

    plan_parser = sub.add_parser("migration-plan")
    plan_parser.add_argument("--observed-head", action="append", default=[])
    plan_parser.add_argument("--expected-head", required=True)
    plan_parser.add_argument("--output", type=Path, required=True)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--source", type=Path, required=True)
    compare_parser.add_argument("--restored", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, required=True)
    compare_parser.add_argument("--source-sha", required=True)
    compare_parser.add_argument("--backup-sha256", required=True)
    compare_parser.add_argument("--marker-committed-at", required=True)
    compare_parser.add_argument("--backup-completed-at", required=True)
    compare_parser.add_argument("--restore-started-at", required=True)
    compare_parser.add_argument("--restore-completed-at", required=True)
    compare_parser.add_argument("--rto-target-seconds", type=int, default=120)
    compare_parser.add_argument("--rpo-target-seconds", type=int, default=60)
    args = parser.parse_args()

    try:
        if args.command == "snapshot":
            return snapshot(args.database_url, args.output, marker_code=args.marker_code)
        if args.command == "digest":
            print(sha256_file(args.file))
            return 0
        if args.command == "migration-plan":
            return migration_plan(
                observed_heads=tuple(args.observed_head),
                expected_head=args.expected_head,
                output=args.output,
            )
        return compare(
            args.source,
            args.restored,
            args.output,
            source_sha=args.source_sha,
            backup_sha256=args.backup_sha256,
            marker_committed_at=args.marker_committed_at,
            backup_completed_at=args.backup_completed_at,
            restore_started_at=args.restore_started_at,
            restore_completed_at=args.restore_completed_at,
            rto_target_seconds=args.rto_target_seconds,
            rpo_target_seconds=args.rpo_target_seconds,
        )
    except (RecoveryEvidenceError, OSError, ValueError) as exc:
        print(f"recovery_evidence_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
