from __future__ import annotations

import re
from typing import Any

from .tracking_fact_schema import safe_tracking_reference

_EMAIL_RE = re.compile(r"^([^@\s])[^@\s]*@([^@\s]+)$", re.IGNORECASE)
_PHONE_DIGIT_RE = re.compile(r"\d")


def mask_support_display_name(value: Any, *, fallback: str = "Customer") -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return fallback
    first = text[0]
    if not first.isalnum():
        return fallback
    return f"{first}•••"


def mask_support_contact(value: Any) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    email_match = _EMAIL_RE.fullmatch(text)
    if email_match:
        domain = email_match.group(2).lower()[:120]
        return f"{email_match.group(1)}***@{domain}"
    digits = "".join(_PHONE_DIGIT_RE.findall(text))
    if len(digits) >= 4:
        return f"phone ending {digits[-2:]}"
    return "contact configured"


def safe_support_tracking_reference(value: Any) -> str | None:
    text = str(value or "").strip()
    return safe_tracking_reference(text) if text else None
