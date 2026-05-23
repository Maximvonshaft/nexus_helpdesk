from __future__ import annotations

import re
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
    return {
        "status": "mock_tracking_ready",
        "tracking_number_redacted": f"{tracking_number[:3]}...{tracking_number[-2:]}",
        "summary": "Tracking lookup is configured as a controlled pilot mock until the approved Speedaf read-only API is enabled.",
    }

