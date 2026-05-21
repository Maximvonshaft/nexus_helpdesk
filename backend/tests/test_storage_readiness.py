from __future__ import annotations

from types import SimpleNamespace

from app.services.storage_readiness import check_storage_readiness


def _settings(tmp_path, *, backend: str = "local", app_env: str = "production"):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(storage_backend=backend, upload_root=upload_root, app_env=app_env)


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


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
        "local_storage_backup_not_configured",
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
    assert _codes(result.warnings) == {"local_storage_backend_active"}
    assert not result.errors


def test_local_storage_readiness_warns_when_backup_path_is_upload_root(tmp_path, monkeypatch):
    settings = _settings(tmp_path, backend="local")
    monkeypatch.delenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", raising=False)
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_PATH", str(settings.upload_root))
    monkeypatch.delenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", raising=False)

    result = check_storage_readiness(settings)

    assert result.ok is True
    assert result.status == "warning"
    assert "local_storage_backup_path_same_as_upload_root" in _codes(result.warnings)
    assert not result.errors


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
