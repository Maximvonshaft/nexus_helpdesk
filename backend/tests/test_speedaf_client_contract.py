from __future__ import annotations

import json

import httpx

from app.services.speedaf.client import SpeedafMcpClient, SpeedafMcpClientError
from app.services.speedaf.redactor import redact_mapping
from app.services.speedaf.schemas import SpeedafMcpConfig


def _config(**overrides):
    values = {
        "enabled": True,
        "base_url": "https://uat-api.speedaf.com",
        "app_code": "test-app-code",
        "secret_key": None,
        "customer_code": "CH000001",
        "platform_source": "API KEY",
        "lookup_caller_id": None,
        "timeout_seconds": 8,
        "country_code_default": "CH",
        "content_type": "text/plain",
        "data_mode": "string",
        "require_sign": False,
    }
    values.update(overrides)
    return SpeedafMcpConfig(**values)


def test_build_envelope_uses_millisecond_timestamp_and_string_data():
    client = SpeedafMcpClient(_config())
    envelope = client.build_envelope("/open-api/mcp/order/query", {"waybillCode": "SPX123", "callerID": "41000000000"})

    assert envelope.query["appCode"] == "test-app-code"
    assert isinstance(envelope.query["timestamp"], int)
    assert envelope.query["timestamp"] >= 1_700_000_000_000
    assert envelope.headers["Content-Type"] == "text/plain"
    assert isinstance(envelope.body["data"], str)
    assert json.loads(envelope.body["data"])["waybillCode"] == "SPX123"


def test_load_config_accepts_support_agent_env_aliases(monkeypatch):
    from app.services.speedaf.client import load_speedaf_mcp_config

    monkeypatch.setenv("SPEEDAF_MCP_ENABLED", "true")
    monkeypatch.delenv("SPEEDAF_MCP_APP_CODE", raising=False)
    monkeypatch.delenv("SPEEDAF_MCP_SECRET_KEY", raising=False)
    monkeypatch.delenv("SPEEDAF_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("SPEEDAF_MCP_LOOKUP_CALLER_ID", raising=False)
    monkeypatch.setenv("SPEEDAF_APP_CODE", "app-from-support-agent")
    monkeypatch.setenv("SPEEDAF_SECRET_KEY", "secret8x")
    monkeypatch.setenv("SPEEDAF_BASE_URL", "https://apis.speedaf.com/open-api/mcp")
    monkeypatch.setenv("SPEEDAF_CUSTOMER_CODE", "CH000001")
    monkeypatch.setenv("SPEEDAF_PLATFORM_SOURCE", "API KEY")
    monkeypatch.setenv("SPEEDAF_LOOKUP_CALLER_ID", "41000000000")
    monkeypatch.setenv("SPEEDAF_TIMEOUT", "20")

    config = load_speedaf_mcp_config()

    assert config.configured is True
    assert config.app_code == "app-from-support-agent"
    assert config.secret_key == "secret8x"
    assert config.base_url == "https://apis.speedaf.com/open-api/mcp"
    assert config.customer_code == "CH000001"
    assert config.platform_source == "API KEY"
    assert config.lookup_caller_id == "41000000000"
    assert config.timeout_seconds == 20


def test_post_does_not_duplicate_Nexus_mcp_base_path():
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"success": True, "data": {"waybillCode": "SPX123"}})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = SpeedafMcpClient(_config(base_url="https://apis.speedaf.com/open-api/mcp"), http_client=http_client)
    response = client.post("/open-api/mcp/order/query", {"waybillCode": "SPX123"})

    assert response.ok is True
    assert seen_urls
    assert seen_urls[0].startswith("https://apis.speedaf.com/open-api/mcp/order/query?")
    assert "/open-api/mcp/open-api/mcp/" not in seen_urls[0]


def test_build_envelope_supports_object_data_mode():
    client = SpeedafMcpClient(_config(data_mode="object", content_type="application/json"))
    envelope = client.build_envelope("/open-api/mcp/order/query", {"waybillCode": "SPX123"})

    assert envelope.headers["Content-Type"] == "application/json"
    assert envelope.body["data"] == {"waybillCode": "SPX123"}


def test_require_sign_fails_explicitly_until_speedaf_confirms_algorithm():
    client = SpeedafMcpClient(_config(require_sign=True))

    try:
        client.build_envelope("/open-api/mcp/order/query", {"waybillCode": "SPX123"})
    except SpeedafMcpClientError as exc:
        assert exc.error.code == "sign_rule_not_configured"
    else:
        raise AssertionError("expected explicit sign failure")


def test_normalize_response_parses_nested_json_data_and_errors():
    client = SpeedafMcpClient(_config())
    ok = client.normalize_response({"success": True, "data": "{\"waybillCode\":\"SPX123\"}"}, status_code=200)
    assert ok.ok is True
    assert ok.data["waybillCode"] == "SPX123"

    failed = client.normalize_response({"success": False, "code": "SIGN_ERROR", "message": "bad sign"}, status_code=200)
    assert failed.ok is False
    assert failed.error is not None
    assert failed.error.code == "SIGN_ERROR"


def test_post_redacts_request_and_response_payloads():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["appCode"] == "test-app-code"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "waybillCode": "SPX123456789CH",
                    "acceptMobile": "41000000000",
                    "acceptAddress": "Confidential Street 1",
                    "acceptName": "Private Recipient",
                    "status": "10",
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = SpeedafMcpClient(_config(), http_client=http_client)
    response = client.post("/open-api/mcp/order/query", {"waybillCode": "SPX123456789CH", "callerID": "41000000000"})

    safe_text = json.dumps(response.safe_summary, ensure_ascii=False)
    assert response.ok is True
    assert "41000000000" not in safe_text
    assert "Confidential Street" not in safe_text
    assert "Private Recipient" not in safe_text


def test_redactor_blocks_sensitive_fields():
    redacted = redact_mapping({
        "callerID": "41000000000",
        "waybillCode": "WBVOICE12345",
        "acceptAddress": "Private Address",
        "acceptName": "Private Recipient",
        "nested": {"acceptMobile": "41000001111", "recipientName": "Nested Recipient"},
    })
    text = json.dumps(redacted, ensure_ascii=False)
    assert "41000000000" not in text
    assert "WBVOICE12345" not in text
    assert "Private Address" not in text
    assert "Private Recipient" not in text
    assert "Nested Recipient" not in text
    assert "41000001111" not in text
