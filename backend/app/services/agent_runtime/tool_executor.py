from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema
from sqlalchemy.orm import Session

from ..knowledge_retrieval_service import retrieve_published_chunks
from ..tracking_fact_schema import TrackingFactResult, safe_tracking_reference
from ..tracking_fact_service import lookup_tracking_fact
from ..speedaf.tracking_fact_source import lookup_speedaf_track_history_fact, lookup_speedaf_tracking_fact
from ..tool_governance import ToolPolicyBlocked, enforce_tool_policy, record_tool_call
from ..webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionToolCall
from ..webchat_ai_decision_runtime.tool_registry import ToolContract, get_tool_contract
from ..nexus_osr.case_context import CaseContext
from ..nexus_osr.tool_execution_service import (
    GovernedToolExecutionOptions,
    execute_controlled_tool_calls,
)

_SECRET_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "raw_payload",
    "provider_payload",
)
_IDENTIFIER_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9][A-Z0-9._-]{7,47}(?![A-Z0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class AgentExecutionContext:
    tenant_key: str
    channel_key: str
    session_id: str
    request_id: str
    customer_message: str
    market_id: int | None = None
    language: str | None = None
    conversation_id: int | None = None
    ticket_id: int | None = None
    customer_id: int | None = None
    country_code: str | None = None
    ai_turn_id: int | None = None
    allowed_tools: frozenset[str] = frozenset()
    granted_permissions: frozenset[str] = frozenset()
    actor_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ToolObservation:
    tool_name: str
    ok: bool
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    elapsed_ms: int = 0

    def prompt_projection(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "ok": self.ok,
            "status": self.status,
            "result": self.result,
            "error_code": self.error_code,
        }


ReadHandler = Callable[[Session, AIDecisionToolCall, AgentExecutionContext], ToolObservation]


def executable_tool_names() -> tuple[str, ...]:
    read_tools = tuple(sorted(_READ_HANDLERS))
    controlled = (
        "handoff.request.create",
        "ticket.create",
        "speedaf.workOrder.create",
        "timeline.event.create",
    )
    return tuple(dict.fromkeys((*read_tools, *controlled)))


def execute_agent_tool_calls(
    db: Session,
    *,
    calls: list[AIDecisionToolCall],
    context: AgentExecutionContext,
    allow_high_risk_writes: bool = False,
) -> list[ToolObservation]:
    observations: list[ToolObservation] = []
    for call in calls:
        started = time.monotonic()
        contract = get_tool_contract(call.tool_name)
        if contract is None:
            observations.append(_audited_error(db, call, context, "unknown_tool", started))
            continue
        if context.allowed_tools and contract.name not in context.allowed_tools:
            observations.append(_audited_error(db, call, context, "tool_not_available", started))
            continue
        if context.granted_permissions and not set(contract.required_permissions).issubset(context.granted_permissions):
            observations.append(_audited_error(db, call, context, "tool_permission_denied", started))
            continue
        schema_error = _validate_arguments(contract, call.arguments)
        if schema_error:
            observations.append(_audited_error(db, call, context, schema_error, started))
            continue
        if contract.allowed_auto_execution_mode == "disabled":
            observations.append(_audited_error(db, call, context, "tool_disabled", started))
            continue
        if contract.confirmation_required and call.requires_confirmation is not True:
            observations.append(_audited_error(db, call, context, "confirmation_required", started))
            continue
        if contract.is_write_tool and contract.risk_level == "high" and not allow_high_risk_writes:
            observations.append(_audited_error(db, call, context, "high_risk_write_tool_blocked", started))
            continue
        try:
            enforce_tool_policy(
                tool_name=contract.name,
                tool_type="read_only" if contract.is_read_tool else "write_action",
                actor_capabilities=context.actor_capabilities,
            )
        except ToolPolicyBlocked as exc:
            observations.append(_audited_error(db, call, context, exc.decision.reason_code, started))
            continue
        if contract.is_read_tool:
            handler = _READ_HANDLERS.get(contract.name)
            if handler is None:
                observations.append(_audited_error(db, call, context, "read_handler_unavailable", started))
                continue
            try:
                observation = handler(db, call, context)
            except Exception as exc:  # pragma: no cover - bounded failure observation
                observation = ToolObservation(
                    tool_name=call.tool_name,
                    ok=False,
                    status="failed",
                    error_code=f"tool_exception:{type(exc).__name__}",
                    elapsed_ms=_elapsed(started),
                )
                _audit_observation(db, call, context, observation)
            observations.append(observation)
            continue
        direct_handler = _WRITE_HANDLERS.get(contract.name)
        if direct_handler is not None:
            try:
                observations.append(direct_handler(db, call, context))
            except Exception as exc:  # pragma: no cover - bounded failure observation
                observation = ToolObservation(
                    tool_name=call.tool_name,
                    ok=False,
                    status="failed",
                    error_code=f"tool_exception:{type(exc).__name__}",
                    elapsed_ms=_elapsed(started),
                )
                _audit_observation(db, call, context, observation)
                observations.append(observation)
            continue
        observations.append(
            _execute_controlled_write(
                db,
                call=call,
                context=context,
                contract=contract,
                allow_high_risk_writes=allow_high_risk_writes,
                started=started,
            )
        )
    return observations


def _validate_arguments(contract: ToolContract, arguments: dict[str, Any]) -> str | None:
    try:
        jsonschema.validate(instance=arguments, schema=contract.input_schema or {"type": "object"})
    except jsonschema.ValidationError as exc:
        return f"invalid_arguments:{exc.validator}"
    return None


def _knowledge_search(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
) -> ToolObservation:
    started = time.monotonic()
    query = str(call.arguments.get("query") or "").strip()
    limit = max(1, min(int(call.arguments.get("limit") or 5), 8))
    retrieval = retrieve_published_chunks(
        db,
        q=query,
        tenant_id=context.tenant_key,
        market_id=context.market_id,
        channel=context.channel_key,
        audience_scope="customer",
        language=context.language,
        limit=limit,
    )
    hits = []
    for hit in retrieval.hits[:limit]:
        answer = str(hit.direct_answer or hit.text or "").strip()
        if not answer:
            continue
        hits.append(
            {
                "source_id": str(hit.item_key)[:180],
                "title": str(hit.title)[:180],
                "answer": answer[:1200],
                "answer_mode": hit.answer_mode,
            }
        )
    observation = ToolObservation(
        tool_name=call.tool_name,
        ok=bool(hits),
        status="success" if hits else "no_results",
        result={"query": query[:240], "hits": hits, "count": len(hits)},
        error_code=None if hits else "knowledge_not_found",
        elapsed_ms=_elapsed(started),
    )
    _audit_observation(db, call, context, observation)
    return observation


def _shipment_query(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
) -> ToolObservation:
    del db
    started = time.monotonic()
    tracking_number = str(call.arguments.get("tracking_number") or "").strip().upper()
    fact = lookup_tracking_fact(
        tracking_number=tracking_number,
        conversation_id=context.conversation_id,
        ticket_id=context.ticket_id,
        request_id=context.request_id,
        country_code=context.country_code,
    )
    return _tracking_observation(call.tool_name, fact, started=started)


def _shipment_history_query(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
) -> ToolObservation:
    del db
    started = time.monotonic()
    tracking_number = str(call.arguments.get("tracking_number") or "").strip().upper()
    fact = lookup_speedaf_track_history_fact(
        tracking_number=tracking_number,
        conversation_id=context.conversation_id,
        ticket_id=context.ticket_id,
        request_id=context.request_id,
    )
    return _tracking_observation(call.tool_name, fact, started=started)


def _waybill_candidates_query(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
) -> ToolObservation:
    del db
    started = time.monotonic()
    fact = lookup_speedaf_tracking_fact(
        tracking_number=None,
        caller_id=str(call.arguments.get("caller_id") or "").strip(),
        country_code=str(call.arguments.get("country_code") or context.country_code or "").strip() or None,
        conversation_id=context.conversation_id,
        ticket_id=context.ticket_id,
        request_id=context.request_id,
    )
    return _tracking_observation(call.tool_name, fact, started=started)


def _tracking_observation(tool_name: str, fact: TrackingFactResult, *, started: float) -> ToolObservation:
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
                "latest_event": fact.latest_event.to_safe_dict() if fact.latest_event else None,
                "recent_events": [event.to_safe_dict() for event in fact.events_summary[:5]],
                "status_context": _safe_value(fact.status_context),
            }
        )
    else:
        result.update(
            {
                "failure_reason": fact.failure_reason,
                "failure_summary": fact.failure_summary,
                "retryable": fact.failure_retryable,
                "needs_customer_confirmation": fact.failure_needs_customer_confirmation,
                "needs_human_review": fact.failure_needs_human_review,
                "safe_candidates": _safe_value(fact.safe_candidates[:10]),
            }
        )
    return ToolObservation(
        tool_name=tool_name,
        ok=bool(fact.fact_evidence_present),
        status="success" if fact.fact_evidence_present else str(fact.tool_status or "no_evidence"),
        result=_safe_value(result),
        error_code=None if fact.fact_evidence_present else str(fact.failure_reason or "tracking_unavailable")[:120],
        elapsed_ms=_elapsed(started),
    )


