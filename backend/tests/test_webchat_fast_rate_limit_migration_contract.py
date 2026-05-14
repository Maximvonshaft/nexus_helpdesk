from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260514_0023_webchat_rate_limits_shared_bucket.py"
SERVICE = ROOT / "app" / "services" / "webchat_fast_rate_limit.py"


def _run_backend(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)


def test_webchat_fast_rate_limit_migration_exists():
    assert MIGRATION.exists()
    content = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260514_0023"' in content
    assert 'sa.Column("bucket_key", sa.String(length=64), nullable=False)' in content
    assert 'ROW_NUMBER() OVER' in content
    assert 'hashlib.sha256(bucket_key.encode("utf-8")).hexdigest()' in content


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


def test_upgrade_existing_255_bucket_key_table_is_converted_to_64(tmp_path: Path):
    db_path = tmp_path / "webchat_rate_limits_existing_255.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    _run_backend([sys.executable, "-m", "alembic", "upgrade", "20260512_fl222"], env=env)

    engine = create_engine(env["DATABASE_URL"])
    legacy_bucket = "legacy-" + ("bucket-key-" * 16)
    hashed_bucket = hashlib.sha256(legacy_bucket.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS webchat_rate_limits"))
        conn.execute(
            text(
                """
                CREATE TABLE webchat_rate_limits (
                    id INTEGER PRIMARY KEY,
                    bucket_key VARCHAR(255) NOT NULL,
                    window_start DATETIME NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_webchat_rate_limits_bucket_key ON webchat_rate_limits (bucket_key)"))
        conn.execute(
            text(
                """
                INSERT INTO webchat_rate_limits (id, bucket_key, window_start, request_count, updated_at)
                VALUES
                    (1, :legacy_bucket, '2026-05-14 00:00:00', 1, '2026-05-14 00:00:00'),
                    (2, :legacy_bucket, '2026-05-14 00:01:00', 7, '2026-05-14 00:01:00'),
                    (3, :hashed_bucket, '2026-05-14 00:00:30', 3, '2026-05-14 00:00:30')
                """
            ),
            {"legacy_bucket": legacy_bucket, "hashed_bucket": hashed_bucket},
        )
    engine.dispose()

    _run_backend([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)

    engine = create_engine(env["DATABASE_URL"])
    insp = inspect(engine)
    try:
        bucket_key_column = next(col for col in insp.get_columns("webchat_rate_limits") if col["name"] == "bucket_key")
        assert getattr(bucket_key_column["type"], "length", None) == 64

        indexes = {idx["name"]: idx for idx in insp.get_indexes("webchat_rate_limits")}
        assert "ux_webchat_rate_limits_bucket_key" in indexes
        assert indexes["ux_webchat_rate_limits_bucket_key"].get("unique") in (True, 1)
        assert "ix_webchat_rate_limits_window_start" in indexes
        assert "ix_webchat_rate_limits_bucket_key" not in indexes

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, bucket_key, request_count FROM webchat_rate_limits ORDER BY id")).mappings().all()
        assert len(rows) == 1
        assert rows[0]["bucket_key"] == hashed_bucket
        assert len(rows[0]["bucket_key"]) == 64
        assert rows[0]["request_count"] == 7
    finally:
        engine.dispose()


def test_upgrade_and_downgrade_drop_webchat_rate_limit_indexes_and_table(tmp_path: Path):
    db_path = tmp_path / "webchat_rate_limits_downgrade.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    _run_backend([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
    _run_backend([sys.executable, "-m", "alembic", "downgrade", "20260512_fl222"], env=env)

    engine = create_engine(env["DATABASE_URL"])
    insp = inspect(engine)
    try:
        assert "webchat_rate_limits" not in set(insp.get_table_names())
    finally:
        engine.dispose()
