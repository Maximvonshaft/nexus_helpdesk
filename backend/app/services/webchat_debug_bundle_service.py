from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import Ticket, TicketEvent
from ..models_webchat_debug import WebchatAIDebugRun, WebchatAIEvalCase, WebchatAITestFinding
from ..tool_models import ToolCallLog
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage
from .nexus_osr.admin_views import build_osr_debug_snapshot

DEBUG_BUNDLE_SCHEMA = "nexus.debug_bundle.v1"
DEBUG_EVENT_SCHEMA = "nexus.ai_debug.v1"
_ALLOWED_FINDING_TYPES = {
    "irrelevant_answer",
    "answered_live_tracking_without_tool_fact",
    "used_kb_for_live_tracking",
    "used_previous_ai_reply_as_fact",
    "used_customer_claim_as_fact",
    "tool_fact_ignored",
    "should_handoff_but_did_not",
    "should_clarify_but_did_not",
    "safety_should_block",
    "safety_false_block",
    "knowledge_miss",
    "tool_error",
    "other",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|credential|password|secret|api[_-]?key|cookie|session|prompt|system|developer|"
    r"provider_(?:payload|request|response|body|group_id)|destination_group_id|fallback_group_id|"
    r"tool_(?:args|arguments|result|results|payload)|(?:^|_)(?:arguments|credentials)(?:$|_)|"
    r"tracking_number|phone|email|customer_reply|raw(?:_|$)|payload)",
    re.I,
)
_RAW_TRACKING_RE = re.compile(
    r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b",
    re.I,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_SECRET_VALUE_RE = re.compile(
    r"(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|\b(?:password|secret|api[_-]?key|credential)\s*[:=]\s*\S+)",
    re.I,
)
_SAFE_TRACKING_KEYS = {
    "suffix",
    "tracking_number_hash",
    "tracking_number_hash_present",
    "sha256_prefix",
    "request_id",
    "background_job_id",
    "item_key",
    "country_scope",
}


def _loads_json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, (dict, list)) else {}


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _sha_prefix(value: Any, *, length: int = 16) -> str:
    return hashlib.sha256(_dumps_json(value).encode("utf-8", errors="ignore")).hexdigest()[:length]


def _redaction_marker(value: Any) -> dict[str, Any]:
    return {
        "redacted": True,
        "present": value not in (None, ""),
        "sha256_prefix": _sha_prefix(value),
    }


def _redact_text(value: Any, *, limit: int = 240, redact_tracking: bool = True) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    text = _SECRET_VALUE_RE.sub("[redacted_secret]", text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    if redact_tracking:
        text = _RAW_TRACKING_RE.sub("[redacted_tracking]", text)
    return text[:limit]


def _safe_str(value: Any, *, limit: int = 160) -> str | None:
    return _redact_text(value, limit=limit)


def _sanitize_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth > 4:
        return {"redacted": True, "type": type(value).__name__, "sha256_prefix": _sha_prefix(value)}
    if _SENSITIVE_KEY_RE.search(key):
        return _redaction_marker(value)
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:60]:
            item_key = str(raw_key)
            safe[item_key] = _redaction_marker(item) if _SENSITIVE_KEY_RE.search(item_key) else _sanitize_value(item, key=item_key, depth=depth + 1)
        return safe
    if isinstance(value, list):
        return [_sanitize_value(item, key=key, depth=depth + 1) for item in value[:30]]
    if isinstance(value, str):
        text = " ".join(value.strip().split())
        if _SECRET_VALUE_RE.search(text) or _EMAIL_RE.search(text) or _PHONE_RE.search(text):
            return _redact_text(text, limit=240, redact_tracking=True)
        if _RAW_TRACKING_RE.search(text) and key.lower() not in _SAFE_TRACKING_KEYS:
            return {"redacted": True, "tracking_like": True, "sha256_prefix": _sha_prefix(text)}
        if len(text) > 240:
            return {"redacted": True, "length": len(text), "sha256_prefix": _sha_prefix(text)}
        return text
    return {"type": type(value).__name__, "sha256_prefix": _sha_prefix(value)}


