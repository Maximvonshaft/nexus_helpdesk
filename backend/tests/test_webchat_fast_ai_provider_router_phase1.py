from __future__ import annotations

import asyncio

import pytest

from app.services.ai_runtime.codex_auth_provider import CodexAuthProvider
from app.services.ai_runtime.provider_router import generate_fast_reply
from app.services.ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from app.services.ai_runtime.safety_contract import redact_secret_text
from app.services.ai_runtime.tool_intent import normalize_tool_intents
from app.services.ai_runtime_probe.endpoint_guard import validate_probe_endpoint
from app.services.webchat_fast_config import get_webchat_fast_settings


def _clear_settings() -> None:
    get_webchat_fast_settings.cache_clear()


def test_provider_router_default_remains_openclaw_responses(monkeypatch):
    monkeypatch.delenv("WEBCHAT_FAST_AI_PROVIDER", raising=False)
    monkeypatch.delenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    _clear_settings()

    settings = get_webchat_fast_settings()

    assert settings.provider == "openclaw_responses"
    assert settings.fallback_provider == "none"


def test_codex_provider_requires_feature_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_auth")
    monkeypatch.delenv("WEBCHAT_FAST_AI_CODEX_ENABLED", raising=False)
    _clear_settings()

    with pytest.raises(RuntimeError, match="WEBCHAT_FAST_AI_CODEX_ENABLED"):
        get_webchat_fast_settings()


def test_codex_auth_missing_token_returns_safe_error():
    class Settings:
        codex_enabled = True
        codex_token = None

    provider = CodexAuthProvider(Settings())  # type: ignore[arg-type]
    request = FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="s1",
        body="hello",
        recent_context=[],
        request_id="r1",
    )

    result = asyncio.run(provider.generate(request))

    assert result.ok is False
    assert result.error_code == "codex_auth_not_configured"
    assert "token" not in str(result.raw_payload_safe_summary or {}).lower()


def test_codex_auth_present_token_does_not_fake_transport_or_leak_secret():
    secret = "codex-secret-token-value"

    class Settings:
        codex_enabled = True
        codex_token = secret

    provider = CodexAuthProvider(Settings())  # type: ignore[arg-type]
    request = FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="s1",
        body="hello",
        recent_context=[],
        request_id="r1",
    )

    result = asyncio.run(provider.generate(request))

    assert result.ok is False
    assert result.error_code == "codex_transport_not_confirmed"
    assert secret not in str(result)
    assert result.raw_payload_safe_summary == {"transport": "not_confirmed", "token_present": True}


def test_secret_redactor_removes_bearer_and_named_tokens():
    text = "Authorization: Bearer abc.def CODEX_AUTH_TOKEN=secret OPENAI_API_KEY=sk-secret auth.json"
    redacted = redact_secret_text(text)

    assert "abc.def" not in redacted
    assert "secret" not in redacted
    assert "auth.json" not in redacted.lower()
    assert "[REDACTED_SECRET]" in redacted


def test_tool_intents_schema_normalizes_but_does_not_execute():
    intents = normalize_tool_intents(
        [
            {"name": "create_ticket", "reason": "human requested", "arguments": {"priority": "medium"}, "confidence": 0.9},
            {"name": "unsafe_db_write", "reason": "nope"},
        ]
    )

    assert len(intents) == 1
    assert intents[0].name == "create_ticket"
    assert intents[0].to_safe_dict()["arguments"] == {"priority": "medium"}


def test_probe_endpoint_guard_rejects_unsafe_urls():
    assert validate_probe_endpoint("http://example.com/responses") == (False, "probe_endpoint_must_be_https")
    assert validate_probe_endpoint("https://user@example.com/responses") == (False, "probe_endpoint_userinfo_forbidden")
    assert validate_probe_endpoint("https://example.com/") == (False, "probe_endpoint_path_required")
    assert validate_probe_endpoint("https://127.0.0.1/responses") == (False, "probe_endpoint_ip_forbidden")


def test_probe_endpoint_guard_domain_allowlist(monkeypatch):
    monkeypatch.setenv("CODEX_AUTH_PROBE_ALLOWED_DOMAINS", "allowed.example")

    assert validate_probe_endpoint("https://other.example/responses") == (False, "probe_endpoint_domain_not_allowed")


def test_router_can_fallback_to_openclaw_provider(monkeypatch):
    class Settings:
        provider = "codex_auth"
        fallback_provider = "openclaw_responses"

    class FailingProvider:
        def __init__(self, settings):
            self.settings = settings

        async def generate(self, request):
            return FastAIProviderResult.unavailable(provider="codex_auth", error_code="codex_transport_not_confirmed")

    class WorkingProvider:
        def __init__(self, settings):
            self.settings = settings

        async def generate(self, request):
            return FastAIProviderResult(
                ok=True,
                ai_generated=True,
                reply_source="openclaw_responses",
                raw_provider="openclaw_responses",
                raw_payload_safe_summary={"test": True},
                reply="Hi",
                intent="greeting",
                tracking_number=None,
                handoff_required=False,
                handoff_reason=None,
                recommended_agent_action=None,
                tool_intents=[],
                elapsed_ms=1,
            )

    from app.services.ai_runtime import provider_router

    def fake_provider_for(name, settings):
        return FailingProvider(settings) if name == "codex_auth" else WorkingProvider(settings)

    monkeypatch.setattr(provider_router, "_provider_for", fake_provider_for)

    result = asyncio.run(
        generate_fast_reply(
            request=FastAIProviderRequest(
                tenant_key="default",
                channel_key="website",
                session_id="s1",
                body="hello",
                recent_context=[],
                request_id="r1",
            ),
            settings=Settings(),  # type: ignore[arg-type]
        )
    )

    assert result.ok is True
    assert result.reply_source == "openclaw_responses"


def test_production_forbids_plaintext_codex_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_auth")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_ENABLED", "true")
    monkeypatch.setenv("CODEX_AUTH_TOKEN", "codex-secret-token-value")
    monkeypatch.delenv("CODEX_AUTH_TOKEN_FILE", raising=False)
    _clear_settings()

    with pytest.raises(RuntimeError, match="CODEX_AUTH_TOKEN is forbidden"):
        get_webchat_fast_settings()
