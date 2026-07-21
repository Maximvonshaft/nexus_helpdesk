from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..webchat_models import WebchatMessage
from . import persona_service
from .bulletin_service import list_active_bulletins
from .customer_memory_service import runtime_memory_context
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


def build_agent_context(
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
) -> dict[str, Any]:
    """Build the canonical sanitized Agent context without pre-running domain Tools."""

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
    memory = runtime_memory_context(
        db,
        tenant_key=tenant_key,
        customer_id=getattr(customer, "id", None),
        market_id=market_id,
        channel=channel_key,
        language=language,
    )
    bulletins = _active_bulletin_context(
        db,
        market_id=market_id,
        country_code=effective_country.country,
        channel=channel_key,
    )
    return sanitize_runtime_context(
        {
            "context_version": "nexus.agent_context.v2",
            "tenant_key": tenant_key,
            "channel_context": {
                "market_id": market_id,
                "channel": channel_key,
                "language": language,
                "audience_scope": audience_scope,
                **effective_country_payload(effective_country),
            },
            "persona_context": _persona_context(profile, match_rank),
            "customer_memory": memory,
            "active_bulletins": bulletins,
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


def _active_bulletin_context(
    db: Session,
    *,
    market_id: int | None,
    country_code: str | None,
    channel: str | None,
) -> list[dict[str, Any]]:
    rows = list_active_bulletins(
        db,
        market_id=market_id,
        country_code=country_code,
        channel=channel,
    )
    return [
        {
            "title": str(row.title or "")[:240],
            "summary": str(row.summary or row.body or "")[:1200],
            "category": str(row.category or "")[:80] or None,
            "severity": str(row.severity or "")[:40] or None,
            "starts_at": row.starts_at.isoformat() if row.starts_at else None,
            "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        }
        for row in rows
        if row.auto_inject_to_ai and row.audience in {"customer", "both", "all"}
    ][:5]


def _persona_context(profile: Any, match_rank: Any) -> dict[str, Any] | None:
    if (
        profile is None
        or not bool(getattr(profile, "is_active", False))
        or int(getattr(profile, "published_version", 0) or 0) <= 0
    ):
        return None
    raw_content = getattr(profile, "published_content_json", None)
    content = sanitize_runtime_context(dict(raw_content)) if isinstance(raw_content, dict) else {}
    nested = content.get("identity_context")
    identity_source = dict(nested) if isinstance(nested, dict) else {}
    for field in (
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
    ):
        if field in content:
            identity_source[field] = content[field]
    identity = sanitize_runtime_context(identity_source)
    return {
        "profile_key": str(getattr(profile, "profile_key", "") or "")[:160],
        "name": str(getattr(profile, "name", "") or "")[:240],
        "summary": _sanitize_text(str(getattr(profile, "published_summary", "") or ""))[:1200],
        "content_json": content,
        "identity_context": identity if isinstance(identity, dict) else {},
        "published_version": int(getattr(profile, "published_version", 0) or 0),
        "match_rank": match_rank,
    }


def _row_text(row: Any) -> str:
    return str(getattr(row, "body_text", None) or getattr(row, "body", None) or "").strip()


def _sanitize_text(value: str) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
