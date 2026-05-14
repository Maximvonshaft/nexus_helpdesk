from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260514_0023_webchat_rate_limits_shared_bucket.py"
SERVICE = ROOT / "app" / "services" / "webchat_fast_rate_limit.py"


def test_webchat_fast_rate_limit_migration_exists():
    assert MIGRATION.exists()
    content = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260514_0023"' in content
    assert 'sa.Column("bucket_key", sa.String(length=64), nullable=False)' in content
    assert 'ROW_NUMBER() OVER' in content


def test_webchat_fast_rate_limit_migration_indexes_contract():
    content = MIGRATION.read_text(encoding="utf-8")
    assert 'op.create_index("ux_webchat_rate_limits_bucket_key", "webchat_rate_limits", ["bucket_key"], unique=True)' in content
    assert 'op.create_index("ix_webchat_rate_limits_window_start", "webchat_rate_limits", ["window_start"], unique=False)' in content
    assert 'op.create_index("ix_webchat_rate_limits_bucket_key"' not in content


def test_webchat_fast_rate_limit_migration_downgrade_contract():
    content = MIGRATION.read_text(encoding="utf-8")
    assert 'op.drop_index("ix_webchat_rate_limits_window_start", table_name="webchat_rate_limits")' in content
    assert 'op.drop_index("ux_webchat_rate_limits_bucket_key", table_name="webchat_rate_limits")' in content
    assert 'op.drop_table("webchat_rate_limits")' in content


def test_webchat_fast_rate_limit_service_does_not_create_schema_in_request_path():
    content = SERVICE.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in content
    assert "CREATE INDEX" not in content
    assert "ON CONFLICT(bucket_key)" in content
    assert 'hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()' in content