def _privacy_report(bundle: dict[str, Any]) -> dict[str, bool]:
    serialized = _dumps_json(bundle)
    lower = serialized.lower()
    return {
        "raw_customer_text_included": False,
        "raw_tracking_number_included": bool(_RAW_TRACKING_RE.search(serialized.replace("tracking_number_hash", ""))),
        "raw_phone_included": bool(_PHONE_RE.search(serialized)),
        "raw_email_included": bool(_EMAIL_RE.search(serialized)),
        "token_included": "bearer " in lower or "visitor_token" in lower,
        "prompt_included": "system prompt" in lower or "developer prompt" in lower,
        "secret_included": bool(_SECRET_VALUE_RE.search(serialized)) or "secret_key" in lower or "api_key" in lower,
        "provider_group_id_included": "@g.us" in lower or "destination_group_id\"" in lower or "fallback_group_id\"" in lower,
        "raw_tool_payload_included": False,
    }


def _event_payload(row: WebchatEvent) -> dict[str, Any]:
    parsed = _loads_json(row.payload_json)
    return parsed if isinstance(parsed, dict) else {}


def _event_belongs_to_turn(row: WebchatEvent, turn: WebchatAITurn) -> bool:
    payload = _event_payload(row)
    if payload.get("ai_turn_id") == turn.id:
        return True
    if turn.reply_message_id and payload.get("message_id") == turn.reply_message_id:
        return True
    if turn.trigger_message_id and payload.get("trigger_message_id") == turn.trigger_message_id:
        return True
    return False


def _runtime_trace(turn: WebchatAITurn, reply_message: WebchatMessage | None) -> dict[str, Any]:
    trace = _loads_json(turn.runtime_trace_json)
    if isinstance(trace, dict) and trace:
        sanitized = _sanitize_value(trace)
        return sanitized if isinstance(sanitized, dict) else {}
    metadata = _loads_json(reply_message.metadata_json if reply_message else None)
    if isinstance(metadata, dict) and isinstance(metadata.get("runtime_trace"), dict):
        sanitized = _sanitize_value(metadata.get("runtime_trace"))
        return sanitized if isinstance(sanitized, dict) else {}
    return {}


def _message_metadata(reply_message: WebchatMessage | None) -> dict[str, Any]:
    metadata = _loads_json(reply_message.metadata_json if reply_message else None)
    return metadata if isinstance(metadata, dict) else {}


def _kb_hits_count(rag_trace: Any) -> int:
    if not isinstance(rag_trace, dict):
        return 0
    for key in ("kb_hits_count", "hit_count", "total", "total_matches"):
        value = rag_trace.get(key)
        if isinstance(value, int):
            return max(0, value)
    top_hits = rag_trace.get("top_hits")
    if isinstance(top_hits, list):
        return len(top_hits)
    return 0


def _top_knowledge_hits(rag_trace: Any) -> list[dict[str, Any]]:
    if not isinstance(rag_trace, dict):
        return []
    top_hits = rag_trace.get("top_hits")
    if not isinstance(top_hits, list):
        return []
    hits: list[dict[str, Any]] = []
    for item in top_hits[:5]:
        if not isinstance(item, dict):
            continue
        hits.append({
            "item_key": _safe_str(item.get("item_key") or item.get("key")),
            "title": _safe_str(item.get("title")),
            "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
            "country_scope": _safe_str(item.get("country_scope")),
            "source": _safe_str(item.get("source")),
        })
    return hits


def _tool_call_rows(db: Session, turn: WebchatAITurn) -> list[ToolCallLog]:
    start = (turn.created_at or utc_now()) - timedelta(minutes=5)
    end = (turn.completed_at or turn.updated_at or utc_now()) + timedelta(minutes=5)
    return (
        db.query(ToolCallLog)
        .filter(
            or_(
                ToolCallLog.ai_turn_id == turn.id,
                and_(
                    ToolCallLog.ticket_id == turn.ticket_id,
                    ToolCallLog.webchat_conversation_id == turn.conversation_id,
                    ToolCallLog.created_at >= start,
                    ToolCallLog.created_at <= end,
                ),
            )
        )
        .order_by(ToolCallLog.created_at.asc(), ToolCallLog.id.asc())
        .limit(50)
        .all()
    )


