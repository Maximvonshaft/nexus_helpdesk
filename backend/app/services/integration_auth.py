from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..auth_service import verify_secret
from ..models import IntegrationClient, IntegrationRequestLog
from ..settings import get_settings
from ..utils.time import utc_now

settings = get_settings()


@dataclass
class AuthenticatedIntegrationClient:
    client_id: int | None
    name: str
    scopes: set[str]
    key_id: str
    rate_limit_per_minute: int
    is_legacy: bool = False


def _parse_scopes(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(',') if item.strip()}


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
            .filter(IntegrationClient.key_id == x_client_key_id, IntegrationClient.is_active.is_(True))
            .first()
        )
        if not client or not verify_secret(x_client_key, client.secret_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid integration credentials')
        client.last_used_at = utc_now()
        db.commit()
        return AuthenticatedIntegrationClient(
            client_id=client.id,
            name=client.name,
            scopes=_parse_scopes(client.scopes_csv),
            key_id=client.key_id,
            rate_limit_per_minute=client.rate_limit_per_minute,
            is_legacy=False,
        )

    if settings.allow_legacy_integration_api_key and settings.integration_api_key and x_api_key == settings.integration_api_key:
        return AuthenticatedIntegrationClient(
            client_id=None,
            name='legacy-env-key',
            scopes={'profile.read', 'task.write'},
            key_id='legacy',
            rate_limit_per_minute=settings.integration_default_rate_limit_per_minute,
            is_legacy=True,
        )

    if not settings.integration_api_key and not x_client_key_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Integration endpoint is disabled until an integration client or legacy API key is configured',
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid integration credentials')


def require_scope(client: AuthenticatedIntegrationClient, scope: str) -> None:
    if scope not in client.scopes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Integration scope not allowed')


def enforce_rate_limit(db: Session, client: AuthenticatedIntegrationClient, endpoint: str) -> None:
    if client.rate_limit_per_minute <= 0:
        return
    window_start = utc_now() - timedelta(minutes=1)
    query = db.query(IntegrationRequestLog).filter(IntegrationRequestLog.endpoint == endpoint, IntegrationRequestLog.created_at >= window_start)
    if client.client_id is None:
        query = query.filter(IntegrationRequestLog.client_id.is_(None))
    else:
        query = query.filter(IntegrationRequestLog.client_id == client.client_id)
    if query.count() >= client.rate_limit_per_minute:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Integration rate limit exceeded')


def stable_request_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def error_code_from_status(status_code: int) -> str:
    mapping = {
        400: 'bad_request',
        401: 'unauthorized',
        403: 'forbidden',
        404: 'not_found',
        409: 'conflict',
        429: 'rate_limited',
        503: 'unavailable',
    }
    return mapping.get(status_code, 'error')


def get_idempotent_response(db: Session, client: AuthenticatedIntegrationClient, endpoint: str, idempotency_key: str, request_hash: str):
    log = db.query(IntegrationRequestLog).filter(
        IntegrationRequestLog.client_id == client.client_id,
        IntegrationRequestLog.endpoint == endpoint,
        IntegrationRequestLog.idempotency_key == idempotency_key,
    ).first()
    if not log:
        return None
    if log.request_hash and log.request_hash != request_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Idempotency-Key was reused with a different payload')
    if log.response_json:
        return json.loads(log.response_json)
    return None


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
    if idempotency_key:
        existing = db.query(IntegrationRequestLog).filter(
            IntegrationRequestLog.client_id == client.client_id,
            IntegrationRequestLog.endpoint == endpoint,
            IntegrationRequestLog.idempotency_key == idempotency_key,
        ).first()
        if existing:
            existing.method = method
            existing.request_hash = request_hash
            existing.status_code = status_code
            existing.error_code = error_code
            existing.response_json = json.dumps(response_payload, ensure_ascii=False)
            existing.created_at = utc_now()
            db.flush()
            return
    db.add(IntegrationRequestLog(
        client_id=client.client_id,
        endpoint=endpoint,
        method=method,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status_code=status_code,
        error_code=error_code,
        response_json=json.dumps(response_payload, ensure_ascii=False),
    ))
    db.flush()
