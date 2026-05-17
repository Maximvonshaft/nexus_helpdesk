from __future__ import annotations

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND_ROOT / "alembic" / "versions" / "20260517_0025_webchat_fast_unique_guards.py"
SESSION_SERVICE = BACKEND_ROOT / "app" / "services" / "webchat_fast_session_service.py"


def test_fast_unique_guard_migration_preflights_duplicates_before_indexing():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "duplicate open WebChat Fast sessions" in source
    assert "duplicate WebChat messages" in source
    assert "HAVING COUNT(*) > 1" in source
    assert "fast_session_id IS NOT NULL" in source
    assert "client_message_id IS NOT NULL" in source


def test_fast_unique_guard_migration_creates_partial_unique_indexes():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "uq_webchat_fast_open_session" in source
    assert "uq_webchat_msg_conversation_client" in source
    assert "CREATE UNIQUE INDEX" in source
    assert "tenant_key, channel_key, fast_session_id, origin" in source
    assert "conversation_id, client_message_id" in source
    assert "status = 'open' AND fast_session_id IS NOT NULL" in source


def test_fast_session_service_handles_unique_conflicts_without_500():
    source = SESSION_SERVICE.read_text(encoding="utf-8")

    assert "IntegrityError" in source
    assert "db.begin_nested()" in source
    assert "_find_fast_conversation" in source
    assert "_find_message" in source
    assert "except IntegrityError" in source
