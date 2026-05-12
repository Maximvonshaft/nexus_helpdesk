from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db import Base
from ..utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class WebchatFastIdempotency(Base):
    __tablename__ = "webchat_fast_idempotency"
    __table_args__ = (
        UniqueConstraint("tenant_key", "session_id", "client_message_id", name="uq_webchat_fast_idem_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(120), index=True)
    client_message_id: Mapped[str] = mapped_column(String(120), index=True)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="processing", index=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    locked_until: Mapped[Any | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    owner_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    last_heartbeat_at: Mapped[Any | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[Any] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[Any] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
    expires_at: Mapped[Any] = mapped_column(UTCDateTime, index=True)


_RETRYABLE_FAILED_CODES = {"ai_unavailable", "openclaw_stream_error", "openclaw_malformed_json", "stream_transport_error"}


def clean(value: Any, *, limit: int = 120) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def clean_body(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:2000]


def _normalize_role(value: Any) -> str | None:
    role = clean(value, limit=40).lower()
    if role in {"customer", "visitor", "user"}:
        return "customer"
    if role in {"ai", "assistant", "agent"}:
        return "ai"
    return None


def normalize_recent_context(recent_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(recent_context or []):
        if not isinstance(item, dict):
            continue
        role = _normalize_role(item.get("role"))
        text = clean_body(item.get("text") if item.get("text") is not None else item.get("body"))[:500]
        if role and text:
            normalized.append({"role": role, "text": text, "seq": str(index)})
    # Stable truncation: keep the newest ten accepted turns in original sequence.
    tail = normalized[-10:]
    return [{"role": item["role"], "text": item["text"]} for item in tail]


def canonical_request_payload(
    *,
    tenant_key: str | None,
    channel_key: str | None,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "tenant_key": clean(tenant_key) or "default",
        "channel_key": clean(channel_key) or "website",
        "session_id": clean(session_id),
        "client_message_id": clean(client_message_id),
        "body": clean_body(body),
        "recent_context": normalize_recent_context(recent_context),
    }


def compute_request_hash(
    *,
    tenant_key: str | None,
    channel_key: str | None,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
) -> str:
    canonical = canonical_request_payload(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        client_message_id=client_message_id,
        body=body,
        recent_context=recent_context,
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


BeginKind = Literal["owner", "replay", "processing", "conflict", "failed_non_retryable"]


@dataclass(frozen=True)
class IdempotencyBeginResult:
    kind: BeginKind
    row: WebchatFastIdempotency | None = None
    response_json: dict[str, Any] | None = None
    error_code: str | None = None


def begin_webchat_fast_idempotency(
    db: Session,
    *,
    tenant_key: str,
    session_id: str,
    client_message_id: str,
    request_hash: str,
    owner_request_id: str | None,
    lock_seconds: int = 60,
    ttl_seconds: int = 600,
) -> IdempotencyBeginResult:
    now = utc_now()
    locked_until = now + timedelta(seconds=lock_seconds)
    expires_at = now + timedelta(seconds=ttl_seconds)
    clean_tenant = clean(tenant_key) or "default"
    clean_session = clean(session_id)
    clean_client = clean(client_message_id)

    row = db.execute(
        select(WebchatFastIdempotency).where(
            WebchatFastIdempotency.tenant_key == clean_tenant,
            WebchatFastIdempotency.session_id == clean_session,
            WebchatFastIdempotency.client_message_id == clean_client,
        )
    ).scalar_one_or_none()
    if row is None:
        row = WebchatFastIdempotency(
            tenant_key=clean_tenant,
            session_id=clean_session,
            client_message_id=clean_client,
            request_hash=request_hash,
            status="processing",
            locked_until=locked_until,
            owner_request_id=owner_request_id,
            attempt_count=1,
            last_heartbeat_at=now,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return begin_webchat_fast_idempotency(
                db,
                tenant_key=tenant_key,
                session_id=session_id,
                client_message_id=client_message_id,
                request_hash=request_hash,
                owner_request_id=owner_request_id,
                lock_seconds=lock_seconds,
                ttl_seconds=ttl_seconds,
            )
        return IdempotencyBeginResult(kind="owner", row=row)

    if row.request_hash != request_hash:
        return IdempotencyBeginResult(kind="conflict", row=row, error_code="idempotency_key_reused_with_different_payload")
    if row.status == "done":
        return IdempotencyBeginResult(kind="replay", row=row, response_json=dict(row.response_json or {}))
    if row.status == "processing" and row.locked_until and row.locked_until > now:
        return IdempotencyBeginResult(kind="processing", row=row, error_code="request_processing")
    if row.status == "failed" and row.error_code not in _RETRYABLE_FAILED_CODES:
        return IdempotencyBeginResult(kind="failed_non_retryable", row=row, error_code=row.error_code or "request_failed")

    row.status = "processing"
    row.error_code = None
    row.locked_until = locked_until
    row.owner_request_id = owner_request_id
    row.attempt_count = int(row.attempt_count or 0) + 1
    row.last_heartbeat_at = now
    row.updated_at = now
    row.expires_at = expires_at
    db.flush()
    return IdempotencyBeginResult(kind="owner", row=row)


def mark_webchat_fast_done(db: Session, row: WebchatFastIdempotency, *, response_json: dict[str, Any]) -> None:
    now = utc_now()
    row.status = "done"
    row.response_json = dict(response_json)
    row.error_code = None
    row.locked_until = None
    row.last_heartbeat_at = now
    row.updated_at = now
    db.flush()


def mark_webchat_fast_failed(db: Session, row: WebchatFastIdempotency, *, error_code: str) -> None:
    now = utc_now()
    row.status = "failed"
    row.error_code = clean(error_code, limit=120) or "request_failed"
    row.locked_until = None
    row.last_heartbeat_at = now
    row.updated_at = now
    db.flush()


def cleanup_expired_webchat_fast_idempotency(db: Session) -> int:
    result = db.execute(delete(WebchatFastIdempotency).where(WebchatFastIdempotency.expires_at < utc_now()))
    db.flush()
    return int(result.rowcount or 0)
