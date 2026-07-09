from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...models import Ticket
from ...webchat_models import WebchatConversation, WebchatMessage
from ..knowledge_retrieval_service import KnowledgeChunkHit
from ..tracking_fact_schema import TrackingFactResult
from .case_context import CaseContext
from .controlled_action_executor import ActionExecutionRequest, ActionExecutionResult, ControlledActionExecutor
from .persistence import audit_runtime_decision, load_case_context, save_case_context
from .runtime_decision_contract import (
    BusinessReplyType,
    EvidenceSource,
    EvidenceType,
    RuntimeAction,
    RuntimeDecision,
    RuntimeToolAction,
    evaluate_runtime_decision,
)
from .tool_execution_facade import OSRToolExecutionFacade, OSRToolExecutionFacadeResult, OSRToolExecutionMode, osr_tool_execution_mode_from_env


def build_case_context_from_webchat(
    db: Session,
    *,
    ticket: Ticket,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage | None = None,
    tracking_fact: TrackingFactResult | None = None,
    issue_type: str | None = None,
) -> CaseContext:
    """Load/update a persistent Case Context from current WebChat runtime facts."""

    existing = load_case_context(db, conversation_id=conversation.id, ticket_id=ticket.id)
    context = existing or CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel=getattr(conversation, "channel_key", None) or "webchat",
        country_code=getattr(ticket, "country_code", None),
        issue_type=issue_type or getattr(conversation, "last_intent", None) or getattr(ticket, "case_type", None),
    )
    if visitor_message is not None:
        context = context.with_inbound_message(
            getattr(visitor_message, "body_text", None) or getattr(visitor_message, "body", None) or "",
            channel=getattr(conversation, "channel_key", None) or context.channel,
            country_code=getattr(ticket, "country_code", None) or context.country_code,
        )
    if tracking_fact is not None:
        context = context.with_mcp_fact(tracking_fact.metadata_payload())
    save_case_context(db, context, tenant_id=getattr(conversation, "tenant_key", None) or "default")
    return context


def evidence_from_tracking_fact(tracking_fact: TrackingFactResult | None) -> list[EvidenceSource]:
    if tracking_fact is None:
        return []
    metadata = tracking_fact.metadata_payload()
    label = tracking_fact.tool_name or "tracking fact"
    if tracking_fact.fact_evidence_present:
        return [
            EvidenceSource(
                evidence_type=EvidenceType.MCP_CURRENT_STATUS,
                source_id=f"tracking_fact:{metadata.get('tracking_number_hash') or 'unknown'}",
                label=label,
                summary=metadata,
                verified=True,
                current_status=True,
                created_at=tracking_fact.checked_at,
            )
        ]
    if tracking_fact.events_summary or tracking_fact.latest_event:
        return [
            EvidenceSource(
                evidence_type=EvidenceType.MCP_HISTORY_ENRICHMENT,
                source_id=f"tracking_history:{metadata.get('tracking_number_hash') or 'unknown'}",
                label=label,
                summary=metadata,
                verified=bool(tracking_fact.tool_status == "success"),
                current_status=False,
                created_at=tracking_fact.checked_at,
            )
        ]
    return []


def evidence_from_knowledge_hits(hits: Iterable[KnowledgeChunkHit] | None) -> list[EvidenceSource]:
    evidence: list[EvidenceSource] = []
    for hit in hits or []:
        metadata = dict(hit.metadata or {})
        metadata.update(hit.source_metadata or {})
        customer_visible = str(metadata.get("visibility") or metadata.get("shareability") or metadata.get("audience_scope") or "").lower() in {
            "customer",
            "customer_visible",
        }
        evidence.append(EvidenceSource(
            evidence_type=EvidenceType.KNOWLEDGE_CUSTOMER_VISIBLE if customer_visible else EvidenceType.KNOWLEDGE_INTERNAL,
            source_id=f"knowledge:{hit.item_key}:{hit.published_version}:{hit.chunk_index}",
            label=hit.title,
            summary={
                "item_id": hit.item_id,
                "item_key": hit.item_key,
                "published_version": hit.published_version,
                "chunk_index": hit.chunk_index,
                "score": hit.score,
                "answer_mode": hit.answer_mode,
                "retrieval_method": hit.retrieval_method,
                "direct_answer_present": bool(hit.direct_answer),
            },
            confidence=min(1.0, max(0.0, float(hit.score or 0) / 100.0)),
            customer_visible=customer_visible,
            verified=customer_visible,
        ))
    return evidence


