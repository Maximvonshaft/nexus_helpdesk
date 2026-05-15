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
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from .webchat_fast_output_parser import FastReplyParseError, ParsedFastReply
from .webchat_fast_reply_metrics import record_fast_reply_metric, record_openclaw_responses_metric
from .webchat_fast_stream_parser import StreamingReplyAbort, StreamingReplyExtractor
from .webchat_handoff_snapshot_service import build_handoff_snapshot_payload, enqueue_webchat_handoff_snapshot_job
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


def _visitor_payload(visitor: Any) -> dict[str, Any]:
    if visitor is None:
        return {}
    if hasattr(visitor, "model_dump"):
        return visitor.model_dump(exclude_none=True)
    if isinstance(visitor, dict):
        return {k: v for k, v in visitor.items() if v is not None}
    return {}


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
    with db_context() as db:
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=tenant_key,
            session_id=session_id,
            client_message_id=client_message_id,
            request_hash=request_hash,
            owner_request_id=request_id,
        )
        return StreamBeginOutcome(
            status=begin.kind,
            request_hash=request_hash,
            row_id=begin.row.id if begin.row is not None else None,
            response_json=begin.response_json,
            error_code=begin.error_code,
        )


def _load_idem_row(row_id: int) -> WebchatFastIdempotency:
    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        return row


def _mark_failed(row_id: int, error_code: str) -> None:
    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_failed(db, row, error_code=error_code)


def _mark_done(row_id: int, response_json: dict[str, Any]) -> None:
    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=response_json)


def _enqueue_handoff(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    client_message_id: str,
    body: str,
    parsed: ParsedFastReply,
    recent_context: list[dict[str, Any]] | None,
    visitor: Any,
) -> bool:
    snapshot = build_handoff_snapshot_payload(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        client_message_id=client_message_id,
        customer_last_message=body,
        ai_reply=parsed.reply,
        intent=parsed.intent,
        tracking_number=parsed.tracking_number,
        handoff_reason=parsed.handoff_reason,
        recommended_agent_action=parsed.recommended_agent_action,
        recent_context=recent_context or [],
        visitor=_visitor_payload(visitor),
    )
    with db_context() as db:
        enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
    return True


def _missing_reply_suffix(parsed_reply: str, emitted_text: str) -> str | None:
    """Return final-safe suffix that has not been emitted yet.

    Normal streams emit safe deltas as provider chunks arrive. Some providers only
    expose the complete text in the final event. After strict final parse has
    accepted the reply, it is safe to emit any remaining customer-visible suffix
    before the terminal final event.
    """

    if not parsed_reply:
        return None
    if not emitted_text:
        return parsed_reply
    if parsed_reply.startswith(emitted_text):
        suffix = parsed_reply[len(emitted_text):]
        return suffix or None
    # The provider rewrote earlier chunks but strict final parse succeeded. Avoid
    # duplicating a potentially stale partial; do not emit more text here.
    return None


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
        if stored.get("reply"):
            yield sse_event("reply_delta", {"text": stored.get("reply") or ""})
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
            delta = extractor.feed_event(event)
            if delta and delta.text:
                yield sse_event("reply_delta", {"text": delta.text})

        final_input: dict[str, Any] | str | None = None
        if last_completed is not None:
            final_input = last_completed.full_text or last_completed.full_payload
        parsed = extractor.final_parse(final_input)

        tail = extractor.flush()
        if tail and tail.text:
            yield sse_event("reply_delta", {"text": tail.text})
        missing_suffix = _missing_reply_suffix(parsed.reply, extractor.emitted_text)
        if missing_suffix:
            yield sse_event("reply_delta", {"text": missing_suffix})

        ticket_creation_queued = False
        if parsed.handoff_required:
            try:
                ticket_creation_queued = _enqueue_handoff(
                    tenant_key=tenant_key,
                    channel_key=channel_key,
                    session_id=session_id,
                    client_message_id=client_message_id,
                    body=body,
                    parsed=parsed,
                    recent_context=recent_context,
                    visitor=visitor,
                )
            except Exception:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _mark_failed(begin.row_id, "handoff_enqueue_failed")
                record_fast_reply_metric(status="handoff_enqueue_failed", intent=parsed.intent, handoff_required=True, elapsed_ms=elapsed_ms)
                yield sse_event("error", {"error_code": "handoff_enqueue_failed", "retry_after_ms": 1500})
                return
        final = public_final_from_parsed(parsed, ticket_creation_queued=ticket_creation_queued, replayed=False)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        final["elapsed_ms"] = elapsed_ms
        _mark_done(begin.row_id, {**final, "reply": parsed.reply})
        record_fast_reply_metric(status="ok", intent=parsed.intent, handoff_required=parsed.handoff_required, elapsed_ms=elapsed_ms)
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
