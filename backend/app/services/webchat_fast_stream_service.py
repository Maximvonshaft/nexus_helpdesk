from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

from sqlalchemy import select

from ..db import db_context
from .webchat_fast_ai_service import _clean_context
from .webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_legacy_v1_request_hash_aliases,
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from .webchat_fast_output_parser import FastReplyParseError, ParsedFastReply, assert_customer_visible_reply_is_safe
from .webchat_fast_reply_metrics import record_fast_reply_metric
from .webchat_fast_session_service import (
    FastRoutingContext,
    append_fast_ai_message,
    append_fast_system_handoff_message,
    extract_fast_business_state,
    fast_public_session_payload,
    get_or_create_fast_conversation,
    get_or_create_fast_ticket,
)
from .webchat_handoff_service import request_webchat_handoff
from .knowledge_prompt_service import summarize_rag_trace


@dataclass(frozen=True)
class StreamBeginOutcome:
    status: str
    request_hash: str
    row_id: int | None = None
    response_json: dict[str, Any] | None = None
    error_code: str | None = None


def sse_event(event: str, payload: dict[str, Any]) -> str:
    safe_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {safe_payload}\n\n"


def public_final_from_parsed(
    parsed: ParsedFastReply,
    *,
    ticket_creation_queued: bool,
    replayed: bool = False,
    evidence_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final = {
        "ok": True,
        "ai_generated": True,
        "reply_source": "provider_runtime_stream",
        "intent": parsed.intent,
        "tracking_number": parsed.tracking_number,
        "handoff_required": parsed.handoff_required,
        "handoff_reason": parsed.handoff_reason,
        "ticket_creation_queued": ticket_creation_queued,
    }
    if replayed:
        final["replayed"] = True
    if evidence_trace:
        final["evidence_trace"] = evidence_trace
    return final


def _stream_evidence_trace(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    if runtime_context:
        return summarize_rag_trace(runtime_context)
    return {
        "retrieval": "hybrid_rag_v2",
        "candidate_count": 0,
        "total_matches": 0,
        "retrieval_methods": [],
        "no_answer_reason": "runtime_context_unavailable",
        "top_hits": [],
        "evidence_pack": [],
        "injected_knowledge": [],
    }


def _context_payload(items: list[Any]) -> list[dict[str, str]]:
    return _clean_context([item if isinstance(item, dict) else asdict(item) for item in items])


def _validated_replay_reply(stored: dict[str, Any]) -> str | None:
    reply = stored.get("reply")
    if reply is None:
        return None
    if not isinstance(reply, str):
        raise FastReplyParseError("Stored replay reply must be a string")
    cleaned = reply.strip()
    if not cleaned:
        return None
    assert_customer_visible_reply_is_safe(cleaned)
    return cleaned


def prepare_webchat_fast_stream(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    request_id: str | None,
) -> StreamBeginOutcome:
    request_hash = compute_request_hash(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        client_message_id=client_message_id,
        body=body,
        recent_context=recent_context,
    )
    request_hash_aliases = compute_legacy_v1_request_hash_aliases(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        client_message_id=client_message_id,
        body=body,
        recent_context=recent_context,
    )
    with db_context() as db:
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=tenant_key,
            session_id=session_id,
            client_message_id=client_message_id,
            request_hash=request_hash,
            request_hash_aliases=request_hash_aliases,
            owner_request_id=request_id,
        )
        return StreamBeginOutcome(
            status=begin.kind,
            request_hash=request_hash,
            row_id=begin.row.id if begin.row is not None else None,
            response_json=begin.response_json,
            error_code=begin.error_code,
        )


def _mark_failed(row_id: int, error_code: str) -> None:
    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_failed(db, row, error_code=error_code)


def _mark_done(row_id: int, response_json: dict[str, Any]) -> None:
    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=response_json)


