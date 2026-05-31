from __future__ import annotations

import os
import re
from typing import Any


TRACKING_RE = re.compile(
    r"\b(?:1Z[A-Z0-9]{16}|[A-Z]{1,4}\d[A-Z0-9]{6,35}|\d{10,40})\b",
    re.IGNORECASE,
)
TRACKING_QUESTION_RE = re.compile(
    r"\b(track|tracking|parcel|package|shipment|delivery)\b",
    re.IGNORECASE,
)


def extract_tracking_number(text: str) -> str | None:
    match = TRACKING_RE.search(text or "")
    if not match:
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", match.group(0).upper())[:40]
    digit_count = sum(ch.isdigit() for ch in normalized)
    if digit_count < 4:
        return None
    if normalized.isdigit() and len(normalized) < 10:
        return None
    return normalized


def is_tracking_question(text: str) -> bool:
    return bool(TRACKING_QUESTION_RE.search(text or ""))


def lookup_tracking(payload: dict[str, Any]) -> dict[str, Any]:
    tracking_number = extract_tracking_number(str(payload.get("tracking_number") or payload.get("text") or ""))
    if not tracking_number:
        return {"status": "tracking_number_required"}
    endpoint = (os.getenv("TRACKING_LOOKUP_ENDPOINT") or "").strip()
    token_file = (os.getenv("TRACKING_LOOKUP_API_KEY_FILE") or "").strip()
    if not endpoint or not token_file:
        return {
            "status": "not_configured",
            "tracking_number_redacted": f"{tracking_number[:3]}...{tracking_number[-2:]}",
            "summary": "Tracking lookup is not connected yet. I have recorded your tracking number and a human agent will follow up if needed.",
        }
    return {
        "status": "not_configured",
        "tracking_number_redacted": f"{tracking_number[:3]}...{tracking_number[-2:]}",
        "summary": "Tracking lookup is not connected yet. I have recorded your tracking number and a human agent will follow up if needed.",
    }
