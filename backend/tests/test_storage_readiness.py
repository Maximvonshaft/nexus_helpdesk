from __future__ import annotations

import json
from datetime import datetime, timezone

from types import SimpleNamespace

from app.services.storage_readiness import check_storage_readiness


def _settings(tmp_path, *, backend: str = "local", app_env: str = "production"):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(storage_backend=backend, upload_root=upload_root, app_env=app_env)


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def _write_marker(path):
    path.write_text(
        json.dumps(
            {
                "schema": "nexus.local-storage-backup.v1",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "file_count": 0,
                "total_bytes": 0,
                "manifest_sha256": "0" * 64,
                "source_matches_backup": True,
            }
        ),
        encoding="utf-8",
    )


def test_s3_storage_readiness_is_ok(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", raising=False)
    result = check_storage_readiness(_settings(tmp_path, backend="s3"))

    assert result.ok is True
    assert result.status == "ok"
    assert result.backend == "s3"
    assert not result.warnings
    assert not result.errors


def test_local_storage_readiness_warns_when_backup_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", raising=False)
    monkeypatch.delenv("LOCAL_STORAGE_BACKUP_PATH", raising=False)
    monkeypatch.delenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", raising=False)
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")

    result = check_storage_readiness(_settings(tmp_path, backend="local"))

    assert result.ok is True
    assert result.status == "warning"
    assert _codes(result.warnings) == {
        "local_storage_backend_active",
        "local_storage_backup_path_not_configured",
        "local_storage_backup_marker_not_configured",
    }
    assert not result.errors


def test_local_storage_readiness_allows_existing_backup_path_with_warning(tmp_path, monkeypatch):
    backup_path = tmp_path / "backup"
    backup_path.mkdir()
    monkeypatch.delenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", raising=False)
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_PATH", str(backup_path))
    monkeypatch.delenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", raising=False)

    result = check_storage_readiness(_settings(tmp_path, backend="local"))

    assert result.ok is True
    assert result.status == "warning"
    assert _codes(result.warnings) == {
        "local_storage_backend_active",
        "local_storage_backup_marker_not_configured",
    }
    assert not result.errors


def test_local_storage_readiness_is_ok_when_backup_is_verified_and_acknowledged(tmp_path, monkeypatch):
    backup_path = tmp_path / "backup"
    backup_path.mkdir()
    monkeypatch.delenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", raising=False)
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_PATH", str(backup_path))
    marker_path = tmp_path / "backup-marker.json"
    _write_marker(marker_path)
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_ACKNOWLEDGED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", str(marker_path))

    result = check_storage_readiness(_settings(tmp_path, backend="local"))

    assert result.ok is True
    assert result.status == "ok"
    assert not result.warnings
    assert not result.errors


def test_local_storage_readiness_errors_when_backup_path_is_upload_root(tmp_path, monkeypatch):
    settings = _settings(tmp_path, backend="local")
    monkeypatch.delenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", raising=False)
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_PATH", str(settings.upload_root))
    monkeypatch.delenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", raising=False)

    result = check_storage_readiness(settings)

    assert result.ok is False
    assert result.status == "error"
    assert "local_storage_backup_path_same_as_upload_root" in _codes(result.errors)


def test_local_storage_readiness_errors_when_remote_storage_is_required(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "false")

    result = check_storage_readiness(_settings(tmp_path, backend="local", app_env="production"))

    assert result.ok is False
    assert result.status == "error"
    assert _codes(result.errors) == {"remote_storage_required_in_production"}


def test_local_storage_readiness_does_not_error_remote_required_outside_production(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "false")

    result = check_storage_readiness(_settings(tmp_path, backend="local", app_env="development"))

    assert result.ok is True
    assert result.status == "warning"
    assert not result.errors
