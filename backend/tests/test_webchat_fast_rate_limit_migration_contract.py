from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260514_0023_webchat_rate_limits_shared_bucket.py"
SERVICE = ROOT / "app" / "services" / "webchat_fast_rate_limit.py"


def test_webchat_fast_rate_limit_migration_exists():
    assert MIGRATION.exists()
    content = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260514_0023"' in content
    assert 'ux_webchat_rate_limits_bucket_key' in content
    assert 'ROW_NUMBER() OVER' in content


def test_webchat_fast_rate_limit_service_does_not_create_schema_in_request_path():
    content = SERVICE.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in content
    assert "CREATE INDEX" not in content
    assert "ON CONFLICT(bucket_key)" in content
