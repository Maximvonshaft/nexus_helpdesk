from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolClassification = Literal["read", "write", "system"]
RiskLevel = Literal["low", "medium", "high"]
AutoExecutionMode = Literal["auto", "policy_gated", "confirmation_required", "disabled"]


@dataclass(frozen=True)
class ToolContract:
    name: str
    classification: ToolClassification
    required_permissions: tuple[str, ...]
    idempotency_key_strategy: str
    risk_level: RiskLevel
    redaction_requirements: tuple[str, ...]
    confirmation_required: bool
    allowed_auto_execution_mode: AutoExecutionMode
    controlled_action_required: bool = False
    description: str | None = None

    @property
    def is_write_tool(self) -> bool:
        return self.classification in {"write", "system"}


TOOL_CONTRACTS: dict[str, ToolContract] = {
    "knowledge.search": ToolContract(
        name="knowledge.search",
        classification="read",
        required_permissions=("knowledge:read",),
        idempotency_key_strategy="sha256(tenant,channel,session,query)",
        risk_level="low",
        redaction_requirements=("no_internal_chunk_payload", "no_secret", "no_raw_customer_pii"),
        confirmation_required=False,
        allowed_auto_execution_mode="auto",
        description="Search approved customer-facing knowledge context.",
    ),
    "support.availability": ToolContract(
        name="support.availability",
        classification="read",
        required_permissions=("webchat:handoff:create",),
        idempotency_key_strategy="sha256(tenant,country,channel,conversation_id,minute_bucket)",
        risk_level="low",
        redaction_requirements=("aggregate_only", "no_agent_identity", "no_secret"),
        confirmation_required=False,
        allowed_auto_execution_mode="auto",
        controlled_action_required=True,
        description="Read aggregate human-support availability, capacity, and queue position for the current conversation scope.",
    ),
    "speedaf.order.query": ToolContract(
        name="speedaf.order.query",
        classification="read",
        required_permissions=("speedaf:tracking:read",),
        idempotency_key_strategy="sha256(tenant,session,tracking_number_hash,request_id)",
        risk_level="medium",
        redaction_requirements=("hash_waybill", "suffix_only", "no_recipient_pii", "no_raw_tool_payload"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        description="Read trusted Speedaf tracking fact for one waybill.",
    ),
    "speedaf.express.track.query": ToolContract(
        name="speedaf.express.track.query",
        classification="read",
        required_permissions=("speedaf:tracking:read",),
        idempotency_key_strategy="sha256(tenant,session,tracking_number_hash,request_id)",
        risk_level="medium",
        redaction_requirements=("hash_waybill", "suffix_only", "no_recipient_pii", "no_raw_track_payload", "no_raw_tool_payload"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        description="Read trusted Speedaf full tracking history for one waybill via express track query.",
    ),
    "speedaf.order.waybillCode.query": ToolContract(
        name="speedaf.order.waybillCode.query",
        classification="read",
        required_permissions=("speedaf:tracking:read",),
        idempotency_key_strategy="sha256(tenant,session,caller_id_hash,country_code)",
        risk_level="medium",
        redaction_requirements=("hash_caller_id", "suffix_only_candidates", "no_raw_waybill"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        description="Find safe waybill candidates for a caller ID.",
    ),
    "handoff.request.create": ToolContract(
        name="handoff.request.create",
        classification="system",
        required_permissions=("webchat:handoff:create",),
        idempotency_key_strategy="active_request_per_conversation + client_message_id derived system message",
        risk_level="medium",
        redaction_requirements=("no_secret", "no_raw_tool_payload", "clip_reason"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
        description="Create or update a ticketless WebChat human handoff request, suspend AI, and enter governed capacity routing.",
    ),
    "ticket.create": ToolContract(
        name="ticket.create",
        classification="write",
        required_permissions=("ticket:create",),
        idempotency_key_strategy="source_dedupe_key + active_ticket_scope",
        risk_level="medium",
        redaction_requirements=("no_secret", "no_raw_tool_payload", "clip_customer_message"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
        description="Create or reuse a customer-support ticket for asynchronous follow-up after customer confirmation.",
    ),
    "conversation.suspend_ai": ToolContract(
        name="conversation.suspend_ai",
        classification="system",
        required_permissions=("webchat:ai:suspend",),
        idempotency_key_strategy="current_handoff_request_id + conversation_id",
        risk_level="medium",
        redaction_requirements=("clip_reason", "no_secret"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
        description="Suspend AI for a conversation; normally executed as part of handoff.request.create.",
    ),
    "conversation.resume_ai": ToolContract(
        name="conversation.resume_ai",
        classification="system",
        required_permissions=("webchat:ai:resume",),
        idempotency_key_strategy="handoff_request_status + conversation_id",
        risk_level="medium",
        redaction_requirements=("clip_reason", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
        description="Resume AI after a human handoff has been closed or explicitly released.",
    ),
    "speedaf.workOrder.create": ToolContract(
        name="speedaf.workOrder.create",
        classification="write",
        required_permissions=("speedaf:work_order:create",),
        idempotency_key_strategy="sha256(ticket_id,conversation_id,waybill_hash,work_order_type)",
        risk_level="high",
        redaction_requirements=("hash_waybill", "hash_caller_id", "no_raw_request", "no_secret"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
        description="Create a Speedaf delivery follow-up work order through backend policy, idempotency, and audit controls.",
    ),
    "speedaf.order.cancel.request": ToolContract(
        name="speedaf.order.cancel.request",
        classification="write",
        required_permissions=("speedaf:order:cancel",),
        idempotency_key_strategy="sha256(waybill_hash,caller_id_hash,reason_code)",
        risk_level="high",
        redaction_requirements=("hash_waybill", "hash_caller_id", "no_raw_request", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
        description="Submit an order cancel request. Not auto-executed without controlled confirmation.",
    ),
    "speedaf.order.updateAddress.request": ToolContract(
        name="speedaf.order.updateAddress.request",
        classification="write",
        required_permissions=("speedaf:order:update_address",),
        idempotency_key_strategy="sha256(waybill_hash,caller_id_hash,address_hash,confirmation_token)",
        risk_level="high",
        redaction_requirements=("hash_waybill", "hash_caller_id", "hash_address", "no_raw_address_in_audit", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
        description="Request address update workflow. Not auto-executed without customer confirmation and controlled action.",
    ),
    "speedaf.voice.callback": ToolContract(
        name="speedaf.voice.callback",
        classification="write",
        required_permissions=("speedaf:voice:callback",),
        idempotency_key_strategy="sha256(voice_session_id,ticket_id,event_type)",
        risk_level="high",
        redaction_requirements=("hash_phone", "no_raw_recording_url", "no_secret"),
        confirmation_required=True,
        allowed_auto_execution_mode="confirmation_required",
        controlled_action_required=True,
        description="Send Speedaf voice callback. Not auto-executed by public WebChat AI.",
    ),
    "timeline.event.create": ToolContract(
        name="timeline.event.create",
        classification="system",
        required_permissions=("timeline:event:create",),
        idempotency_key_strategy="sha256(conversation_id,ticket_id,event_type,client_message_id)",
        risk_level="low",
        redaction_requirements=("safe_summary_only", "no_secret", "no_raw_tool_payload"),
        confirmation_required=False,
        allowed_auto_execution_mode="policy_gated",
        controlled_action_required=True,
        description="Write an internal timeline/audit event with a safe summary only.",
    ),
}

TOOL_NAME_ALIASES: dict[str, str] = {
    "support_knowledge_retrieve": "knowledge.search",
    "support_availability": "support.availability",
    "speedaf_lookup": "speedaf.order.query",
    "speedaf_query_waybills": "speedaf.order.waybillCode.query",
    "speedaf.order.waybill_code.query": "speedaf.order.waybillCode.query",
    "speedaf_create_work_order": "speedaf.workOrder.create",
    "speedaf.work_order.create": "speedaf.workOrder.create",
    "speedaf_cancel_order": "speedaf.order.cancel.request",
    "speedaf.order.cancel": "speedaf.order.cancel.request",
    "speedaf_update_address": "speedaf.order.updateAddress.request",
    "speedaf.order.update_address": "speedaf.order.updateAddress.request",
}


def canonical_tool_name(name: str | None) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    return TOOL_NAME_ALIASES.get(cleaned, cleaned)


def get_tool_contract(name: str | None) -> ToolContract | None:
    return TOOL_CONTRACTS.get(canonical_tool_name(name))


def require_tool_contract(name: str) -> ToolContract:
    contract = get_tool_contract(name)
    if contract is None:
        raise KeyError(f"unknown tool: {name}")
    return contract


def registered_tool_names() -> tuple[str, ...]:
    return tuple(sorted(TOOL_CONTRACTS))


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
        }
        for contract in TOOL_CONTRACTS.values()
    ]