def _tool_call_out(row: ToolCallLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "tool_name": row.tool_name,
        "provider": row.provider,
        "tool_type": row.tool_type,
        "status": row.status,
        "error_code": row.error_code,
        "error_message": _safe_str(row.error_message, limit=240),
        "elapsed_ms": row.elapsed_ms,
        "timeout_ms": row.timeout_ms,
        "redaction_applied": bool(row.redaction_applied),
        "background_job_id": row.background_job_id,
        "request_id": row.request_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "input_hash_present": bool(row.input_hash),
        "output_hash_present": bool(row.output_hash),
        "input_summary": _sanitize_value(_loads_json(row.input_summary) or row.input_summary, key="input_summary"),
        "output_summary": _sanitize_value(_loads_json(row.output_summary) or row.output_summary, key="output_summary"),
    }


def _event_out(row: WebchatEvent) -> dict[str, Any]:
    payload = _event_payload(row)
    return {
        "event_id": row.id,
        "event_type": row.event_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "phase": payload.get("phase") or row.event_type,
        "status": payload.get("status"),
        "payload": _sanitize_value(payload),
    }


def _safe_request_id(turn: WebchatAITurn, conversation: WebchatConversation) -> str:
    base_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
    return f"webchat-ai-job-{conversation.public_id}-{base_message_id}"


