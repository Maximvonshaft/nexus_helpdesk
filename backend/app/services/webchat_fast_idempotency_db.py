from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any, Literal

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


# These failures are produced before a durable customer-visible success exists.
# They must not poison the idempotency key, otherwise the browser stream path can
# fail before any visible reply and then block the non-stream fallback/retry with
# failed_non_retryable. Business invariant: exact duplicate customer action may
# retry after upstream transport, parser, safety-abort, or handoff enqueue
# failures; successful responses and different-payload conflicts remain strictly
# idempotent.
_RETRYABLE_FAILED_CODES = {
    "ai_unavailable",
    "ai_invalid_output",
    "ai_safety_abort",
    "ai_unexpected_tool_call",
    "openclaw_stream_error",
    "openclaw_malformed_json",
    "stream_transport_error",
    "stream_internal_error",
    "handoff_enqueue_failed",
}


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


def _encode_hash_payload(canonical: dict[str, Any]) -> str:
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_request_payload(
    *,
    tenant_key: str | None,
    channel_key: str | None,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    # Idempotency identity must represent the customer action, not mutable browser
    # context. recent_context is still accepted here for call-site compatibility
    # and remains available to AI generation code, but it is deliberately excluded
    # from the request hash so weak-network retry, stale cache, multi-tab state,
    # and stream fallback do not convert the same client_message_id into a false
    # conflict.
    _ = recent_context
    return {
        "tenant_key": clean(tenant_key) or "default",
        "channel_key": clean(channel_key) or "website",
        "session_id": clean(session_id),
        "client_message_id": clean(client_message_id),
        "body": clean_body(body),
    }


def legacy_v1_canonical_request_payload(
    *,
    tenant_key: str | None,
    channel_key: str | None,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return the pre-PR-113 idempotency identity for rollout compatibility only.

    v1 included mutable recent_context in the request hash. New writes must not use
    this identity, but existing rows created before the hash fix may still carry
    a v1 hash during the short idempotency TTL window after deployment.
    """

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
    return _encode_hash_payload(canonical)


def compute_legacy_v1_request_hash(
    *,
    tenant_key: str | None,
    channel_key: str | None,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
) -> str:
    canonical = legacy_v1_canonical_request_payload(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        client_message_id=client_message_id,
        body=body,
        recent_context=recent_context,
    )
    return _encode_hash_payload(canonical)


def compute_legacy_v1_request_hash_aliases(
    *,
    tenant_key: str | None,
    channel_key: str | None,
    session_id: str,
    client_message_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
) -> tuple[str, ...]:
    """Return bounded legacy v1 hash candidates for mixed-version deploys.

    The exact pre-deploy browser recent_context is not stored in the idempotency
    table, so perfect reconstruction is impossible. These aliases cover the
    common safe cases: unchanged context, missing/empty context, and suffix drift
    when a browser retries after context truncation or appending. Different body
    values still conflict because every alias includes the cleaned body.
    """

    normalized = normalize_recent_context(recent_context)
    candidate_contexts: list[list[dict[str, str]]] = [normalized, []]
    for index in range(1, len(normalized)):
        candidate_contexts.append(normalized[index:])

    aliases: list[str] = []
    seen: set[str] = set()
    for context in candidate_contexts:
        alias = compute_legacy_v1_request_hash(
            tenant_key=tenant_key,
            channel_key=channel_key,
            session_id=session_id,
            client_message_id=client_message_id,
            body=body,
            recent_context=context,
        )
        if alias not in seen:
            aliases.append(alias)
            seen.add(alias)
    return tuple(aliases)


BeginKind = Literal["owner", "replay", "processing", "conflict", "failed_non_retryable"]


@dataclass(frozen=True)
class IdempotencyBeginResult:
    kind: BeginKind
    row: WebchatFastIdempotency | None = None
    response_json: dict[str, Any] | None = None
    error_code: str | None = None


def _key_filters(*, tenant_key: str, session_id: str, client_message_id: str):
    return (
        WebchatFastIdempotency.tenant_key == tenant_key,
        WebchatFastIdempotency.session_id == session_id,
        WebchatFastIdempotency.client_message_id == client_message_id,
    )


def _dialect_name(db: Session) -> str:
    bind = db.get_bind()
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "") or "").lower()


def _coerce_utc(dt: Any) -> Any:
    if dt is None or not hasattr(dt, "tzinfo"):
        return dt
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _matching_request_hashes(request_hash: str, request_hash_aliases: tuple[str, ...] | None) -> set[str]:
    matches = {request_hash}
    for alias in request_hash_aliases or ():
        if isinstance(alias, str) and alias:
            matches.add(alias)
    return matches


def _resolve_locked_row(
    db: Session,
    *,
    row: WebchatFastIdempotency,
    request_hash: str,
    request_hash_aliases: tuple[str, ...] | None,
    owner_request_id: str | None,
    now: Any,
    locked_until: Any,
    expires_at: Any,
    newly_inserted: bool,
) -> IdempotencyBeginResult:
    now = _coerce_utc(now)
    row_locked_until = _coerce_utc(row.locked_until)
    if row.request_hash not in _matching_request_hashes(request_hash, request_hash_aliases):
        return IdempotencyBeginResult(kind="conflict", row=row, error_code="idempotency_key_reused_with_different_payload")
    if row.status == "done":
        return IdempotencyBeginResult(kind="replay", row=row, response_json=dict(row.response_json or {}))
    if row.status == "processing" and row_locked_until and row_locked_until > now:
        return IdempotencyBeginResult(kind="owner", row=row) if newly_inserted else IdempotencyBeginResult(kind="processing", row=row, error_code="request_processing")
    if row.status == "failed" and row.error_code not in _RETRYABLE_FAILED_CODES:
        return IdempotencyBeginResult(kind="failed_non_retryable", row=row, error_code=row.error_code or "request_failed")

    row.status = "processing"
    row.error_code = None
    row.locked_until = locked_until
    row.owner_request_id = owner_request_id
    row.attempt_count = 1 if newly_inserted else int(row.attempt_count or 0) + 1
    row.last_heartbeat_at = now
    row.updated_at = now
    row.expires_at = expires_at
    db.flush()
    return IdempotencyBeginResult(kind="owner", row=row)


def _begin_webchat_fast_idempotency_postgres(
    db: Session,
    *,
    tenant_key: str,
    session_id: str,
    client_message_id: str,
    request_hash: str,
    request_hash_aliases: tuple[str, ...] | None,
    owner_request_id: str | None,
    now: Any,
    locked_until: Any,
    expires_at: Any,
) -> IdempotencyBeginResult:
    insert_result = db.execute(
        pg_insert(WebchatFastIdempotency)
        .values(
            tenant_key=tenant_key,
            session_id=session_id,
            client_message_id=client_message_id,
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
        .on_conflict_do_nothing(index_elements=["tenant_key", "session_id", "client_message_id"])
        .returning(WebchatFastIdempotency.id)
    )
    inserted_id = insert_result.scalar_one_or_none()
    row = db.execute(
        select(WebchatFastIdempotency)
        .where(*_key_filters(tenant_key=tenant_key, session_id=session_id, client_message_id=client_message_id))
        .with_for_update()
    ).scalar_one()
    return _resolve_locked_row(
        db,
        row=row,
        request_hash=request_hash,
        request_hash_aliases=request_hash_aliases,
        owner_request_id=owner_request_id,
        now=now,
        locked_until=locked_until,
        expires_at=expires_at,
        newly_inserted=inserted_id is not None,
    )


def _begin_webchat_fast_idempotency_compatible(
    db: Session,
    *,
    tenant_key: str,
    session_id: str,
    client_message_id: str,
    request_hash: str,
    request_hash_aliases: tuple[str, ...] | None,
    owner_request_id: str | None,
    now: Any,
    locked_until: Any,
    expires_at: Any,
) -> IdempotencyBeginResult:
    row = db.execute(
        select(WebchatFastIdempotency).where(
            *_key_filters(tenant_key=tenant_key, session_id=session_id, client_message_id=client_message_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = WebchatFastIdempotency(
            tenant_key=tenant_key,
            session_id=session_id,
            client_message_id=client_message_id,
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
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.execute(
                select(WebchatFastIdempotency).where(
                    *_key_filters(tenant_key=tenant_key, session_id=session_id, client_message_id=client_message_id)
                )
            ).scalar_one()
            return _resolve_locked_row(
                db,
                row=row,
                request_hash=request_hash,
                request_hash_aliases=request_hash_aliases,
                owner_request_id=owner_request_id,
                now=now,
                locked_until=locked_until,
                expires_at=expires_at,
                newly_inserted=False,
            )
        return IdempotencyBeginResult(kind="owner", row=row)

    return _resolve_locked_row(
        db,
        row=row,
        request_hash=request_hash,
        request_hash_aliases=request_hash_aliases,
        owner_request_id=owner_request_id,
        now=now,
        locked_until=locked_until,
        expires_at=expires_at,
        newly_inserted=False,
    )


def begin_webchat_fast_idempotency(
    db: Session,
    *,
    tenant_key: str,
    session_id: str,
    client_message_id: str,
    request_hash: str,
    owner_request_id: str | None,
    request_hash_aliases: tuple[str, ...] | None = None,
    lock_seconds: int = 60,
    ttl_seconds: int = 600,
) -> IdempotencyBeginResult:
    now = utc_now()
    locked_until = now + timedelta(seconds=lock_seconds)
    expires_at = now + timedelta(seconds=ttl_seconds)
    clean_tenant = clean(tenant_key) or "default"
    clean_session = clean(session_id)
    clean_client = clean(client_message_id)

    if _dialect_name(db) == "postgresql":
        return _begin_webchat_fast_idempotency_postgres(
            db,
            tenant_key=clean_tenant,
            session_id=clean_session,
            client_message_id=clean_client,
            request_hash=request_hash,
            request_hash_aliases=request_hash_aliases,
            owner_request_id=owner_request_id,
            now=now,
            locked_until=locked_until,
            expires_at=expires_at,
        )
    return _begin_webchat_fast_idempotency_compatible(
        db,
        tenant_key=clean_tenant,
        session_id=clean_session,
        client_message_id=clean_client,
        request_hash=request_hash,
        request_hash_aliases=request_hash_aliases,
        owner_request_id=owner_request_id,
        now=now,
        locked_until=locked_until,
        expires_at=expires_at,
    )


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