from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..settings import Settings, get_settings

_BACKUP_MARKER_SCHEMA = "nexus.local-storage-backup.v1"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name}_invalid") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name}_out_of_range")
    return value


@dataclass(frozen=True)
class StorageReadinessIssue:
    code: str
    message: str
    severity: str = "warning"
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class StorageReadinessResult:
    status: str
    backend: str
    upload_root: str
    warnings: tuple[StorageReadinessIssue, ...] = ()
    errors: tuple[StorageReadinessIssue, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "backend": self.backend,
            "upload_root": self.upload_root,
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.warnings:
            payload["warnings"] = [warning.as_dict() for warning in self.warnings]
        if self.errors:
            payload["errors"] = [error.as_dict() for error in self.errors]
        return payload


def _path_status(raw_path: str) -> tuple[str, dict[str, Any]]:
    path = Path(raw_path).expanduser()
    details = {"path": str(path)}
    if not path.exists():
        return "missing", details
    details["is_dir"] = path.is_dir()
    details["is_file"] = path.is_file()
    return "exists", details


def _parse_completed_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("completed_at_missing")
    normalized = value.strip().replace("Z", "+00:00")
    completed_at = datetime.fromisoformat(normalized)
    if completed_at.tzinfo is None:
        raise ValueError("completed_at_timezone_missing")
    return completed_at.astimezone(timezone.utc)


def _read_backup_marker(path: Path, *, maximum_age_seconds: int) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("marker_not_file")
    if path.stat().st_size > 64 * 1024:
        raise ValueError("marker_too_large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _BACKUP_MARKER_SCHEMA:
        raise ValueError("marker_schema_invalid")
    completed_at = _parse_completed_at(payload.get("completed_at"))
    age_seconds = max(0, int((datetime.now(timezone.utc) - completed_at).total_seconds()))
    if age_seconds > maximum_age_seconds:
        raise ValueError("marker_stale")
    file_count = payload.get("file_count")
    total_bytes = payload.get("total_bytes")
    manifest_sha256 = payload.get("manifest_sha256")
    if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count < 0:
        raise ValueError("marker_file_count_invalid")
    if not isinstance(total_bytes, int) or isinstance(total_bytes, bool) or total_bytes < 0:
        raise ValueError("marker_total_bytes_invalid")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        raise ValueError("marker_manifest_invalid")
    if any(character not in "0123456789abcdef" for character in manifest_sha256.lower()):
        raise ValueError("marker_manifest_invalid")
    return {
        "schema": _BACKUP_MARKER_SCHEMA,
        "completed_at": completed_at.isoformat(),
        "age_seconds": age_seconds,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "manifest_sha256": manifest_sha256.lower(),
        "source_matches_backup": payload.get("source_matches_backup") is True,
    }


def check_storage_readiness(settings: Settings | None = None) -> StorageReadinessResult:
    """Return non-destructive storage readiness signals for production gates."""
    active_settings = settings or get_settings()
    backend = active_settings.storage_backend
    upload_root = active_settings.upload_root
    warnings: list[StorageReadinessIssue] = []
    errors: list[StorageReadinessIssue] = []
    evidence: dict[str, Any] = {}

    if backend == "s3":
        return StorageReadinessResult(status="ok", backend=backend, upload_root=str(upload_root))

    if backend != "local":
        errors.append(
            StorageReadinessIssue(
                code="storage_backend_unsupported",
                severity="error",
                message="STORAGE_BACKEND must be local or s3.",
                details={"backend": backend},
            )
        )
        return StorageReadinessResult(status="error", backend=backend, upload_root=str(upload_root), errors=tuple(errors))

    backup_acknowledged = _env_bool("LOCAL_STORAGE_BACKUP_ACKNOWLEDGED", False)
    backup_required = _env_bool("LOCAL_STORAGE_BACKUP_REQUIRED", True)
    enforce_freshness = _env_bool("LOCAL_STORAGE_BACKUP_ENFORCE_FRESHNESS", False)
    maximum_age_seconds = _env_int(
        "LOCAL_STORAGE_BACKUP_MAX_AGE_SECONDS",
        86400,
        minimum=300,
        maximum=604800,
    )
    backup_path = os.getenv("LOCAL_STORAGE_BACKUP_PATH", "").strip()
    marker_path = os.getenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", "").strip()
    backup_verified = False

    if not upload_root.exists() or not upload_root.is_dir():
        errors.append(
            StorageReadinessIssue(
                code="local_storage_upload_root_missing",
                severity="error",
                message="UPLOAD_ROOT must exist and be a directory when STORAGE_BACKEND=local.",
                details={"upload_root": str(upload_root)},
            )
        )
    elif not os.access(upload_root, os.W_OK):
        errors.append(
            StorageReadinessIssue(
                code="local_storage_upload_root_not_writable",
                severity="error",
                message="UPLOAD_ROOT is not writable by the application process.",
                details={"upload_root": str(upload_root)},
            )
        )

    if _env_bool("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", False) and active_settings.app_env == "production":
        errors.append(
            StorageReadinessIssue(
                code="remote_storage_required_in_production",
                severity="error",
                message="REQUIRE_REMOTE_STORAGE_IN_PRODUCTION=true but STORAGE_BACKEND=local.",
            )
        )

    if backup_required:
        if not backup_path:
            issue = StorageReadinessIssue(
                code="local_storage_backup_path_not_configured",
                severity="error" if enforce_freshness else "warning",
                message="LOCAL_STORAGE_BACKUP_PATH is required for local attachment backup qualification.",
            )
            (errors if enforce_freshness else warnings).append(issue)
        else:
            status, details = _path_status(backup_path)
            if status == "missing":
                issue = StorageReadinessIssue(
                    code="local_storage_backup_path_missing",
                    severity="error" if enforce_freshness else "warning",
                    message="LOCAL_STORAGE_BACKUP_PATH is configured but does not exist on this host.",
                    details=details,
                )
                (errors if enforce_freshness else warnings).append(issue)
            else:
                try:
                    if Path(backup_path).expanduser().resolve() == upload_root.resolve():
                        errors.append(
                            StorageReadinessIssue(
                                code="local_storage_backup_path_same_as_upload_root",
                                severity="error",
                                message="LOCAL_STORAGE_BACKUP_PATH must be independent from UPLOAD_ROOT.",
                            )
                        )
                except OSError:
                    errors.append(
                        StorageReadinessIssue(
                            code="local_storage_backup_path_unresolvable",
                            severity="error",
                            message="LOCAL_STORAGE_BACKUP_PATH could not be resolved safely.",
                        )
                    )

        if not marker_path:
            issue = StorageReadinessIssue(
                code="local_storage_backup_marker_not_configured",
                severity="error" if enforce_freshness else "warning",
                message="A fresh backup verification marker is required to prove source and backup equality.",
            )
            (errors if enforce_freshness else warnings).append(issue)
        else:
            try:
                marker = _read_backup_marker(
                    Path(marker_path).expanduser(),
                    maximum_age_seconds=maximum_age_seconds,
                )
                if not marker["source_matches_backup"]:
                    raise ValueError("marker_source_mismatch")
                evidence["local_backup"] = marker
                backup_verified = True
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                issue = StorageReadinessIssue(
                    code=f"local_storage_backup_{str(exc)[:80]}",
                    severity="error" if enforce_freshness else "warning",
                    message="Local attachment backup verification evidence is missing, invalid, stale or mismatched.",
                )
                (errors if enforce_freshness else warnings).append(issue)

    if not (backup_acknowledged and backup_verified):
        issue = StorageReadinessIssue(
            code="local_storage_backend_active",
            severity="error" if enforce_freshness else "warning",
            message="Local attachment storage is allowed only with acknowledged, fresh and matching backup evidence.",
            details={
                "backup_acknowledged": backup_acknowledged,
                "backup_verified": backup_verified,
                "maximum_age_seconds": maximum_age_seconds,
            },
        )
        (errors if enforce_freshness else warnings).insert(0, issue)

    status = "error" if errors else ("warning" if warnings else "ok")
    return StorageReadinessResult(
        status=status,
        backend=backend,
        upload_root=str(upload_root),
        warnings=tuple(warnings),
        errors=tuple(errors),
        evidence=evidence,
    )