def build_ai_debug_bundle(db: Session, *, turn: WebchatAITurn) -> tuple[dict[str, Any], WebchatAIDebugRun]:
    conversation = db.get(WebchatConversation, turn.conversation_id)
    ticket = db.get(Ticket, turn.ticket_id)
    if conversation is None or ticket is None:
        raise ValueError("webchat_debug_context_missing")
    visitor_message = db.get(WebchatMessage, turn.latest_visitor_message_id or turn.trigger_message_id)
    reply_message = db.get(WebchatMessage, turn.reply_message_id) if turn.reply_message_id else None
    metadata = _message_metadata(reply_message)
    runtime_trace = _runtime_trace(turn, reply_message)
    rag_trace = metadata.get("rag_trace") if isinstance(metadata.get("rag_trace"), dict) else None
    trace_fields = runtime_trace.get("runtime_trace_context_fields") if isinstance(runtime_trace.get("runtime_trace_context_fields"), dict) else {}
    events = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == turn.conversation_id, WebchatEvent.ticket_id == turn.ticket_id)
        .order_by(WebchatEvent.id.asc())
        .limit(300)
        .all()
    )
    turn_events = [row for row in events if _event_belongs_to_turn(row, turn)]
    tool_rows = _tool_call_rows(db, turn)
    tool_calls = [_tool_call_out(row) for row in tool_rows]
    speedaf_tools = [row for row in tool_rows if str(row.tool_name or "").startswith("speedaf.")]
    fact_evidence = bool(metadata.get("fact_evidence_present") or trace_fields.get("tracking_fact_evidence_present"))
    tool_fact_present = fact_evidence or any(row.status == "success" and row.tool_type in {"read_only", "read"} for row in speedaf_tools)
    tracking_intent = bool(trace_fields.get("tracking_intent_detected") or speedaf_tools or metadata.get("tracking_number_hash"))
    live_tracking_allowed = bool(trace_fields.get("live_tracking_answer_allowed") if "live_tracking_answer_allowed" in trace_fields else fact_evidence)
    kb_hits = _kb_hits_count(rag_trace)
    customer_visible_created = bool(turn.reply_message_id and reply_message is not None)
    safety_status = _safe_str(metadata.get("decision_level") or metadata.get("safety_status"))
    request_id = _safe_request_id(turn, conversation)
    evidence = {
        "tracking_intent_detected": tracking_intent,
        "tool_facts_present": tool_fact_present,
        "tracking_fact_evidence_present": fact_evidence,
        "kb_hits_count": kb_hits,
        "recent_context_count": int(trace_fields.get("structured_recent_context_count") or 0),
        "prior_ai_messages_count": int(trace_fields.get("prior_ai_messages_count") or 0),
        "customer_claim_count": int(trace_fields.get("customer_claim_count") or 0),
        "memory_system": _safe_str(metadata.get("memory_system") or trace_fields.get("memory_system") or "not_enabled"),
        "support_memory_ledger_used_by_runtime": bool(trace_fields.get("support_memory_ledger_used_by_runtime")),
    }
    policy = {
        "previous_ai_replies_are_not_facts": True,
        "customer_messages_are_claims_not_verified_facts": True,
        "tracking_status_requires_tool_fact": True,
        "kb_cannot_answer_live_tracking_status": True,
        "tool_result_overrides_kb": True,
        "live_tracking_answer_allowed": live_tracking_allowed,
        "allowed_reply_types": ["answer"] if live_tracking_allowed else ["clarifying_question", "handoff_notice", "null_reply"],
    }
    osr_snapshot = build_osr_debug_snapshot(
        db,
        tenant_id=conversation.tenant_key or "default",
        conversation_id=turn.conversation_id,
        ticket_id=turn.ticket_id,
    )
    bundle = {
        "schema": DEBUG_BUNDLE_SCHEMA,
        "conversation_id": turn.conversation_id,
        "ticket_id": turn.ticket_id,
        "ai_turn_id": turn.id,
        "visitor_message_id": turn.latest_visitor_message_id or turn.trigger_message_id,
        "reply_message_id": turn.reply_message_id,
        "request_id": request_id,
        "summary": {
            "status": turn.status,
            "status_reason": _safe_str(turn.status_reason, limit=240),
            "intent": _safe_str(trace_fields.get("ai_decision_intent") or runtime_trace.get("ai_decision_intent")),
            "reply_type": _safe_str(metadata.get("runtime_reply_type") or runtime_trace.get("reply_type")),
            "reply_source": _safe_str(turn.reply_source or metadata.get("reply_source")),
            "provider_status": _safe_str(metadata.get("provider_status")),
            "channel": _safe_str(conversation.channel_key),
            "customer_visible_message_created": customer_visible_created,
            "completed_at": turn.completed_at.isoformat() if turn.completed_at else None,
        },
        "evidence": evidence,
        "policy": policy,
        "osr": osr_snapshot,
        "tool_calls": tool_calls,
        "knowledge": {
            "retrieval": _safe_str((rag_trace or {}).get("retrieval") if isinstance(rag_trace, dict) else None),
            "kb_hits_count": kb_hits,
            "top_hits": _top_knowledge_hits(rag_trace),
        },
        "safety": {
            "safety_status": safety_status or "unknown",
            "fact_gate_reason": _safe_str(turn.fact_gate_reason or metadata.get("fact_gate_reason"), limit=240),
            "unsupported_claims": runtime_trace.get("unsupported_claims") if isinstance(runtime_trace.get("unsupported_claims"), int) else 0,
        },
        "visible_message": {
            "created": customer_visible_created,
            "origin": "provider_runtime" if customer_visible_created else None,
            "channel": _safe_str(metadata.get("reply_channel") or conversation.channel_key),
            "provider_status": _safe_str(metadata.get("provider_status")),
        },
        "timeline": [_event_out(row) for row in turn_events[-80:]],
        "privacy": {},
    }
    bundle["privacy"] = _privacy_report(bundle)
    run = db.query(WebchatAIDebugRun).filter(WebchatAIDebugRun.ai_turn_id == turn.id).first()
    if run is None:
        run = WebchatAIDebugRun(
            conversation_id=turn.conversation_id,
            ticket_id=turn.ticket_id,
            ai_turn_id=turn.id,
            debug_bundle_json="{}",
            privacy_report_json="{}",
        )
        db.add(run)
        db.flush()
    run.visitor_message_id = turn.latest_visitor_message_id or turn.trigger_message_id
    run.reply_message_id = turn.reply_message_id
    run.request_id = request_id
    run.channel = conversation.channel_key
    run.status = turn.status
    run.status_reason = turn.status_reason
    run.intent = bundle["summary"].get("intent")
    run.reply_type = bundle["summary"].get("reply_type")
    run.reply_source = bundle["summary"].get("reply_source")
    run.provider_status = bundle["summary"].get("provider_status")
    run.tracking_intent_detected = tracking_intent
    run.tracking_fact_evidence_present = fact_evidence
    run.tool_facts_present = tool_fact_present
    run.live_tracking_answer_allowed = live_tracking_allowed
    run.kb_hits_count = kb_hits
    run.tool_call_count = len(tool_calls)
    run.runtime_event_count = len(turn_events)
    run.prior_ai_messages_count = int(evidence["prior_ai_messages_count"])
    run.customer_claim_count = int(evidence["customer_claim_count"])
    run.memory_system = str(evidence["memory_system"] or "unknown")
    run.support_memory_ledger_used_by_runtime = bool(evidence["support_memory_ledger_used_by_runtime"])
    run.safety_status = bundle["safety"].get("safety_status")
    run.fact_gate_reason = bundle["safety"].get("fact_gate_reason")
    run.customer_visible_message_created = customer_visible_created
    run.debug_bundle_json = _dumps_json(bundle)
    run.privacy_report_json = _dumps_json(bundle["privacy"])
    run.completed_at = turn.completed_at
    run.updated_at = utc_now()
    db.flush()
    bundle["debug_run_id"] = run.id
    run.debug_bundle_json = _dumps_json(bundle)
    db.flush()
    return bundle, run


