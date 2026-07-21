from __future__ import annotations

from types import SimpleNamespace

from app.services import agent_tool_handlers
from app.services.agent_integration_service import execute_integration_operation
from app.services.agent_runtime.runtime import _available_tools
from app.services.agent_tool_contracts import bootstrap_agent_tool_contracts
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract


def _release_snapshot(*, with_integrations: bool = True) -> dict:
    return {
        "source": "deployment",
        "manifest": {
            "integrations": (
                [{"resource_key": "integration.crm", "version": 1}]
                if with_integrations
                else []
            ),
            "knowledge": [],
        },
        "resolved": {
            "resources": [
                {
                    "id": 1,
                    "resource_key": "integration.crm",
                    "config_type": "integration",
                    "version": 1,
                    "content": {
                        "kind": "http",
                        "name": "CRM",
                        "base_url": "https://crm.example.com",
                        "credential_ref": None,
                        "host_allowlist": ["crm.example.com"],
                        "timeout_seconds": 5,
                        "max_response_bytes": 10000,
                        "operations": [
                            {
                                "key": "customer_case_lookup",
                                "description": "Read a bounded customer case summary.",
                                "mode": "read",
                                "method": "GET",
                                "path": "/cases",
                                "input_schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                },
                                "result_allowlist": ["status", "category"],
                                "risk_level": "medium",
                                "requires_confirmation": False,
                                "enabled": True,
                            },
                            {
                                "key": "case_note_create",
                                "description": "Create a confirmed internal case note.",
                                "mode": "write",
                                "method": "POST",
                                "path": "/notes",
                                "input_schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                },
                                "result_allowlist": ["id", "status"],
                                "risk_level": "high",
                                "requires_confirmation": True,
                                "enabled": True,
                            },
                        ],
                        "enabled": True,
                    },
                }
            ]
            if with_integrations
            else [],
        },
    }


def _request(arguments: dict):
    return SimpleNamespace(
        action=SimpleNamespace(
            tool_name="integration.search",
            arguments=arguments,
        ),
        audit_context={"tenant_id": "tenant-a"},
        channel="webchat",
        country_code=None,
        case_context=SimpleNamespace(),
        idempotency_key="search-1",
    )


def test_progressive_search_is_one_read_only_canonical_tool() -> None:
    bootstrap_agent_tool_contracts()
    contract = get_tool_contract("integration.search")
    assert contract is not None
    assert contract.classification == "read"
    assert contract.risk_level == "low"
    assert contract.customer_visible_result is False
    properties = contract.input_schema["properties"]
    assert properties["keywords"]["items"]["pattern"] == "^[A-Za-z0-9_.-]+$"
    assert "query" not in properties


def test_progressive_search_reads_only_the_release_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_tool_handlers,
        "list_integration_catalog",
        lambda *_args, **_kwargs: [
            {
                "resource_key": "integration.crm",
                "name": "CRM",
                "kind": "http",
                "credential_configured": True,
                "operations": [
                    {
                        "key": "customer_case_lookup",
                        "description": "Read a bounded customer case summary.",
                        "mode": "read",
                        "risk_level": "medium",
                        "requires_confirmation": False,
                        "enabled": True,
                    },
                    {
                        "key": "case_note_create",
                        "description": "Create a confirmed internal case note.",
                        "mode": "write",
                        "risk_level": "high",
                        "requires_confirmation": True,
                        "enabled": True,
                    },
                ],
            }
        ],
    )
    handlers = agent_tool_handlers.build_agent_tool_handlers(
        None,
        conversation=None,
        ticket=None,
        customer=None,
    )
    result = handlers["integration.search"](
        _request(
            {
                "keywords": ["crm", "case"],
                "mode": "read",
                "limit": 5,
            }
        )
    )
    assert result.ok is True
    assert result.status == "executed"
    assert result.summary["count"] == 1
    assert result.summary["matches"] == [
        {
            "integration_key": "integration.crm",
            "operation": "customer_case_lookup",
            "description": "Read a bounded customer case summary.",
            "mode": "read",
            "risk_level": "medium",
            "requires_confirmation": False,
            "score": 2,
        }
    ]
    assert "credential_configured" not in str(result.summary)


def test_runtime_derives_search_only_for_release_bound_integration_invocation() -> None:
    bootstrap_agent_tool_contracts()
    metadata = {
        "agent_execution_context": {
            "granted_permissions": ["integration:read"],
        }
    }
    available = _available_tools(
        metadata,
        runtime_policy={"allowed_tools": []},
        release_snapshot=_release_snapshot(),
        playbooks=[{"tools": ["integration.read"]}],
        allow_high_risk_writes=False,
    )
    assert "integration.read" in available
    assert "integration.search" in available

    without_release_integration = _available_tools(
        metadata,
        runtime_policy={"allowed_tools": []},
        release_snapshot=_release_snapshot(with_integrations=False),
        playbooks=[{"tools": ["integration.read"]}],
        allow_high_risk_writes=False,
    )
    assert "integration.read" not in without_release_integration
    assert "integration.search" not in without_release_integration


def test_integration_execution_blocks_missing_result_projection() -> None:
    snapshot = _release_snapshot()
    operation = snapshot["resolved"]["resources"][0]["content"]["operations"][0]
    operation["result_allowlist"] = []
    result = execute_integration_operation(
        None,
        integration_key="integration.crm",
        operation="customer_case_lookup",
        arguments={},
        expected_write=False,
        release_snapshot=snapshot,
    )
    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "integration_result_projection_required"
