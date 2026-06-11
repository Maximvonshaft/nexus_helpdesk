import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from app.api import admin as admin_api
from app.services.provider_runtime.router import ProviderRuntimeRouter, _apply_env_overrides
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class _NoRuleResult:
    def mappings(self):
        return self

    def first(self):
        return None


class _FakeDB:
    def __init__(self):
        self.audit_writes = 0

    def execute(self, *_args, **_kwargs):
        return _NoRuleResult()

    def commit(self):
        self.audit_writes += 1

    def rollback(self):
        pass


class _FakeAdapter:
    async def generate(self, _db, request):
        return ProviderResult(
            ok=True,
            provider="codex_direct",
            elapsed_ms=1,
            structured_output={
                "customer_reply": "Please share your tracking number.",
                "language": "en",
                "intent": "tracking_missing_number",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            },
        )


def _provider_request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req-governance",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="session-governance",
        scenario="webchat_fast_reply",
        body="Where is my package?",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
        metadata={},
    )


@pytest.mark.asyncio
async def test_router_no_db_rule_defaults_to_codex_direct_without_fallback(monkeypatch):
    monkeypatch.delenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", raising=False)
    seen: list[str] = []

    monkeypatch.setattr("app.services.provider_runtime.bootstrap_provider_runtime", lambda: None)

    def fake_get(provider_name, _db):
        seen.append(provider_name)
        return _FakeAdapter()

    monkeypatch.setattr("app.services.provider_runtime.router.ProviderRegistry.get", fake_get)

    result = await ProviderRuntimeRouter(_FakeDB()).route(_provider_request())

    assert result.ok is True
    assert seen == ["codex_direct"]


def test_codex_direct_with_explicit_empty_json_fallback_stays_empty(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", "codex_direct")
    monkeypatch.setenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", "[]")
    monkeypatch.setenv("WEBCHAT_FAST_AI_FALLBACK_PROVIDER", "rule_engine")

    primary, fallbacks, *_ = _apply_env_overrides(
        "openai_responses",
        ["rule_engine"],
        "speedaf_webchat_fast_reply_v1",
        10000,
        False,
        100,
    )

    assert primary == "codex_direct"
    assert fallbacks == []


def test_prod_env_defaults_codex_direct_and_disables_openclaw_sidecar():
    env_text = (REPO / "deploy" / ".env.prod.example").read_text(encoding="utf-8")

    required = [
        "WEBCHAT_FAST_AI_PROVIDER=provider_runtime",
        "WEBCHAT_FAST_AI_FALLBACK_PROVIDER=none",
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER=codex_direct",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]",
        "CODEX_DIRECT_ENABLED=true",
        "OPENCLAW_INTEGRATION_ENABLED=false",
        "CODEX_SIDECAR_INTEGRATION_ENABLED=false",
    ]
    forbidden = [
        "PROVIDER_RUNTIME_PRIMARY_PROVIDER=codex_app_server",
        "PROVIDER_RUNTIME_FALLBACK_PROVIDERS=openclaw_responses",
        "OPENCLAW_SYNC_ENABLED=true",
        "OPENCLAW_EVENT_DRIVER_ENABLED=true",
        "100.",
    ]

    for item in required:
        assert item in env_text
    for item in forbidden:
        assert item not in env_text


def test_default_compose_has_no_sidecar_services_or_runtime_token_mounts():
    compose_text = (REPO / "deploy" / "docker-compose.server.yml").read_text(encoding="utf-8")
    forbidden = [
        "codex-app-server-bridge:",
        "codex-appserver-runtime:",
        "codex-app-server-upstream:",
        "codex-private-reply-engine:",
        "/run/openclaw_responses_token",
        "/run/openclaw_native_responses_token",
        "/run/nexus/codex_app_server_bridge_token",
    ]
    for item in forbidden:
        assert item not in compose_text
    for item in ["worker-outbound:", "worker-background:", "worker-handoff-snapshot:", "worker-webchat-ai:"]:
        assert item in compose_text


def test_openclaw_admin_gate_returns_404_when_disabled(monkeypatch):
    monkeypatch.setattr(admin_api.settings, "openclaw_integration_enabled", False)

    with pytest.raises(admin_api.HTTPException) as exc_info:
        admin_api.ensure_openclaw_integration_enabled()

    assert exc_info.value.status_code == 404
