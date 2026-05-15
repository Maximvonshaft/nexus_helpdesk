from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = [
    re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"(CODEX_AUTH_TOKEN|OPENAI_API_KEY|refresh_token|access_token|token)\s*[=:]\s*[^,\s]+", re.IGNORECASE),
    re.compile(r"auth\.json", re.IGNORECASE),
]


def redact_secret_text(value: Any) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text


def safe_exception_message(exc: BaseException | str | None) -> str:
    if exc is None:
        return ""
    return redact_secret_text(str(exc))[:500]


def safe_endpoint_summary(url: str | None) -> str | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return "configured_endpoint"
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    except Exception:
        return "configured_endpoint"
