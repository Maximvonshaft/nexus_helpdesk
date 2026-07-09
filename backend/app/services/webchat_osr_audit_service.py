from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..models_osr import CaseContextRecord, RuntimeDecisionAuditRecord
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage
from .knowledge_retrieval_service import KnowledgeChunkHit
from .nexus_osr.persistence import save_case_context
from .nexus_osr.runtime_bridge import (
    audit_existing_webchat_runtime_decision,
    build_case_context_from_webchat,
    build_runtime_decision_from_existing_runtime,
)
from .nexus_osr.runtime_decision_contract import BusinessReplyType, RuntimeAction
from .tracking_fact_schema import TrackingFactResult
from .webchat_ai_turn_service import safe_write_webchat_event

LOGGER = logging.getLogger("nexusdesk")

_LIVE_TRACKING_REPLY_RE = re.compile(
    r"\b(out for delivery|delivered|in transit|arrived|picked up|returned|customs|signed|delivery failed|派送中|已签收|运输中|已到达|已揽收|已退回|清关|派送失败)\b",
    re.IGNORECASE,
)


def _loads_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_str(value: Any, *, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text[:limit] if text else None


def _reply_text(reply_message: WebchatMessage | None) -> str:
    return str(getattr(reply_message, "body_text", None) or getattr(reply_message, "body", None) or "") if reply_message else ""


def _reply_looks_like_live_tracking_answer(reply_message: WebchatMessage | None) -> bool:
    return bool(_LIVE_TRACKING_REPLY_RE.search(_reply_text(reply_message)))


def _reply_message_for_turn(db: Session, turn: WebchatAITurn, result: dict[str, Any] | None) -> WebchatMessage | None:
    message_id = getattr(turn, "reply_message_id", None) or (result or {}).get("message_id")
    if not message_id:
        return None
    return db.query(WebchatMessage).filter(WebchatMessage.id == int(message_id), WebchatMessage.conversation_id == turn.conversation_id).first()


def _metadata_from_reply(reply_message: WebchatMessage | None) -> dict[str, Any]:
    return _loads_json(getattr(reply_message, "metadata_json", None))


def _rag_trace_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    rag_trace = metadata.get("rag_trace")
    return rag_trace if isinstance(rag_trace, dict) else {}


def _tracking_fact_from_metadata(metadata: dict[str, Any], result: dict[str, Any] | None = None) -> TrackingFactResult | None:
    explicit = (result or {}).get("osr_tracking_fact")
    if isinstance(explicit, TrackingFactResult):
        return explicit
    fact_present = bool(metadata.get("fact_evidence_present"))
    tool_status = _safe_str(metadata.get("tool_status"))
    if not fact_present and not tool_status:
        return None
    status_context = metadata.get("status_context") if isinstance(metadata.get("status_context"), dict) else {}
    return TrackingFactResult(
        ok=fact_present,
        tracking_number=None,
        status=_safe_str(status_context.get("code") or metadata.get("status")),
        status_label=_safe_str(status_context.get("label") or metadata.get("status_label")),
        checked_at=_safe_str(metadata.get("checked_at")),
        tool_name=_safe_str(metadata.get("tool_name")) or "speedaf.order.query",
        tool_status=tool_status,
        pii_redacted=True,
        fact_evidence_present=fact_present,
        failure_reason=_safe_str(metadata.get("tracking_fact_failure_reason")),
        status_context=status_context,
        lookup_elapsed_ms=metadata.get("lookup_elapsed_ms") if isinstance(metadata.get("lookup_elapsed_ms"), int) else None,
    )


def _knowledge_hits_from_rag_trace(rag_trace: dict[str, Any]) -> list[KnowledgeChunkHit]:
    top_hits = rag_trace.get("top_hits")
    if not isinstance(top_hits, list):
        return []
    hits: list[KnowledgeChunkHit] = []
    for index, item in enumerate(top_hits[:10]):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        source_metadata = item.get("source_metadata") if isinstance(item.get("source_metadata"), dict) else {}
        for key in ("visibility", "shareability", "audience_scope", "channel", "language", "knowledge_kind"):
            if key in item and key not in metadata:
                metadata[key] = item[key]
        hits.append(KnowledgeChunkHit(
            item_id=int(item.get("item_id") or item.get("id") or 0),
            item_key=str(item.get("item_key") or item.get("key") or f"rag_hit_{index}"),
            title=str(item.get("title") or item.get("label") or "RAG hit"),
            published_version=int(item.get("published_version") or 0),
            chunk_index=int(item.get("chunk_index") or index),
            score=float(item.get("score") or 0.0),
            text="",
            metadata=metadata,
            retrieval_method=item.get("retrieval_method"),
            direct_answer=item.get("direct_answer") if isinstance(item.get("direct_answer"), str) else None,
            answer_mode=item.get("answer_mode") if isinstance(item.get("answer_mode"), str) else None,
            source_metadata=source_metadata,
        ))
    return hits


def _has_customer_visible_knowledge(hits: list[KnowledgeChunkHit]) -> bool:
    for hit in hits:
        merged = {**(hit.metadata or {}), **(hit.source_metadata or {})}
        value = str(merged.get("visibility") or merged.get("shareability") or merged.get("audience_scope") or "").lower()
        if value in {"customer", "customer_visible"}:
            return True
    return False


def _tracking_intent_present(metadata: dict[str, Any], result: dict[str, Any] | None) -> bool:
    if bool(metadata.get("fact_evidence_present")):
        return True
    runtime_trace = (result or {}).get("runtime_trace")
    trace_fields = runtime_trace.get("runtime_trace_context_fields") if isinstance(runtime_trace, dict) and isinstance(runtime_trace.get("runtime_trace_context_fields"), dict) else {}
    return bool(trace_fields.get("tracking_intent_detected") or metadata.get("tracking_number_hash") or metadata.get("safe_tracking_reference"))


def _business_reply_type(*, result: dict[str, Any] | None, metadata: dict[str, Any], tracking_fact: TrackingFactResult | None, knowledge_hits: list[KnowledgeChunkHit], reply_message: WebchatMessage | None) -> BusinessReplyType:
    status = str((result or {}).get("status") or "").lower()
    if status in {"review_required", "failed_no_public_reply", "suppressed"}:
        return BusinessReplyType.NO_ANSWER
    if bool((result or {}).get("runtime_handoff_required")) or bool(metadata.get("runtime_handoff_required")):
        return BusinessReplyType.HANDOFF_NOTICE
    if tracking_fact and tracking_fact.fact_evidence_present and tracking_fact.pii_redacted and _tracking_intent_present(metadata, result):
        return BusinessReplyType.TRACKING_STATUS_ANSWER
    if _tracking_intent_present(metadata, result) and _reply_looks_like_live_tracking_answer(reply_message):
        return BusinessReplyType.TRACKING_STATUS_ANSWER
    if _has_customer_visible_knowledge(knowledge_hits):
        return BusinessReplyType.KNOWLEDGE_ANSWER
    return BusinessReplyType.CLARIFICATION


def _next_action(reply_type: BusinessReplyType) -> RuntimeAction:
    if reply_type == BusinessReplyType.HANDOFF_NOTICE:
        return RuntimeAction.REQUEST_HANDOFF
    if reply_type == BusinessReplyType.NO_ANSWER:
        return RuntimeAction.BLOCK
    return RuntimeAction.REPLY


def _case_context_summary(row: CaseContextRecord | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "case_context_id": row.id,
        "status": row.status,
        "channel": row.channel,
        "country_code": row.country_code,
        "issue_type": row.issue_type,
        "safe_tracking_reference": row.safe_tracking_reference,
        "tracking_number_hash_present": bool(row.tracking_number_hash),
        "handoff_requested": bool(row.handoff_requested),
        "ticket_created": bool(row.ticket_created),
    }


def _audit_summary(audit: RuntimeDecisionAuditRecord, case_context: CaseContextRecord | None) -> dict[str, Any]:
    return {
        "mode": "audit_only",
        "audit_id": audit.id,
        "allowed": bool(audit.allowed),
        "business_reply_type": audit.business_reply_type,
        "next_action": audit.next_action,
        "risk_level": audit.risk_level,
        "violation_codes": [str(item.get("code")) for item in (audit.violations_json or []) if isinstance(item, dict) and item.get("code")],
        "warning_count": len(audit.warnings_json or []),
        "case_context": _case_context_summary(case_context),
    }


def _update_reply_metadata(db: Session, reply_message: WebchatMessage | None, summary: dict[str, Any]) -> None:
    if reply_message is None:
        return
    metadata = _metadata_from_reply(reply_message)
    metadata["osr_audit"] = summary
    reply_message.metadata_json = _dumps_json(metadata)
    db.flush()


def _record_osr_ticket_event(db: Session, *, ticket: Ticket, conversation: WebchatConversation, turn: WebchatAITurn, visitor_message: WebchatMessage, summary: dict[str, Any]) -> None:
    payload = {
        "event": "osr_runtime_decision_audited",
        "conversation_id": conversation.id,
        "ticket_id": ticket.id,
        "visitor_message_id": visitor_message.id,
        "ai_turn_id": turn.id,
        **summary,
    }
    db.add(TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.field_updated,
        note="OSR runtime decision audit recorded",
        payload_json=_dumps_json(payload),
    ))
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="osr.runtime_decision.audited",
        payload=payload,
    )