def create_test_finding(
    db: Session,
    *,
    run: WebchatAIDebugRun,
    current_user_id: int | None,
    finding_type: str,
    severity: str = "medium",
    tester_note: str | None = None,
    expected_behavior: str | None = None,
    actual_behavior: str | None = None,
) -> WebchatAITestFinding:
    if finding_type not in _ALLOWED_FINDING_TYPES:
        finding_type = "other"
    severity = severity if severity in {"low", "medium", "high", "critical"} else "medium"
    row = WebchatAITestFinding(
        debug_run_id=run.id,
        ai_turn_id=run.ai_turn_id,
        conversation_id=run.conversation_id,
        ticket_id=run.ticket_id,
        finding_type=finding_type,
        severity=severity,
        tester_note=(tester_note or "")[:2000] or None,
        expected_behavior=(expected_behavior or "")[:2000] or None,
        actual_behavior=(actual_behavior or "")[:2000] or None,
        bundle_snapshot_json=run.debug_bundle_json,
        created_by=current_user_id,
    )
    db.add(row)
    db.flush()
    return row


def create_eval_case_from_finding(
    db: Session,
    *,
    finding: WebchatAITestFinding,
    current_user_id: int | None,
) -> WebchatAIEvalCase:
    run = db.get(WebchatAIDebugRun, finding.debug_run_id)
    if run is None:
        raise ValueError("debug_run_not_found")
    bundle = _loads_json(run.debug_bundle_json)
    summary = bundle.get("summary") if isinstance(bundle, dict) else {}
    evidence = bundle.get("evidence") if isinstance(bundle, dict) else {}
    policy = bundle.get("policy") if isinstance(bundle, dict) else {}
    base_key = f"qa.{finding.finding_type}.turn.{finding.ai_turn_id}"
    case_key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", base_key).strip("-").lower()[:200]
    existing = db.query(WebchatAIEvalCase).filter(WebchatAIEvalCase.case_key == case_key).first()
    if existing is not None:
        return existing
    row = WebchatAIEvalCase(
        case_key=case_key,
        source_debug_run_id=run.id,
        source_finding_id=finding.id,
        scenario=finding.tester_note or finding.finding_type,
        intent=summary.get("intent"),
        channel=summary.get("channel"),
        language=None,
        input_redacted_summary=f"ai_turn_id={finding.ai_turn_id}; visitor_message_id={run.visitor_message_id}",
        expected_policy_json=_dumps_json(policy),
        expected_reply_type=summary.get("reply_type"),
        required_evidence_json=_dumps_json(evidence),
        forbidden_sources_json=_dumps_json(["previous_ai_reply", "customer_claim", "kb_for_live_tracking"]),
        created_by=current_user_id,
    )
    db.add(row)
    db.flush()
    return row
