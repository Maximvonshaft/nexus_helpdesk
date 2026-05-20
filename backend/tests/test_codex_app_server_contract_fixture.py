from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "app_server_contract_fixture.py"

spec = importlib.util.spec_from_file_location("codex_app_server_contract_fixture", FIXTURE_PATH)
assert spec is not None
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
assert spec.loader is not None
spec.loader.exec_module(fixture)


def _client() -> TestClient:
    return TestClient(fixture.app)


def test_contract_fixture_healthz():
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "codex-app-server-contract-fixture"}


def test_contract_fixture_accepts_chatgpt_auth_tokens_without_echoing_value():
    sample_value = "sample-contract-alpha"
    response = _client().post(
        "/account/login/start",
        json={
            "type": "chatgptAuthTokens",
            "accessToken": sample_value,
            "chatgptAccountId": "acct_demo",
            "chatgptPlanType": "plus",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["sessionId"].startswith("fixture-session-")
    assert data["account"]["type"] == "chatgptAuthTokens"
    assert data["account"]["credential_fingerprint"].startswith("sha256:")
    assert data["account"]["chatgpt_account_id_present"] is True
    assert data["account"]["chatgpt_plan_type_present"] is True
    assert data["capabilities"] == {"replyTurn": False, "loginOnly": True}
    assert sample_value not in response.text


def test_contract_fixture_accepts_api_key_without_echoing_value():
    sample_value = "sample-contract-beta"
    response = _client().post(
        "/account/login/start",
        json={"type": "apiKey", "apiKey": sample_value},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["account"]["type"] == "apiKey"
    assert data["account"]["credential_fingerprint"].startswith("sha256:")
    assert sample_value not in response.text


def test_contract_fixture_rejects_bad_shape():
    response = _client().post(
        "/account/login/start",
        json={"type": "chatgptAuthTokens"},
    )

    assert response.status_code == 422
