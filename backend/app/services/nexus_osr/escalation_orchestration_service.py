from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from ...enums import EventType, SourceChannel, TicketPriority
from ...models import Customer, Ticket, TicketEvent
from ...utils.time import utc_now
from ...webchat_models import WebchatConversation, WebchatHandoffRequest
from ..webchat_ai_turn_service import safe_write_webchat_event
from ..webchat_handoff_service import request_webchat_handoff
from .auto_ticket_service import AutoTicketResult, create_or_reuse_ticket_from_case_context
from .case_context import CaseContext, redact_case_text
from .persistence import audit_runtime_decision, load_escalation_policies, resolve_human_hours_policy, save_case_context
from .policies import EscalationAction, EscalationDecision, HumanAvailabilityDecision, HumanAvailabilityStatus, evaluate_escalation
from .runtime_decision_contract import (
    BusinessReplyType,
    RuntimeAction,
    RuntimeDecision,
    RuntimeDecisionEvaluation,
    RuntimeToolAction,
    evaluate_runtime_decision,
)


class EscalationOrchestrationAction(StrEnum):
    CONTINUE_AI = "continue_ai"
    REQUEST_HANDOFF = "request_handoff"
    CREATE_TICKET_OFFLINE = "create_ticket_offline"
    CREATE_TICKET_CUSTOMER_CANNOT_WAIT = "create_ticket_customer_cannot_wait"
    CREATE_TICKET_HIGH_RISK = "create_ticket_high_risk"


@dataclass(frozen=True)
class EscalationOrchestrationResult:
    action: EscalationOrchestrationAction
    case_context: CaseContext
    human_availability: HumanAvailabilityDecision
    escalation: EscalationDecision
    runtime_evaluation: RuntimeDecisionEvaluation
    handoff_request: WebchatHandoffRequest | None = None
    ticket_result: AutoTicketResult | None = None
    audit_id: int | None = None
    event_payload: dict[str, Any] | None = None

    @property
    def ticket(self) -> Ticket | None:
        return self.ticket_result.ticket if self.ticket_result else None


_CUSTOMER_CANNOT_WAIT_RE = re.compile(
    r"\b(can't wait|cannot wait|cant wait|urgent|asap|immediately|right now|too late)\b|等不了|马上|立刻|加急|很急|等不及",
    re.IGNORECASE,
)


