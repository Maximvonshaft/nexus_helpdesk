from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any

from .webchat_fast_config import get_webchat_fast_settings
from .webchat_fast_output_parser import FastReplyParseError, ParsedFastReply, UnexpectedToolCallError, parse_openclaw_fast_reply
from .webchat_fast_reply_metrics import record_fast_reply_metric, record_openclaw_responses_metric
from .webchat_openclaw_responses_client import OpenClawResponsesError, call_openclaw_responses


@dataclass(frozen=True)
class WebchatFastReplyResult:
    ok: bool
    ai_generated: bool
    reply_source: str | None
    reply: str | None
    intent: str | None
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    ticket_creation_queued: bool
    elapsed_ms: int
    error_code: str | None = None
    retry_after_ms: int | None = None

    def to_response(self) -> dict[str, Any]:
        payload = asdict(self)
        # Keep the public response compact while preserving explicit false/null contract.
        return payload


def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _clean_context(recent_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    settings = get_webchat_fast_settings()
    items = recent_context or []
    cleaned: list[dict[str, str]] = []
    for item in items[-settings.history_turns * 2:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"customer", "visitor", "user", "ai", "assistant", "agent"}:
            continue
        normalized_role = "customer" if role in {"customer", "visitor", "user"} else "ai"
        text = _clip(str(item.get("text") or item.get("body") or ""), 500)
        if text:
            cleaned.append({"role": normalized_role, "text": text})
    return cleaned[-settings.history_turns * 2:]


def _context_block(recent_context: list[dict[str, str]]) -> str:
    if not recent_context:
        return "(none)"
    lines = []
    for item in recent_context:
        speaker = "Customer" if item["role"] == "customer" else "AI"
        lines.append(f"{speaker}: {item['text']}")
    return "\n".join(lines)


def _instructions() -> str:
    return (
        "You are Speedy, Speedaf's public WebChat AI assistant.\n\n"
        "Hard rules:\n"
        "- Reply in the customer's language.\n"
        "- The customer-visible reply must be short, helpful, and natural.\n"
        "- Do not invent parcel status, delivery result, customs result, refund, compensation, or SLA.\n"
        "- If a tracking number is missing, ask for it naturally.\n"
        "- If manual support is needed, say so naturally.\n"
        "- Return valid JSON only.\n"
        "- No markdown.\n"
        "- No hidden reasoning.\n"
        "- No internal tool names.\n"
        "- No OpenClaw, gateway, prompt, token, localhost, port, or system details.\n\n"
        "JSON schema:\n"
        "{\n"
        "  \"reply\": \"customer visible AI reply\",\n"
        "  \"intent\": \"greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other\",\n"
        "  \"tracking_number\": null,\n"
        "  \"handoff_required\": false,\n"
        "  \"handoff_reason\": null,\n"
        "  \"recommended_agent_action\": null\n"
        "}\n"
    )


def _input_text(*, body: str, recent_context: list[dict[str, str]]) -> str:
    settings = get_webchat_fast_settings()
    text = (
        "Recent context:\n"
        f"{_context_block(recent_context)}\n\n"
        "Customer message:\n"
        f"{_clip(body, 2000)}"
    )
    return text[: settings.max_prompt_chars]


def _session_key(*, tenant_key: str, session_id: str) -> str:
    raw = f"webchat-fast:{tenant_key or 'default'}:{session_id}"
    if len(raw) <= 180:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]
    return f"webchat-fast:{digest}"


def _success_from_parsed(parsed: ParsedFastReply, *, elapsed_ms: int) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="openclaw_responses",
        reply=parsed.reply,
        intent=parsed.intent,
        tracking_number=parsed.tracking_number,
        handoff_required=parsed.handoff_required,
        handoff_reason=parsed.handoff_reason,
        recommended_agent_action=parsed.recommended_agent_action,
        ticket_creation_queued=False,
        elapsed_ms=elapsed_ms,
    )


def _error_response(error_code: str, *, elapsed_ms: int) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=False,
        ai_generated=False,
        reply_source=None,
        reply=None,
        intent=None,
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=elapsed_ms,
        error_code=error_code,
        retry_after_ms=1500,
    )


async def generate_webchat_fast_reply(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    request_id: str | None = None,
) -> WebchatFastReplyResult:
    """Generate one AI-only WebChat reply through OpenClaw /v1/responses.

    This function must remain DB-free. Ticket creation, message persistence,
    old AI turns, and polling are deliberately outside this path.
    """

    started = time.monotonic()
    settings = get_webchat_fast_settings()
    if not settings.enabled or not settings.is_openclaw_configured:
        result = _error_response("ai_unavailable", elapsed_ms=0)
        record_fast_reply_metric(status="ai_unavailable", elapsed_ms=0)
        return result

    normalized_body = _clip(body, 2000)
    context = _clean_context(recent_context)
    try:
        response = await call_openclaw_responses(
            session_key=_session_key(tenant_key=tenant_key, session_id=session_id),
            instructions=_instructions(),
            input_text=_input_text(body=normalized_body, recent_context=context),
            request_id=request_id,
            settings=settings,
        )
        record_openclaw_responses_metric(status="ok", agent_id=settings.openclaw_responses_agent_id, elapsed_ms=response.elapsed_ms)
        parsed = parse_openclaw_fast_reply(response.payload)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        result = _success_from_parsed(parsed, elapsed_ms=elapsed_ms)
        record_fast_reply_metric(status="ok", intent=result.intent, handoff_required=result.handoff_required, elapsed_ms=elapsed_ms)
        return result
    except UnexpectedToolCallError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_fast_reply_metric(status="ai_unexpected_tool_call", elapsed_ms=elapsed_ms)
        return _error_response("ai_unexpected_tool_call", elapsed_ms=elapsed_ms)
    except FastReplyParseError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_fast_reply_metric(status="ai_invalid_output", elapsed_ms=elapsed_ms)
        return _error_response("ai_invalid_output", elapsed_ms=elapsed_ms)
    except OpenClawResponsesError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_openclaw_responses_metric(status="unavailable", agent_id=settings.openclaw_responses_agent_id, elapsed_ms=elapsed_ms)
        record_fast_reply_metric(status="ai_unavailable", elapsed_ms=elapsed_ms)
        return _error_response("ai_unavailable", elapsed_ms=elapsed_ms)
