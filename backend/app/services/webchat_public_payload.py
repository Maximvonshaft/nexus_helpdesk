from __future__ import annotations

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


def public_webchat_metadata(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    safe = {key: metadata[key] for key in PUBLIC_METADATA_KEYS if key in metadata}
    return safe or None
