from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

from sqlalchemy import select

from ..db import db_context
from . import webchat_openclaw_responses_client as openclaw_client
from .webchat_fast_ai_service import _clean_context, _input_text, _instructions, _session_key
from .webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_legacy_v1_request_hash_aliases,
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from .webchat_fast_output_parser import FastReplyParseError, ParsedFastReply, assert_customer_visible_reply_is_safe
from .webchat_fast_reply_metrics import record_fast_reply_metric, record_openclaw_responses_metric
from .webchat_fast_session_service import (
    append_fast_ai_message,
    append_fast_system_handoff_message,
    extract_fast_business_state,
    get_or_create_fast_conversation,
    get_or_create_fast_ticket,
)
from .webchat_fast_stream_parser import StreamingReplyAbort, StreamingReplyExtractor
from .webchat_openclaw_responses_client import OpenClawResponsesError
from .webchat_openclaw_stream_adapter import Completed


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


def public_final_from_parsed(parsed: ParsedFastReply, *, ticket_creation_queued: bool, replayed: bool = False) -> dict[str, Any]:
    final = {
        "ok": True,
        "ai_generated": True,
        "reply_source": "openclaw_responses_stream",
        "intent": parsed.intent,
        "tracking_number": parsed.tracking_number,
        "handoff_required": parsed.handoff_required,
        "handoff_reason": parsed.handoff_reason,
        "ticket_creation_queued": ticket_creation_queued,
    }
    if replayed:
        final["replayed"] = True
    return final


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
) -> int | None:
    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=tenant_key, channel_key=channel_key, session_id=session_id)
        append_fast_ai_message(db, conversation=conversation, reply=parsed.reply, client_message_id=client_message_id, metadata={"handoff_required": parsed.handoff_required, "reply_source": "openclaw_responses_stream"})
        if not parsed.handoff_required:
            return None
        business_state = extract_fast_business_state(body=body, context=recent_context or [], session_id=session_id)
        if parsed.tracking_number:
            business_state = type(business_state)(intent=business_state.intent, issue_type=business_state.issue_type, tracking_number=parsed.tracking_number, fast_issue_key=f"tracking:{parsed.tracking_number}:intent:{business_state.issue_type}"[:240], missing_fields=())
        ticket = get_or_create_fast_ticket(db, conversation=conversation, business_state=business_state, handoff_reason=parsed.handoff_reason, recommended_agent_action=parsed.recommended_agent_action, customer_message=body)
        append_fast_system_handoff_message(db, conversation=conversation, handoff_reason=parsed.handoff_reason, recommended_agent_action=parsed.recommended_agent_action, client_message_id=client_message_id)
        return ticket.id


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
) -> AsyncIterator[str]:
    if begin.status == "replay":
        stored = dict(begin.response_json or {})
        yield sse_event("meta", {"replayed": True})
        try:
            replay_reply = _validated_replay_reply(stored)
        except FastReplyParseError:
            record_fast_reply_metric(status="replay_invalid_output", elapsed_ms=0)
            yield sse_event("error", {"error_code": "ai_invalid_output", "retry_after_ms": 1500, "replayed": True})
            return
        if replay_reply:
            yield sse_event("reply_delta", {"text": replay_reply})
        final = {k: v for k, v in stored.items() if k != "reply"}
        final["replayed"] = True
        yield sse_event("final", final)
        return

    if begin.row_id is None:
        yield sse_event("error", {"error_code": begin.error_code or "idempotency_error", "retry_after_ms": 1500})
        return

    started = time.monotonic()
    extractor = StreamingReplyExtractor()
    last_completed: Completed | None = None
    try:
        yield sse_event("meta", {"replayed": False, "stream_version": "V2.2.2"})
        context = _context_payload(recent_context or [])
        async for event in openclaw_client.call_openclaw_responses_stream(
            session_key=_session_key(tenant_key=tenant_key, session_id=session_id),
            instructions=_instructions(),
            input_text=_input_text(body=body, recent_context=context),
            request_id=request_id,
            settings=settings,
        ):
            if isinstance(event, Completed):
                last_completed = event
                continue
            extractor.feed_event(event)

        final_input: dict[str, Any] | str | None = None
        if last_completed is not None:
            final_input = last_completed.full_text or last_completed.full_payload
        parsed = extractor.final_parse(final_input)
        try:
            ticket_id = _persist_stream_result(tenant_key=tenant_key, channel_key=channel_key, session_id=session_id, client_message_id=client_message_id, body=body, parsed=parsed, recent_context=recent_context)
        except Exception:
            if parsed.handoff_required:
                _mark_failed(begin.row_id, "handoff_enqueue_failed")
                record_fast_reply_metric(status="handoff_enqueue_failed", elapsed_ms=int((time.monotonic() - started) * 1000))
                yield sse_event("error", {"error_code": "handoff_enqueue_failed", "retry_after_ms": 1500})
                return
            raise
        final = public_final_from_parsed(parsed, ticket_creation_queued=False, replayed=False)
        if ticket_id is not None:
            final["ticket_id"] = ticket_id
        elapsed_ms = int((time.monotonic() - started) * 1000)
        final["elapsed_ms"] = elapsed_ms
        _mark_done(begin.row_id, {**final, "reply": parsed.reply})
        record_fast_reply_metric(status="ok", intent=parsed.intent, handoff_required=parsed.handoff_required, elapsed_ms=elapsed_ms)
        if parsed.reply:
            yield sse_event("reply_delta", {"text": parsed.reply})
        yield sse_event("final", final)
    except StreamingReplyAbort as exc:
        _mark_failed(begin.row_id, exc.error_code)
        record_fast_reply_metric(status=exc.error_code, elapsed_ms=int((time.monotonic() - started) * 1000))
        yield sse_event("error", {"error_code": exc.error_code, "retry_after_ms": 1500})
    except FastReplyParseError:
        _mark_failed(begin.row_id, "ai_invalid_output")
        record_fast_reply_metric(status="ai_invalid_output", elapsed_ms=int((time.monotonic() - started) * 1000))
        yield sse_event("error", {"error_code": "ai_invalid_output", "retry_after_ms": 1500})
    except OpenClawResponsesError:
        _mark_failed(begin.row_id, "ai_unavailable")
        record_openclaw_responses_metric(status="unavailable", agent_id=settings.openclaw_responses_agent_id, elapsed_ms=int((time.monotonic() - started) * 1000))
        yield sse_event("error", {"error_code": "ai_unavailable", "retry_after_ms": 1500})
    except Exception:
        _mark_failed(begin.row_id, "stream_internal_error")
        record_fast_reply_metric(status="stream_internal_error", elapsed_ms=int((time.monotonic() - started) * 1000))
        yield sse_event("error", {"error_code": "stream_internal_error", "retry_after_ms": 1500})