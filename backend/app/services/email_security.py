from __future__ import annotations

import html
import re
from email.utils import parseaddr

from sqlalchemy.orm import Session

from ..models import EmailSuppression
from ..utils.normalize import normalize_email


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email_address(value: str | None) -> str | None:
    _, addr = parseaddr(value or "")
    cleaned = addr.strip().lower()
    if not cleaned or "\r" in cleaned or "\n" in cleaned:
        return None
    if not EMAIL_RE.match(cleaned):
        return None
    return cleaned


def reject_header_injection(*values: str | None) -> None:
    for value in values:
        if value and ("\r" in value or "\n" in value):
            raise ValueError("email_header_injection")


def sanitize_email_body(value: str) -> str:
    cleaned = re.sub(r"(?is)<\s*script[^>]*>.*?<\s*/\s*script\s*>", "", value or "")
    cleaned = re.sub(r"(?is)on\w+\s*=\s*(['\"]).*?\1", "", cleaned)
    return html.unescape(cleaned).strip()


def is_email_suppressed(db: Session, email: str | None) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    return (
        db.query(EmailSuppression.id)
        .filter(EmailSuppression.email_normalized == normalized, EmailSuppression.is_active.is_(True))
        .first()
        is not None
    )
