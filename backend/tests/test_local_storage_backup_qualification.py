from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "local_storage_backup_qualification",
    ROOT / "scripts" / "qualification" / "local_storage_backup.py",
)
assert SPEC is not None and SPEC.loader is not None
backup_qualification = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_qualification
SPEC.loader.exec_module(backup_qualification)

from app.services.storage_readiness import check_storage_readiness


def _settings(upload_root: Path):
    return SimpleNamespace(
        storage_backend="local",
        upload_root=upload_root,
        app_env="production",
    )


def test_matching_backup_generates_fresh_readiness_evidence(tmp_path, monkeypatch):
    source = tmp_path / "uploads"
    backup = tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")
    (backup / "a.txt").write_text("alpha", encoding="utf-8")
    (source / "nested").mkdir()
    (backup / "nested").mkdir()
    (source / "nested" / "b.bin").write_bytes(b"beta")
    (backup / "nested" / "b.bin").write_bytes(b"beta")

    payload = backup_qualification.verify_backup(source, backup)
    marker = backup_qualification.write_marker(backup, payload)

    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_PATH", str(backup))
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", str(marker))
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_MAX_AGE_SECONDS", "86400")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_ENFORCE_FRESHNESS", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_ACKNOWLEDGED", "true")
    monkeypatch.setenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", "false")

    result = check_storage_readiness(_settings(source))

    assert result.ok is True
    assert result.status == "ok"
    evidence = result.evidence["local_backup"]
    assert evidence["source_matches_backup"] is True
    assert evidence["file_count"] == 2
    assert evidence["total_bytes"] == 9
    assert len(evidence["manifest_sha256"]) == 64
    rendered = json.dumps(result.as_dict())
    assert "a.txt" not in rendered
    assert "b.bin" not in rendered
    assert "alpha" not in rendered
    assert "beta" not in rendered


def test_mismatched_backup_is_rejected(tmp_path):
    source = tmp_path / "uploads"
    backup = tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")
    (backup / "a.txt").write_text("different", encoding="utf-8")

    try:
        backup_qualification.verify_backup(source, backup)
    except ValueError as exc:
        assert str(exc) == "source_backup_manifest_mismatch"
    else:
        raise AssertionError("mismatched backup must fail")


def test_stale_marker_blocks_controlled_readiness(tmp_path, monkeypatch):
    source = tmp_path / "uploads"
    backup = tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    marker = backup / ".nexus-backup-verified.json"
    marker.write_text(
        json.dumps(
            {
                "schema": "nexus.local-storage-backup.v1",
                "completed_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                "source_matches_backup": True,
                "file_count": 0,
                "total_bytes": 0,
                "manifest_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_PATH", str(backup))
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_MARKER_PATH", str(marker))
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_MAX_AGE_SECONDS", "86400")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_ENFORCE_FRESHNESS", "true")
    monkeypatch.setenv("LOCAL_STORAGE_BACKUP_ACKNOWLEDGED", "true")
    monkeypatch.setenv("REQUIRE_REMOTE_STORAGE_IN_PRODUCTION", "false")

    result = check_storage_readiness(_settings(source))

    assert result.ok is False
    assert result.status == "error"
    assert any(issue.code == "local_storage_backup_marker_stale" for issue in result.errors)
