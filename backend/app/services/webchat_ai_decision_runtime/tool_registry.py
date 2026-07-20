from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolClassification = Literal["read", "write", "system"]
RiskLevel = Literal["low", "medium", "high"]
AutoExecutionMode = Literal["auto", "policy_gated", "confirmation_required", "disabled"]


@dataclass(frozen=True)
class ToolContract:
    """Canonical contract for every Tool exposed to an Agent."""

    name: str
    classification: ToolClassification
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    required_permissions: tuple[str, ...] = ()
    idempotency_key_strategy: str = "request_id"
    risk_level: RiskLevel = "low"
    redaction_requirements: tuple[str, ...] = ("no_secret", "no_raw_tool_payload")
    confirmation_required: bool = False
    allowed_auto_execution_mode: AutoExecutionMode = "auto"
    controlled_action_required: bool = False
    customer_visible_result: bool = True

    @property
    def is_write_tool(self) -> bool:
        return self.classification in {"write", "system"}

    @property
    def is_read_tool(self) -> bool:
        return self.classification == "read"

    def prompt_projection(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "classification": self.classification,
            "input_schema": self.input_schema,
            "confirmation_required": self.confirmation_required,
        }


def _schema(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_TRACKING_NUMBER = {"type": "string", "minLength": 8, "maxLength": 48}

TOOL_CONTRACTS: dict[str, ToolContract] = {
    "knowledge.search": ToolContract(
        name="knowledge.search",
        classification="read",
        description="Search approved knowledge visible to the current audience.",
        input_schema=_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            required=("query",),
        ),
        required_permissions=("knowledge:read",),
        idempotency_key_strategy="sha256(tenant,channel,session,query)",
        redaction_requirements=("no_internal_chunk_payload", "no_secret", "no_raw_customer_pii"),
    ),

"support.availability": ToolContract(
    name="support.availability",
    classification="read",
    description="Read aggregate human-support availability, capacity and the current queue position.",
    input_schema=_schema({}),
    required_permissions=("webchat:handoff:create",),
    idempotency_key_strategy="sha256(tenant,country,channel,conversation,minute)",
    redaction_requirements=("aggregate_only", "no_agent_identity", "no_secret"),
    controlled_action_required=True,
),
    "speedaf.order.query": ToolContract(
        name="speedaf.order.query",
        classification="read",
        description="Query the current Speedaf shipment fact for one waybill.",
        input_schema=_schema({"tracking_number": _TRACKING_NUMBER}, required=("tracking_number",)),
        required_permissions=("speedaf:tracking:read",),
        idempotency_key_strategy="sha256(tenant,session,tracking_number,request_id)",
        risk_level="medium",
        redaction_requirements=("hash_waybill", "suffix_only", "no_recipient_pii", "no_raw_tool_payload"),
    ),
    "speedaf.express.track.query": ToolContract(
        name="speedaf.express.track.query",
        classification="read",
        description="Query Speedaf shipment event history for one waybill.",
        input_schema=_schema({"tracking_number": _TRACKING_NUMBER}, required=("tracking_number",)),
        required_permissions=("speedaf:tracking:read",),
        idempotency_key_strategy="sha256(tenant,session,tracking_number,request_id)",
        risk_level="medium",
        redaction_requirements=("hash_waybill", "suffix_only", "no_recipient_pii", "no_raw_track_payload"),
    ),
    "speedaf.order.waybillCode.query": ToolContract(
        name="speedaf.order.waybillCode.query",
        classification="read",
        description="Find safe candidate waybills linked to a caller identifier.",
        input_schema=_schema(
            {
                "caller_id": {"type": "string", "minLength": 6, "maxLength": 80},
                "country_code": {"type": "string", "minLength": 2, "maxLength": 8},
            },
            required=("caller_id",),
        ),
        required_permissions=("speedaf:tracking:read",),
        idempotency_key_strategy="sha256(tenant,session,caller_id,country_code)",
        risk_level="medium",
        redaction_requirements=("hash_caller_id", "suffix_only_candidates", "no_raw_waybill"),
    ),
    "handoff.request.create": ToolContract(
        name="handoff.request.create",
        classification="system",
        description="Request human support and suspend autonomous replies for the conversation.",
        input_schema=_schema(
            {
                "reason": {"type": "string", "minLength": 1, "maxLength": 240},
                "recommended_agent_action": {"type": "string", "maxLength": 1000},
            },
            required=("reason",),
        ),
        required_permissions=("webchat:handoff:create",),
        idempotency_key_strategy="active_request_per_conversation",
        risk_level="medium",
        redaction_requirements=("no_secret", "no_raw_tool_payload", "clip_reason"),
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
    ),
    "ticket.create": ToolContract(
        name="ticket.create",
        classification="write",
        description="Create or reuse a support ticket for the current conversation.",
        input_schema=_schema(
            {
                "title": {"type": "string", "maxLength": 200},
                "description": {"type": "string", "maxLength": 4000},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "issue_type": {"type": "string", "maxLength": 120},
            }
        ),
        required_permissions=("ticket:create",),
        idempotency_key_strategy="source_dedupe_key + active_ticket_scope",
        risk_level="medium",
        redaction_requirements=("no_secret", "no_raw_tool_payload", "clip_customer_message"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
    ),
    "conversation.suspend_ai": ToolContract(
        name="conversation.suspend_ai",
        classification="system",
        description="Suspend autonomous replies for the current conversation.",
        input_schema=_schema({"reason": {"type": "string", "maxLength": 240}}),
        required_permissions=("webchat:ai:suspend",),
        idempotency_key_strategy="conversation_id + active_handoff_request",
        risk_level="medium",
        redaction_requirements=("clip_reason", "no_secret"),
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
    ),
    "conversation.resume_ai": ToolContract(
        name="conversation.resume_ai",
        classification="system",
        description="Resume autonomous replies after human review has completed.",
        input_schema=_schema({"reason": {"type": "string", "maxLength": 240}}),
        required_permissions=("webchat:ai:resume",),
        idempotency_key_strategy="conversation_id + handoff_status",
        risk_level="medium",
        redaction_requirements=("clip_reason", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
    ),
    "speedaf.workOrder.create": ToolContract(
        name="speedaf.workOrder.create",
        classification="write",
        description="Create a Speedaf delivery follow-up work order.",
        input_schema=_schema(
            {
                "tracking_number": _TRACKING_NUMBER,
                "work_order_type": {"type": "string", "minLength": 1, "maxLength": 40},
                "description": {"type": "string", "maxLength": 500},
            },
            required=("tracking_number", "work_order_type"),
        ),
        required_permissions=("speedaf:work_order:create",),
        idempotency_key_strategy="sha256(ticket,conversation,tracking_number,work_order_type)",
        risk_level="high",
        redaction_requirements=("hash_waybill", "hash_caller_id", "no_raw_request", "no_secret"),
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
    ),
    "speedaf.order.cancel.request": ToolContract(
        name="speedaf.order.cancel.request",
        classification="write",
        description="Submit a customer-confirmed order cancellation request.",
        input_schema=_schema(
            {
                "tracking_number": _TRACKING_NUMBER,
                "reason_code": {"type": "string", "minLength": 1, "maxLength": 40},
            },
            required=("tracking_number", "reason_code"),
        ),
        required_permissions=("speedaf:order:cancel",),
        idempotency_key_strategy="sha256(tracking_number,caller_id,reason_code)",
        risk_level="high",
        redaction_requirements=("hash_waybill", "hash_caller_id", "no_raw_request", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
    ),
    "speedaf.order.updateAddress.request": ToolContract(
        name="speedaf.order.updateAddress.request",
        classification="write",
        description="Submit a customer-confirmed delivery address update request.",
        input_schema=_schema(
            {
                "tracking_number": _TRACKING_NUMBER,
                "address": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
            required=("tracking_number", "address"),
        ),
        required_permissions=("speedaf:order:update_address",),
        idempotency_key_strategy="sha256(tracking_number,caller_id,address,confirmation_token)",
        risk_level="high",
        redaction_requirements=("hash_waybill", "hash_caller_id", "hash_address", "no_raw_address_in_audit", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
    ),
    "speedaf.voice.callback": ToolContract(
        name="speedaf.voice.callback",
        classification="write",
        description="Request a customer-confirmed voice callback.",
        input_schema=_schema(
            {
                "phone": {"type": "string", "minLength": 6, "maxLength": 80},
                "reason": {"type": "string", "maxLength": 500},
            },
            required=("phone",),
        ),
        required_permissions=("speedaf:voice:callback",),
        idempotency_key_strategy="sha256(voice_session,ticket,event_type)",
        risk_level="high",
        redaction_requirements=("hash_phone", "no_raw_recording_url", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
    ),
    "timeline.event.create": ToolContract(
        name="timeline.event.create",
        classification="system",
        description="Write a safe internal timeline event for the current case.",
        input_schema=_schema(
            {
                "event_type": {"type": "string", "maxLength": 120},
                "summary": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            required=("summary",),
        ),
        required_permissions=("timeline:event:create",),
        idempotency_key_strategy="sha256(conversation,ticket,event_type,client_message)",
        redaction_requirements=("safe_summary_only", "no_secret", "no_raw_tool_payload"),
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
        customer_visible_result=False,
    ),
}


def canonical_tool_name(name: str | None) -> str:
    """Normalize spelling only; aliases are intentionally unsupported."""

    return " ".join(str(name or "").strip().split())


def get_tool_contract(name: str | None) -> ToolContract | None:
    return TOOL_CONTRACTS.get(canonical_tool_name(name))


def require_tool_contract(name: str) -> ToolContract:
    contract = get_tool_contract(name)
    if contract is None:
        raise KeyError(f"unknown Tool: {name}")
    return contract


def registered_tool_names() -> tuple[str, ...]:
    return tuple(sorted(TOOL_CONTRACTS))


def prompt_tool_catalog(*, names: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
    selected = names or registered_tool_names()
    return [
        contract.prompt_projection()
        for name in selected
        if (contract := get_tool_contract(name)) is not None
    ]


def safe_registry_summary() -> list[dict[str, object]]:
    return [
        {
            "name": contract.name,
            "classification": contract.classification,
            "risk_level": contract.risk_level,
            "confirmation_required": contract.confirmation_required,
            "controlled_action_required": contract.controlled_action_required,
            "allowed_auto_execution_mode": contract.allowed_auto_execution_mode,
            "idempotency_key_strategy": contract.idempotency_key_strategy,
            "redaction_requirements": list(contract.redaction_requirements),
            "input_schema": contract.input_schema,
        }
        for contract in TOOL_CONTRACTS.values()
    ]
