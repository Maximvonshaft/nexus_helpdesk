from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..models import IntegrationClient, IntegrationRequestLog
from ..services.permissions import ensure_can_manage_runtime
from .deps import get_current_user


router = APIRouter(prefix="/api/admin/integration-observability", tags=["admin-integration-observability"])

_SENSITIVE_KEYS = {"password", "secret", "token", "authorization", "credential", "api_key", "apikey"}
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _split_scopes(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).strip().lower()
            redacted[key] = "[redacted]" if normalized in _SENSITIVE_KEYS else _redact_json(child)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value[:50]]
    return value


def _safe_response_preview(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return str(raw)[:500]
    return _redact_json(parsed)


def _find_request_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("request_id", "requestId", "x_request_id", "X-Request-Id"):
            found = value.get(key)
            if found:
                return str(found)
        for child in value.values():
            found = _find_request_id(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_request_id(child)
            if found:
                return found
    return None


def _is_retryable(*, status_code: int | None, error_code: str | None) -> bool:
    if status_code in _RETRYABLE_STATUS_CODES:
        return True
    normalized_error = (error_code or "").lower()
    if "idempotency_key_reused" in normalized_error:
        return False
    return any(token in normalized_error for token in ("timeout", "retryable", "temporarily", "rate_limited"))


def _status_bucket(*, status_code: int | None, error_code: str | None, retryable: bool) -> str:
    if retryable:
        return "retryable"
    if status_code is None and not error_code:
        return "pending"
    if status_code and 200 <= status_code < 400 and not error_code:
        return "success"
    if status_code == 409 or "idempotency_key_reused" in (error_code or ""):
        return "conflict"
    return "failed"


def _serialize_log(row: IntegrationRequestLog) -> dict[str, Any]:
    client = row.client
    preview = _safe_response_preview(row.response_json)
    retryable = _is_retryable(status_code=row.status_code, error_code=row.error_code)
    return {
        "id": row.id,
        "client_id": row.client_id,
        "client_name": client.name if client else None,
        "client_key_id": client.key_id if client else None,
        "scopes": _split_scopes(client.scopes_csv if client else None),
        "endpoint": row.endpoint,
        "method": row.method,
        "idempotency_key": row.idempotency_key,
        "request_hash": row.request_hash,
        "status_code": row.status_code,
        "error_code": row.error_code,
        "request_id": _find_request_id(preview),
        "retryable": retryable,
        "status_bucket": _status_bucket(status_code=row.status_code, error_code=row.error_code, retryable=retryable),
        "response_preview": preview,
        "created_at": _iso(row.created_at),
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    retryable_count = 0
    error_codes: set[str] = set()
    endpoints: set[str] = set()
    clients: set[str] = set()
    missing_request_id = 0
    for item in items:
        by_status[item["status_bucket"]] = by_status.get(item["status_bucket"], 0) + 1
        retryable_count += 1 if item["retryable"] else 0
        if item.get("error_code"):
            error_codes.add(str(item["error_code"]))
        if item.get("endpoint"):
            endpoints.add(str(item["endpoint"]))
        if item.get("client_name") or item.get("client_key_id"):
            clients.add(str(item.get("client_name") or item.get("client_key_id")))
        if not item.get("request_id"):
            missing_request_id += 1
    return {
        "total": len(items),
        "by_status": by_status,
        "retryable": retryable_count,
        "error_codes": sorted(error_codes),
        "endpoints": sorted(endpoints),
        "clients": sorted(clients),
        "missing_request_id": missing_request_id,
    }


@router.get("/requests")
def list_integration_request_logs(
    limit: int = Query(default=50, ge=1, le=100),
    q: str | None = Query(default=None, max_length=160),
    endpoint: str | None = Query(default=None, max_length=160),
    client_id: int | None = None,
    status_bucket: str | None = Query(default=None, max_length=40),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    ensure_can_manage_runtime(current_user, db)
    query = (
        db.query(IntegrationRequestLog)
        .outerjoin(IntegrationClient)
        .options(joinedload(IntegrationRequestLog.client))
        .order_by(IntegrationRequestLog.created_at.desc(), IntegrationRequestLog.id.desc())
    )
    if client_id is not None:
        query = query.filter(IntegrationRequestLog.client_id == client_id)
    if endpoint:
        query = query.filter(IntegrationRequestLog.endpoint == endpoint.strip())
    if q and q.strip():
        needle = q.strip()
        query = query.filter(or_(
            IntegrationRequestLog.endpoint.contains(needle),
            IntegrationRequestLog.method.contains(needle),
            IntegrationRequestLog.idempotency_key.contains(needle),
            IntegrationRequestLog.request_hash.contains(needle),
            IntegrationRequestLog.error_code.contains(needle),
            IntegrationRequestLog.response_json.contains(needle),
            IntegrationClient.name.contains(needle),
            IntegrationClient.key_id.contains(needle),
        ))

    fetched = query.limit(min(limit * 4, 300)).all()
    items = [_serialize_log(row) for row in fetched]
    normalized_status = (status_bucket or "all").strip().lower()
    if normalized_status and normalized_status != "all":
        items = [item for item in items if item["status_bucket"] == normalized_status]

    visible = items[:limit]
    return {
        "items": visible,
        "summary": _summary(items),
        "total": len(items),
        "has_more": len(items) > limit,
        "filters": {
            "q": q or None,
            "endpoint": endpoint or None,
            "client_id": client_id,
            "status_bucket": normalized_status or "all",
            "limit": limit,
        },
    }
