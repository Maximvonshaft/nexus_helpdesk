from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "sidecar.py"

spec = importlib.util.spec_from_file_location("codex_reply_sidecar", SIDECAR_PATH)
assert spec is not None
sidecar = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(sidecar)

STRICT_REPLY_KEYS = {
    "reply",
    "intent",
    "tracking_number",
    "handoff_required",
    "handoff_reason",
    "recommended_agent_action",
}


def _client() -> TestClient:
    return TestClient(sidecar.app)


def _payload(body: str = "Hello, where is my parcel?") -> dict[str, object]:
    return {
        "request_id": "test-request",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "test-session",
        "body": body,
        "recent_context": [],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "strict_schema": "speedaf_webchat_fast_reply_v1",
    }


def test_sidecar_healthz_is_always_safe():
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_sidecar_readyz_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CODEX_REPLY_BRIDGE_MODE", raising=False)
    monkeypatch.delenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN", raising=False)

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "bridge_disabled"


def test_sidecar_reply_requires_shared_token_in_stub_mode(monkeypatch):
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_MODE", "stub")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_REQUIRE_AUTH", "true")
    monkeypatch.delenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN", raising=False)

    response = _client().post("/reply", json=_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "bridge_auth_not_configured"


def test_sidecar_reply_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_MODE", "stub")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN", "expected")

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Bridge-Token": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "bridge_auth_failed"


def test_sidecar_stub_returns_only_strict_fast_reply(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_MODE", "stub")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN", "expected")

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Bridge-Token": "expected"})

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == STRICT_REPLY_KEYS
    assert data["intent"] == "tracking_missing_number"
    assert data["handoff_required"] is False


def test_sidecar_stub_forbidden_in_production_by_default(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_MODE", "stub")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN", "expected")
    monkeypatch.delenv("CODEX_REPLY_BRIDGE_ALLOW_STUB_IN_PRODUCTION", raising=False)

    response = _client().post("/reply", json=_payload(), headers={"X-Nexus-Bridge-Token": "expected"})

    assert response.status_code == 503
    assert response.json()["error_code"] == "stub_forbidden_in_production"


def test_sidecar_upstream_requires_url(monkeypatch):
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_MODE", "upstream")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CODEX_REPLY_BRIDGE_SHARED_TOKEN", "expected")
    monkeypatch.delenv("CODEX_REPLY_BRIDGE_UPSTREAM_URL", raising=False)

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "upstream_url_not_configured"


def test_sidecar_normalizer_rejects_internal_reply():
    with pytest.raises(Exception):
        sidecar._normalize_strict_reply(
            {
                "reply": "OpenClaw gateway on localhost is available.",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
            }
        )