def build_runtime_decision_from_existing_runtime(
    *,
    business_reply_type: BusinessReplyType,
    next_action: RuntimeAction,
    customer_reply: str | None,
    tracking_fact: TrackingFactResult | None = None,
    knowledge_hits: Iterable[KnowledgeChunkHit] | None = None,
    case_context: CaseContext | None = None,
    tool_actions: list[RuntimeToolAction] | None = None,
    risk_level: str = "low",
    handoff_required: bool = False,
    ticket_required: bool = False,
    routing_required: bool = False,
    language: str | None = None,
) -> RuntimeDecision:
    evidence = []
    evidence.extend(evidence_from_tracking_fact(tracking_fact))
    evidence.extend(evidence_from_knowledge_hits(knowledge_hits))
    if case_context is not None:
        evidence.append(EvidenceSource(
            evidence_type=EvidenceType.CASE_CONTEXT,
            source_id=f"case_context:{case_context.ticket_id or case_context.conversation_id or 'unknown'}",
            label="Case Context",
            summary=case_context.as_dict(),
            verified=False,
            current_status=False,
        ))
    return RuntimeDecision(
        business_reply_type=business_reply_type,
        next_action=next_action,
        customer_reply=customer_reply,
        language=language,
        risk_level=risk_level,
        evidence_sources=evidence,
        tool_actions=tool_actions or [],
        handoff_required=handoff_required,
        ticket_required=ticket_required,
        routing_required=routing_required,
    )


def audit_existing_webchat_runtime_decision(
    db: Session,
    *,
    ticket: Ticket,
    conversation: WebchatConversation,
    decision: RuntimeDecision,
    case_context: CaseContext | None = None,
):
    evaluation = evaluate_runtime_decision(decision)
    return audit_runtime_decision(
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


def execute_runtime_decision_tool_proposals(
    db: Session,
    *,
    decision: RuntimeDecision,
    case_context: CaseContext,
    ticket: Ticket | None = None,
    conversation: WebchatConversation | None = None,
    mode: OSRToolExecutionMode | str | None = None,
) -> OSRToolExecutionFacadeResult:
    """Execute RuntimeDecision tool proposals through the governed facade.

    Missing mode resolves from `OSR_TOOL_EXECUTION_MODE`, which defaults to
    observe_only. This bridge never directly calls provider-native tool calls and
    never sends customer-visible messages.
    """

    tool_calls = [
        {
            "tool_name": action.tool_name,
            "arguments": dict(action.arguments or {}),
            "requires_confirmation": action.requires_confirmation,
            "idempotency_key": action.result_source_id,
        }
        for action in decision.tool_actions
    ]
    return OSRToolExecutionFacade(db).execute(
        tool_calls=tool_calls,
        case_context=case_context,
        channel=case_context.channel or getattr(conversation, "channel_key", None),
        country_code=case_context.country_code or getattr(ticket, "country_code", None),
        tenant_id=getattr(conversation, "tenant_key", None) or "default",
        conversation=conversation,
        ticket=ticket,
        mode=mode if mode is not None else osr_tool_execution_mode_from_env(),
    )


def execute_osr_tool_action(
    executor: ControlledActionExecutor,
    *,
    action: RuntimeToolAction,
    case_context: CaseContext,
    channel: str | None,
    country_code: str | None,
    idempotency_key: str | None = None,
) -> ActionExecutionResult:
    return executor.execute(ActionExecutionRequest(
        action=action,
        channel=channel,
        country_code=country_code,
        case_context=case_context,
        idempotency_key=idempotency_key,
    ))


def mark_ticket_created_action(decision: RuntimeDecision, *, ticket_id: int | str) -> RuntimeDecision:
    actions = list(decision.tool_actions)
    actions.append(RuntimeToolAction(
        tool_name="ticket.create",
        arguments={"ticket_id": ticket_id},
        executed=True,
        result_source_id=f"ticket:{ticket_id}",
    ))
    return replace(decision, tool_actions=actions, ticket_required=True)
