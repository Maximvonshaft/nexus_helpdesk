from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...enums import EventType, SourceChannel, TicketPriority
from ...models import Customer, Ticket, TicketEvent
from ...models_agent_routing import ConversationControl
from ...tool_models import ToolCallLog
from ...utils.time import utc_now
from ...webchat_models import WebchatConversation, WebchatHandoffRequest
from ..knowledge_retrieval_service import retrieve_published_chunks
from ..speedaf.tracking_fact_source import (
    lookup_speedaf_track_history_fact,
    lookup_speedaf_tracking_fact,
)
from ..tracking_fact_schema import TrackingFactResult, safe_tracking_reference
from ..tracking_fact_service import lookup_tracking_fact
from ..agent_availability_service import availability_summary
from ..agent_routing_service import request_handoff
from ..webchat_ai_decision_runtime.policy_gate import PolicyGateResult, validate_ai_decision
from ..webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionToolCall
from ..webchat_ai_decision_runtime.tool_registry import (
    canonical_tool_name,
    get_tool_contract,
)
from ..webchat_ai_turn_service import safe_write_webchat_event
from .auto_ticket_service import create_or_reuse_ticket_from_case_context
from .case_context import CaseContext, redact_case_text
from .controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
    ControlledActionExecutor,
)
from .persistence import (
    audit_runtime_decision,
    resolve_tool_execution_policy,
    save_case_context,
)
from .runtime_decision_contract import (
    BusinessReplyType,
    RuntimeAction,
    RuntimeDecision,
    RuntimeDecisionEvaluation,
    RuntimeDecisionViolation,
    RuntimeToolAction,
)


@dataclass(frozen=True)
class GovernedToolExecutionOptions:
    allow_high_risk_write_execution: bool = False
    allowed_high_risk_write_tools: frozenset[str] = frozenset()
    customer_confirmation_granted: bool = False
    human_confirmation_granted: bool = False
    allowed_tool_names: frozenset[str] | None = None
    granted_permissions: frozenset[str] = frozenset()


def runtime_tool_actions_from_tool_calls(
    tool_calls: Iterable[Any],
) -> list[RuntimeToolAction]:
    """Convert strict runtime proposals into bounded canonical actions."""

    actions: list[RuntimeToolAction] = []
    for item in tool_calls or []:
        data = _tool_call_dict(item)
        tool_name = canonical_tool_name(
            data.get("tool_name") or data.get("name") or data.get("tool")
        )
        if not tool_name:
            continue
        arguments = (
            data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        )
        actions.append(
            RuntimeToolAction(
                tool_name=tool_name,
                arguments=_bounded_execution_arguments(arguments),
                requires_confirmation=bool(data.get("requires_confirmation")),
                executed=False,
            )
        )
    return actions


