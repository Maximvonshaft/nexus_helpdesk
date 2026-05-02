from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_webchat_api_uses_conversation_tenant_for_send_and_poll_rate_limit():
    api = (BACKEND / "app/api/webchat.py").read_text(encoding="utf-8")
    assert "get_public_conversation_or_404" in api
    assert "conversation.tenant_key" in api
    assert api.count("tenant_key=conversation.tenant_key") >= 2
    assert "tenant_key=\"default\", conversation_id=conversation_id" not in api


def test_webchat_rate_limit_bucket_includes_tenant_key():
    rate_limit = (BACKEND / "app/services/webchat_rate_limit.py").read_text(encoding="utf-8")
    assert "tenant_key" in rate_limit
    assert "return f\"{tenant_key}:{scope}:{_client_ip(request)}\"" in rate_limit
