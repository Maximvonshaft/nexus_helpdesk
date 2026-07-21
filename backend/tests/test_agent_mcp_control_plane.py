from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import agent_integration_service as integrations


def _integration(*, expected_schema: dict | None = None):
    return SimpleNamespace(
        resource_key="integration.mcp.test",
        version=3,
        content={
            "kind": "mcp_http",
            "name": "Test MCP",
            "base_url": "https://mcp.example.com/mcp",
            "credential_ref": None,
            "host_allowlist": ["mcp.example.com"],
            "timeout_seconds": 5,
            "max_response_bytes": 100000,
            "operations": [
                {
                    "key": "shipment_lookup",
                    "description": "Read shipment status.",
                    "mode": "read",
                    "method": "POST",
                    "path": "/mcp",
                    "input_schema": expected_schema
                    or {
                        "type": "object",
                        "properties": {"tracking_number": {"type": "string"}},
                        "required": ["tracking_number"],
                        "additionalProperties": False,
                    },
                    "result_allowlist": ["status"],
                    "risk_level": "medium",
                    "requires_confirmation": False,
                    "enabled": True,
                }
            ],
            "enabled": True,
        },
    )


def _release_snapshot(integration) -> dict:
    return {
        "source": "deployment",
        "resolved": {
            "resources": [
                {
                    "id": 1,
                    "resource_key": integration.resource_key,
                    "config_type": "integration",
                    "version": integration.version,
                    "content": integration.content,
                }
            ]
        },
    }


def test_mcp_doctor_accepts_release_frozen_matching_capabilities(monkeypatch) -> None:
    integration = _integration()
    monkeypatch.setattr(
        integrations,
        "_initialize_mcp_session",
        lambda *_args, **_kwargs: (
            "session-1",
            "2025-11-25",
            {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "test", "version": "1"},
                "capabilities": {"tools": {"listChanged": True}},
            },
        ),
    )
    monkeypatch.setattr(
        integrations,
        "_list_mcp_tools",
        lambda *_args, **_kwargs: [
            {
                "name": "shipment_lookup",
                "title": None,
                "description": "Read shipment status.",
                "inputSchema": integration.content["operations"][0]["input_schema"],
            },
            {
                "name": "unmanaged_write_tool",
                "title": None,
                "description": "Must never become available automatically.",
                "inputSchema": {"type": "object"},
            },
        ],
    )

    report = integrations.doctor_mcp_integration(
        None,
        integration_key=integration.resource_key,
        release_snapshot=_release_snapshot(integration),
    )

    assert report.healthy is True
    assert report.protocol_version == "2025-11-25"
    assert report.missing_tools == ()
    assert report.schema_mismatches == ()
    assert report.unmanaged_tools == ("unmanaged_write_tool",)
    assert report.schema_digest


def test_mcp_doctor_blocks_schema_drift(monkeypatch) -> None:
    integration = _integration()
    monkeypatch.setattr(
        integrations,
        "_initialize_mcp_session",
        lambda *_args, **_kwargs: (
            None,
            "2025-11-25",
            {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "test", "version": "1"},
                "capabilities": {"tools": {}},
            },
        ),
    )
    monkeypatch.setattr(
        integrations,
        "_list_mcp_tools",
        lambda *_args, **_kwargs: [
            {
                "name": "shipment_lookup",
                "title": None,
                "description": "Read shipment status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"waybill": {"type": "integer"}},
                    "required": ["waybill"],
                },
            }
        ],
    )

    report = integrations.doctor_mcp_integration(
        None,
        integration_key=integration.resource_key,
        release_snapshot=_release_snapshot(integration),
    )

    assert report.healthy is False
    assert report.schema_mismatches == ("shipment_lookup",)


def test_mcp_transport_never_reclassifies_business_mode(monkeypatch) -> None:
    integration = _integration()
    integration.content["operations"][0]["mode"] = "write"
    integration.content["operations"][0]["requires_confirmation"] = True
    snapshot = _release_snapshot(integration)

    result = integrations.execute_integration_operation(
        None,
        integration_key=integration.resource_key,
        operation="shipment_lookup",
        arguments={"tracking_number": "CH020000129135"},
        expected_write=False,
        release_snapshot=snapshot,
    )

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "integration_operation_classification_mismatch"


def test_mcp_initialize_precedes_initialized_notification(monkeypatch) -> None:
    integration = _integration()
    calls: list[tuple[str, bool]] = []

    def rpc(_integration, *, method, params, request_id, session_id, protocol_version, notification=False):
        del params, request_id, protocol_version
        calls.append((method, notification))
        if method == "initialize":
            return (
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test", "version": "1"},
                },
                200,
                {"Mcp-Session-Id": "session-1"},
            )
        assert session_id == "session-1"
        return {}, 202, {}

    monkeypatch.setattr(integrations, "_mcp_rpc", rpc)
    integrations._clear_mcp_session(integration)

    session_id, version, result = integrations._initialize_mcp_session(
        integration,
        force=True,
    )

    assert session_id == "session-1"
    assert version == "2025-11-25"
    assert result["serverInfo"]["name"] == "test"
    assert calls == [
        ("initialize", False),
        ("notifications/initialized", True),
    ]
