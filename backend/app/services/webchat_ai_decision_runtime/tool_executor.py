from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import Ticket
from app.webchat_models import WebchatConversation
from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webchat_fast_session_service import (
    FastBusinessState,
    FastRoutingContext,
    append_fast_system_handoff_message,
    get_or_create_fast_ticket,
)
from app.services.webchat_handoff_service import request_webchat_handoff

from .audit import log_ai_decision_audit, stable_json_hash
from .policy_gate import PolicyGateResult
from .schemas import AIDecision, AIDecisionToolCall
from .tool_registry import get_tool_contract


@dataclass(frozen=True)
class ToolExecutionRecord:
    tool_name: str
    status: str
    idempotency_key: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    ok: bool
    records: tuple[ToolExecutionRecord, ...] = field(default_factory=tuple)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "records": [record.__dict__ for record in self.records],
        }


def _clip(value: Any, limit: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:limit] if cleaned else None


def _call_idempotency_key(
    *,
    call: AIDecisionToolCall,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    conversation: WebchatConversation | None = None,
    ticket: Ticket | None = None,
) -> str:
    if call.idempotency_key:
        return call.idempotency_key
    payload = {
        "tool_name": call.tool_name,
        "tenant_key": tenant_key,
        "channel_key": channel_key,
        "session_id": session_id,
        "client_message_id": client_message_id,
        "conversation_id": getattr(conversation, "id", None),
        "ticket_id": getattr(ticket, "id", None),
        "arguments": call.arguments,
    }
    return stable_json_hash(payload)


def _handoff_reason(decision: AIDecision, call: AIDecisionToolCall) -> str:
    args = call.arguments or {}
    return _clip(args.get("reason") or args.get("reason_code") or decision.handoff_reason, 240) or "ai_requested_human_review"


def _recommended_action(decision: AIDecision, call: AIDecisionToolCall) -> str:
    args = call.arguments or {}
    return _clip(
        args.get("recommended_agent_action")
        or args.get("agent_action")
        or f"Review AI decision intent={decision.intent}; validate evidence before taking any controlled action.",
        1000,
    ) or "Review AI decision and reply with verified information."


def execute_decision_tools(
    db: Session,
    *,
    decision: AIDecision,
    policy_result: PolicyGateResult,
    conversation: WebchatConversation,
    business_state: FastBusinessState,
    routing_context: FastRoutingContext | None,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    customer_message: str,
    request_id: str | None = None,
) -> ToolExecutionResult:
    """Execute only the phase-one safe subset of AI-requested tools.

    Phase one intentionally executes handoff.request.create through existing
    ticket/handoff services. High-risk Speedaf write tools remain blocked by
    Policy Gate and are recorded as skipped rather than executed.
    """

    if not policy_result.ok:
        return ToolExecutionResult(
            ok=False,
            records=(
                ToolExecutionRecord(
                    tool_name="policy_gate",
                    status="blocked",
                    error_code="policy_gate_failed",
                    result=policy_result.safe_summary(),
                ),
            ),
        )

    records: list[ToolExecutionRecord] = []
    ticket: Ticket | None = None

    for call in decision.tool_calls:
        contract = get_tool_contract(call.tool_name)
        if contract is None:
            records.append(ToolExecutionRecord(tool_name=call.tool_name, status="blocked", error_code="unknown_tool"))
            continue
        idem = _call_idempotency_key(
            call=call,
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            client_message_id=client_message_id,
            conversation=conversation,
            ticket=ticket,
        )
        if call.tool_name == "handoff.request.create":
            reason = _handoff_reason(decision, call)
            recommended_action = _recommended_action(decision, call)
            ticket = get_or_create_fast_ticket(
                db,
                conversation=conversation,
                business_state=business_state,
                handoff_reason=reason,
                recommended_agent_action=recommended_action,
                customer_message=customer_message,
                routing_context=routing_context,
            )
            handoff_message = append_fast_system_handoff_message(
                db,
                conversation=conversation,
                handoff_reason=reason,
                recommended_agent_action=recommended_action,
                client_message_id=client_message_id,
            )
            request_row = request_webchat_handoff(
                db,
                conversation=conversation,
                ticket=ticket,
                source="ai_decision_runtime",
                trigger_type="handoff.request.create",
                reason_code=reason,
                reason_text=reason,
                recommended_agent_action=recommended_action,
                trigger_message_id=handoff_message.id,
                requested_by_actor_type="ai",
                note=f"AI decision runtime tool call; idem={idem[:40]}",
            )
            records.append(
                ToolExecutionRecord(
                    tool_name=call.tool_name,
                    status="executed",
                    idempotency_key=idem,
                    result={
                        "ticket_id": ticket.id,
                        "handoff_request_id": request_row.id,
                        "conversation_ai_suspended": True,
                    },
                )
            )
            continue

        if call.tool_name == "speedaf.order.query":
            # Tracking lookup is executed before provider decision in WebChat Fast
            # and attached as trusted fact metadata; never execute a second lookup
            # here unless a future action-executor flow explicitly owns it.
            records.append(
                ToolExecutionRecord(
                    tool_name=call.tool_name,
                    status="already_resolved_by_context",
                    idempotency_key=idem,
                    result={"tracking_number_hash": hash_tracking_number(business_state.tracking_number)},
                )
            )
            continue

        records.append(
            ToolExecutionRecord(
                tool_name=call.tool_name,
                status="skipped_contract_only_phase_one",
                idempotency_key=idem,
                result={
                    "classification": contract.classification,
                    "risk_level": contract.risk_level,
                    "allowed_auto_execution_mode": contract.allowed_auto_execution_mode,
                    "confirmation_required": contract.confirmation_required,
                },
            )
        )

    log_ai_decision_audit(
        event="webchat_ai_decision_tools_executed",
        request_id=request_id,
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        conversation_id=conversation.id,
        ticket_id=ticket.id if ticket is not None else getattr(conversation, "ticket_id", None),
        payload={"decision": decision.safe_public_summary(), "execution": [record.__dict__ for record in records]},
    )
    return ToolExecutionResult(ok=all(record.status not in {"blocked"} for record in records), records=tuple(records))
