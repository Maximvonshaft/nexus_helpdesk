from __future__ import annotations

import hashlib
import secrets
from datetime import timezone, timedelta
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from ..utils.time import utc_now
from ..webchat_models import WebchatConversation

MAX_MESSAGE_CHARS = 2000
MAX_FIELD_CHARS = 300
MAX_URL_CHARS = 700
WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7


def clip(value: str | None, limit: int = MAX_FIELD_CHARS) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    return cleaned[:limit] if cleaned else None


def clip_body(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="message body is required")
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"message body exceeds {MAX_MESSAGE_CHARS} characters",
        )
    return cleaned


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_optional(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def new_public_id() -> str:
    token = secrets.token_urlsafe(18).replace("-", "").replace("_", "")
    return f"wc_{token[:24]}"


def new_visitor_token() -> str:
    return secrets.token_urlsafe(32)


def new_visitor_token_expiry():
    return utc_now() + timedelta(days=WEBCHAT_VISITOR_TOKEN_TTL_DAYS)


def ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def origin_from_request(
    request: Request,
    explicit_origin: str | None = None,
) -> str | None:
    origin = explicit_origin or request.headers.get("origin")
    if origin:
        return clip(origin, 255)
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return clip(f"{parsed.scheme}://{parsed.netloc}", 255)


def validate_visitor_token(
    conversation: WebchatConversation,
    token: str | None,
) -> None:
    if not token or hash_token(token) != conversation.visitor_token_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webchat visitor token",
        )
    expires_at = ensure_aware_utc(
        getattr(conversation, "visitor_token_expires_at", None)
    )
    now = ensure_aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid webchat visitor token",
        )
