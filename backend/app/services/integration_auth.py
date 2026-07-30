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
from ..models import IntegrationClient, IntegrationRequestLog, Tenant
from ..models_integration_scope import IntegrationClientScope
from ..settings import get_settings
from ..utils.time import utc_now

settings = get_settings()
_PLATFORM_SCOPE_MAP = {
    "profile.read": "platform.profile.read",
    "task.write": "platform.task.write",
}


@dataclass(frozen=True)
class AuthenticatedIntegrationClient:
    client_id: int | None
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


def _principal_from_client(
    db: Session,
    *,
    client: IntegrationClient,
) -> AuthenticatedIntegrationClient:
    scope = db.get(IntegrationClientScope, int(client.id))
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_principal_scope_required",
        )
    if scope.scope_type == "tenant":
        tenant = db.get(Tenant, int(scope.tenant_id)) if scope.tenant_id else None
        if tenant is None or not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="integration_principal_tenant_unavailable",
            )
    elif scope.scope_type != "platform" or scope.tenant_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="integration_principal_scope_invalid",
        )
    return AuthenticatedIntegrationClient(
        client_id=client.id,
        name=client.name,
        scopes=_parse_scopes(client.scopes_csv),
        key_id=client.key_id,
        rate_limit_per_minute=client.rate_limit_per_minute,
        scope_type=scope.scope_type,
        tenant_id=scope.tenant_id,
        is_legacy=False,
    )


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
        if not client or not verify_secret(x_client_key, client.secret_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid integration credentials",
            )
        principal = _principal_from_client(db, client=client)
        client.last_used_at = utc_now()
        db.flush()
        return principal

    if (
        settings.allow_legacy_integration_api_key
        and settings.integration_api_key
        and x_api_key == settings.integration_api_key
    ):
        # Legacy environment keys are explicit platform principals. They cannot
        # inherit Tenant access and must use platform-specific capabilities.
        return AuthenticatedIntegrationClient(
            client_id=None,
            name="legacy-env-key",
            scopes=frozenset({"platform.profile.read", "platform.task.write"}),
            key_id="legacy",
            rate_limit_per_minute=settings.integration_default_rate_limit_per_minute,
            scope_type="platform",
            tenant_id=None,
            is_legacy=True,
        )

    if not settings.integration_api_key and not x_client_key_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Integration endpoint is disabled until an integration client "
                "or legacy API key is configured"
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid integration credentials",
    )


def require_scope(client: AuthenticatedIntegrationClient, scope: str) -> None:
    required = _PLATFORM_SCOPE_MAP.get(scope, scope) if client.is_platform else scope
    if required not in client.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Integration scope not allowed",
        )


def enforce_rate_limit(
    db: Session,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
) -> None:
    if client.rate_limit_per_minute <= 0:
        return
    window_start = utc_now() - timedelta(minutes=1)
    query = db.query(IntegrationRequestLog).filter(
        IntegrationRequestLog.endpoint == endpoint,
        IntegrationRequestLog.created_at >= window_start,
    )
    if client.client_id is None:
        query = query.filter(IntegrationRequestLog.client_id.is_(None))
    else:
        query = query.filter(IntegrationRequestLog.client_id == client.client_id)
    if query.count() >= client.rate_limit_per_minute:
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
    query = db.query(IntegrationRequestLog).filter(
        IntegrationRequestLog.endpoint == endpoint,
        IntegrationRequestLog.idempotency_key == idempotency_key,
    )
    if client.client_id is None:
        query = query.filter(IntegrationRequestLog.client_id.is_(None))
    else:
        query = query.filter(IntegrationRequestLog.client_id == client.client_id)
    return query


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


def begin_integration_idempotency(
    db: Session,
    *,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    method: str,
    idempotency_key: str,
    request_hash: str,
) -> IntegrationIdempotencyBegin:
    """Reserve one client-bound idempotency key before business effects."""

    existing = (
        _integration_log_query(db, client, endpoint, idempotency_key)
        .with_for_update()
        .first()
    )
    if existing is not None:
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
        return _classify_idempotency_row(existing, request_hash)
    return IntegrationIdempotencyBegin(kind="owner", row=row)


def get_idempotent_response(
    db: Session,
    client: AuthenticatedIntegrationClient,
    endpoint: str,
    idempotency_key: str,
    request_hash: str,
):
    log = _integration_log_query(db, client, endpoint, idempotency_key).first()
    if not log:
        return None
    if log.request_hash and log.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was reused with a different payload",
        )
    return _decode_response_json(log.response_json)


def _safe_log_payload(endpoint: str, response_payload: dict) -> dict[str, Any]:
    if endpoint == "integration.profile":
        return {
            "schema": "nexus.integration-profile-log.v2",
            "ok": bool(response_payload.get("ok")),
            "found": bool(response_payload.get("found")),
            "pii_persisted": False,
        }
    allowed = {
        key: response_payload.get(key)
        for key in ("ok", "case_ref", "status", "message", "error_code", "detail")
        if key in response_payload
    }
    return {
        "schema": "nexus.integration-response-log.v2",
        **allowed,
        "pii_persisted": False,
    }


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
    error_code: str | None = None,
) -> None:
    persisted_payload = _safe_log_payload(endpoint, response_payload)
    encoded = json.dumps(
        persisted_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if idempotency_key:
        existing = _integration_log_query(
            db,
            client,
            endpoint,
            idempotency_key,
        ).first()
        if existing:
            if (
                existing.request_hash
                and request_hash
                and existing.request_hash != request_hash
                and existing.response_json
            ):
                db.flush()
                return
            existing.method = method
            existing.request_hash = request_hash
            existing.status_code = status_code
            existing.error_code = error_code
            existing.response_json = encoded
            existing.created_at = utc_now()
            db.flush()
            return
    db.add(
        IntegrationRequestLog(
            client_id=client.client_id,
            endpoint=endpoint,
            method=method,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status_code=status_code,
            error_code=error_code,
            response_json=encoded,
        )
    )
    db.flush()
