from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_service import verify_secret
from ..models import IntegrationClient, IntegrationRequestLog
from ..models_job_scope import IntegrationClientScope, IntegrationRequestLogEnvelope
from ..settings import get_settings
from ..utils.time import utc_now

settings = get_settings()
INTEGRATION_RECEIPT_SCHEMA = "nexus.integration-receipt.v2"
INTEGRATION_RECEIPT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class AuthenticatedIntegrationClient:
    client_id: int
    name: str
    scopes: frozenset[str]
    key_id: str
    rate_limit_per_minute: int
    scope_type: str
    tenant_id: int | None
    is_legacy: bool = False

    @property
    def is_platform(self) -> bool:
        return self.scope_type == "platform"


@dataclass(frozen=True)
class IntegrationIdempotencyBegin:
    kind: str
    row: IntegrationRequestLog | None = None
    response_json: dict[str, Any] | None = None
    error_code: str | None = None


def _parse_scopes(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _authenticated_scope(
    db: Session,
    *,
    client: IntegrationClient,
) -> IntegrationClientScope:
    scope = db.get(IntegrationClientScope, int(client.id))
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_client_scope_required",
        )
    if scope.scope_type == "tenant" and scope.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_client_tenant_scope_invalid",
        )
    if scope.scope_type == "platform" and scope.tenant_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_client_platform_scope_invalid",
        )
    if scope.scope_type not in {"tenant", "platform"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_client_scope_invalid",
        )
    return scope


def authenticate_integration_client(
    db: Session,
    *,
    x_client_key_id: str | None,
    x_client_key: str | None,
    x_api_key: str | None,
) -> AuthenticatedIntegrationClient:
    if x_client_key_id and x_client_key:
        client = (
            db.query(IntegrationClient)
            .filter(
                IntegrationClient.key_id == x_client_key_id,
                IntegrationClient.is_active.is_(True),
            )
            .first()
        )
        if client is None or not verify_secret(x_client_key, client.secret_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid integration credentials",
            )
        scope = _authenticated_scope(db, client=client)
        client.last_used_at = utc_now()
        db.flush()
        return AuthenticatedIntegrationClient(
            client_id=int(client.id),
            name=client.name,
            scopes=_parse_scopes(client.scopes_csv),
            key_id=client.key_id,
            rate_limit_per_minute=int(client.rate_limit_per_minute),
            scope_type=scope.scope_type,
            tenant_id=int(scope.tenant_id) if scope.tenant_id is not None else None,
            is_legacy=False,
        )

    if x_api_key:
        # R15 closes the environment-wide credential path. It has no durable
        # Tenant or Platform principal and therefore cannot be authorized safely.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="legacy_integration_api_key_disabled",
        )

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Integration endpoint requires an explicitly scoped client",
    )


def require_scope(client: AuthenticatedIntegrationClient, scope: str) -> None:
    if scope not in client.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Integration scope not allowed",
        )


def require_principal_scope(
    client: AuthenticatedIntegrationClient,
    *,
    tenant_scope: str,
    platform_scope: str,
) -> None:
    require_scope(
        client,
        platform_scope if client.is_platform else tenant_scope,
    )


def enforce_rate_limit(
    db: Session,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
) -> None:
    if client.rate_limit_per_minute <= 0:
        return
    window_start = utc_now() - timedelta(minutes=1)
    count = (
        db.query(IntegrationRequestLog.id)
        .filter(
            IntegrationRequestLog.endpoint == endpoint,
            IntegrationRequestLog.created_at >= window_start,
            IntegrationRequestLog.client_id == client.client_id,
        )
        .count()
    )
    if count >= client.rate_limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Integration rate limit exceeded",
        )


def stable_request_hash(payload: dict) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def error_code_from_status(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        429: "rate_limited",
        503: "unavailable",
    }
    return mapping.get(status_code, "error")


def _integration_log_query(
    db: Session,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    idempotency_key: str,
):
    return db.query(IntegrationRequestLog).filter(
        IntegrationRequestLog.client_id == client.client_id,
        IntegrationRequestLog.endpoint == endpoint,
        IntegrationRequestLog.idempotency_key == idempotency_key,
    )


def _decode_response_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else {"data": data}


def _classify_idempotency_row(
    row: IntegrationRequestLog,
    request_hash: str,
) -> IntegrationIdempotencyBegin:
    if row.request_hash and row.request_hash != request_hash:
        return IntegrationIdempotencyBegin(
            kind="conflict",
            row=row,
            error_code="idempotency_key_reused_with_different_payload",
        )
    response_payload = _decode_response_json(row.response_json)
    if response_payload is not None:
        return IntegrationIdempotencyBegin(
            kind="replay",
            row=row,
            response_json=response_payload,
        )
    if row.status_code is None:
        return IntegrationIdempotencyBegin(
            kind="processing",
            row=row,
            error_code="request_processing",
        )
    return IntegrationIdempotencyBegin(
        kind="failed",
        row=row,
        error_code=row.error_code or "request_failed",
    )


