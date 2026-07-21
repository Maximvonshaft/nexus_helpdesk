from __future__ import annotations

from .webchat_ai_decision_runtime import tool_registry as registry

_BOOTSTRAPPED = False


def bootstrap_agent_tool_contracts() -> None:
    """Register Agent extensions in the one canonical Tool Registry."""

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
                    "integration_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "operation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                    "arguments": {"type": "object", "maxProperties": 100},
                },
                required=("integration_key", "operation"),
            ),
            required_permissions=("integration:read",),
            idempotency_key_strategy="sha256(tenant,integration,operation,arguments)",
            risk_level="medium",
            allowed_auto_execution_mode="policy_gated",
            redaction_requirements=(
                "allowlisted_projection_only",
                "no_secret",
                "bounded_response",
            ),
        ),
        "integration.write": registry.ToolContract(
            name="integration.write",
            classification="write",
            description="Call one published customer-confirmed enterprise integration write operation.",
            input_schema=registry._schema(
                {
                    "integration_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "operation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                    "arguments": {"type": "object", "maxProperties": 100},
                },
                required=("integration_key", "operation"),
            ),
            required_permissions=("integration:write",),
            idempotency_key_strategy=(
                "sha256(tenant,integration,operation,arguments,confirmation)"
            ),
            risk_level="high",
            confirmation_required=True,
            allowed_auto_execution_mode="confirmation_required",
            controlled_action_required=True,
            redaction_requirements=(
                "allowlisted_projection_only",
                "no_secret",
                "bounded_response",
            ),
        ),
        "specialist.delegate": registry.ToolContract(
            name="specialist.delegate",
            classification="read",
            description=(
                "Delegate one bounded read-only analysis task to an approved "
                "specialist and return structured evidence to the parent Agent."
            ),
            input_schema=registry._schema(
                {
                    "specialist": {
                        "type": "string",
                        "enum": [
                            "knowledge_researcher",
                            "policy_reviewer",
                            "case_summarizer",
                            "translation_reviewer",
                            "data_analyst",
                        ],
                    },
                    "task": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 3000,
                    },
                    "evidence_refs": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                    },
                },
                required=("specialist", "task"),
            ),
            idempotency_key_strategy=(
                "sha256(tenant,session,specialist,task,evidence_refs,release)"
            ),
            risk_level="medium",
            allowed_auto_execution_mode="policy_gated",
            controlled_action_required=True,
            customer_visible_result=False,
            redaction_requirements=(
                "no_raw_task_in_audit",
                "no_hidden_reasoning",
                "no_secret",
                "evidence_refs_only",
                "bounded_response",
            ),
        ),
    }
    for name, contract in contracts.items():
        existing = registry.TOOL_CONTRACTS.get(name)
        if existing is not None and existing != contract:
            raise RuntimeError(f"conflicting canonical Tool contract: {name}")
        registry.TOOL_CONTRACTS[name] = contract
    _BOOTSTRAPPED = True
