from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "upstream_adapter.py"

spec = importlib.util.spec_from_file_location("codex_upstream_adapter", ADAPTER_PATH)
assert spec is not None
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
assert spec.loader is not None
spec.loader.exec_module(adapter)

STRICT_REPLY_KEYS = {
    "reply",
    "intent",
    "tracking_number",
    "handoff_required",
    "handoff_reason",
    "recommended_agent_action",
}


def _client() -> TestClient:
    return TestClient(adapter.app)


def _payload() -> dict[str, object]:
    return {
        "request_id": "upstream-test-request",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "upstream-test-session",
        "body": "Hello, where is my parcel?",
        "recent_context": [],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "strict_schema": "speedaf_webchat_fast_reply_v1",
    }


def _write_auth_file(tmp_path, value: str = "sample-profile-value") -> Path:
    auth_file = tmp_path / "codex_auth_profile.json"
    auth_file.write_text(
        '{"profiles":{"p":{"type":"token","provider":"openai-codex","access":"' + value + '"}}}',
        encoding="utf-8",
    )
    return auth_file


def _configure_codex_mode(monkeypatch, tmp_path, *, reply_enabled: bool = False) -> str:
    sample_value = "sample-profile-value"
    auth_file = _write_auth_file(tmp_path, sample_value)
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "codex_app_server")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")
    monkeypatch.setenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE", str(auth_file))
    monkeypatch.setenv("CODEX_UPSTREAM_APP_SERVER_BASE_URL", "http://127.0.0.1:18795")
    monkeypatch.setenv("CODEX_UPSTREAM_APP_SERVER_REPLY_ENABLED", "true" if reply_enabled else "false")
    return sample_value


def test_upstream_adapter_healthz_is_safe():
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "codex-upstream-adapter"}


def test_upstream_adapter_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CODEX_UPSTREAM_ADAPTER_MODE", raising=False)
    monkeypatch.delenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", raising=False)

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "upstream_adapter_disabled"


def test_upstream_adapter_contract_fixture_requires_auth_config(monkeypatch):
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "contract_fixture")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.delenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", raising=False)

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "upstream_adapter_auth_not_configured"


def test_upstream_adapter_contract_fixture_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "contract_fixture")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Upstream-Token": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "upstream_adapter_auth_failed"


def test_upstream_adapter_contract_fixture_returns_strict_reply(monkeypatch):
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "contract_fixture")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == STRICT_REPLY_KEYS
    assert data["intent"] == "tracking_missing_number"
    assert data["handoff_required"] is False


