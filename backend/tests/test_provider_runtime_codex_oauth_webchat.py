from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.ai_runtime.schemas import FastAIProviderRequest
from app.services.provider_runtime.adapters.codex_app_server import CodexAppServerAdapter
from app.services.provider_runtime.output_contracts import OutputContracts
from app.services.provider_runtime.webchat_fast_dispatcher import (
    build_webchat_fast_provider_request,
    dispatch_webchat_fast_reply,
)
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from app.services.provider_runtime_status import get_provider_runtime_status
from app.services.webchat_fast_config import get_webchat_fast_settings


ROOT = Path(__file__).resolve().parents[2]


def _load_bridge_module(monkeypatch, *, mode="real", upstream_url="http://127.0.0.1:18795/reply"):
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_MODE", mode)
    monkeypatch.setenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", upstream_url)
    monkeypatch.setenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "mocked-runtime")
    spec = importlib.util.spec_from_file_location(
        "codex_app_server_bridge_proxy_test",
        ROOT / "deploy" / "codex_app_server_bridge_proxy.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SuccessAdapter(ProviderAdapter):
    name = "codex_app_server"

    async def generate(self, db, req):
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=25,
            structured_output={
                "reply": "I can help with that.",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
            },
            raw_payload_safe_summary={"bridge_status": 200},
        )


def test_webchat_fast_config_accepts_provider_runtime_without_bridge_token_file(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
    monkeypatch.delenv("CODEX_APP_SERVER_TOKEN_FILE", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    get_webchat_fast_settings.cache_clear()

    settings = get_webchat_fast_settings()

    assert settings.provider == "provider_runtime"
    assert settings.codex_app_server_token_file is None


def test_parser_accepts_reply_schema():
    parsed = OutputContracts.validate_and_parse(
        "speedaf_webchat_fast_reply_v1",
        """
        {
          "reply": "Thanks. I can help with that.",
          "intent": "other",
          "tracking_number": null,
          "handoff_required": false,
          "handoff_reason": null,
          "recommended_agent_action": null
        }
        """,
    )

    assert parsed["customer_reply"] == "Thanks. I can help with that."
    assert parsed["ticket_should_create"] is False


@pytest.mark.asyncio
async def test_provider_runtime_default_route_uses_openclaw_fallback_at_zero_canary_and_writes_audit(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    mock_db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None

    def db_execute(stmt, params=None, *args, **kwargs):
        if "INSERT INTO provider_runtime_audit_logs" in str(stmt):
            return Mock()
        return select_result

    mock_db.execute.side_effect = db_execute
    class OpenClawSuccessAdapter(SuccessAdapter):
        name = "openclaw_responses"

    ProviderRegistry.register("codex_app_server", lambda db: SuccessAdapter())
    ProviderRegistry.register("openclaw_responses", lambda db: OpenClawSuccessAdapter())

    req = ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=1000,
    )

    result = await ProviderRuntimeRouter(mock_db).route(req)

    assert result.ok is True
    assert result.provider == "openclaw_responses"
    assert result.structured_output["customer_reply"] == "I can help with that."
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_fast_provider_runtime_reads_reply_or_customer_reply():
    req = FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        body="hello",
        recent_context=[],
        request_id="req1",
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"), patch(
        "app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route",
        new=AsyncMock(return_value=ProviderResult(
            ok=True,
            provider="codex_app_server",
            elapsed_ms=10,
            structured_output={
                "reply": "Reply field works.",
                "intent": "other",
                "handoff_required": False,
            },
            raw_payload_safe_summary={"safe": True},
        )),
    ):
        result = await dispatch_webchat_fast_reply(request=req)

    assert result.ok is True
    assert result.reply == "Reply field works."
    assert "access_token" not in str(result.raw_payload_safe_summary)
    assert "refresh_token" not in str(result.raw_payload_safe_summary)


@pytest.mark.asyncio
async def test_fast_provider_runtime_router_exception_returns_unavailable_result():
    req = FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="sess-router-exception",
        body="hello",
        recent_context=[],
        request_id="req-router-exception",
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"), patch(
        "app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route",
        new=AsyncMock(side_effect=RuntimeError("route failed")),
    ):
        result = await dispatch_webchat_fast_reply(request=req)

    assert result.ok is False
    assert result.raw_provider == "provider_runtime"
    assert result.error_code == "router_exception"
    assert result.elapsed_ms == 0