def _speedaf_work_order_create(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
) -> ToolObservation:
    from ...models import Ticket
    from ...webchat_models import WebchatConversation
    from ..background_jobs import enqueue_speedaf_work_order_create_job
    from ..speedaf.status_map import is_auto_work_order_type_allowed

    started = time.monotonic()
    if not _env_bool("WEBCHAT_AI_AUTO_WORK_ORDER_ENABLED", False):
        return _audited_error(db, call, context, "tool_disabled", started)
    if not _env_bool("SPEEDAF_WORK_ORDER_CREATE_ENABLED", False):
        return _audited_error(db, call, context, "provider_write_disabled", started)
    tracking_number = str(call.arguments.get("tracking_number") or "").strip().upper()
    work_order_type = str(call.arguments.get("work_order_type") or "").strip()
    if not is_auto_work_order_type_allowed(work_order_type):
        return _audited_error(db, call, context, "work_order_type_not_allowed", started)
    conversation = db.get(WebchatConversation, context.conversation_id) if context.conversation_id else None
    ticket = db.get(Ticket, context.ticket_id) if context.ticket_id else None
    if conversation is None or ticket is None:
        return _audited_error(db, call, context, "conversation_or_ticket_required", started)
    caller_id = _first_phone(
        getattr(conversation, "visitor_phone", None),
        getattr(ticket, "preferred_reply_contact", None),
        getattr(getattr(ticket, "customer", None), "phone", None),
    )
    if not caller_id:
        return _audited_error(db, call, context, "caller_id_required", started)
    description = str(call.arguments.get("description") or "Customer requested delivery follow-up.").strip()[:500]
    job = enqueue_speedaf_work_order_create_job(
        db,
        ticket_id=ticket.id,
        conversation_id=conversation.id,
        waybill_code=tracking_number,
        caller_id=caller_id,
        description=description,
        work_order_type=work_order_type,
    )
    observation = ToolObservation(
        tool_name=call.tool_name,
        ok=True,
        status="queued",
        result={
            "work_order_type": work_order_type,
            "reference": safe_tracking_reference(tracking_number),
            "job_id": job.id,
        },
        elapsed_ms=_elapsed(started),
    )
    _audit_observation(db, call, context, observation)
    return observation


