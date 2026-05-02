from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_webchat_api_exposes_incremental_poll_contract():
    api = (BACKEND / "app/api/webchat.py").read_text(encoding="utf-8")
    assert "after_id" in api
    assert "limit" in api
    assert "list_public_messages(db, conversation_id, resolved_token, after_id=after_id, limit=limit)" in api


def test_webchat_service_clamps_incremental_poll_limit():
    service = (BACKEND / "app/services/webchat_service.py").read_text(encoding="utf-8")
    assert "DEFAULT_PUBLIC_MESSAGE_LIMIT" in service
    assert "MAX_PUBLIC_MESSAGE_LIMIT" in service
    assert "safe_limit = max(1, min" in service
    assert "WebchatMessage.id > after_id" in service
    assert "next_after_id" in service
