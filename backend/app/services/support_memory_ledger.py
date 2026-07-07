from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Ticket, TicketEvent, TicketOutboundMessage, User
from ..services.permissions import ensure_ticket_visible
from ..services.tracking_fact_schema import hash_tracking_number
from ..tool_models import ToolCallLog
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatHandoffRequest, WebchatMessage
from .webchat_ai_turn_service import ai_snapshot, safe_ai_turn_runtime_trace
from .webchat_handoff_service import serialize_handoff_request

SPEEDAF_EVIDENCE_MARKERS = ("speedaf", "tracking_fact", "waybill", "work_order")
MAX_TIMELINE_ITEMS = 40
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
WAYBILL_RE = re.compile(r"\b[A-Z]{2}\d{8,18}\b", re.IGNORECASE)


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _clip(value: Any, limit: int = 240) -> str | None:
    cleaned = " ".join(_redact_free_text(value).strip().split())
    return cleaned[:limit] if cleaned else None


def _redact_free_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = EMAIL_RE.sub("[redacted_email]", text)
    text = WAYBILL_RE.sub(lambda match: f"parcel ending {match.group(0)[-6:]}", text)
    text = PHONE_RE.sub("[redacted_phone]", text)
    return text


def _split_fields(value: Any) -> list[str]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return []
    parts = cleaned.replace("；", ";").replace("，", ",").replace("\n", ",").split(",")
    return [part.strip()[:120] for part in parts if part.strip()][:12]


SAFE_TRACKING_REFERENCE_RE = re.compile(
    r"(?:parcel\s+ending|tracking\s+(?:number\s+)?ending|运单尾号|单号尾号|尾号)\s*[:：#-]?\s*([A-Z0-9]{4,8})",
    re.IGNORECASE,
)


def _safe_tracking_suffix_from_messages(messages: list[WebchatMessage]) -> str | None:
    for row in messages:
        text = str(getattr(row, "body_text", None) or getattr(row, "body", None) or "")
        match = SAFE_TRACKING_REFERENCE_RE.search(text)
        if match:
            return match.group(1).upper()[-6:]
    return None


