from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.ai_runtime.codex_app_server_provider import CodexAppServerProvider
from app.services.ai_runtime.provider_router import _provider_for
from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.provider_runtime.adapters.codex_app_server import CodexAppServerAdapter
from app.services.provider_runtime.schemas import ProviderRequest
from app.services.webchat_fast_config import get_webchat_fast_settings


def _clear_settings() -> None:
    get_webchat_fast_settings.cache_clear()


class _Settings:
    enabled = True
    codex_app_server_enabled = True
    codex_app_server_bridge_url = "http://127.0.0.1:18793/reply"
    codex_app_server_token = "test-token"
    codex_app_server_timeout_ms = 15000


def _request() -> FastAIProviderRequest:
    return FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="s1",
        body="Where is my parcel?",
        recent_context=[],
        request_id="r1",
    )


def test_provider_router_supports_codex_app_server():
    provider = _provider_for("codex_app_server", _Settings())  # type: ignore[arg-type]

    assert isinstance(provider, CodexAppServerProvider)


def test_provider_runtime_codex_payload_includes_persona_and_knowledge_context():
    request = ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="s1",
        scenario="webchat_fast_reply",
        body="Can I change address?",
        recent_context=[],
        tracking_fact_summary=None,
        tracking_fact_evidence_present=False,
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=1000,
        metadata={
            "persona_context": {"profile_key": "default.website.en"},
            "knowledge_context": {
                "locked_facts": [{"item_key": "address.policy", "answer": "Address changes are available before dispatch."}],
                "hits": [{"item_key": "address.policy", "text": "Before dispatch only."}],
            },
            "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
        },
    )

    payload = CodexAppServerAdapter._reply_payload(request)

    assert payload["persona_context"]["profile_key"] == "default.website.en"
    assert payload["knowledge_context"]["hits"][0]["item_key"] == "address.policy"
    assert payload["grounding_contract"]["mode"] == "ai_grounded_locked_facts"
    assert payload["grounding_contract"]["locked_facts_present"] is True
    assert payload["safety_policy"]["knowledge_scope"] == "policy_sop_faq_only"
    assert payload["tracking_fact_evidence_present"] is False


def test_codex_app_server_provider_success(monkeypatch):
    async def fake_call_bridge(self, request):
        return 200, {
            "reply": "Please share your tracking number so I can check your parcel status.",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }

    monkeypatch.setattr(CodexAppServerProvider, "_call_bridge", fake_call_bridge)
    provider = CodexAppServerProvider(_Settings())  # type: ignore[arg-type]

    result = asyncio.run(provider.generate(_request()))

    assert result.ok is True
    assert result.reply_source == "codex_app_server"
    assert result.raw_provider == "codex_app_server"
    assert result.intent == "tracking_missing_number"
    assert result.raw_payload_safe_summary == {
        "bridge": "codex_app_server",
        "status_code": 200,
        "error_code": None,
        "parsed": True,
        "elapsed_ms": result.elapsed_ms,
    }


def test_codex_app_server_provider_invalid_json_returns_safe_error(monkeypatch):
    async def fake_call_bridge(self, request):
        return 200, {"reply": "missing required fields"}

    monkeypatch.setattr(CodexAppServerProvider, "_call_bridge", fake_call_bridge)
    provider = CodexAppServerProvider(_Settings())  # type: ignore[arg-type]

    result = asyncio.run(provider.generate(_request()))

    assert result.ok is False
    assert result.error_code == "ai_invalid_output"
    assert result.raw_provider == "codex_app_server"
    assert result.raw_payload_safe_summary["parsed"] is False  # type: ignore[index]


def test_codex_app_server_provider_http_error_returns_safe_error(monkeypatch):
    async def fake_call_bridge(self, request):
        response = httpx.Response(503, request=httpx.Request("POST", "http://127.0.0.1:18793/reply"))
        raise httpx.HTTPStatusError("service unavailable", request=response.request, response=response)

    monkeypatch.setattr(CodexAppServerProvider, "_call_bridge", fake_call_bridge)
    provider = CodexAppServerProvider(_Settings())  # type: ignore[arg-type]

    result = asyncio.run(provider.generate(_request()))

    assert result.ok is False
    assert result.error_code == "codex_app_server_http_error"
    assert result.raw_payload_safe_summary["status_code"] == 503  # type: ignore[index]


def test_codex_app_server_provider_missing_config_is_safe():
    class Settings:
        enabled = True
        codex_app_server_enabled = False
        codex_app_server_bridge_url = None
        codex_app_server_token = None
        codex_app_server_timeout_ms = 15000

    provider = CodexAppServerProvider(Settings())  # type: ignore[arg-type]

    result = asyncio.run(provider.generate(_request()))

    assert result.ok is False
    assert result.error_code == "codex_app_server_not_configured"
    assert result.raw_payload_safe_summary["error_code"] == "codex_app_server_not_configured"  # type: ignore[index]


def test_codex_app_server_config_requires_feature_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.delenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", raising=False)
    _clear_settings()

    with pytest.raises(RuntimeError, match="WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED"):
        get_webchat_fast_settings()


def test_codex_app_server_config_allows_development_plain_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "local-token")
    _clear_settings()

    settings = get_webchat_fast_settings()

    assert settings.provider == "codex_app_server"
    assert settings.codex_app_server_token == "local-token"
    assert settings.is_codex_app_server_configured is True


def test_codex_app_server_production_forbids_plain_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "plain-token-forbidden")
    monkeypatch.delenv("CODEX_APP_SERVER_TOKEN_FILE", raising=False)
    _clear_settings()

    with pytest.raises(RuntimeError, match="CODEX_APP_SERVER_TOKEN is forbidden"):
        get_webchat_fast_settings()


def test_codex_app_server_production_token_file_is_compatible_with_provider_runtime(monkeypatch, tmp_path):
    token_file = tmp_path / "codex_app_server_token"
    token_file.write_text("file-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("CODEX_APP_SERVER_CANARY_PERCENT", "100")
    monkeypatch.setenv("CODEX_APP_SERVER_KILL_SWITCH", "false")
    monkeypatch.delenv("CODEX_APP_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_RESPONSES_TOKEN_FILE", raising=False)
    _clear_settings()

    settings = get_webchat_fast_settings()

    assert settings.provider == "provider_runtime"
    assert settings.codex_app_server_token == "file-token"
    assert settings.is_codex_app_server_configured is True