def test_upstream_adapter_auth_status_does_not_expose_secret(monkeypatch, tmp_path):
    sample_value = "sample-profile-value"
    auth_file = _write_auth_file(tmp_path, sample_value)
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "contract_fixture")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")
    monkeypatch.setenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE", str(auth_file))

    response = _client().get("/auth/status", headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 200
    data = response.json()
    assert data["shared_token_configured"] is True
    assert data["request_token_present"] is True
    selected = data["discovery"]["selected"]
    assert selected["source_kind"] == "auth_profile_file"
    assert selected["usable"] is True
    assert selected["credential_kind"] == "token"
    assert selected["login_type"] == "chatgptAuthTokens"
    assert selected["fingerprint"].startswith("sha256:")
    login_boundary = data["login_payload_boundary"]
    assert login_boundary["source_kind"] == "auth_profile_file"
    assert login_boundary["login_type"] == "chatgptAuthTokens"
    assert login_boundary["payload_ready"] is True
    assert login_boundary["secret_fingerprint"].startswith("sha256:")
    transport_boundary = data["transport_boundary"]
    assert transport_boundary["configured"] is False
    assert transport_boundary["error_code"] == "app_server_base_url_missing"
    assert transport_boundary["account_login_start_request"] is False
    assert transport_boundary["external_network_call"] is False
    provider_runtime = data["provider_runtime"]
    assert provider_runtime["provider"] == "codex_app_server"
    assert provider_runtime["safety_level"] == "reply_only"
    assert provider_runtime["boundary"] == {
        "browser_cookie_scraping": False,
        "chatgpt_session_scraping": False,
        "shell_execution": False,
        "file_write": False,
        "tool_execution": False,
        "direct_ticket_action": False,
        "direct_customer_outbound_send": False,
    }
    assert "expected" not in response.text
    assert sample_value not in response.text


def test_upstream_adapter_transport_status_requires_auth(monkeypatch):
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "codex_app_server")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")

    response = _client().get("/transport/status", headers={"X-Nexus-Upstream-Token": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "upstream_adapter_auth_failed"


def test_upstream_adapter_transport_login_start_dry_run(monkeypatch, tmp_path):
    sample_value = _configure_codex_mode(monkeypatch, tmp_path)
    monkeypatch.setenv("CODEX_UPSTREAM_APP_SERVER_LOGIN_DRY_RUN", "true")

    response = _client().post("/transport/login-start", headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["login_payload_boundary"]["payload_ready"] is True
    assert data["transport_boundary"]["base_url_accepted"] is True
    assert data["transport_boundary"]["account_login_start_request"] is False
    assert sample_value not in response.text


def test_upstream_adapter_codex_mode_requires_auth_source(monkeypatch):
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "codex_app_server")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")
    monkeypatch.delenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE", raising=False)
    monkeypatch.delenv("CODEX_CLI_AUTH_FILE", raising=False)
    monkeypatch.delenv("CODEX_UPSTREAM_API_KEY_FILE", raising=False)

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "codex_auth_source_missing"


def test_upstream_adapter_provider_status_reports_reply_capability(monkeypatch, tmp_path):
    _configure_codex_mode(monkeypatch, tmp_path, reply_enabled=True)

    response = _client().get("/provider/status", headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 200
    runtime = response.json()["provider_runtime"]
    assert runtime["provider"] == "codex_app_server"
    assert runtime["runtime"] == "private_upstream_adapter"
    assert runtime["capabilities"]["webchat_fast_reply"] is True
    assert runtime["capabilities"]["tool_execution"] is False
    assert runtime["capabilities"]["ticket_action"] is False


def test_upstream_adapter_codex_mode_reply_transport_disabled_by_default(monkeypatch, tmp_path):
    sample_value = _configure_codex_mode(monkeypatch, tmp_path, reply_enabled=False)

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 503
    assert response.json()["error_code"] == "codex_app_server_reply_transport_disabled"
    assert sample_value not in response.text


def test_upstream_adapter_codex_mode_reply_transport_success(monkeypatch, tmp_path):
    sample_value = _configure_codex_mode(monkeypatch, tmp_path, reply_enabled=True)

    class Result:
        ok = True
        status_code = 200
        response_payload = {
            "reply": "Please share your tracking number so I can check your parcel status.",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }
        safe_summary = {"transport": "codex_app_server_reply", "ok": True}
        error_code = None

    async def fake_post_reply_turn(*, settings, reply_payload):
        assert settings.app_server_base_url == "http://127.0.0.1:18795"
        assert settings.reply_path == "/reply"
        assert reply_payload["body"] == "Hello, where is my parcel?"
        return Result()

    monkeypatch.setattr(adapter, "post_reply_turn", fake_post_reply_turn)

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == STRICT_REPLY_KEYS
    assert data["intent"] == "tracking_missing_number"
    assert sample_value not in response.text


def test_upstream_adapter_codex_mode_reply_transport_rejects_invalid_fast_reply(monkeypatch, tmp_path):
    _configure_codex_mode(monkeypatch, tmp_path, reply_enabled=True)

    class Result:
        ok = True
        status_code = 200
        response_payload = {"reply": "missing required fields"}
        safe_summary = {"transport": "codex_app_server_reply", "ok": True}
        error_code = None

    async def fake_post_reply_turn(*, settings, reply_payload):
        return Result()

    monkeypatch.setattr(adapter, "post_reply_turn", fake_post_reply_turn)

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 502
    assert response.json()["error_code"] == "upstream_invalid_fast_reply"