def _safe_tracking_from_evidence(evidence: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    fallback_hash: str | None = None
    for item in evidence:
        summary = item.get("summary") if isinstance(item, dict) else None
        if not isinstance(summary, dict):
            continue
        tracking_hash = summary.get("tracking_number_hash")
        if isinstance(tracking_hash, str) and tracking_hash.strip() and fallback_hash is None:
            fallback_hash = _clip(tracking_hash, 120)
        nested_fact = summary.get("tracking_fact")
        if isinstance(nested_fact, dict):
            nested_hash = nested_fact.get("tracking_number_hash")
            if isinstance(nested_hash, str) and nested_hash.strip() and fallback_hash is None:
                fallback_hash = _clip(nested_hash, 120)
            nested_suffix = nested_fact.get("tracking_reference_suffix") or nested_fact.get("waybill_suffix")
            if isinstance(nested_suffix, str) and nested_suffix.strip():
                return nested_suffix.strip().upper()[-6:], _clip(nested_fact.get("tracking_number_hash") or summary.get("tracking_number_hash"), 120) or fallback_hash
            nested_safe_reference = nested_fact.get("safe_tracking_reference")
            if isinstance(nested_safe_reference, str):
                match = SAFE_TRACKING_REFERENCE_RE.search(nested_safe_reference)
                if match:
                    return match.group(1).upper()[-6:], _clip(nested_fact.get("tracking_number_hash") or summary.get("tracking_number_hash"), 120) or fallback_hash
        suffix = summary.get("tracking_reference_suffix") or summary.get("waybill_suffix")
        if isinstance(suffix, str) and suffix.strip():
            return suffix.strip().upper()[-6:], _clip(summary.get("tracking_number_hash") or summary.get("waybill_hash"), 120) or fallback_hash
        safe_reference = summary.get("safe_tracking_reference")
        if isinstance(safe_reference, str):
            match = SAFE_TRACKING_REFERENCE_RE.search(safe_reference)
            if match:
                return match.group(1).upper()[-6:], _clip(summary.get("tracking_number_hash") or summary.get("waybill_hash"), 120) or fallback_hash
        candidates = summary.get("safe_candidates")
        if isinstance(candidates, list) and candidates:
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            candidate_suffix = first.get("waybill_suffix")
            if isinstance(candidate_suffix, str) and candidate_suffix.strip():
                return candidate_suffix.strip().upper()[-6:], _clip(first.get("waybill_hash"), 120) or fallback_hash
    return None, fallback_hash


def _tracking_summary(ticket: Ticket, conversation: WebchatConversation, *, messages: list[WebchatMessage] | None = None, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    tracking = (
        getattr(ticket, "tracking_number", None)
        or getattr(conversation, "last_tracking_number", None)
        or ""
    ).strip().upper()
    evidence_suffix, evidence_hash = _safe_tracking_from_evidence(evidence or [])
    message_suffix = _safe_tracking_suffix_from_messages(messages or [])
    suffix = tracking[-6:] if tracking else None
    safe_suffix = suffix or evidence_suffix or message_suffix
    safe_hash = hash_tracking_number(tracking) if tracking else evidence_hash
    source = "ticket.tracking_number" if getattr(ticket, "tracking_number", None) else ("webchat.last_tracking_number" if getattr(conversation, "last_tracking_number", None) else None)
    if not source and evidence_suffix:
        source = "message.metadata.safe_tracking_reference"
    if not source and message_suffix:
        source = "webchat.message_safe_tracking_reference"
    if not source and evidence_hash:
        source = "message.metadata.tracking_number_hash"
    return {
        "present": bool(tracking or safe_suffix or safe_hash),
        "suffix": safe_suffix,
        "hash": safe_hash,
        "source": source,
        "raw_exposed": False,
    }


def _payload_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    safe_keys = (
        "fact_evidence_present",
        "fact_source",
        "tool_name",
        "tool_status",
        "tracking_number_hash",
        "tracking_reference_suffix",
        "safe_tracking_reference",
        "tracking_fact_failure_reason",
        "candidate_count",
        "safe_candidates",
        "status",
        "reason",
        "reason_code",
        "fallback_reason",
        "provider_status",
        "reply_source",
        "external_send",
        "safety_level",
        "handoff_request_id",
        "workOrderType",
        "job_id",
        "dedupe_key",
    )
    summary = {key: payload.get(key) for key in safe_keys if payload.get(key) not in (None, "")}
    tracking_fact = payload.get("tracking_fact")
    if isinstance(tracking_fact, dict):
        safe_tracking_fact_keys = (
            "fact_source",
            "tool_name",
            "tool_status",
            "tracking_number_hash",
            "tracking_reference_suffix",
            "safe_tracking_reference",
            "lookup_elapsed_ms",
            "status_context",
        )
        summary["tracking_fact"] = {
            key: tracking_fact.get(key)
            for key in safe_tracking_fact_keys
            if tracking_fact.get(key) not in (None, "")
        }
    return summary


def _message_metadata_evidence(row: WebchatMessage) -> dict[str, Any] | None:
    metadata = _loads_json(getattr(row, "metadata_json", None))
    if not isinstance(metadata, dict):
        return None
    joined = " ".join(str(metadata.get(key, "")) for key in ("tool_name", "fact_source", "fallback_reason", "provider_status")).lower()
    if not metadata.get("fact_evidence_present") and not any(marker in joined for marker in SPEEDAF_EVIDENCE_MARKERS):
        return None
    return {
        "kind": "message_evidence",
        "label": _clip(metadata.get("tool_name") or metadata.get("fact_source") or metadata.get("generated_by") or "message evidence", 120),
        "status": _clip(metadata.get("tool_status") or metadata.get("provider_status") or metadata.get("fallback_reason") or row.delivery_status, 120),
        "summary": _payload_summary(metadata),
        "created_at": _iso(row.created_at),
        "source_id": f"webchat_message:{row.id}",
    }


def _event_payload_evidence(row: WebchatEvent | TicketEvent) -> dict[str, Any] | None:
    payload = _loads_json(getattr(row, "payload_json", None))
    note = getattr(row, "note", None)
    event_type = _enum_value(getattr(row, "event_type", None)) or ""
    haystack = f"{event_type} {note or ''} {json.dumps(payload or {}, ensure_ascii=False, default=str)}".lower()
    if not any(marker in haystack for marker in SPEEDAF_EVIDENCE_MARKERS + ("policy", "handoff", "ai_turn", "outbound")):
        return None
    return {
        "kind": "ticket_event" if isinstance(row, TicketEvent) else "webchat_event",
        "label": event_type,
        "status": _clip((payload or {}).get("status") if isinstance(payload, dict) else None, 120),
        "summary": _payload_summary(payload) if isinstance(payload, dict) else {},
        "created_at": _iso(row.created_at),
        "source_id": f"{'ticket_event' if isinstance(row, TicketEvent) else 'webchat_event'}:{row.id}",
    }


def _tool_call_evidence(row: ToolCallLog) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "tool_type": row.tool_type,
        "provider": row.provider,
        "error_code": row.error_code,
        "elapsed_ms": row.elapsed_ms,
    }
    if row.output_summary:
        summary["output_summary"] = _clip(row.output_summary, 500)
    return {
        "kind": "tool_call",
        "label": row.tool_name,
        "status": row.status,
        "summary": {key: value for key, value in summary.items() if value not in (None, "")},
        "created_at": _iso(row.created_at),
        "source_id": f"tool_call:{row.id}",
    }


def _outbound_evidence(row: TicketOutboundMessage) -> dict[str, Any]:
    return {
        "kind": "outbound",
        "label": _enum_value(row.channel) or "outbound",
        "status": _enum_value(row.status),
        "summary": {
            "provider_status": row.provider_status,
            "failure_code": row.failure_code,
            "delivery_status": row.delivery_status,
            "sent_at": _iso(row.sent_at),
        },
        "created_at": _iso(row.created_at),
        "source_id": f"outbound:{row.id}",
    }


def _ai_turn_evidence(row: WebchatAITurn) -> dict[str, Any]:
    runtime_trace = None
    if getattr(row, "runtime_trace_json", None):
        try:
            runtime_trace = safe_ai_turn_runtime_trace(json.loads(row.runtime_trace_json))
        except (TypeError, ValueError):
            runtime_trace = None
    summary: dict[str, Any] = {
        "reply_source": row.reply_source,
        "fallback_reason": row.fallback_reason,
        "fact_gate_reason": row.fact_gate_reason,
        "bridge_elapsed_ms": row.bridge_elapsed_ms,
        "is_public_reply_allowed": row.is_public_reply_allowed,
    }
    if runtime_trace:
        summary["runtime_trace"] = runtime_trace
    return {
        "kind": "ai_turn",
        "label": f"AI turn {row.id}",
        "status": row.status,
        "summary": summary,
        "created_at": _iso(row.created_at),
        "source_id": f"ai_turn:{row.id}",
    }


def _speedaf_evidence_score(item: dict[str, Any]) -> int:
    haystack = f"{item.get('label', '')} {item.get('summary', '')}".lower()
    if not any(marker in haystack for marker in SPEEDAF_EVIDENCE_MARKERS):
        return -1
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    score = 10
    nested_fact = summary.get("tracking_fact") if isinstance(summary, dict) else None
    if isinstance(nested_fact, dict):
        score += 100
        if isinstance(nested_fact.get("status_context"), dict):
            score += 60
        if nested_fact.get("tracking_reference_suffix") or nested_fact.get("safe_tracking_reference"):
            score += 40
        if nested_fact.get("lookup_elapsed_ms") not in (None, ""):
            score += 15
        if str(nested_fact.get("tool_status") or "").lower() == "success":
            score += 20
    if summary.get("tracking_reference_suffix") or summary.get("safe_tracking_reference"):
        score += 40
    if summary.get("tracking_number_hash"):
        score += 10
    if str(summary.get("tool_status") or item.get("status") or "").lower() == "success":
        score += 20
    if item.get("kind") == "tool_call":
        score += 15
        if str(item.get("status") or "").lower() == "success":
            score += 10
    return score


def _select_speedaf_evidence(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        (index, item, _speedaf_evidence_score(item))
        for index, item in enumerate(evidence)
    ]
    candidates = [(index, item, score) for index, item, score in candidates if score >= 0]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate[2], -candidate[0]))[1]