def execute_controlled_tool_calls(
    db: Session,
    *,
    tool_calls: Iterable[Any],
    case_context: CaseContext,
    channel: str | None = None,
    country_code: str | None = None,
    tenant_id: str = "default",
    conversation: WebchatConversation | None = None,
    ticket: Ticket | None = None,
    customer: Customer | None = None,
    ai_decision: AIDecision | None = None,
    options: GovernedToolExecutionOptions | None = None,
) -> list[ActionExecutionResult]:
    options = options or GovernedToolExecutionOptions()
    raw_calls = [_tool_call_dict(item) for item in (tool_calls or [])]
    actions = runtime_tool_actions_from_tool_calls(raw_calls)
    if not actions:
        return []

    channel = channel or case_context.channel or getattr(conversation, "channel_key", None)
    if conversation is None and _numeric(case_context.conversation_id):
        conversation = db.get(WebchatConversation, int(case_context.conversation_id))
    if ticket is None and _numeric(case_context.ticket_id):
        ticket = db.get(Ticket, int(case_context.ticket_id))
    if ticket is None and conversation is not None and conversation.ticket_id:
        ticket = db.get(Ticket, int(conversation.ticket_id))
    if country_code is None:
        country_code = case_context.country_code or getattr(ticket, "country_code", None)
    if customer is None:
        customer = _customer_for_context(
            db,
            conversation=conversation,
            ticket=ticket,
        )

    policy_gate_decision = ai_decision or _decision_for_policy_gate(raw_calls, actions)
    gate_result = validate_ai_decision(
        policy_gate_decision,
        allow_high_risk_write_execution=options.allow_high_risk_write_execution,
        allowed_high_risk_write_tools=set(options.allowed_high_risk_write_tools),
        granted_permissions=(
            set(options.granted_permissions)
            if options.allowed_tool_names is not None
            else None
        ),
        customer_confirmation_granted=options.customer_confirmation_granted,
        human_confirmation_granted=options.human_confirmation_granted,
        enforce_confirmation_requirements=False,
    )
    if not gate_result.ok:
        return [
            _blocked_by_policy_gate(
                db,
                action=action,
                gate_result=gate_result,
                case_context=case_context,
                tenant_id=tenant_id,
                channel=channel,
                country_code=country_code,
                conversation=conversation,
                ticket=ticket,
                idempotency_key=_idempotency_key_for_action(
                    action,
                    case_context=case_context,
                    tenant_id=tenant_id,
                    channel=channel,
                    country_code=country_code,
                ),
            )
            for action in actions
        ]

    results: list[ActionExecutionResult] = []
    for action in actions:
        idempotency_key = _idempotency_key_for_action(
            action,
            case_context=case_context,
            tenant_id=tenant_id,
            channel=channel,
            country_code=country_code,
        )
        contract = get_tool_contract(action.tool_name)
        registry_error = None
        if contract is None:
            registry_error = "unknown_tool"
        elif (
            options.allowed_tool_names is not None
            and action.tool_name not in options.allowed_tool_names
        ):
            registry_error = "tool_not_available"
        elif (
            options.allowed_tool_names is not None
            and not set(contract.required_permissions).issubset(
                options.granted_permissions
            )
        ):
            registry_error = "tool_permission_denied"
        if registry_error is not None:
            results.append(
                _blocked_by_registry(
                    db,
                    action=action,
                    error_code=registry_error,
                    case_context=case_context,
                    tenant_id=tenant_id,
                    channel=channel,
                    country_code=country_code,
                    conversation=conversation,
                    ticket=ticket,
                    idempotency_key=idempotency_key,
                )
            )
            continue
        duplicate = _existing_executed_log(
            db,
            action=action,
            idempotency_key=idempotency_key,
            conversation=conversation,
            ticket=ticket,
        )
        if duplicate is not None:
            results.append(
                ActionExecutionResult(
                    ok=True,
                    tool_name=action.tool_name,
                    status="duplicate",
                    summary={
                        "tool_call_log_id": duplicate.id,
                        "idempotency_key": idempotency_key,
                    },
                    case_context=case_context,
                )
            )
            continue

        policy = resolve_tool_execution_policy(
            db,
            tool_name=action.tool_name,
            country_code=country_code,
            channel=channel,
        )
        executor = ControlledActionExecutor(
            policies={action.tool_name: policy} if policy else {},
            handlers=_production_handlers(
                db,
                conversation=conversation,
                ticket=ticket,
                customer=customer,
            ),
            allowed_high_risk_write_tools=(
                options.allowed_high_risk_write_tools
                if options.allow_high_risk_write_execution
                else frozenset()
            ),
        )
        started = time.monotonic()
        result = executor.execute(
            ActionExecutionRequest(
                action=action,
                channel=channel,
                country_code=country_code,
                case_context=case_context,
                idempotency_key=idempotency_key,
                audit_context={
                    "tenant_id": tenant_id,
                    "customer_confirmation_granted": bool(
                        options.customer_confirmation_granted
                    ),
                    "human_confirmation_granted": bool(
                        options.human_confirmation_granted
                    ),
                },
            )
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if result.case_context is not None:
            case_context = result.case_context
            save_case_context(db, case_context, tenant_id=tenant_id)
        _write_tool_call_log(
            db,
            action=action,
            result=result,
            case_context=case_context,
            channel=channel,
            conversation=conversation,
            ticket=ticket,
            idempotency_key=idempotency_key,
            elapsed_ms=elapsed_ms,
        )
        _audit_tool_decision(
            db,
            action=action,
            result=result,
            case_context=case_context,
            tenant_id=tenant_id,
            channel=channel,
            country_code=country_code,
            conversation=conversation,
            ticket=ticket,
        )
        results.append(result)
    return results


def _customer_for_context(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
) -> Customer | None:
    if ticket is not None and ticket.customer_id:
        return db.get(Customer, ticket.customer_id)
    if conversation is None:
        return None
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    return (
        db.get(Customer, control.customer_id)
        if control is not None and control.customer_id is not None
        else None
    )


def _production_handlers(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    customer: Customer | None,
) -> dict[str, ActionHandler]:
    def knowledge_search(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        query = str(request.action.arguments.get("query") or "").strip()
        limit = max(1, min(int(request.action.arguments.get("limit") or 5), 8))
        retrieval = retrieve_published_chunks(
            db,
            q=query,
            tenant_id=str(request.audit_context.get("tenant_id") or "default"),
            market_id=getattr(ticket, "market_id", None),
            channel=request.channel,
            audience_scope="customer",
            language=None,
            limit=limit,
        )
        hits = []
        for hit in retrieval.hits[:limit]:
            answer = str(hit.direct_answer or hit.text or "").strip()
            if answer:
                hits.append(
                    {
                        "source_id": str(hit.item_key)[:180],
                        "title": str(hit.title)[:180],
                        "answer": answer[:1200],
                        "answer_mode": hit.answer_mode,
                    }
                )
        return ActionExecutionResult(
            ok=bool(hits),
            tool_name=request.action.tool_name,
            status="executed" if hits else "no_results",
            summary={"query": query[:240], "hits": hits, "count": len(hits)},
            customer_visible_summary=(
                None if hits else "No approved knowledge result was found."
            ),
            case_context=request.case_context,
            error_code=None if hits else "knowledge_not_found",
        )

    def shipment_query(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        tracking_number = str(
            request.action.arguments.get("tracking_number") or ""
        ).strip().upper()
        fact = lookup_tracking_fact(
            tracking_number=tracking_number,
            conversation_id=getattr(conversation, "id", None),
            ticket_id=getattr(ticket, "id", None),
            request_id=request.idempotency_key,
            country_code=request.country_code,
        )
        return _tracking_action_result(
            request,
            fact,
        )

    def shipment_history_query(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        tracking_number = str(
            request.action.arguments.get("tracking_number") or ""
        ).strip().upper()
        fact = lookup_speedaf_track_history_fact(
            tracking_number=tracking_number,
            conversation_id=getattr(conversation, "id", None),
            ticket_id=getattr(ticket, "id", None),
            request_id=request.idempotency_key,
        )
        return _tracking_action_result(request, fact)

    def waybill_candidates_query(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        fact = lookup_speedaf_tracking_fact(
            tracking_number=None,
            caller_id=str(
                request.action.arguments.get("caller_id") or ""
            ).strip(),
            country_code=(
                str(
                    request.action.arguments.get("country_code")
                    or request.country_code
                    or ""
                ).strip()
                or None
            ),
            conversation_id=getattr(conversation, "id", None),
            ticket_id=getattr(ticket, "id", None),
            request_id=request.idempotency_key,
        )
        return _tracking_action_result(request, fact)

    def support_availability(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        current = _resolve_conversation(
            db,
            conversation=conversation,
            case_context=request.case_context,
        )
        if current is None:
            return ActionExecutionResult(
                False,
                request.action.tool_name,
                "failed",
                error_code="conversation_required",
            )
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == current.id)
            .first()
        )
        if control is None:
            return ActionExecutionResult(
                False,
                request.action.tool_name,
                "failed",
                error_code="conversation_control_required",
            )
        request_row = (
            db.get(
                WebchatHandoffRequest,
                current.current_handoff_request_id,
            )
            if current.current_handoff_request_id is not None
            else None
        )
        if (
            request_row is not None
            and request_row.conversation_id != current.id
        ):
            request_row = None
        summary = availability_summary(
            db,
            tenant_key=control.tenant_key,
            country_code=control.country_code,
            channel_key=control.channel_key,
            request_row=request_row,
        )
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary=summary,
            customer_visible_summary=_availability_customer_summary(summary),
            case_context=request.case_context,
        )

    def ticket_create(request: ActionExecutionRequest) -> ActionExecutionResult:
        if request.case_context is None:
            return ActionExecutionResult(
                False,
                request.action.tool_name,
                "failed",
                error_code="case_context_required",
            )
        current_conversation = _resolve_conversation(
            db,
            conversation=conversation,
            case_context=request.case_context,
        )
        current_customer = customer or _customer_for_context(
            db,
            conversation=current_conversation,
            ticket=ticket,
        )
        result = create_or_reuse_ticket_from_case_context(
            db,
            case_context=request.case_context,
            customer=current_customer,
            conversation=current_conversation,
            source_channel=_source_channel(request.channel),
            title=_optional_arg(request.action.arguments, "title", 200),
            description=_optional_arg(request.action.arguments, "description", 4000),
            priority=_priority_arg(request.action.arguments),
            issue_type=_optional_arg(request.action.arguments, "issue_type", 120),
        )
        if current_conversation is not None:
            current_conversation.ticket_id = result.ticket.id
            current_conversation.updated_at = utc_now()
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary={
                "ticket_id": result.ticket.id,
                "ticket_no": result.ticket.ticket_no,
                "created": result.created,
                "idempotency_key": request.idempotency_key,
            },
            customer_visible_summary=result.customer_visible_summary,
            case_context=result.case_context,
        )

    def handoff_request(request: ActionExecutionRequest) -> ActionExecutionResult:
        ctx = request.case_context
        current_conversation = _resolve_conversation(
            db,
            conversation=conversation,
            case_context=ctx,
        )
        if ctx is None or current_conversation is None:
            return ActionExecutionResult(
                False,
                request.action.tool_name,
                "failed",
                error_code="handoff_context_required",
            )
        reason = (
            _optional_arg(request.action.arguments, "reason", 160)
            or "human_review_required"
        )
        request_row = request_handoff(
            db,
            conversation=current_conversation,
            source="ai_auto",
            trigger_type="osr_tool_call",
            reason_code=reason,
            reason_text=reason,
            recommended_agent_action=_optional_arg(
                request.action.arguments,
                "recommended_agent_action",
                1000,
            ),
            requested_by_actor_type="system",
        )
        next_context = ctx.mark_handoff_requested(summary=reason)
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary={
                "handoff_request_id": request_row.id,
                "status": request_row.status,
                "assigned_agent_id": request_row.assigned_agent_id,
            },
            customer_visible_summary="A human support handoff has been requested.",
            case_context=next_context,
        )

    def timeline_event_create(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        ctx = request.case_context
        summary = (
            _optional_arg(request.action.arguments, "summary", 500)
            or _optional_arg(request.action.arguments, "note", 500)
            or "OSR internal event"
        )
        payload = {
            "source": "nexus_osr",
            "tool_name": request.action.tool_name,
            "summary": summary,
            "idempotency_key": request.idempotency_key,
        }
        event_id = None
        if ticket is not None:
            row = TicketEvent(
                ticket_id=ticket.id,
                actor_id=None,
                event_type=EventType.internal_note_added,
                note=summary,
                payload_json=json.dumps(payload, ensure_ascii=False, default=str),
            )
            db.add(row)
            db.flush()
            event_id = row.id
        if conversation is not None:
            event = safe_write_webchat_event(
                db,
                conversation_id=conversation.id,
                ticket_id=conversation.ticket_id,
                event_type="osr.timeline_event",
                payload=payload,
            )
            if event_id is None and event is not None:
                event_id = event.id
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary={"event_id": event_id, "summary": summary},
            customer_visible_summary=None,
            case_context=ctx,
        )

    return {
        "knowledge.search": knowledge_search,
        "support.availability": support_availability,
        "speedaf.order.query": shipment_query,
        "speedaf.express.track.query": shipment_history_query,
        "speedaf.order.waybillCode.query": waybill_candidates_query,
        "ticket.create": ticket_create,
        "handoff.request.create": handoff_request,
        "timeline.event.create": timeline_event_create,
    }


def _resolve_conversation(
    db: Session,
    *,
    conversation: WebchatConversation | None,
    case_context: CaseContext | None,
) -> WebchatConversation | None:
    if conversation is not None:
        return conversation
    if case_context is not None and _numeric(case_context.conversation_id):
        return db.get(WebchatConversation, int(case_context.conversation_id))
    return None



_EXECUTABLE_TOOL_NAMES = (
    "handoff.request.create",
    "knowledge.search",
    "speedaf.express.track.query",
    "speedaf.order.query",
    "speedaf.order.waybillCode.query",
    "support.availability",
    "ticket.create",
    "timeline.event.create",
)


def executable_tool_names() -> tuple[str, ...]:
    return _EXECUTABLE_TOOL_NAMES


def _tracking_action_result(
    request: ActionExecutionRequest,
    fact: TrackingFactResult,
) -> ActionExecutionResult:
    result: dict[str, Any] = {
        "reference": safe_tracking_reference(fact.tracking_number),
        "checked_at": fact.checked_at,
        "observed_at": fact.observed_at,
        "freshness": fact.freshness,
        "evidence_state": fact.evidence_state,
        "source_authority": fact.source_authority,
    }
    if fact.fact_evidence_present:
        result.update(
            {
                "status": fact.status,
                "status_label": fact.status_label,
                "latest_event": (
                    fact.latest_event.to_safe_dict()
                    if fact.latest_event
                    else None
                ),
                "recent_events": [
                    event.to_safe_dict()
                    for event in fact.events_summary[:5]
                ],
                "status_context": _safe_value(
                    "status_context",
                    fact.status_context,
                ),
            }
        )
    else:
        result.update(
            {
                "failure_reason": fact.failure_reason,
                "failure_summary": fact.failure_summary,
                "retryable": fact.failure_retryable,
                "needs_customer_confirmation": (
                    fact.failure_needs_customer_confirmation
                ),
                "needs_human_review": fact.failure_needs_human_review,
                "safe_candidates": _safe_value(
                    "safe_candidates",
                    fact.safe_candidates[:10],
                ),
            }
        )
    return ActionExecutionResult(
        ok=bool(fact.fact_evidence_present),
        tool_name=request.action.tool_name,
        status=(
            "executed"
            if fact.fact_evidence_present
            else str(fact.tool_status or "no_evidence")
        ),
        summary=result,
        customer_visible_summary=None,
        case_context=request.case_context,
        error_code=(
            None
            if fact.fact_evidence_present
            else str(fact.failure_reason or "tracking_unavailable")[:120]
        ),
    )


def _availability_customer_summary(summary: dict[str, Any]) -> str:
    online = int(summary.get("online_agents") or 0)
    available = int(summary.get("available_capacity") or 0)
    queued = int(summary.get("queue_count") or 0)
    raw_position = summary.get("queue_position")
    position = int(raw_position) if isinstance(raw_position, int) else None
    if online <= 0:
        return "No human support agent is currently online."
    if available > 0:
        return (
            "Human support is available with "
            f"{available} open conversation slot(s)."
        )
    if position is not None and position > 0:
        ahead = max(0, position - 1)
        if ahead == 0:
            return (
                "Human support is currently at capacity. "
                "This customer is next in the eligible queue."
            )
        return (
            "Human support is currently at capacity with "
            f"{ahead} conversation(s) ahead of this customer."
        )
    return (
        "Human support is currently at capacity with "
        f"{queued} conversation(s) waiting."
    )


def _raw_policy_tool_calls(
    raw_calls: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    """Preserve raw model arguments for Registry schema validation.

    Execution receives bounded arguments through RuntimeToolAction only after
    malformed or additional properties have been rejected by the canonical
    Tool Registry JSON Schema.
    """

    normalized: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for raw in raw_calls:
        data = _tool_call_dict(raw)
        tool_name = canonical_tool_name(
            data.get("tool_name") or data.get("name") or data.get("tool")
        )
        if not tool_name:
            continue
        arguments = (
            data.get("arguments")
            if isinstance(data.get("arguments"), dict)
            else {}
        )
        normalized.append((data, tool_name, arguments))
    return normalized

def _decision_for_policy_gate(
    raw_calls: list[dict[str, Any]],
    actions: list[RuntimeToolAction],
) -> AIDecision:
    return AIDecision.model_construct(
        customer_reply=None,
        intent=(
            "handoff_request"
            if any(
                action.tool_name == "handoff.request.create"
                for action in actions
            )
            else "general_support"
        ),
        confidence=1.0,
        risk_level="medium" if actions else "low",
        next_action="call_tool",
        handoff_required=any(
            action.tool_name == "handoff.request.create"
            for action in actions
        ),
        handoff_reason=(
            "customer_requested_human"
            if any(
                action.tool_name == "handoff.request.create"
                for action in actions
            )
            else None
        ),
        tool_calls=[
            AIDecisionToolCall.model_construct(
                tool_name=tool_name,
                arguments=dict(arguments),
                idempotency_key=None,
                requires_confirmation=bool(data.get("requires_confirmation")),
            )
            for data, tool_name, arguments in _raw_policy_tool_calls(raw_calls)
        ],
        evidence_used=[],
        safety_notes=[],
    )

def _blocked_by_registry(
    db: Session,
    *,
    action: RuntimeToolAction,
    error_code: str,
    case_context: CaseContext,
    tenant_id: str,
    channel: str | None,
    country_code: str | None,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    idempotency_key: str | None,
) -> ActionExecutionResult:
    result = ActionExecutionResult(
        ok=False,
        tool_name=action.tool_name,
        status="blocked",
        error_code=error_code,
        error_message=error_code,
        case_context=case_context,
    )
    _write_tool_call_log(
        db,
        action=action,
        result=result,
        case_context=case_context,
        channel=channel,
        conversation=conversation,
        ticket=ticket,
        idempotency_key=idempotency_key,
        elapsed_ms=0,
    )
    _audit_tool_decision(
        db,
        action=action,
        result=result,
        case_context=case_context,
        tenant_id=tenant_id,
        channel=channel,
        country_code=country_code,
        conversation=conversation,
        ticket=ticket,
    )
    return result


def _blocked_by_policy_gate(
    db: Session,
    *,
    action: RuntimeToolAction,
    gate_result: PolicyGateResult,
    case_context: CaseContext,
    tenant_id: str,
    channel: str | None,
    country_code: str | None,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    idempotency_key: str | None,
) -> ActionExecutionResult:
    first = gate_result.violations[0] if gate_result.violations else None
    result = ActionExecutionResult(
        ok=False,
        tool_name=action.tool_name,
        status="blocked",
        summary={"policy_gate": gate_result.safe_summary()},
        case_context=case_context,
        error_code=first.code if first else "policy_gate_blocked",
        error_message=(
            first.message if first else "PolicyGate blocked this tool action."
        ),
    )
    _write_tool_call_log(
        db,
        action=action,
        result=result,
        case_context=case_context,
        channel=channel,
        conversation=conversation,
        ticket=ticket,
        idempotency_key=idempotency_key,
        elapsed_ms=0,
    )
    _audit_tool_decision(
        db,
        action=action,
        result=result,
        case_context=case_context,
        tenant_id=tenant_id,
        channel=channel,
        country_code=country_code,
        conversation=conversation,
        ticket=ticket,
    )
    return result


def _write_tool_call_log(
    db: Session,
    *,
    action: RuntimeToolAction,
    result: ActionExecutionResult,
    case_context: CaseContext,
    channel: str | None,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
    idempotency_key: str | None,
    elapsed_ms: int,
) -> ToolCallLog:
    input_summary = _summary_json(
        {
            "tool_name": action.tool_name,
            "arguments": action.arguments,
            "channel": channel,
            "country_code": case_context.country_code,
            "case_context": {
                "safe_tracking_reference": case_context.safe_tracking_reference,
                "tracking_number_hash_present": bool(case_context.tracking_number_hash),
                "contact_methods_count": len(case_context.contact_methods),
                "missing_info": list(case_context.missing_info),
            },
        }
    )
    output_summary = _summary_json(
        {
            "status": result.status,
            "ok": result.ok,
            "error_code": result.error_code,
            "summary": result.summary,
        }
    )
    row = ToolCallLog(
        tool_name=action.tool_name,
        provider="nexus_osr",
        tool_type="controlled_action",
        conversation_id=str(
            getattr(conversation, "public_id", None)
            or case_context.conversation_id
            or ""
        )[:160]
        or None,
        webchat_conversation_id=(
            getattr(conversation, "id", None)
            or (
                int(case_context.conversation_id)
                if _numeric(case_context.conversation_id)
                else None
            )
        ),
        ticket_id=(
            getattr(ticket, "id", None)
            or (int(case_context.ticket_id) if _numeric(case_context.ticket_id) else None)
        ),
        actor_type="ai_runtime_proposal",
        actor_id=None,
        request_id=idempotency_key,
        input_hash=_sha256(input_summary),
        input_summary=input_summary,
        output_hash=_sha256(output_summary),
        output_summary=output_summary,
        status=result.status,
        error_code=result.error_code,
        error_message=(
            redact_case_text(result.error_message, limit=500)
            if result.error_message
            else None
        ),
        elapsed_ms=elapsed_ms,
        redaction_applied=True,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _audit_tool_decision(
    db: Session,
    *,
    action: RuntimeToolAction,
    result: ActionExecutionResult,
    case_context: CaseContext,
    tenant_id: str,
    channel: str | None,
    country_code: str | None,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
) -> None:
    violation = (
        None
        if result.ok
        else RuntimeDecisionViolation(
            code=result.error_code or "tool_execution_blocked",
            message=result.error_message or result.status,
            severity="high" if result.status == "blocked" else "medium",
        )
    )
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.TOOL_ACTION_RESULT,
        next_action=RuntimeAction.CALL_TOOL,
        customer_reply=None,
        risk_level=str(action.arguments.get("risk_level") or "medium"),
        tool_actions=[
            RuntimeToolAction(
                tool_name=action.tool_name,
                arguments=_safe_tool_arguments(action.arguments),
                requires_confirmation=action.requires_confirmation,
                executed=result.ok and result.status == "executed",
                result_source_id=_result_source_id(result),
            )
        ],
        audit_reasons=[result.status, result.error_code or "ok"],
    )
    audit_runtime_decision(
        db,
        decision=decision,
        evaluation=RuntimeDecisionEvaluation(
            allowed=result.ok,
            violations=[violation] if violation else [],
        ),
        case_context=case_context,
        tenant_id=tenant_id,
        channel=channel,
        country_code=country_code,
        conversation_id=getattr(conversation, "id", None),
        ticket_id=(
            getattr(ticket, "id", None)
            or (int(case_context.ticket_id) if _numeric(case_context.ticket_id) else None)
        ),
    )


def _existing_executed_log(
    db: Session,
    *,
    action: RuntimeToolAction,
    idempotency_key: str | None,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
) -> ToolCallLog | None:
    if not idempotency_key:
        return None
    query = db.query(ToolCallLog).filter(
        ToolCallLog.tool_name == action.tool_name,
        ToolCallLog.request_id == idempotency_key,
        ToolCallLog.status == "executed",
    )
    if conversation is not None:
        # Conversation remains the stable idempotency scope when ticket.create
        # transitions the case from ticketless to ticket-backed.
        query = query.filter(ToolCallLog.webchat_conversation_id == conversation.id)
    elif ticket is not None:
        query = query.filter(ToolCallLog.ticket_id == ticket.id)
    return query.order_by(ToolCallLog.id.desc()).first()


def _tool_call_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, AIDecisionToolCall):
        return item.model_dump(exclude_none=True)
    if hasattr(item, "model_dump"):
        dumped = item.model_dump(exclude_none=True)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return dict(item) if isinstance(item, dict) else {}


def _idempotency_key_for_action(
    action: RuntimeToolAction,
    *,
    case_context: CaseContext,
    tenant_id: str,
    channel: str | None,
    country_code: str | None,
) -> str | None:
    contract = get_tool_contract(action.tool_name)
    if contract is None or not contract.is_write_tool:
        # Reads execute against current state and return a fresh Observation.
        return None
    conversation_scope = str(case_context.conversation_id or "")[:160]
    ticket_scope = (
        ""
        if conversation_scope
        else str(case_context.ticket_id or "")[:160]
    )
    canonical = json.dumps(
        {
            "tenant_id": str(tenant_id or "default")[:120],
            "channel": str(channel or "")[:80],
            "country_code": str(country_code or "")[:16],
            "conversation_id": conversation_scope,
            "ticket_id": ticket_scope,
            "tool_name": action.tool_name,
            "arguments": action.arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256(canonical)


def _bounded_execution_arguments(
    value: dict[str, Any],
    *,
    depth: int = 0,
) -> dict[str, Any]:
    if depth >= 5:
        return {"truncated": "[truncated]"}
    output: dict[str, Any] = {}
    for raw_key, item in list((value or {}).items())[:80]:
        key = str(raw_key)[:80]
        lowered = key.lower()
        if any(
            token in lowered
            for token in (
                "token",
                "secret",
                "password",
                "authorization",
                "api_key",
            )
        ):
            continue
        if lowered in {
            "raw",
            "raw_payload",
            "provider_payload",
            "request",
            "response",
        }:
            continue
        output[key] = _bounded_execution_value(item, depth=depth + 1)
    return output


def _bounded_execution_value(value: Any, *, depth: int) -> Any:
    if depth >= 5:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, dict):
        return _bounded_execution_arguments(value, depth=depth)
    if isinstance(value, (list, tuple)):
        return [
            _bounded_execution_value(item, depth=depth + 1)
            for item in list(value)[:50]
        ]
    return str(value)[:1000]


def _safe_tool_arguments(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key)[:80]: _safe_value(str(key), item)
        for key, item in (value or {}).items()
    }


def _safe_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(
        token in lowered
        for token in ("token", "secret", "password", "authorization", "api_key")
    ):
        return "[redacted_secret]"
    if any(token in lowered for token in ("tracking", "waybill")):
        return redact_case_text(value, limit=120) or "[redacted_tracking]"
    if any(token in lowered for token in ("phone", "caller", "contact", "email")):
        return redact_case_text(value, limit=120) or "[redacted_contact]"
    if any(token in lowered for token in ("address", "recipient")):
        return "[redacted_address]"
    if lowered in {
        "raw",
        "raw_payload",
        "payload",
        "request",
        "response",
        "body",
        "message",
        "customer_message",
    }:
        return "[redacted_payload]"
    if isinstance(value, dict):
        return {
            str(child_key)[:80]: _safe_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(key, item) for item in value[:20]]
    if isinstance(value, str):
        return redact_case_text(value, limit=500)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact_case_text(value, limit=500)


def _optional_arg(
    arguments: dict[str, Any],
    key: str,
    limit: int,
) -> str | None:
    value = arguments.get(key)
    return redact_case_text(value, limit=limit) if value is not None else None


def _priority_arg(arguments: dict[str, Any]) -> TicketPriority:
    value = str(arguments.get("priority") or "medium").lower()
    try:
        return TicketPriority(value)
    except ValueError:
        return TicketPriority.medium


def _source_channel(channel: str | None) -> SourceChannel:
    value = str(channel or "webchat").lower()
    if value in {"webchat", "web_chat"}:
        return SourceChannel.web_chat
    try:
        return SourceChannel(value)
    except ValueError:
        return SourceChannel.web_chat


def _summary_json(value: dict[str, Any]) -> str:
    return json.dumps(
        _safe_value("summary", value),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )[:4000]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _numeric(value: Any) -> bool:
    return value is not None and str(value).isdigit()


def _result_source_id(result: ActionExecutionResult) -> str | None:
    if result.summary.get("ticket_id"):
        return f"ticket:{result.summary['ticket_id']}"
    if result.summary.get("handoff_request_id"):
        return f"handoff:{result.summary['handoff_request_id']}"
    if result.summary.get("event_id"):
        return f"event:{result.summary['event_id']}"
    return None