def _first_phone(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        match = re.search(r"\+?\d[\d\s().-]{6,}\d", text)
        if match:
            cleaned = re.sub(r"[\s().-]+", "", match.group(0))
            if 8 <= len(re.sub(r"\D", "", cleaned)) <= 18:
                return cleaned[:80]
    return None


def _execute_controlled_write(
    db: Session,
    *,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
    contract: ToolContract,
    allow_high_risk_writes: bool,
    started: float,
) -> ToolObservation:
    from ...models import Customer, Ticket
    from ...webchat_models import WebchatConversation

    conversation = db.get(WebchatConversation, context.conversation_id) if context.conversation_id else None
    ticket = db.get(Ticket, context.ticket_id) if context.ticket_id else None
    customer = db.get(Customer, context.customer_id) if context.customer_id else None
    case_context = CaseContext(
        conversation_id=context.conversation_id,
        ticket_id=context.ticket_id,
        channel=context.channel_key,
        country_code=context.country_code,
    ).with_inbound_message(context.customer_message, channel=context.channel_key, country_code=context.country_code)
    tool_decision = AIDecision(
        customer_reply=None,
        intent="tool_execution",
        confidence=1.0,
        risk_level=contract.risk_level,
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[call],
    )
    results = execute_controlled_tool_calls(
        db,
        tool_calls=[call.model_dump(exclude_none=True)],
        case_context=case_context,
        channel=context.channel_key,
        country_code=context.country_code,
        tenant_id=context.tenant_key,
        conversation=conversation,
        ticket=ticket,
        customer=customer,
        ai_decision=tool_decision,
        options=GovernedToolExecutionOptions(
            allow_high_risk_write_execution=allow_high_risk_writes,
            allowed_high_risk_write_tools=frozenset({contract.name}) if allow_high_risk_writes else frozenset(),
        ),
    )
    if not results:
        return _audited_error(db, call, context, "tool_not_executed", started)
    result = results[0]
    safe_result = {
        "summary": _safe_value(result.summary or {}),
        "customer_visible_summary": str(result.customer_visible_summary or "")[:1000] or None,
    }
    return ToolObservation(
        tool_name=call.tool_name,
        ok=bool(result.ok),
        status=str(result.status or ("success" if result.ok else "failed"))[:80],
        result={key: value for key, value in safe_result.items() if value not in (None, "", [], {})},
        error_code=str(result.error_code or "")[:120] or None,
        elapsed_ms=_elapsed(started),
    )


def _audit_observation(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
    observation: ToolObservation,
) -> None:
    contract = get_tool_contract(call.tool_name)
    record_tool_call(
        tool_name=call.tool_name,
        provider="agent_runtime",
        tool_type="read_only" if contract is None or contract.is_read_tool else "write_action",
        input_payload=call.arguments,
        output_payload=observation.prompt_projection(),
        status=observation.status,
        error_code=observation.error_code,
        elapsed_ms=observation.elapsed_ms,
        conversation_id=str(context.conversation_id) if context.conversation_id is not None else None,
        webchat_conversation_id=context.conversation_id,
        ticket_id=context.ticket_id,
        ai_turn_id=context.ai_turn_id,
        actor_type="agent_runtime",
        request_id=context.request_id,
        db=db,
    )


def _audited_error(
    db: Session,
    call: AIDecisionToolCall,
    context: AgentExecutionContext,
    error_code: str,
    started: float,
) -> ToolObservation:
    observation = _error(call.tool_name, error_code, started)
    _audit_observation(db, call, context, observation)
    return observation


def _error(tool_name: str, error_code: str, started: float) -> ToolObservation:
    return ToolObservation(
        tool_name=tool_name,
        ok=False,
        status="blocked" if error_code in {
            "unknown_tool",
            "tool_not_available",
            "tool_permission_denied",
            "tool_disabled",
            "confirmation_required",
            "high_risk_write_tool_blocked",
        } else "failed",
        error_code=error_code[:160],
        elapsed_ms=_elapsed(started),
    )


def _elapsed(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        bounded = value[:2000]
        return _IDENTIFIER_RE.sub(_redact_identifier, bounded)
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            normalized = str(key).lower()
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                continue
            safe[str(key)[:100]] = _safe_value(item, depth=depth + 1)
        return safe
    try:
        return _safe_value(json.loads(json.dumps(value, default=str)), depth=depth + 1)
    except Exception:
        return type(value).__name__


def _redact_identifier(match: re.Match[str]) -> str:
    token = match.group(0)
    compact = re.sub(r"[^A-Z0-9]", "", token.upper())
    if not any(char.isdigit() for char in compact) or len(compact) < 10:
        return token
    return f"reference ending {compact[-6:]}"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_READ_HANDLERS: dict[str, ReadHandler] = {
    "knowledge.search": _knowledge_search,
    "speedaf.order.query": _shipment_query,
    "speedaf.express.track.query": _shipment_history_query,
    "speedaf.order.waybillCode.query": _waybill_candidates_query,
}

_WRITE_HANDLERS: dict[str, ReadHandler] = {
    "speedaf.workOrder.create": _speedaf_work_order_create,
}