@pytest.mark.asyncio
async def test_fast_provider_runtime_all_failed_preserves_error_code_without_typeerror():
    req = FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="sess-all-failed",
        body="hello",
        recent_context=[],
        request_id="req-all-failed",
    )

    with patch("app.services.provider_runtime.webchat_fast_dispatcher.SessionLocal"), patch(
        "app.services.provider_runtime.webchat_fast_dispatcher.ProviderRuntimeRouter.route",
        new=AsyncMock(
            return_value=ProviderResult(
                ok=False,
                provider="provider_runtime",
                elapsed_ms=42,
                error_code="openclaw_responses_unavailable",
                structured_output=None,
                raw_payload_safe_summary={"safe": True},
            )
        ),
    ):
        result = await dispatch_webchat_fast_reply(request=req)

    assert result.ok is False
    assert result.raw_provider == "provider_runtime"
    assert result.error_code == "openclaw_responses_unavailable"
    assert result.elapsed_ms == 42


def test_provider_runtime_dispatcher_builds_webchat_fast_request():
    req = FastAIProviderRequest(
        tenant_key="default",
        channel_key="website",
        session_id="sess-build",
        body="hello",
        recent_context=[{"role": "customer", "text": "hello"}],
        request_id=None,
        tracking_fact_summary="Trusted tracking fact",
        tracking_fact_evidence_present=True,
    )

    runtime_req = build_webchat_fast_provider_request(req)

    assert runtime_req.request_id == "req_unknown"
    assert runtime_req.tenant_id == "default"
    assert runtime_req.tenant_key == "default"
    assert runtime_req.channel_key == "website"
    assert runtime_req.session_id == "sess-build"
    assert runtime_req.scenario == "webchat_fast_reply"
    assert runtime_req.body == "hello"
    assert runtime_req.recent_context == [{"role": "customer", "text": "hello"}]
    assert runtime_req.tracking_fact_summary == "Trusted tracking fact"
    assert runtime_req.tracking_fact_evidence_present is True
    assert runtime_req.output_contract == "speedaf_webchat_fast_reply_v1"
    assert runtime_req.timeout_ms == 10000
    assert runtime_req.metadata == {}