def audit_completed_webchat_ai_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    visitor_message: WebchatMessage,
    turn: WebchatAITurn | None,
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if turn is None:
        return None
    reply_message = _reply_message_for_turn(db, turn, result)
    metadata = _metadata_from_reply(reply_message)
    tracking_fact = _tracking_fact_from_metadata(metadata, result)
    rag_trace = _rag_trace_from_metadata(metadata)
    knowledge_hits = _knowledge_hits_from_rag_trace(rag_trace)
    case_context = build_case_context_from_webchat(
        db,
        ticket=ticket,
        conversation=conversation,
        visitor_message=visitor_message,
        tracking_fact=tracking_fact,
        issue_type="tracking" if _tracking_intent_present(metadata, result) else None,
    )
    if metadata:
        case_context = case_context.with_mcp_fact({key: value for key, value in metadata.items() if key in {
            "fact_evidence_present",
            "fact_source",
            "tool_name",
            "tool_status",
            "pii_redacted",
            "checked_at",
            "tracking_number_hash",
            "tracking_reference_suffix",
            "safe_tracking_reference",
            "lookup_elapsed_ms",
            "status_context",
            "tracking_fact_failure_reason",
        }})
        save_case_context(db, case_context, tenant_id=getattr(conversation, "tenant_key", None) or "default")
    reply_type = _business_reply_type(result=result, metadata=metadata, tracking_fact=tracking_fact, knowledge_hits=knowledge_hits, reply_message=reply_message)
    decision = build_runtime_decision_from_existing_runtime(
        business_reply_type=reply_type,
        next_action=_next_action(reply_type),
        customer_reply=_reply_text(reply_message) if reply_message else None,
        tracking_fact=tracking_fact,
        knowledge_hits=knowledge_hits,
        case_context=case_context,
        handoff_required=reply_type == BusinessReplyType.HANDOFF_NOTICE,
        language=_safe_str(metadata.get("language")),
    )
    audit = audit_existing_webchat_runtime_decision(db, ticket=ticket, conversation=conversation, decision=decision, case_context=case_context)
    row = db.query(CaseContextRecord).filter(CaseContextRecord.conversation_id == conversation.id, CaseContextRecord.ticket_id == ticket.id).order_by(CaseContextRecord.id.desc()).first()
    summary = _audit_summary(audit, row)
    _update_reply_metadata(db, reply_message, summary)
    _record_osr_ticket_event(db, ticket=ticket, conversation=conversation, turn=turn, visitor_message=visitor_message, summary=summary)
    LOGGER.info("webchat_osr_runtime_decision_audited", extra={"event_payload": {"conversation_id": conversation.id, "ticket_id": ticket.id, "ai_turn_id": turn.id, **summary}})
    return summary
