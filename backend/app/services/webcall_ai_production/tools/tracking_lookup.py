from __future__ import annotations

import re
import os
from typing import Any


TRACKING_RE = re.compile(r"[A-Z0-9][A-Z0-9\- ]{5,40}", re.IGNORECASE)


def extract_tracking_number(text: str) -> str | None:
    match = TRACKING_RE.search(text or "")
    if not match:
        return None
    return re.sub(r"[^A-Z0-9]", "", match.group(0).upper())[:40]


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
            "summary": "Tracking lookup is not configured. I cannot verify this shipment right now and will hand the request to a human agent.",
        }
    return {
        "status": "not_configured",
        "tracking_number_redacted": f"{tracking_number[:3]}...{tracking_number[-2:]}",
        "summary": "Tracking lookup provider interface is configured but the approved read-only adapter has not been enabled in this build.",
    }
