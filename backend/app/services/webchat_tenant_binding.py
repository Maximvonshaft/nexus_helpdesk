from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models_webchat_binding import WebchatPublicOriginBinding
from ..settings import get_settings
from ..webchat_models import WebchatConversation

settings = get_settings()
_SESSION_SCOPE_KEY = "nexus.webchat_public_scope.v1"
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_NON_PRODUCTION_ENVS = {"development", "test", "local"}


@dataclass(frozen=True)
class VerifiedWebchatPublicScope:
    tenant_key: str
    channel_key: str
    normalized_origin: str | None
    binding_id: int | None
    authority: str


def normalize_public_origin(value: str | None, *, allow_http_local: bool = True) -> str | None:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return None
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_webchat_origin") from exc
    if (
        scheme not in {"https", "http"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=400, detail="invalid_webchat_origin")
    if scheme == "http" and not (
        allow_http_local and host in {"localhost", "127.0.0.1", "::1"}
    ):
        raise HTTPException(status_code=400, detail="insecure_webchat_origin")
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    return f"{scheme}://{netloc}"


def request_public_origin(request: Request) -> str | None:
    header_origin = request.headers.get("origin")
    if header_origin:
        return normalize_public_origin(header_origin)
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return normalize_public_origin(f"{parsed.scheme}://{parsed.netloc}")
    return None


def _normalize_scope_key(value: str | None, *, field: str, default: str) -> str:
    text = str(value or default).strip() or default
    pattern = _TENANT_RE if field == "tenant" else _CHANNEL_RE
    if not pattern.fullmatch(text):
        raise HTTPException(status_code=400, detail=f"invalid_webchat_{field}_scope")
    return text


def _scope_mismatch() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="webchat_public_scope_mismatch")


def _binding_for_origin(db: Session, origin: str) -> WebchatPublicOriginBinding | None:
    return (
        db.query(WebchatPublicOriginBinding)
        .filter(
            WebchatPublicOriginBinding.normalized_origin == origin,
            WebchatPublicOriginBinding.is_active.is_(True),
        )
        .first()
    )


def resolve_public_webchat_scope(
    db: Session,
    *,
    request: Request,
    requested_tenant_key: str | None,
    requested_channel_key: str | None,
    conversation_id: str | None = None,
    app_env: str | None = None,
) -> VerifiedWebchatPublicScope:
    environment = str(app_env or settings.app_env or "production").strip().lower()
    origin = request_public_origin(request)
    binding = _binding_for_origin(db, origin) if origin else None

    if binding is None:
        if environment not in _NON_PRODUCTION_ENVS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="webchat_public_binding_required",
            )
        tenant = _normalize_scope_key(requested_tenant_key, field="tenant", default="default")
        channel = _normalize_scope_key(requested_channel_key, field="channel", default="default")
        scope = VerifiedWebchatPublicScope(
            tenant_key=tenant,
            channel_key=channel,
            normalized_origin=origin,
            binding_id=None,
            authority="non_production_legacy",
        )
    else:
        tenant = _normalize_scope_key(binding.tenant_key, field="tenant", default="default")
        channel = _normalize_scope_key(binding.channel_key, field="channel", default="default")
        requested_tenant = str(requested_tenant_key or "").strip()
        requested_channel = str(requested_channel_key or "").strip()
        # "default" is the historical client placeholder. Any other explicit
        # mismatch is rejected rather than silently interpreted as authority.
        if requested_tenant not in {"", "default", tenant}:
            raise _scope_mismatch()
        if requested_channel not in {"", "default", channel}:
            raise _scope_mismatch()
        scope = VerifiedWebchatPublicScope(
            tenant_key=tenant,
            channel_key=channel,
            normalized_origin=origin,
            binding_id=int(binding.id),
            authority="server_origin_binding",
        )

    if conversation_id:
        existing = (
            db.query(WebchatConversation)
            .filter(WebchatConversation.public_id == str(conversation_id).strip())
            .first()
        )
        if existing is not None:
            existing_origin = normalize_public_origin(existing.origin) if existing.origin else None
            if (
                existing.tenant_key != scope.tenant_key
                or existing.channel_key != scope.channel_key
                or existing_origin != scope.normalized_origin
            ):
                raise _scope_mismatch()
    db.info[_SESSION_SCOPE_KEY] = scope
    return scope


def current_verified_webchat_scope(db: Session) -> VerifiedWebchatPublicScope | None:
    value = db.info.get(_SESSION_SCOPE_KEY)
    return value if isinstance(value, VerifiedWebchatPublicScope) else None


@event.listens_for(Session, "before_flush")
def _apply_verified_scope_to_new_conversations(session: Session, _flush_context, _instances) -> None:
    scope = current_verified_webchat_scope(session)
    if scope is None:
        return
    for row in session.new:
        if not isinstance(row, WebchatConversation):
            continue
        row.tenant_key = scope.tenant_key
        row.channel_key = scope.channel_key
        row.origin = scope.normalized_origin
