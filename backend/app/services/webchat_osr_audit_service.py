from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Ticket
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .nexus_osr.case_context import CaseContext, redact_case_text
from .nexus_osr.persistence import audit_runtime_decision
from .nexus_osr.runtime_bridge import build_case_context_from_webchat
from .nexus_osr.runtime_decision_contract import (
    BusinessReplyType,
    EvidenceSource,
    EvidenceType,
    RuntimeAction,
    RuntimeDecision,
    evaluate_runtime_decision,
)


def audit_completed_webchat_ai_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist a safe OSR audit sidecar for a completed WebChat AI turn.

    This module is intentionally audit-only. It does not generate customer-visible
    message bodies, does not call tools, and does not alter WebChat outbound
    behavior. Raw visitor text is only used to update short-lived Case Context
    through the existing redaction helpers.
    """

    case_context = build_case_context_from_webchat(
        db,
        ticket=ticket,
        conversation=conversation,
        visitor_message=visitor_message,
        issue_type=getattr(conversation, "last_intent", None) or getattr(ticket, "case_type", None),
    )
    decision = _runtime_decision_from_webchat_result(result or {}, case_context=case_context)
    evaluation = evaluate_runtime_decision(decision)
    row = audit_runtime_decision(
        db,
        decision=decision,
        evaluation=evaluation,
        case_context=case_context,
        tenant_id=getattr(conversation, "tenant_key", None) or "default",
        channel=getattr(conversation, "channel_key", None),
        country_code=getattr(ticket, "country_code", None),
        conversation_id=conversation.id,
        ticket_id=ticket.id,
    )
    return {
        "mode": "webchat_osr_audit",
        "audit_id": row.id,
        "allowed": bool(row.allowed),
        "business_reply_type": row.business_reply_type,
        "next_action": row.next_action,
        "risk_level": row.risk_level,
        "conversation_id": conversation.id,
        "ticket_id": ticket.id,
        "ai_turn_id": turn.id,
        "result_status": _safe_result_value((result or {}).get("status")),
        "result_reason": _safe_result_value((result or {}).get("reason") or (result or {}).get("fallback_reason")),
    }


def _runtime_decision_from_webchat_result(result: dict[str, Any], *, case_context: CaseContext) -> RuntimeDecision:
    status = _safe_result_value(result.get("status")) or "unknown"
    reason = _safe_result_value(result.get("reason") or result.get("fallback_reason"))
    osr_escalation = result.get("osr_escalation") if isinstance(result.get("osr_escalation"), dict) else {}
    handoff_required = bool(result.get("runtime_handoff_required")) or status == "handoff_requested"
    ticket_required = bool(result.get("ticket_id") or osr_escalation.get("ticket_id") or reason == "osr_ticket_created")

    if handoff_required:
        reply_type = BusinessReplyType.HANDOFF_NOTICE
        next_action = RuntimeAction.REQUEST_HANDOFF
        risk_level = "high"
    elif ticket_required:
        # The WebChat safe worker is not creating a customer-visible ticket notice
        # here; it only records that the turn was closed by an OSR ticket path.
        reply_type = BusinessReplyType.NO_ANSWER
        next_action = RuntimeAction.CREATE_TICKET
        risk_level = "high"
    elif status in {"review_required", "skipped", "superseded", "failed", "timeout"}:
        reply_type = BusinessReplyType.NO_ANSWER
        next_action = RuntimeAction.BLOCK
        risk_level = "medium" if status == "review_required" else "low"
    else:
        reply_type = BusinessReplyType.CLARIFICATION
        next_action = RuntimeAction.REPLY
        risk_level = "low"

    return RuntimeDecision(
        business_reply_type=reply_type,
        next_action=next_action,
        customer_reply=None,
        risk_level=risk_level,
        evidence_sources=[
            EvidenceSource(
                evidence_type=EvidenceType.CASE_CONTEXT,
                source_id=f"case_context:{case_context.ticket_id or case_context.conversation_id or 'unknown'}",
                label="Case Context",
                summary=case_context.as_dict(),
                verified=False,
                current_status=False,
            )
        ],
        handoff_required=handoff_required,
        ticket_required=ticket_required,
        audit_reasons=[item for item in ["webchat_ai_turn_completed", f"status:{status}", f"reason:{reason}" if reason else None] if item],
    )


def _safe_result_value(value: Any) -> str | None:
    if value is None:
        return None
    return redact_case_text(str(value), limit=160) or None
