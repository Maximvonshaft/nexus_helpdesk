from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceType(StrEnum):
    """Runtime evidence taxonomy for customer-service decisions.

    The taxonomy is deliberately business-facing, not provider-facing.  It keeps
    MCP facts, customer-visible knowledge, case context, customer claims, and
    previous AI replies separate so downstream policies cannot accidentally treat
    conversation history as verified operational truth.
    """

    MCP_CURRENT_STATUS = "mcp.current_status"
    MCP_HISTORY_ENRICHMENT = "mcp.history_enrichment"
    MCP_TICKET_STATUS = "mcp.ticket_status"
    MCP_OPERATION_RESULT = "mcp.operation_result"
    KNOWLEDGE_CUSTOMER_VISIBLE = "knowledge.customer_visible"
    KNOWLEDGE_INTERNAL = "knowledge.internal"
    CASE_CONTEXT = "case_context"
    CUSTOMER_CLAIM = "customer_claim"
    PREVIOUS_AI_REPLY = "previous_ai_reply"
    HUMAN_AGENT_NOTE = "human_agent_note"
    SYSTEM_EVENT = "system_event"


class BusinessReplyType(StrEnum):
    TRACKING_STATUS_ANSWER = "tracking_status_answer"
    KNOWLEDGE_ANSWER = "knowledge_answer"
    CLARIFICATION = "clarification"
    HANDOFF_NOTICE = "handoff_notice"
    TICKET_CREATED_NOTICE = "ticket_created_notice"
    TOOL_ACTION_RESULT = "tool_action_result"
    COMPLAINT_ESCALATION = "complaint_escalation"
    COMPENSATION_ESCALATION = "compensation_escalation"
    NO_ANSWER = "no_answer"


class RuntimeAction(StrEnum):
    REPLY = "reply"
    ASK_MISSING_INFO = "ask_missing_info"
    CALL_TOOL = "call_tool"
    CREATE_TICKET = "create_ticket"
    REQUEST_HANDOFF = "request_handoff"
    ROUTE_TO_GROUP = "route_to_group"
    BLOCK = "block"


@dataclass(frozen=True)
class EvidenceSource:
    evidence_type: EvidenceType | str
    source_id: str
    label: str
    summary: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    customer_visible: bool = False
    verified: bool = False
    current_status: bool = False
    created_at: str | None = None

    def normalized_type(self) -> EvidenceType | str:
        try:
            return EvidenceType(str(self.evidence_type))
        except ValueError:
            return str(self.evidence_type)

    def safe_summary(self) -> dict[str, Any]:
        """Return summary fields safe for debug/audit surfaces.

        This contract object does not expose raw tracking numbers, addresses,
        phone numbers, or provider payloads by design.  Callers must already pass
        redacted summaries.
        """

        return dict(self.summary or {})


@dataclass(frozen=True)
class RuntimeToolAction:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    executed: bool = False
    result_source_id: str | None = None


@dataclass(frozen=True)
class RuntimeDecision:
    business_reply_type: BusinessReplyType | str
    next_action: RuntimeAction | str
    customer_reply: str | None = None
    language: str | None = None
    risk_level: str = "low"
    evidence_sources: list[EvidenceSource] = field(default_factory=list)
    tool_actions: list[RuntimeToolAction] = field(default_factory=list)
    handoff_required: bool = False
    ticket_required: bool = False
    routing_required: bool = False
    audit_reasons: list[str] = field(default_factory=list)

    def normalized_reply_type(self) -> BusinessReplyType | str:
        try:
            return BusinessReplyType(str(self.business_reply_type))
        except ValueError:
            return str(self.business_reply_type)

    def normalized_action(self) -> RuntimeAction | str:
        try:
            return RuntimeAction(str(self.next_action))
        except ValueError:
            return str(self.next_action)


@dataclass(frozen=True)
class RuntimeDecisionViolation:
    code: str
    message: str
    severity: str = "high"


