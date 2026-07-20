from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..webchat_models import WebchatMessage
from . import persona_service
from .effective_country import effective_country_payload, resolve_effective_country

MAX_STRUCTURED_RECENT_CONTEXT = 12
MAX_RECENT_CONTEXT_TEXT_CHARS = 1000
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
_SECRET_KEYS = {
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "raw_payload",
    "provider_payload",
}


def build_webchat_runtime_context(
    db: Session,
    *,
    tenant_key: str,
    channel_key: str,
    body: str,
    market_id: int | None = None,
    language: str | None = None,
    audience_scope: str = "customer",
    ticket: Any = None,
    conversation: Any = None,
    customer: Any = None,
    channel_payload: dict[str, Any] | None = None,
    **_legacy: Any,
) -> dict[str, Any]:
    """Build generic Agent context without pre-running domain retrieval or tools."""

    profile, match_rank = persona_service.resolve_preview(
        db,
        market_id=market_id,
        channel=channel_key,
        language=language,
    )
    effective_country = resolve_effective_country(
        ticket=ticket,
        conversation=conversation,
        customer=customer,
        market_id=market_id,
        channel_payload=channel_payload or {},
    )
    recent = build_structured_recent_context(
        db=db,
        conversation=conversation,
        current_body=body,
    )
    return sanitize_runtime_context(
        {
            "context_version": "nexus.agent_context.v1",
            "tenant_key": tenant_key,
            "channel_context": {
                "market_id": market_id,
                "channel": channel_key,
                "language": language,
                "audience_scope": audience_scope,
                **effective_country_payload(effective_country),
            },
            "persona_context": _persona_context(profile, match_rank),
            "recent_conversation": recent,
            "agent_execution_context": {
                "conversation_id": getattr(conversation, "id", None),
                "ticket_id": getattr(ticket, "id", None),
                "customer_id": getattr(customer, "id", None),
                "country_code": effective_country.country,
            },
        }
    )


def build_structured_recent_context(
    *,
    db: Session | None = None,
    conversation: Any = None,
    history_rows: list[Any] | None = None,
    current_message_id: int | None = None,
    current_body: str | None = None,
    limit: int = MAX_STRUCTURED_RECENT_CONTEXT,
) -> list[dict[str, Any]]:
    rows = list(history_rows or [])
    if not rows and db is not None and getattr(conversation, "id", None) is not None:
        rows = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.conversation_id == conversation.id)
            .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
    current = " ".join(str(current_body or "").split())
    skipped_current = False
    output: list[dict[str, Any]] = []
    for row in rows[-limit:]:
        row_id = getattr(row, "id", None)
        if current_message_id is not None and row_id == current_message_id:
            continue
        text = _row_text(row)
        direction = str(getattr(row, "direction", "") or "").strip().lower()
        if not text:
            continue
        if not skipped_current and current and direction == "visitor" and " ".join(text.split()) == current:
            skipped_current = True
            continue
        output.append(
            {
                "role": "customer" if direction == "visitor" else "assistant",
                "text": _sanitize_text(text)[:MAX_RECENT_CONTEXT_TEXT_CHARS],
                "message_id": row_id,
            }
        )
    return output[-limit:]


def build_runtime_context_guard(
    *,
    structured_recent_context: list[dict[str, Any]] | None,
    **_legacy: Any,
) -> dict[str, Any]:
    """Compatibility projection for observability; no domain answer policy."""

    recent = [item for item in structured_recent_context or [] if isinstance(item, dict)]
    return {
        "context_guard": {
            "recent_context_count": len(recent),
            "customer_message_count": sum(1 for item in recent if item.get("role") == "customer"),
            "assistant_message_count": sum(1 for item in recent if item.get("role") == "assistant"),
            "business_truth_policy": "owned_by_skills_and_tool_observations",
        }
    }


def sanitize_runtime_context(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value)[:4000]
    if isinstance(value, (list, tuple, set)):
        return [sanitize_runtime_context(item, depth=depth + 1) for item in list(value)[:30]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: sanitize_runtime_context(item, depth=depth + 1)
            for key, item in list(value.items())[:60]
            if str(key).strip().lower() not in _SECRET_KEYS
        }
    return str(value)[:200]


def _persona_context(profile: Any, match_rank: Any) -> dict[str, Any] | None:
    if profile is None:
        return None
    fields = (
        "brand_name",
        "assistant_name",
        "role_label",
        "identity_statement",
        "identity_answer_rule",
        "handoff_boundary",
        "tone",
        "capabilities",
        "guardrails",
        "disallowed_identity_claims",
    )
    identity = {
        field: getattr(profile, field, None)
        for field in fields
        if getattr(profile, field, None) not in (None, "", [], {})
    }
    return {"match_rank": match_rank, "identity_context": identity}


def _row_text(row: Any) -> str:
    return str(getattr(row, "body_text", None) or getattr(row, "body", None) or "").strip()


def _sanitize_text(value: str) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