def _purpose_for_endpoint(endpoint: str) -> str:
    return {
        "integration.profile": "human_support_profile",
        "integration.task": "human_support_task",
    }.get(endpoint, "integration_control")


def _safe_response_payload(
    endpoint: str,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist only a bounded technical replay receipt.

    Profile data is deliberately excluded. Task idempotency preserves the small
    customer-safe response needed by callers without copying Customer or Case
    payloads into an untracked free-text log.
    """

    if endpoint == "integration.task":
        allowed = {
            "ok",
            "case_ref",
            "status",
            "message",
            "error_code",
            "detail",
            "retry_after_ms",
        }
    elif endpoint == "integration.profile":
        allowed = {"ok", "found", "channel", "error_code", "detail"}
    else:
        allowed = {"ok", "status", "error_code", "detail"}
    safe: dict[str, Any] = {"schema": INTEGRATION_RECEIPT_SCHEMA}
    for key in allowed:
        value = response_payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                safe[key] = value
    return safe


def _ensure_log_envelope(
    db: Session,
    *,
    row: IntegrationRequestLog,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    target_tenant_id: int | None,
) -> IntegrationRequestLogEnvelope:
    envelope = db.get(IntegrationRequestLogEnvelope, int(row.id))
    if envelope is None:
        envelope = IntegrationRequestLogEnvelope(
            log_id=int(row.id),
            client_id=client.client_id,
            principal_scope_type=client.scope_type,
            tenant_id=target_tenant_id,
            purpose=_purpose_for_endpoint(endpoint),
            response_schema=INTEGRATION_RECEIPT_SCHEMA,
            expires_at=utc_now()
            + timedelta(days=INTEGRATION_RECEIPT_RETENTION_DAYS),
        )
        db.add(envelope)
    else:
        if (
            envelope.client_id != client.client_id
            or envelope.principal_scope_type != client.scope_type
            or envelope.tenant_id != target_tenant_id
            or envelope.purpose != _purpose_for_endpoint(endpoint)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="integration_log_scope_conflict",
            )
        envelope.updated_at = utc_now()
    db.flush()
    return envelope


def begin_integration_idempotency(
    db: Session,
    *,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    method: str,
    idempotency_key: str,
    request_hash: str,
    target_tenant_id: int,
) -> IntegrationIdempotencyBegin:
    existing = (
        _integration_log_query(db, client, endpoint, idempotency_key)
        .with_for_update()
        .first()
    )
    if existing is not None:
        _ensure_log_envelope(
            db,
            row=existing,
            client=client,
            endpoint=endpoint,
            target_tenant_id=target_tenant_id,
        )
        return _classify_idempotency_row(existing, request_hash)

    row = IntegrationRequestLog(
        client_id=client.client_id,
        endpoint=endpoint,
        method=method,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status_code=None,
        error_code=None,
        response_json=None,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = (
            _integration_log_query(db, client, endpoint, idempotency_key)
            .with_for_update()
            .first()
        )
        if existing is None:
            raise
        _ensure_log_envelope(
            db,
            row=existing,
            client=client,
            endpoint=endpoint,
            target_tenant_id=target_tenant_id,
        )
        return _classify_idempotency_row(existing, request_hash)
    _ensure_log_envelope(
        db,
        row=row,
        client=client,
        endpoint=endpoint,
        target_tenant_id=target_tenant_id,
    )
    return IntegrationIdempotencyBegin(kind="owner", row=row)


def record_integration_response(
    db: Session,
    *,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    method: str,
    idempotency_key: str | None,
    request_hash: str | None,
    status_code: int,
    response_payload: dict,
    target_tenant_id: int,
    error_code: str | None = None,
) -> None:
    safe_payload = _safe_response_payload(endpoint, response_payload)
    row: IntegrationRequestLog | None = None
    if idempotency_key:
        row = _integration_log_query(
            db,
            client,
            endpoint,
            idempotency_key,
        ).first()
        if row is not None:
            if (
                row.request_hash
                and request_hash
                and row.request_hash != request_hash
                and row.response_json
            ):
                db.flush()
                return
            row.method = method
            row.request_hash = request_hash
            row.status_code = status_code
            row.error_code = error_code
            row.response_json = json.dumps(
                safe_payload,
                ensure_ascii=False,
                sort_keys=True,
            )
            row.created_at = utc_now()
    if row is None:
        row = IntegrationRequestLog(
            client_id=client.client_id,
            endpoint=endpoint,
            method=method,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status_code=status_code,
            error_code=error_code,
            response_json=json.dumps(
                safe_payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        db.add(row)
        db.flush()
    _ensure_log_envelope(
        db,
        row=row,
        client=client,
        endpoint=endpoint,
        target_tenant_id=target_tenant_id,
    )
    db.flush()


__all__ = [
    "AuthenticatedIntegrationClient",
    "IntegrationIdempotencyBegin",
    "authenticate_integration_client",
    "begin_integration_idempotency",
    "enforce_rate_limit",
    "error_code_from_status",
    "record_integration_response",
    "require_principal_scope",
    "require_scope",
    "stable_request_hash",
]