def _persist_stream_result(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    body: str,
    parsed: ParsedFastReply,
    recent_context: list[dict[str, Any]] | None,
    routing_context: FastRoutingContext | None = None,
    tracking_fact_metadata: dict[str, Any] | None = None,
    evidence_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=tenant_key, channel_key=channel_key, session_id=session_id)
        metadata = {"handoff_required": parsed.handoff_required, "reply_source": "provider_runtime_stream"}
        if tracking_fact_metadata:
            metadata["tracking_fact"] = tracking_fact_metadata
        if evidence_trace:
            metadata["rag_trace"] = evidence_trace
        append_fast_ai_message(db, conversation=conversation, reply=parsed.reply, client_message_id=client_message_id, metadata=metadata)
        if not parsed.handoff_required:
            return fast_public_session_payload(db, conversation)
        business_state = extract_fast_business_state(body=body, context=recent_context or [], session_id=session_id)
        if parsed.tracking_number:
            business_state = type(business_state)(intent=business_state.intent, issue_type=business_state.issue_type, tracking_number=parsed.tracking_number, fast_issue_key=f"tracking:{parsed.tracking_number}:intent:{business_state.issue_type}"[:240], missing_fields=())
        ticket = get_or_create_fast_ticket(
            db,
            conversation=conversation,
            business_state=business_state,
            handoff_reason=parsed.handoff_reason,
            recommended_agent_action=parsed.recommended_agent_action,
            customer_message=body,
            routing_context=routing_context,
        )
        handoff_message = append_fast_system_handoff_message(db, conversation=conversation, handoff_reason=parsed.handoff_reason, recommended_agent_action=parsed.recommended_agent_action, client_message_id=client_message_id)
        request_webchat_handoff(
            db,
            conversation=conversation,
            ticket=ticket,
            source="ai_auto",
            trigger_type="stream_ai_result_handoff_required",
            reason_code=parsed.handoff_reason or "stream_ai_result_requires_human_review",
            reason_text=parsed.handoff_reason,
            recommended_agent_action=parsed.recommended_agent_action,
            trigger_message_id=handoff_message.id,
            requested_by_actor_type="ai",
        )
        session_payload = fast_public_session_payload(db, conversation)
        session_payload["ticket_id"] = ticket.id
        return session_payload


async def stream_webchat_fast_reply_events(
    *,
    begin: StreamBeginOutcome,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    visitor: Any,
    request_id: str | None,
    settings: Any,
    routing_context: FastRoutingContext | None = None,
    tracking_fact_summary: str | None = None,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_fact_evidence_present: bool = False,
    runtime_context: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    if begin.status == "replay":
        stored = dict(begin.response_json or {})
        yield sse_event("replay", {"replayed": True})
        try:
            replay_reply = _validated_replay_reply(stored)
        except FastReplyParseError:
            record_fast_reply_metric(status="replay_invalid_output", elapsed_ms=0)
            yield sse_event("error", {"error_code": "ai_invalid_output", "retry_after_ms": 1500, "replayed": True})
            return
        final = {k: v for k, v in stored.items() if k != "reply"}
        final["replayed"] = True
        with db_context() as db:
            conversation = get_or_create_fast_conversation(db, tenant_key=tenant_key, channel_key=channel_key, session_id=session_id)
            session_payload = fast_public_session_payload(db, conversation)
            final.update(session_payload)
            final["webchat_session"] = session_payload
        yield sse_event("final", final)
        if replay_reply:
            yield sse_event("reply_delta", {"text": replay_reply})
        return

    if begin.row_id is None:
        yield sse_event("error", {"error_code": begin.error_code or "idempotency_error", "retry_after_ms": 1500})
        return

    yield sse_event("meta", {"replayed": False, "stream_version": "provider_runtime_compat"})
    _mark_failed(begin.row_id, "stream_provider_retired")
    record_fast_reply_metric(status="stream_provider_retired", elapsed_ms=0)
    yield sse_event("error", {"error_code": "stream_provider_retired", "retry_after_ms": 1500})
