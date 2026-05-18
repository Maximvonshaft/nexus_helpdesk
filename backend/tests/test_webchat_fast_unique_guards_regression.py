from __future__ import annotations

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text(encoding="utf-8")


def test_fast_conversation_open_session_unique_guard_is_declared() -> None:
    models = _read("app/webchat_models.py")
    migration = _read("alembic/versions/20260518_0025_webchat_fast_unique_guards.py")

    assert "uq_webchat_fast_open_session" in models
    assert '"tenant_key"' in models
    assert '"channel_key"' in models
    assert '"fast_session_id"' in models
    assert '"origin"' in models
    assert "unique=True" in models
    assert "fast_session_id IS NOT NULL AND status = 'open'" in models

    assert 'revision = "20260518_0025"' in migration
    assert 'down_revision = "20260516_0024"' in migration
    assert "_normalize_duplicate_open_fast_conversations" in migration
    assert "_create_partial_unique_index_if_missing" in migration
    assert "uq_webchat_fast_open_session" in migration
    assert "fast_session_id IS NOT NULL AND status = 'open'" in migration


def test_webchat_message_client_message_id_unique_guard_is_declared() -> None:
    models = _read("app/webchat_models.py")
    migration = _read("alembic/versions/20260518_0025_webchat_fast_unique_guards.py")

    assert "uq_webchat_messages_conversation_client" in models
    assert '"conversation_id"' in models
    assert '"client_message_id"' in models
    assert "client_message_id IS NOT NULL" in models
    assert "unique=True" in models

    assert "_normalize_duplicate_client_message_ids" in migration
    assert "uq_webchat_messages_conversation_client" in migration
    assert "client_message_id IS NOT NULL" in migration


def test_service_layer_requeries_existing_rows_after_integrity_error() -> None:
    source = _read("app/services/webchat_fast_session_service.py")

    assert "from sqlalchemy.exc import IntegrityError" in source
    assert "def _find_open_fast_conversation" in source
    assert "with db.begin_nested()" in source
    assert "except IntegrityError" in source
    assert "existing = _find_open_fast_conversation" in source
    assert "existing = _find_message(db, conversation_id=conversation_id, client_message_id=msg_id)" in source


def test_widget_non_stream_fallback_accepts_handoff_reply_contract() -> None:
    source = _read("app/static/webchat/widget.js")

    assert "data && data.ok === true && data.reply" in source
    assert "data && data.ok === true && data.ai_generated === true && data.reply" not in source
    assert "Support handoff requested" in source
