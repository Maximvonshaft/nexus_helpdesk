from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MAX_ERROR_SUMMARY_CHARS = 500

_SENSITIVE_WORDS_RE = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|credential|cookie|session[_-]?key|prompt|message|body|content|transcript|customer)",
    re.IGNORECASE,
)
_ASSIGNMENT_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(session[_-]?key\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)[^\n\r]+"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(secret\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(token\s*[:=]\s*)[^\s,;&]+"),
]


def _preview(raw: str) -> str:
    value = raw[:160]
    for pattern in _ASSIGNMENT_PATTERNS:
        value = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    if _SENSITIVE_WORDS_RE.search(value):
        return "[REDACTED_ERROR_TEXT]"
    return value[:160]


def redact_sensitive_error_text(
    error_text: Any,
    *,
    error_code: str | None = None,
    error_class: str | None = None,
) -> str | None:
    """Return a bounded diagnostic summary without storing raw secrets, prompts, or customer text."""
    if error_text in (None, ""):
        return None
    raw = str(error_text)
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    contains_sensitive_marker = bool(
        _SENSITIVE_WORDS_RE.search(raw) or any(pattern.search(raw) for pattern in _ASSIGNMENT_PATTERNS)
    )
    payload = {
        "redacted": True,
        "type": "error_summary",
        "error_code": (error_code or error_class or "error")[:120],
        "error_class": (error_class or error_code or "Error")[:120],
        "length": len(raw),
        "sha256_prefix": digest,
        "contains_sensitive_marker": contains_sensitive_marker,
        "safe_preview": "[REDACTED_ERROR_TEXT]" if contains_sensitive_marker else _preview(raw),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)[:MAX_ERROR_SUMMARY_CHARS]
