from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.services.ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from app.services.webchat_fast_config import get_webchat_fast_settings


def _clear_settings() -> None:
    get_webchat_fast_settings.cache_clear()


@dataclass
class Settings:
    provider: str = "codex_app_server"
    fallback_provider: str = "none"
    codex_app_server_canary_percent: int = 100
    codex_app_server_kill_switch: bool = False


class FakeProvider:
    def __init__(self, name: str, ok: bool = True, error_code: str = "ai_unavailable") -> None:
        self.name = name
        self.ok = ok
        self.error_code = error_code

    async def generate(self, request):
        if not self.ok:
            return FastAIProviderResult.unavailable(provider=self.name, error_code=self.error_code, elapsed_ms=12)
        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source=self.name,
            raw_provider=self.name,
            raw_payload_safe_summary={"provider": self.name},
            reply="ok from " + self.name,
            intent="other",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_intents=[],
            elapsed_ms=7,
        )


def _request(request_id: str = "r1") -> FastAIProviderRequest:
    return FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="s1",
        body="hello",
        recent_context=[],
        request_id=request_id,
    )


def test_effective_provider_non_codex_stays_configured():
    from app.services.ai_runtime.provider_router import _effective_provider_name

    provider, route = _effective_provider_name(
        request=_request(),
        settings=Settings(provider="openclaw_responses"),  # type: ignore[arg-type]
    )

    assert provider == "openclaw_responses"
    assert route == "configured_provider"


def test_codex_kill_switch_routes_to_openclaw():
    from app.services.ai_runtime.provider_router import _effective_provider_name

    provider, route = _effective_provider_name(
        request=_request(),
        settings=Settings(codex_app_server_kill_switch=True),  # type: ignore[arg-type]
    )

    assert provider == "openclaw_responses"
    assert route == "kill_switch_openclaw"


def test_codex_canary_zero_routes_to_openclaw():
    from app.services.ai_runtime.provider_router import _effective_provider_name

    provider, route = _effective_provider_name(
        request=_request(),
        settings=Settings(codex_app_server_canary_percent=0),  # type: ignore[arg-type]
    )

    assert provider == "openclaw_responses"
    assert route == "canary_skipped_openclaw"


def test_codex_canary_full_routes_to_codex():
    from app.services.ai_runtime.provider_router import _effective_provider_name

    provider, route = _effective_provider_name(
        request=_request(),
        settings=Settings(codex_app_server_canary_percent=100),  # type: ignore[arg-type]
    )

    assert provider == "codex_app_server"
    assert route == "canary_full"


def test_generate_fast_reply_records_codex_route_and_success(monkeypatch):
    from app.services.ai_runtime import provider_router

    events: list[dict[str, object]] = []

    def fake_metric(**kwargs):
        events.append(kwargs)

    def fake_provider_for(name, settings):
        return FakeProvider(name)

    monkeypatch.setattr(provider_router, "record_codex_app_server_metric", fake_metric)
    monkeypatch.setattr(provider_router, "_provider_for", fake_provider_for)

    result = asyncio.run(provider_router.generate_fast_reply(request=_request(), settings=Settings()))  # type: ignore[arg-type]

    assert result.ok is True
    assert result.reply_source == "codex_app_server"
    assert events[0] == {"status": "route", "route": "canary_full"}
    assert events[1]["status"] == "ok"
    assert events[1]["route"] == "canary_full"


def test_generate_fast_reply_kill_switch_uses_openclaw_without_codex_success_metric(monkeypatch):
    from app.services.ai_runtime import provider_router

    events: list[dict[str, object]] = []

    def fake_metric(**kwargs):
        events.append(kwargs)

    def fake_provider_for(name, settings):
        return FakeProvider(name)

    monkeypatch.setattr(provider_router, "record_codex_app_server_metric", fake_metric)
    monkeypatch.setattr(provider_router, "_provider_for", fake_provider_for)

    result = asyncio.run(
        provider_router.generate_fast_reply(
            request=_request(),
            settings=Settings(codex_app_server_kill_switch=True),  # type: ignore[arg-type]
        )
    )

    assert result.ok is True
    assert result.reply_source == "openclaw_responses"
    assert events == [{"status": "route", "route": "kill_switch_openclaw"}]


def test_generate_fast_reply_codex_failure_fallback_ok_records_metric(monkeypatch):
    from app.services.ai_runtime import provider_router

    events: list[dict[str, object]] = []

    def fake_metric(**kwargs):
        events.append(kwargs)

    def fake_provider_for(name, settings):
        if name == "codex_app_server":
            return FakeProvider(name, ok=False, error_code="codex_app_server_unavailable")
        return FakeProvider(name, ok=True)

    monkeypatch.setattr(provider_router, "record_codex_app_server_metric", fake_metric)
    monkeypatch.setattr(provider_router, "_provider_for", fake_provider_for)

    result = asyncio.run(
        provider_router.generate_fast_reply(
            request=_request(),
            settings=Settings(fallback_provider="openclaw_responses"),  # type: ignore[arg-type]
        )
    )

    assert result.ok is True
    assert result.reply_source == "openclaw_responses"
    assert events[0] == {"status": "route", "route": "canary_full"}
    assert events[1]["status"] == "fallback_ok"
    assert events[1]["error_code"] == "codex_app_server_unavailable"


def test_codex_canary_percent_config_validation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "local-token")
    monkeypatch.setenv("CODEX_APP_SERVER_CANARY_PERCENT", "101")
    _clear_settings()

    with pytest.raises(RuntimeError, match="CODEX_APP_SERVER_CANARY_PERCENT"):
        get_webchat_fast_settings()


def test_codex_kill_switch_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "local-token")
    monkeypatch.setenv("CODEX_APP_SERVER_KILL_SWITCH", "true")
    _clear_settings()

    settings = get_webchat_fast_settings()

    assert settings.codex_app_server_kill_switch is True


def test_production_codex_canary_requires_openclaw_token_file(monkeypatch, tmp_path):
    codex_token_file = tmp_path / "codex_app_server_token"
    codex_token_file.write_text("codex-file-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN_FILE", str(codex_token_file))
    monkeypatch.setenv("CODEX_APP_SERVER_CANARY_PERCENT", "1")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "none")
    monkeypatch.delenv("OPENCLAW_RESPONSES_TOKEN_FILE", raising=False)
    _clear_settings()

    with pytest.raises(RuntimeError, match="OPENCLAW_RESPONSES_TOKEN_FILE"):
        get_webchat_fast_settings()


def test_production_codex_kill_switch_requires_openclaw_token_file(monkeypatch, tmp_path):
    codex_token_file = tmp_path / "codex_app_server_token"
    codex_token_file.write_text("codex-file-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "codex_app_server")
    monkeypatch.setenv("WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18793/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN_FILE", str(codex_token_file))
    monkeypatch.setenv("CODEX_APP_SERVER_KILL_SWITCH", "true")
    monkeypatch.setenv("CODEX_APP_SERVER_CANARY_PERCENT", "100")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "none")
    monkeypatch.delenv("OPENCLAW_RESPONSES_TOKEN_FILE", raising=False)
    _clear_settings()

    with pytest.raises(RuntimeError, match="OPENCLAW_RESPONSES_TOKEN_FILE"):
        get_webchat_fast_settings()
