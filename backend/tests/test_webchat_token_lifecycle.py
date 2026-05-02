from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_webchat_conversation_has_expiry_column():
    models = (BACKEND / "app/webchat_models.py").read_text(encoding="utf-8")
    assert "visitor_token_expires_at" in models


def test_webchat_service_checks_expiry_window():
    service = (BACKEND / "app/services/webchat_service.py").read_text(encoding="utf-8")
    assert "WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7" in service
    assert "expires_at <= utc_now()" in service
    assert "_new_token_expiry" in service


def test_webchat_migration_contains_runtime_fields():
    migration = (BACKEND / "alembic/versions/20260502_0015_webchat_runtime_hardening.py").read_text(encoding="utf-8")
    assert "visitor_token_expires_at" in migration
    assert "client_message_id" in migration
    assert "uq_webchat_message_client_id" in migration
