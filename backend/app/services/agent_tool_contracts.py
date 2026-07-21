from __future__ import annotations

from .webchat_ai_decision_runtime import tool_registry as registry

_BOOTSTRAPPED = False


def bootstrap_agent_tool_contracts() -> None:
    """Register configurable Agent extensions in the one canonical Tool Registry."""

    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    contracts = {
        "customer.memory.read": registry.ToolContract(
            name="customer.memory.read",
            classification="read",
            description="Read governed long-term facts for the current customer.",
            input_schema=registry._schema({}),
            required_permissions=("customer:memory:read",),
            idempotency_key_strategy="sha256(tenant,customer,memory_policy_version)",
            risk_level="low",
            redaction_requirements=("no_secret", "no_restricted_memory", "no_raw_transcript"),
        ),
        "customer.memory.write": registry.ToolContract(
            name="customer.memory.write",
            classification="write",
            description="Save one explicitly confirmed, policy-permitted customer fact.",
            input_schema=registry._schema(
                {
                    "memory_key": {"type": "string", "minLength": 1, "maxLength": 120},
                    "value": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "consent_basis": {"type": "string", "minLength": 1, "maxLength": 80},
                },
                required=("memory_key", "value", "consent_basis"),
            ),
            required_permissions=("customer:memory:write",),
            idempotency_key_strategy="sha256(tenant,customer,memory_key,value)",
            risk_level="high",
            redaction_requirements=("no_secret", "no_sensitive_category", "bounded_value"),
            confirmation_required=True,
            allowed_auto_execution_mode="confirmation_required",
            controlled_action_required=True,
            customer_visible_result=False,
        ),
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