def _build_next_actions(
    *,
    ticket: Ticket,
    conversation: WebchatConversation,
    handoff: WebchatHandoffRequest | None,
    tracking: dict[str, Any],
    latest_speedaf: dict[str, Any] | None,
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if getattr(conversation, "ai_suspended", False):
        actions.append({"key": "review_handoff", "label": "AI paused; review handoff and decide reply/resume", "tone": "warning"})
    if handoff and handoff.status in {"requested", "accepted"}:
        actions.append({"key": "handoff_active", "label": handoff.recommended_agent_action or "Handle active customer handoff", "tone": "warning"})
    if getattr(ticket, "required_action", None):
        actions.append({"key": "required_action", "label": _clip(ticket.required_action, 180) or "Complete required action", "tone": "default"})
    if getattr(ticket, "missing_fields", None):
        actions.append({"key": "collect_missing_fields", "label": "Collect missing fields before customer-facing resolution", "tone": "warning"})
    if tracking.get("present") and not latest_speedaf:
        actions.append({"key": "refresh_speedaf_evidence", "label": "Check latest Speedaf evidence before quoting parcel status", "tone": "default"})
    if not actions:
        actions.append({"key": "review_context", "label": "Review latest message and evidence before replying", "tone": "default"})
    return actions[:6]


def build_support_memory_ledger(db: Session, *, ticket_id: int, current_user: User) -> dict[str, Any]:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)

    conversation = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="webchat conversation not found for ticket")

    messages = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation.id)
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(30)
        .all()
    )
    ai_turns = (
        db.query(WebchatAITurn)
        .filter(WebchatAITurn.conversation_id == conversation.id)
        .order_by(WebchatAITurn.id.desc())
        .limit(12)
        .all()
    )
    webchat_events = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == conversation.id)
        .order_by(WebchatEvent.id.desc())
        .limit(24)
        .all()
    )
    ticket_events = (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.id.desc())
        .limit(24)
        .all()
    )
    tool_calls = (
        db.query(ToolCallLog)
        .filter(or_(ToolCallLog.ticket_id == ticket.id, ToolCallLog.webchat_conversation_id == conversation.id))
        .order_by(ToolCallLog.id.desc())
        .limit(20)
        .all()
    )
    outbound_messages = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket.id)
        .order_by(TicketOutboundMessage.id.desc())
        .limit(12)
        .all()
    )
    handoff = db.query(WebchatHandoffRequest).filter_by(id=conversation.current_handoff_request_id).first() if conversation.current_handoff_request_id else None

    evidence: list[dict[str, Any]] = []
    evidence.extend(_ai_turn_evidence(row) for row in ai_turns)
    evidence.extend(item for row in messages if (item := _message_metadata_evidence(row)) is not None)
    evidence.extend(item for row in webchat_events if (item := _event_payload_evidence(row)) is not None)
    evidence.extend(item for row in ticket_events if (item := _event_payload_evidence(row)) is not None)
    evidence.extend(_tool_call_evidence(row) for row in tool_calls)
    evidence.extend(_outbound_evidence(row) for row in outbound_messages)
    evidence.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    evidence = evidence[:MAX_TIMELINE_ITEMS]

    latest_speedaf = _select_speedaf_evidence(evidence)
    tracking = _tracking_summary(ticket, conversation, messages=messages, evidence=evidence)
    latest_ai_turn = ai_turns[0] if ai_turns else None
    ai_state = {
        **ai_snapshot(conversation),
        "last_turn": _ai_turn_evidence(latest_ai_turn) if latest_ai_turn else None,
        "last_bridge_elapsed_ms": latest_ai_turn.bridge_elapsed_ms if latest_ai_turn else None,
        "last_ai_reply_source": latest_ai_turn.reply_source if latest_ai_turn else None,
        "last_ai_status": latest_ai_turn.status if latest_ai_turn else None,
    }

    return {
        "generated_at": _iso(utc_now()),
        "source": "derived_support_memory_ledger",
        "ticket": {
            "id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "status": _enum_value(ticket.status),
            "conversation_state": _enum_value(ticket.conversation_state),
            "source_channel": _enum_value(ticket.source_channel),
            "market_code": getattr(getattr(ticket, "market", None), "code", None),
            "country_code": ticket.country_code,
        },
        "conversation": {
            "id": conversation.public_id,
            "status": conversation.status,
            "channel_key": conversation.channel_key,
            "origin": conversation.origin,
            "last_seen_at": _iso(conversation.last_seen_at),
            "updated_at": _iso(conversation.updated_at),
        },
        "current_intent": _clip(conversation.last_intent or ticket.ai_classification or ticket.case_type, 120),
        "customer_request": _clip(ticket.customer_request or ticket.last_customer_message, 240),
        "required_action": _clip(ticket.required_action, 240),
        "missing_fields": _split_fields(ticket.missing_fields),
        "tracking": tracking,
        "ai_state": ai_state,
        "handoff": serialize_handoff_request(db, handoff, current_user=current_user, conversation=conversation, ticket=ticket) if handoff else None,
        "latest_speedaf_evidence": latest_speedaf,
        "evidence_summary": {
            "messages": len(messages),
            "ai_turns": len(ai_turns),
            "webchat_events": len(webchat_events),
            "ticket_events": len(ticket_events),
            "tool_calls": len(tool_calls),
            "outbound_messages": len(outbound_messages),
        },
        "evidence_timeline": evidence,
        "next_actions": _build_next_actions(ticket=ticket, conversation=conversation, handoff=handoff, tracking=tracking, latest_speedaf=latest_speedaf),
    }