def evaluate_and_orchestrate_escalation(
    db: Session,
    *,
    ticket: Ticket,
    conversation: WebchatConversation,
    case_context: CaseContext,
    inbound_message: str,
    queue_key: str,
    ai_attempt_count: int = 0,
    now: datetime | None = None,
    customer: Customer | None = None,
    trigger_message_id: int | None = None,
    ai_turn_id: int | None = None,
) -> EscalationOrchestrationResult:
    """Policy-driven handoff/ticket orchestration for Nexus OSR.

    This service deliberately reuses the existing WebChat handoff service and
    auto-ticket service. It does not send customer-visible text and does not
    create a parallel handoff system.
    """

    country_code = (case_context.country_code or getattr(ticket, "country_code", None) or "GLOBAL").upper()
    channel = case_context.channel or getattr(conversation, "channel_key", None) or "webchat"
    context = case_context.with_inbound_message(inbound_message or "", channel=channel, country_code=country_code)
    policies = load_escalation_policies(db, country_code=country_code, channel=channel) or None
    escalation = evaluate_escalation(inbound_message or "", ai_attempt_count=ai_attempt_count, policies=policies)
    human_policy = resolve_human_hours_policy(db, country_code=country_code, channel=channel, queue_key=queue_key)
    human = human_policy.evaluate(now) if human_policy else HumanAvailabilityDecision(
        status=HumanAvailabilityStatus.OFFLINE,
        queue_key=queue_key,
        reason="human_hours_policy_missing",
        auto_ticket_when_offline=True,
    )
    cannot_wait = _customer_cannot_wait(inbound_message)
    action = _decide_action(escalation=escalation, human=human, cannot_wait=cannot_wait)

    if action == EscalationOrchestrationAction.CONTINUE_AI:
        save_case_context(db, context, tenant_id=getattr(conversation, "tenant_key", None) or "default")
        decision = _runtime_decision(action=action, escalation=escalation, audit_reasons=["continue_ai"])
        evaluation, audit_id = _audit(db, decision=decision, case_context=context, ticket=ticket, conversation=conversation, country_code=country_code, channel=channel)
        payload = _event_payload(action=action, human=human, escalation=escalation, audit_id=audit_id)
        return EscalationOrchestrationResult(action, context, human, escalation, evaluation, audit_id=audit_id, event_payload=payload)

    if action == EscalationOrchestrationAction.REQUEST_HANDOFF:
        context = context.mark_handoff_requested(summary=_handover_summary(escalation, human))
        save_case_context(db, context, tenant_id=getattr(conversation, "tenant_key", None) or "default")
        request = request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="nexus_osr",
            trigger_type="escalation_policy",
            reason_code=escalation.risk_key or human.reason or "human_required",
            reason_text=_handover_summary(escalation, human),
            recommended_agent_action="Review the OSR escalation and take over the customer conversation.",
            trigger_message_id=trigger_message_id,
            ai_turn_id=ai_turn_id,
            requested_by_actor_type="nexus_osr",
        )
        decision = _runtime_decision(action=action, escalation=escalation, handoff=True, audit_reasons=[human.reason])
        evaluation, audit_id = _audit(db, decision=decision, case_context=context, ticket=ticket, conversation=conversation, country_code=country_code, channel=channel)
        payload = _event_payload(action=action, human=human, escalation=escalation, audit_id=audit_id, handoff_request_id=request.id)
        _write_orchestration_events(db, ticket=ticket, conversation=conversation, payload=payload)
        return EscalationOrchestrationResult(action, context, human, escalation, evaluation, handoff_request=request, audit_id=audit_id, event_payload=payload)

    context = _context_for_ticket(context, action=action, escalation=escalation, human=human)
    ticket_result = create_or_reuse_ticket_from_case_context(
        db,
        case_context=context,
        customer=customer,
        conversation=conversation,
        source_channel=_source_channel(channel),
        priority=TicketPriority.high if action == EscalationOrchestrationAction.CREATE_TICKET_HIGH_RISK else TicketPriority.medium,
        issue_type=escalation.risk_key or context.issue_type,
    )
    context = ticket_result.case_context
    decision = _runtime_decision(
        action=action,
        escalation=escalation,
        ticket=True,
        audit_reasons=[human.reason, "customer_cannot_wait" if cannot_wait else "ticket_required"],
        ticket_id=ticket_result.ticket.id,
    )
    evaluation, audit_id = _audit(db, decision=decision, case_context=context, ticket=ticket_result.ticket, conversation=conversation, country_code=country_code, channel=channel)
    payload = _event_payload(action=action, human=human, escalation=escalation, audit_id=audit_id, ticket_id=ticket_result.ticket.id, ticket_created=ticket_result.created)
    _write_orchestration_events(db, ticket=ticket_result.ticket, conversation=conversation, payload=payload)
    return EscalationOrchestrationResult(action, context, human, escalation, evaluation, ticket_result=ticket_result, audit_id=audit_id, event_payload=payload)


def _customer_cannot_wait(value: str | None) -> bool:
    return bool(_CUSTOMER_CANNOT_WAIT_RE.search(str(value or "")))


def _decide_action(*, escalation: EscalationDecision, human: HumanAvailabilityDecision, cannot_wait: bool) -> EscalationOrchestrationAction:
    if cannot_wait:
        return EscalationOrchestrationAction.CREATE_TICKET_CUSTOMER_CANNOT_WAIT
    if not escalation.matched or escalation.action == EscalationAction.TRY_AI_RESOLUTION:
        return EscalationOrchestrationAction.CONTINUE_AI
    if human.is_online and escalation.handoff_required:
        return EscalationOrchestrationAction.REQUEST_HANDOFF
    if escalation.risk_key in {"legal_threat", "compensation", "formal_complaint"}:
        return EscalationOrchestrationAction.CREATE_TICKET_HIGH_RISK
    return EscalationOrchestrationAction.CREATE_TICKET_OFFLINE


def _context_for_ticket(context: CaseContext, *, action: EscalationOrchestrationAction, escalation: EscalationDecision, human: HumanAvailabilityDecision) -> CaseContext:
    summary = _handover_summary(escalation, human)
    next_context = context.mark_handoff_requested(summary=summary)
    if not next_context.issue_type:
        object.__setattr__(next_context, "issue_type", escalation.risk_key or str(action))
    return next_context


