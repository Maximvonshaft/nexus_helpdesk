from __future__ import annotations

import json
from typing import Any

PUBLIC_METADATA_KEYS = {
    "external_send",
    "fact_evidence_present",
    "generated_by",
    "reply_source",
}

# Voice turns remain durable canonical conversation records for AI context,
# operator handoff, and audit. The visitor receives them through the live-voice
# surface, so the ordinary text-message projection must not publish them again.
PUBLIC_WEBCHAT_HIDDEN_MESSAGE_TYPES = frozenset({"voice_transcript"})


def public_webchat_message_visible(message_type: str | None) -> bool:
    normalized = str(message_type or "text").strip().lower() or "text"
    return normalized not in PUBLIC_WEBCHAT_HIDDEN_MESSAGE_TYPES


def parse_public_webchat_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def public_webchat_metadata(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    safe = {key: metadata[key] for key in PUBLIC_METADATA_KEYS if key in metadata}
    return safe or None


def public_webchat_message_payload(row: Any) -> dict[str, Any]:
    body_text = getattr(row, "body_text", None) or row.body
    metadata = parse_public_webchat_json(getattr(row, "metadata_json", None))
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "body_text": body_text,
        "message_type": getattr(row, "message_type", None) or "text",
        "payload_json": parse_public_webchat_json(getattr(row, "payload_json", None)),
        "metadata_json": public_webchat_metadata(metadata),
        "client_message_id": getattr(row, "client_message_id", None),
        "ai_turn_id": getattr(row, "ai_turn_id", None),
        "delivery_status": getattr(row, "delivery_status", None) or "sent",
        "action_status": getattr(row, "action_status", None),
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