@dataclass(frozen=True)
class RuntimeDecisionEvaluation:
    allowed: bool
    violations: list[RuntimeDecisionViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def require_allowed(self) -> None:
        if not self.allowed:
            joined = "; ".join(f"{item.code}: {item.message}" for item in self.violations)
            raise ValueError(joined or "runtime decision is not allowed")


_FACTUAL_REPLY_TYPES = {
    BusinessReplyType.TRACKING_STATUS_ANSWER,
    BusinessReplyType.TOOL_ACTION_RESULT,
    BusinessReplyType.TICKET_CREATED_NOTICE,
}

_ESCALATION_REPLY_TYPES = {
    BusinessReplyType.HANDOFF_NOTICE,
    BusinessReplyType.COMPLAINT_ESCALATION,
    BusinessReplyType.COMPENSATION_ESCALATION,
}


def _types(decision: RuntimeDecision) -> set[EvidenceType | str]:
    return {source.normalized_type() for source in decision.evidence_sources}


def _has_verified_current_status(decision: RuntimeDecision) -> bool:
    for source in decision.evidence_sources:
        if source.normalized_type() != EvidenceType.MCP_CURRENT_STATUS:
            continue
        if source.verified and source.current_status:
            return True
    return False


def _has_customer_visible_knowledge(decision: RuntimeDecision) -> bool:
    return any(
        source.normalized_type() == EvidenceType.KNOWLEDGE_CUSTOMER_VISIBLE and source.customer_visible
        for source in decision.evidence_sources
    )


def _has_executed_action(decision: RuntimeDecision, tool_name: str) -> bool:
    return any(action.tool_name == tool_name and action.executed for action in decision.tool_actions)


def evaluate_runtime_decision(decision: RuntimeDecision) -> RuntimeDecisionEvaluation:
    """Validate a Nexus OSR decision before it can become customer-visible.

    This is a product-level guardrail.  It is stricter than prompt guidance and
    complements the existing WebChat policy gate.  It intentionally treats
    customer claims and previous AI replies as non-authoritative for factual
    customer-facing outcomes.
    """

    violations: list[RuntimeDecisionViolation] = []
    warnings: list[str] = []
    reply_type = decision.normalized_reply_type()
    action = decision.normalized_action()
    evidence_types = _types(decision)

    if EvidenceType.PREVIOUS_AI_REPLY in evidence_types and reply_type in _FACTUAL_REPLY_TYPES:
        violations.append(RuntimeDecisionViolation(
            code="previous_ai_reply_used_as_fact",
            message="Previous AI replies cannot support factual customer-visible outcomes.",
        ))

    if EvidenceType.CUSTOMER_CLAIM in evidence_types and reply_type in _FACTUAL_REPLY_TYPES:
        violations.append(RuntimeDecisionViolation(
            code="customer_claim_used_as_fact",
            message="Customer claims are signals and cannot support verified operational outcomes.",
        ))

    if reply_type == BusinessReplyType.TRACKING_STATUS_ANSWER and not _has_verified_current_status(decision):
        violations.append(RuntimeDecisionViolation(
            code="tracking_status_without_mcp_current_status",
            message="Tracking status answers require verified MCP current-status evidence.",
        ))

    if reply_type == BusinessReplyType.TRACKING_STATUS_ANSWER and EvidenceType.KNOWLEDGE_CUSTOMER_VISIBLE in evidence_types:
        warnings.append("knowledge_used_with_tracking_answer: knowledge may explain policy, but cannot replace MCP current status")

    if reply_type == BusinessReplyType.KNOWLEDGE_ANSWER and not _has_customer_visible_knowledge(decision):
        violations.append(RuntimeDecisionViolation(
            code="knowledge_answer_without_customer_visible_knowledge",
            message="Knowledge answers require customer-visible scoped knowledge evidence.",
            severity="medium",
        ))

    if reply_type == BusinessReplyType.TICKET_CREATED_NOTICE and not _has_executed_action(decision, "ticket.create"):
        violations.append(RuntimeDecisionViolation(
            code="ticket_created_notice_without_ticket_create_action",
            message="Ticket-created notices require an executed ticket.create action.",
        ))

    if reply_type in _ESCALATION_REPLY_TYPES and not (decision.handoff_required or decision.ticket_required):
        violations.append(RuntimeDecisionViolation(
            code="escalation_without_handoff_or_ticket",
            message="Escalation replies must request handoff or create/reuse a ticket.",
            severity="medium",
        ))

    if action == RuntimeAction.ROUTE_TO_GROUP and not decision.ticket_required:
        violations.append(RuntimeDecisionViolation(
            code="routing_without_ticket",
            message="WhatsApp/operator group routing requires a ticket context.",
            severity="medium",
        ))

    if action == RuntimeAction.BLOCK and decision.customer_reply:
        violations.append(RuntimeDecisionViolation(
            code="blocked_decision_has_customer_reply",
            message="Blocked decisions must not produce a customer-visible reply body.",
        ))

    return RuntimeDecisionEvaluation(allowed=not violations, violations=violations, warnings=warnings)
