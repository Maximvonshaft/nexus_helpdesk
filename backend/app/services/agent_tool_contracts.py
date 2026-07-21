from __future__ import annotations

from .webchat_ai_decision_runtime import tool_registry as registry

_BOOTSTRAPPED = False


def bootstrap_agent_tool_contracts() -> None:
    """Register configurable integrations in the one canonical Tool Registry."""

    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    contracts = {
        "integration.read": registry.ToolContract(
            name="integration.read",
            classification="read",
            description="Call one published read-only enterprise integration operation.",
            input_schema=registry._schema(
                {
                    "integration_key": {"type": "string", "minLength": 1, "maxLength": 120},
                    "operation": {"type": "string", "minLength": 1, "maxLength": 160},
                    "arguments": {"type": "object", "maxProperties": 100},
                },
                required=("integration_key", "operation"),
            ),
            required_permissions=("integration:read",),
            idempotency_key_strategy="sha256(tenant,integration,operation,arguments)",
            risk_level="medium",
            allowed_auto_execution_mode="policy_gated",
            redaction_requirements=("allowlisted_projection_only", "no_secret", "bounded_response"),
        ),
        "integration.write": registry.ToolContract(
            name="integration.write",
            classification="write",
            description="Call one published customer-confirmed enterprise integration write operation.",
            input_schema=registry._schema(
                {
                    "integration_key": {"type": "string", "minLength": 1, "maxLength": 120},
                    "operation": {"type": "string", "minLength": 1, "maxLength": 160},
                    "arguments": {"type": "object", "maxProperties": 100},
                },
                required=("integration_key", "operation"),
            ),
            required_permissions=("integration:write",),
            idempotency_key_strategy="sha256(tenant,integration,operation,arguments,confirmation)",
            risk_level="high",
            confirmation_required=True,
            allowed_auto_execution_mode="confirmation_required",
            controlled_action_required=True,
            redaction_requirements=("allowlisted_projection_only", "no_secret", "bounded_response"),
        ),
    }
    for name, contract in contracts.items():
        existing = registry.TOOL_CONTRACTS.get(name)
        if existing is not None and existing != contract:
            raise RuntimeError(f"conflicting canonical Tool contract: {name}")
        registry.TOOL_CONTRACTS[name] = contract
    _BOOTSTRAPPED = True