def test_provider_runtime_status_credential_summary_has_no_raw_tokens(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_URL", "http://127.0.0.1:18794/login")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_MODE", "real")
    monkeypatch.setenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", "http://127.0.0.1:18795/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_REPLY_GENERATION_BACKEND", "codex_app_server")
    get_webchat_fast_settings.cache_clear()

    mock_db = Mock()
    credential = Mock()
    credential.mappings.return_value.first.return_value = {
        "encrypted_access_token": "encrypted-access-token",
        "encrypted_refresh_token": "encrypted-refresh-token",
        "expires_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
    }
    route = Mock()
    route.first.return_value = (1,)
    mock_db.execute.side_effect = [credential, route]

    status = get_provider_runtime_status(mock_db)

    codex = next(item for item in status["providers"] if item["name"] == "codex_app_server")
    assert codex["diagnostics"]["active_credential_exists"] is True
    assert codex["diagnostics"]["has_access"] is True
    assert codex["diagnostics"]["has_refresh"] is True
    assert codex["diagnostics"]["bridge_url_configured"] is True
    assert codex["diagnostics"]["login_url_configured"] is True
    assert codex["diagnostics"]["route_rule_exists"] is True
    assert codex["diagnostics"]["bridge_mode"] == "real"
    assert codex["diagnostics"]["real_upstream_configured"] is True
    rendered = str(status)
    assert "encrypted-access-token" not in rendered
    assert "encrypted-refresh-token" not in rendered


def test_stub_bridge_is_not_accepted_as_production_ready(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBCHAT_FAST_AI_PROVIDER", "provider_runtime")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_URL", "http://127.0.0.1:18794/login")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_MODE", "stub")
    monkeypatch.delenv("CODEX_APP_SERVER_REAL_UPSTREAM_URL", raising=False)
    get_webchat_fast_settings.cache_clear()

    mock_db = Mock()
    credential = Mock()
    credential.mappings.return_value.first.return_value = {
        "encrypted_access_token": "encrypted-access-token",
        "encrypted_refresh_token": "encrypted-refresh-token",
        "expires_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
    }
    route = Mock()
    route.first.return_value = (1,)
    mock_db.execute.side_effect = [credential, route]

    status = get_provider_runtime_status(mock_db)

    codex = next(item for item in status["providers"] if item["name"] == "codex_app_server")
    assert status["ok"] is False
    assert codex["configured"] is False
    assert codex["diagnostics"]["bridge_mode"] == "stub"
    assert "provider_runtime codex_app_server bridge is in stub mode" in status["warnings"]


def test_bridge_reply_calls_mocked_upstream_and_passes_oauth_session(monkeypatch):
    bridge = _load_bridge_module(monkeypatch)
    captured = {}

    class UpstreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return json.dumps({
                "reply": "dynamic upstream reply",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["authorization"] = req.headers.get("Authorization")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return UpstreamResponse()

    monkeypatch.setattr(bridge.request, "urlopen", fake_urlopen)

    handler = Mock()
    handler.headers = {}
    reply = bridge.call_real_upstream(
        handler,
        {
            "login": {
                "type": "chatgptAuthTokens",
                "accessToken": "oauth-access-token",
                "chatgptAccountId": "acct-1",
                "chatgptPlanType": "plus",
            },
            "body": "hello",
            "messages": [],
            "contract": "speedaf_webchat_fast_reply_v1",
        },
    )

    assert reply["reply"] == "dynamic upstream reply"
    assert captured["url"] == "http://127.0.0.1:18795/reply"
    assert captured["authorization"].split(" ", 1) == ["Bearer", "oauth-access-token"]
    assert captured["payload"]["chatgptAccountId"] == "acct-1"
    assert captured["payload"]["chatgptPlanType"] == "plus"
    assert reply["reply"] != "Thanks. I received your message and will help with this request."
    assert "oauth-access-token" not in str(reply)


def test_bridge_readyz_safe_fields_do_not_expose_tokens(monkeypatch, tmp_path):
    token_file = tmp_path / "bridge_token"
    token_file.write_text("internal-bridge-token", encoding="utf-8")
    monkeypatch.setenv("TOKEN_FILE", str(token_file))
    bridge = _load_bridge_module(monkeypatch, mode="stub", upstream_url="")
    bridge._LOGIN_STATE["access_token"] = "oauth-access-token"

    payload = bridge.readiness_payload()

    assert payload["mode"] == "stub"
    assert payload["real_upstream_configured"] is False
    assert payload["accepts_oauth_login"] is True
    assert payload["reply_generation_backend"] == "unconfigured"
    assert payload["token_file_configured"] is True
    assert payload["oauth_session_present"] is True
    rendered = str(payload)
    assert "oauth-access-token" not in rendered
    assert "access_token" not in rendered
    assert "internal-bridge-token" not in rendered


@pytest.mark.asyncio
async def test_missing_credential_returns_controlled_unavailable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    adapter = CodexAppServerAdapter(Mock(), "http://127.0.0.1:18794/reply")
    mock_db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None
    mock_db.execute.return_value = select_result

    req = ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=1000,
    )

    result = await adapter.generate(mock_db, req)

    assert result.ok is False
    assert result.error_code == "no_active_credential"


@pytest.mark.asyncio
async def test_expired_credential_uses_oauth_refresh_manager(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "bridge-token")
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_URL", "http://127.0.0.1:18794/login")
    adapter = CodexAppServerAdapter(Mock(), "http://127.0.0.1:18794/reply")
    mock_db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = {
        "id": "cred1",
        "account_id": "acct1",
        "chatgpt_plan_type": "plus",
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    mock_db.execute.return_value = select_result

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "reply": "ok",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise AssertionError("fast reply hot path must not call bridge readyz")

        async def post(self, *args, **kwargs):
            return Response()

    req = ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=1000,
    )

    with patch(
        "app.services.provider_runtime.adapters.codex_app_server.OAuthRefreshManager.get_valid_access_token",
        new=AsyncMock(return_value="fresh-access-token"),
    ) as refresh, patch(
        "app.services.provider_runtime.adapters.codex_app_server.httpx.AsyncClient",
        return_value=Client(),
    ):
        result = await adapter.generate(mock_db, req)

    assert result.ok is True
    refresh.assert_awaited_once_with("default", "cred1")
    assert "fresh-access-token" not in str(result.raw_payload_safe_summary)
    assert result.raw_payload_safe_summary["auth_mode"] == "per_request"
    assert result.raw_payload_safe_summary["hotpath_readyz"] is False


@pytest.mark.asyncio
async def test_adapter_hot_path_sends_per_request_login_without_readyz_or_legacy_login(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "bridge-token")
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_URL", "http://127.0.0.1:18794/login")
    adapter = CodexAppServerAdapter(Mock(), "http://127.0.0.1:18794/reply")
    mock_db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = {
        "id": "cred1",
        "account_id": "acct1",
        "chatgpt_plan_type": "plus",
    }
    mock_db.execute.return_value = select_result

    class Client:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise AssertionError("fast reply hot path must not call bridge readyz")

        async def post(self, *args, **kwargs):
            if args and str(args[0]).endswith("/login"):
                raise AssertionError("fast reply hot path must not call legacy /login")
            self.calls.append({"args": args, "kwargs": kwargs})

            class Response:
                status_code = 200
                headers = {
                    "X-Nexus-Codex-Elapsed-Ms": "123",
                    "X-Nexus-Codex-Backend": "openclaw_codex_local_warm_pool",
                }

                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "reply": "ok",
                        "intent": "other",
                        "tracking_number": None,
                        "handoff_required": False,
                        "handoff_reason": None,
                        "recommended_agent_action": None,
                    }

            return Response()

    client = Client()
    req = ProviderRequest(
        request_id="req1",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess1",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=1000,
    )

    with patch(
        "app.services.provider_runtime.adapters.codex_app_server.OAuthRefreshManager.get_valid_access_token",
        new=AsyncMock(return_value="fresh-access-token"),
    ), patch(
        "app.services.provider_runtime.adapters.codex_app_server.httpx.AsyncClient",
        return_value=client,
    ):
        result = await adapter.generate(mock_db, req)

    assert result.ok is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert str(call["args"][0]).endswith("/reply")
    assert call["kwargs"]["json"]["login"]["accessToken"] == "fresh-access-token"
    assert call["kwargs"]["headers"]["X-Nexus-Request-Id"] == "req1"
    assert "X-Nexus-Request-Deadline-Ms" in call["kwargs"]["headers"]
    assert result.raw_payload_safe_summary["auth_mode"] == "per_request"
    assert result.raw_payload_safe_summary["hotpath_readyz"] is False
    assert result.raw_payload_safe_summary["bridge_elapsed_ms"] == 123
    rendered = str(result.raw_payload_safe_summary)
    assert "fresh-access-token" not in rendered
    assert "bridge-token" not in rendered
    assert "Authorization" not in rendered
