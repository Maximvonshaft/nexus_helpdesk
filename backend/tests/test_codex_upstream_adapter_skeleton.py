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
    auth_file = tmp_path / "codex_auth_profile.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "contract_fixture")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")
    monkeypatch.setenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE", str(auth_file))

    response = _client().get("/auth/status", headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 200
    data = response.json()
    assert data["shared_token_configured"] is True
    assert data["request_token_present"] is True
    assert data["sources"]["auth_profile_file_present"] is True
    assert data["boundary"] == {
        "browser_cookie_scraping": False,
        "chatgpt_session_scraping": False,
        "shell_execution": False,
        "file_write": False,
        "tool_execution": False,
    }
    assert "expected" not in response.text


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


def test_upstream_adapter_codex_mode_is_explicitly_not_implemented(monkeypatch, tmp_path):
    auth_file = tmp_path / "codex_auth_profile.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_MODE", "codex_app_server")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN", "expected")
    monkeypatch.setenv("CODEX_UPSTREAM_AUTH_PROFILE_FILE", str(auth_file))

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Upstream-Token": "expected"})

    assert response.status_code == 501
    assert response.json()["error_code"] == "codex_app_server_transport_not_implemented"
