from __future__ import annotations

from typing import Any

PUBLIC_METADATA_KEYS = {
    "external_send",
    "fact_evidence_present",
    "generated_by",
    "reply_source",
}


def public_webchat_metadata(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    safe = {key: metadata[key] for key in PUBLIC_METADATA_KEYS if key in metadata}
    return safe or None
