from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..settings import Settings, get_settings


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "backend": self.backend,
            "upload_root": self.upload_root,
        }
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


def check_storage_readiness(settings: Settings | None = None) -> StorageReadinessResult:
    """Return non-destructive storage readiness signals for production gates.

    PR-4 intentionally does not force S3. Local storage remains accepted for pilot
    operations, but readiness exposes explicit warnings when local uploads do not
    have a configured backup target or backup marker.
    """

    active_settings = settings or get_settings()
    backend = active_settings.storage_backend
    upload_root = active_settings.upload_root
    warnings: list[StorageReadinessIssue] = []
    errors: list[StorageReadinessIssue] = []

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

    warnings.append(
        StorageReadinessIssue(
            code="local_storage_backend_active",
            message="Local attachment storage is active. This is acceptable for pilot operations only when uploads are covered by host-level backup or migration runbook.",
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

    backup_required = _env_bool("LOCAL_STORAGE_BACKUP_REQUIRED", True)
    backup_path = os.getenv("LOCAL_STORAGE_BACKUP_PATH", "").strip()
    marker_path = os.getenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", "").strip()

    if backup_required:
        if not backup_path and not marker_path:
            warnings.append(
                StorageReadinessIssue(
                    code="local_storage_backup_not_configured",
                    message="LOCAL_STORAGE_BACKUP_REQUIRED=true but neither LOCAL_STORAGE_BACKUP_PATH nor LOCAL_STORAGE_BACKUP_MARKER_PATH is configured.",
                    details={"upload_root": str(upload_root)},
                )
            )
        if backup_path:
            status, details = _path_status(backup_path)
            if status == "missing":
                warnings.append(
                    StorageReadinessIssue(
                        code="local_storage_backup_path_missing",
                        message="LOCAL_STORAGE_BACKUP_PATH is configured but does not exist on this host.",
                        details=details,
                    )
                )
            else:
                try:
                    if Path(backup_path).expanduser().resolve() == upload_root.resolve():
                        warnings.append(
                            StorageReadinessIssue(
                                code="local_storage_backup_path_same_as_upload_root",
                                message="LOCAL_STORAGE_BACKUP_PATH points to the same directory as UPLOAD_ROOT and does not prove an independent backup target.",
                                details=details,
                            )
                        )
                except OSError:
                    pass
        if marker_path:
            status, details = _path_status(marker_path)
            if status == "missing":
                warnings.append(
                    StorageReadinessIssue(
                        code="local_storage_backup_marker_missing",
                        message="LOCAL_STORAGE_BACKUP_MARKER_PATH is configured but the marker file does not exist.",
                        details=details,
                    )
                )

    status = "error" if errors else ("warning" if warnings else "ok")
    return StorageReadinessResult(status=status, backend=backend, upload_root=str(upload_root), warnings=tuple(warnings), errors=tuple(errors))