def _handover_summary(escalation: EscalationDecision, human: HumanAvailabilityDecision) -> str:
    parts = ["Nexus OSR escalation orchestration."]
    if escalation.risk_key:
        parts.append(f"Risk: {escalation.risk_key}.")
    if escalation.reason:
        parts.append(f"Reason: {escalation.reason}.")
    parts.append(f"Human availability: {human.status} ({human.reason}).")
    return redact_case_text(" ".join(parts), limit=500)


def _runtime_decision(
    *,
    action: EscalationOrchestrationAction,
    escalation: EscalationDecision,
    handoff: bool = False,
    ticket: bool = False,
    audit_reasons: list[str] | None = None,
    ticket_id: int | None = None,
) -> RuntimeDecision:
    if action == EscalationOrchestrationAction.REQUEST_HANDOFF:
        reply_type = BusinessReplyType.HANDOFF_NOTICE
        next_action = RuntimeAction.REQUEST_HANDOFF
    elif action in {EscalationOrchestrationAction.CREATE_TICKET_OFFLINE, EscalationOrchestrationAction.CREATE_TICKET_CUSTOMER_CANNOT_WAIT, EscalationOrchestrationAction.CREATE_TICKET_HIGH_RISK}:
        reply_type = BusinessReplyType.TICKET_CREATED_NOTICE
        next_action = RuntimeAction.CREATE_TICKET
        ticket = True
    else:
        reply_type = BusinessReplyType.CLARIFICATION
        next_action = RuntimeAction.REPLY
    tool_actions = []
    if ticket and ticket_id is not None:
        tool_actions.append(RuntimeToolAction(tool_name="ticket.create", arguments={"ticket_id": ticket_id}, executed=True, result_source_id=f"ticket:{ticket_id}"))
    return RuntimeDecision(
        business_reply_type=reply_type,
        next_action=next_action,
        customer_reply=None,
        risk_level="high" if action == EscalationOrchestrationAction.CREATE_TICKET_HIGH_RISK else ("medium" if escalation.matched else "low"),
        tool_actions=tool_actions,
        handoff_required=handoff,
        ticket_required=ticket,
        audit_reasons=[item for item in (audit_reasons or []) if item],
    )


def _audit(db: Session, *, decision: RuntimeDecision, case_context: CaseContext, ticket: Ticket, conversation: WebchatConversation, country_code: str, channel: str) -> tuple[RuntimeDecisionEvaluation, int | None]:
    evaluation = evaluate_runtime_decision(decision)
    row = audit_runtime_decision(
        db,
        decision=decision,
        evaluation=evaluation,
        case_context=case_context,
        tenant_id=getattr(conversation, "tenant_key", None) or "default",
        channel=channel,
        country_code=country_code,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
    )
    return evaluation, row.id


def _event_payload(*, action: EscalationOrchestrationAction, human: HumanAvailabilityDecision, escalation: EscalationDecision, audit_id: int | None, **extra: Any) -> dict[str, Any]:
    payload = {
        "source": "nexus_osr",
        "action": str(action),
        "human_status": str(human.status),
        "human_reason": human.reason,
        "queue_key": human.queue_key,
        "risk_key": escalation.risk_key,
        "escalation_action": str(escalation.action),
        "audit_id": audit_id,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return _safe_payload(payload)


def _write_orchestration_events(db: Session, *, ticket: Ticket, conversation: WebchatConversation, payload: dict[str, Any]) -> None:
    db.add(TicketEvent(ticket_id=ticket.id, actor_id=None, event_type=EventType.field_updated, note="Nexus OSR escalation orchestration", payload_json=json.dumps(payload, ensure_ascii=False, default=str)))
    safe_write_webchat_event(db, conversation_id=conversation.id, ticket_id=ticket.id, event_type="nexus_osr.escalation_orchestrated", payload=payload)
    db.flush()


def _source_channel(channel: str | None) -> SourceChannel:
    return SourceChannel.whatsapp if str(channel or "").lower() == SourceChannel.whatsapp.value else SourceChannel.web_chat


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str):
        return redact_case_text(value, limit=500)
    return value
