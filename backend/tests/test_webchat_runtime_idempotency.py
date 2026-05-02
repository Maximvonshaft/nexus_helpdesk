from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_webchat_models_have_client_message_id_contract():
    models = (BACKEND / "app/webchat_models.py").read_text(encoding="utf-8")
    assert "client_message_id" in models
    assert "uq_webchat_message_client_id" in models
    assert "conversation_id", "direction", "client_message_id"


def test_webchat_service_short_circuits_duplicate_client_message_id():
    service = (BACKEND / "app/services/webchat_service.py").read_text(encoding="utf-8")
    assert "client_message_id" in service
    assert "existing_message" in service
    assert "idempotent" in service
    assert "enqueue_webchat_ai_reply_job" in service
    duplicate_section = service.split("if normalized_client_message_id:", 1)[1].split("message = WebchatMessage", 1)[0]
    assert "return" in duplicate_section
    assert "enqueue_webchat_ai_reply_job" not in duplicate_section


def test_webchat_api_accepts_client_message_id():
    api = (BACKEND / "app/api/webchat.py").read_text(encoding="utf-8")
    assert "client_message_id" in api
    assert "payload.client_message_id" in api
