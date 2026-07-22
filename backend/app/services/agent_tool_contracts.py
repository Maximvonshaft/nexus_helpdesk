from __future__ import annotations

from .webchat_ai_decision_runtime import tool_registry as registry

_BOOTSTRAPPED = False


def bootstrap_agent_tool_contracts() -> None:
    """Register Agent extensions in the one canonical Tool Registry."""

    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    contracts = {
        "customer.confirmation.request": registry.ToolContract(
            name="customer.confirmation.request",
            classification="system",
            description=(
                "Ask the customer to confirm one exact confirmation-required Tool "
                "action. The server binds the challenge to the target Tool and "
                "argument digest; a later customer response cannot authorize any "
                "other action."
            ),
            input_schema=registry._schema(
                {
                    "tool_name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                    "arguments": {
                        "type": "object",
                        "maxProperties": 100,
                    },
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                },
                required=("tool_name", "arguments", "question"),
            ),
            required_permissions=("webchat:confirmation:create",),
            idempotency_key_strategy=(
                "active_confirmation_per_conversation_and_argument_digest"
            ),
            risk_level="low",
            allowed_auto_execution_mode="auto",
            controlled_action_required=True,
            customer_visible_result=True,
            redaction_requirements=(
                "encrypted_target_arguments",
                "safe_argument_keys_only",
                "no_secret",
                "bounded_question",
            ),
        ),
        "integration.search": registry.ToolContract(
            name="integration.search",
            classification="read",
            description=(
                "Search operations from enterprise integrations frozen into the "
                "current Agent Release before selecting integration.read or "
                "integration.write."
            ),
            input_schema=registry._schema(
                {
                    "keywords": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                            "pattern": "^[A-Za-z0-9_.-]+$",
                        },
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["read", "write", "all"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                required=("keywords",),
            ),
            required_permissions=("integration:read",),
            idempotency_key_strategy=(
                "sha256(tenant,release,keywords,mode,limit)"
            ),
            risk_level="low",
            allowed_auto_execution_mode="auto",
            controlled_action_required=True,
            customer_visible_result=False,
            redaction_requirements=(
                "release_catalog_only",
                "no_credential_state",
                "no_secret",
                "no_operation_execution",
                "bounded_response",
            ),
        ),
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
                "Delegate the current case to one approved read-only specialist "
                "for a server-defined objective and return structured evidence "
                "to the parent Agent."
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
                    "objective": {
                        "type": "string",
                        "enum": [
                            "investigate_current_request",
                            "check_policy_consistency",
                            "summarize_current_case",
                            "review_current_translation",
                            "analyze_available_data",
                        ],
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
                required=("specialist", "objective"),
            ),
            idempotency_key_strategy=(
                "sha256(tenant,session,specialist,objective,evidence_refs,release)"
            ),
            risk_level="medium",
            allowed_auto_execution_mode="policy_gated",
            controlled_action_required=True,
            customer_visible_result=False,
            redaction_requirements=(
                "server_generated_task_only",
                "no_hidden_reasoning",
                "no_customer_identifier",
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
