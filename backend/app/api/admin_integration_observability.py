from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Query as SQLAlchemyQuery, Session, joinedload

from ..db import get_db
from ..models import IntegrationClient, IntegrationRequestLog
from ..services.audit_service import log_admin_audit
from ..services.permissions import ensure_can_manage_runtime
from ..settings import get_settings
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/integration-observability", tags=["admin-integration-observability"])
settings = get_settings()

SENSITIVE_RESPONSE_KEYS = {
    "api_key",
    "authorization",
    "contact",
    "contact_id",
    "email",
    "key",
    "name",
    "password",
    "phone",
    "secret",
    "token",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _scope_for_endpoint(endpoint: str) -> str:
    mapping = {
        "integration.profile": "profile.read",
        "integration.task": "task.write",
    }
    return mapping.get(endpoint, "unknown")


def _status_family(status_code: int | None) -> str:
    if status_code is None:
        return "processing"
    if status_code < 100:
        return "unknown"
    return f"{status_code // 100}xx"


def _retryable(status_code: int | None, error_code: str | None) -> bool:
    return status_code is None or status_code == 429 or status_code >= 500 or error_code in {"request_processing", "rate_limited", "unavailable"}


def _retryable_condition():
    return or_(
        IntegrationRequestLog.status_code.is_(None),
        IntegrationRequestLog.status_code == 429,
        IntegrationRequestLog.status_code >= 500,
        IntegrationRequestLog.error_code.in_(["request_processing", "rate_limited", "unavailable"]),
    )


def _redact_payload(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            normalized_key = str(key).lower()
            if any(marker in normalized_key for marker in SENSITIVE_RESPONSE_KEYS):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_payload(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item, depth + 1) for item in value[:8]]
    if isinstance(value, str):
        return value[:160]
    return value


def _safe_response_preview(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:240]
    preview = json.dumps(_redact_payload(data), ensure_ascii=False, separators=(",", ":"))
    return preview if len(preview) <= 420 else f"{preview[:417]}..."


def _apply_filters(
    query: SQLAlchemyQuery,
    *,
    status_filter: str,
    client_id: int | None,
    endpoint: str | None,
    error_code: str | None,
    has_idempotency_key: bool | None,
    retryable: bool | None,
    q: str | None,
) -> SQLAlchemyQuery:
    status_value = (status_filter or "all").strip().lower()
    if status_value in {"success", "ok", "2xx"}:
        query = query.filter(IntegrationRequestLog.status_code >= 200, IntegrationRequestLog.status_code < 300)
    elif status_value in {"error", "failed"}:
        query = query.filter(IntegrationRequestLog.status_code >= 400)
    elif status_value == "4xx":
        query = query.filter(IntegrationRequestLog.status_code >= 400, IntegrationRequestLog.status_code < 500)
    elif status_value == "5xx":
        query = query.filter(IntegrationRequestLog.status_code >= 500, IntegrationRequestLog.status_code < 600)
    elif status_value == "processing":
        query = query.filter(IntegrationRequestLog.status_code.is_(None))
    elif status_value == "retryable":
        query = query.filter(_retryable_condition())
    elif status_value not in {"", "all"} and status_value.isdigit():
        query = query.filter(IntegrationRequestLog.status_code == int(status_value))

    if client_id is not None:
        if client_id == 0:
            query = query.filter(IntegrationRequestLog.client_id.is_(None))
        else:
            query = query.filter(IntegrationRequestLog.client_id == client_id)
    if endpoint:
        query = query.filter(IntegrationRequestLog.endpoint.ilike(f"%{endpoint.strip()}%"))
    if error_code:
        query = query.filter(IntegrationRequestLog.error_code == error_code.strip())
    if has_idempotency_key is True:
        query = query.filter(IntegrationRequestLog.idempotency_key.is_not(None))
    elif has_idempotency_key is False:
        query = query.filter(IntegrationRequestLog.idempotency_key.is_(None))
    if retryable is True:
        query = query.filter(_retryable_condition())
    elif retryable is False:
        query = query.filter(~_retryable_condition())
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(
            IntegrationRequestLog.endpoint.ilike(term),
            IntegrationRequestLog.error_code.ilike(term),
            IntegrationRequestLog.idempotency_key.ilike(term),
            IntegrationRequestLog.request_id.ilike(term),
            IntegrationClient.name.ilike(term),
            IntegrationClient.key_id.ilike(term),
        ))
    return query


def _filtered_log_query(
    db: Session,
    *,
    status_filter: str,
    client_id: int | None,
    endpoint: str | None,
    error_code: str | None,
    has_idempotency_key: bool | None,
    retryable: bool | None,
    q: str | None,
) -> SQLAlchemyQuery:
    query = db.query(IntegrationRequestLog).outerjoin(IntegrationClient, IntegrationRequestLog.client_id == IntegrationClient.id)
    return _apply_filters(
        query,
        status_filter=status_filter,
        client_id=client_id,
        endpoint=endpoint,
        error_code=error_code,
        has_idempotency_key=has_idempotency_key,
        retryable=retryable,
        q=q,
    )


def _summary(query: SQLAlchemyQuery) -> dict[str, Any]:
    total = query.order_by(None).count()
    success_count = query.filter(IntegrationRequestLog.status_code >= 200, IntegrationRequestLog.status_code < 300).order_by(None).count()
    error_count = query.filter(IntegrationRequestLog.status_code >= 400).order_by(None).count()
    retryable_count = query.filter(_retryable_condition()).order_by(None).count()
    processing_count = query.filter(IntegrationRequestLog.status_code.is_(None)).order_by(None).count()
    idempotency_conflict_count = query.filter(IntegrationRequestLog.error_code == "idempotency_key_reused_with_different_payload").order_by(None).count()
    rate_limited_count = query.filter(or_(IntegrationRequestLog.status_code == 429, IntegrationRequestLog.error_code == "rate_limited")).order_by(None).count()
    unique_clients = query.with_entities(IntegrationRequestLog.client_id).distinct().order_by(None).all()
    last_row = query.order_by(IntegrationRequestLog.created_at.desc()).first()
    return {
        "total": total,
        "success_count": success_count,
        "error_count": error_count,
        "retryable_count": retryable_count,
        "processing_count": processing_count,
        "idempotency_conflict_count": idempotency_conflict_count,
        "rate_limited_count": rate_limited_count,
        "unique_clients": len(unique_clients),
        "last_created_at": _iso(last_row.created_at if last_row else None),
    }


def _usage(query: SQLAlchemyQuery) -> list[dict[str, Any]]:
    success_expr = case((IntegrationRequestLog.status_code.between(200, 299), 1), else_=0)
    error_expr = case((IntegrationRequestLog.status_code >= 400, 1), else_=0)
    retryable_expr = case((_retryable_condition(), 1), else_=0)
    rows = (
        query.with_entities(
            IntegrationRequestLog.endpoint,
            IntegrationRequestLog.method,
            func.count(IntegrationRequestLog.id),
            func.sum(success_expr),
            func.sum(error_expr),
            func.sum(retryable_expr),
            func.max(IntegrationRequestLog.created_at),
        )
        .group_by(IntegrationRequestLog.endpoint, IntegrationRequestLog.method)
        .order_by(func.count(IntegrationRequestLog.id).desc(), IntegrationRequestLog.endpoint.asc())
        .limit(20)
        .all()
    )
    return [
        {
            "endpoint": endpoint,
            "method": method,
            "scope": _scope_for_endpoint(endpoint),
            "count": count,
            "success_count": int(success_count or 0),
            "error_count": int(error_count or 0),
            "retryable_count": int(retryable_count or 0),
            "avg_latency_ms": None,
            "latency_available": False,
            "last_seen_at": _iso(last_seen_at),
        }
        for endpoint, method, count, success_count, error_count, retryable_count, last_seen_at in rows
    ]


def _client_registry(db: Session) -> list[dict[str, Any]]:
    success_expr = case((IntegrationRequestLog.status_code.between(200, 299), 1), else_=0)
    error_expr = case((IntegrationRequestLog.status_code >= 400, 1), else_=0)
    retryable_expr = case((_retryable_condition(), 1), else_=0)
    count_rows = (
        db.query(
            IntegrationRequestLog.client_id,
            func.count(IntegrationRequestLog.id),
            func.sum(success_expr),
            func.sum(error_expr),
            func.sum(retryable_expr),
            func.max(IntegrationRequestLog.created_at),
        )
        .group_by(IntegrationRequestLog.client_id)
        .all()
    )
    counts = {
        client_id: {
            "total": total,
            "success_count": int(success_count or 0),
            "error_count": int(error_count or 0),
            "retryable_count": int(retryable_count or 0),
            "last_log_created_at": _iso(last_log_created_at),
        }
        for client_id, total, success_count, error_count, retryable_count, last_log_created_at in count_rows
    }
    rows = db.query(IntegrationClient).order_by(IntegrationClient.name.asc()).all()
    clients = []
    for row in rows:
        metrics = counts.get(row.id, {})
        clients.append({
            "id": row.id,
            "name": row.name,
            "key_id": row.key_id,
            "scopes": [scope.strip() for scope in (row.scopes_csv or "").split(",") if scope.strip()],
            "rate_limit_per_minute": row.rate_limit_per_minute,
            "is_active": row.is_active,
            "last_used_at": _iso(row.last_used_at),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "request_count": metrics.get("total", 0),
            "success_count": metrics.get("success_count", 0),
            "error_count": metrics.get("error_count", 0),
            "retryable_count": metrics.get("retryable_count", 0),
            "last_log_created_at": metrics.get("last_log_created_at"),
        })
    if None in counts:
        legacy = counts[None]
        clients.append({
            "id": 0,
            "name": "legacy-env-key",
            "key_id": "legacy",
            "scopes": ["profile.read", "task.write"],
            "rate_limit_per_minute": settings.integration_default_rate_limit_per_minute,
            "is_active": settings.allow_legacy_integration_api_key,
            "last_used_at": None,
            "created_at": None,
            "updated_at": None,
            "request_count": legacy.get("total", 0),
            "success_count": legacy.get("success_count", 0),
            "error_count": legacy.get("error_count", 0),
            "retryable_count": legacy.get("retryable_count", 0),
            "last_log_created_at": legacy.get("last_log_created_at"),
        })
    return clients


def _serialize_log(row: IntegrationRequestLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_id": row.client_id if row.client_id is not None else 0,
        "client_name": row.client.name if row.client else "legacy-env-key",
        "endpoint": row.endpoint,
        "method": row.method,
        "scope": _scope_for_endpoint(row.endpoint),
        "status_code": row.status_code,
        "status_family": _status_family(row.status_code),
        "error_code": row.error_code,
        "idempotency_key": row.idempotency_key,
        "idempotency_key_present": bool(row.idempotency_key),
        "request_hash_present": bool(row.request_hash),
        "request_id": row.request_id,
        "request_id_available": bool(row.request_id),
        "retryable": _retryable(row.status_code, row.error_code),
        "latency_ms": None,
        "latency_available": False,
        "response_preview": _safe_response_preview(row.response_json),
        "created_at": _iso(row.created_at),
    }


def _contracts() -> list[dict[str, Any]]:
    return [
        {
            "method": "GET",
            "path": "/api/v1/integration/profile/{contact_id}",
            "scope": "profile.read",
            "idempotency_required": False,
            "request_id_header": settings.request_id_header,
        },
        {
            "method": "POST",
            "path": "/api/v1/integration/task",
            "scope": "task.write",
            "idempotency_required": settings.integration_require_idempotency_key,
            "request_id_header": settings.request_id_header,
        },
        {
            "method": "GET",
            "path": "/api/admin/integration-observability",
            "scope": "runtime.manage",
            "idempotency_required": False,
            "request_id_header": settings.request_id_header,
        },
        {
            "method": "GET",
            "path": "/api/admin/integration-clients",
            "scope": "runtime.manage",
            "idempotency_required": False,
            "request_id_header": settings.request_id_header,
        },
    ]


def _filter_payload(
    *,
    status_filter: str,
    client_id: int | None,
    endpoint: str | None,
    error_code: str | None,
    has_idempotency_key: bool | None,
    retryable: bool | None,
    q: str | None,
    limit: int,
) -> dict[str, Any]:
    return {
        "status": status_filter,
        "client_id": client_id,
        "endpoint": endpoint,
        "error_code": error_code,
        "has_idempotency_key": has_idempotency_key,
        "retryable": retryable,
        "q": q,
        "limit": limit,
    }


@router.get("")
def get_integration_observability(
    status_filter: str = Query(default="all", alias="status"),
    client_id: int | None = Query(default=None),
    endpoint: str | None = Query(default=None, max_length=160),
    error_code: str | None = Query(default=None, max_length=120),
    has_idempotency_key: bool | None = Query(default=None),
    retryable: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    query = _filtered_log_query(
        db,
        status_filter=status_filter,
        client_id=client_id,
        endpoint=endpoint,
        error_code=error_code,
        has_idempotency_key=has_idempotency_key,
        retryable=retryable,
        q=q,
    )
    rows = (
        query.options(joinedload(IntegrationRequestLog.client))
        .order_by(IntegrationRequestLog.created_at.desc(), IntegrationRequestLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "ok": True,
        "filters": _filter_payload(
            status_filter=status_filter,
            client_id=client_id,
            endpoint=endpoint,
            error_code=error_code,
            has_idempotency_key=has_idempotency_key,
            retryable=retryable,
            q=q,
            limit=limit,
        ),
        "summary": _summary(query),
        "usage": _usage(query),
        "clients": _client_registry(db),
        "items": [_serialize_log(row) for row in rows],
        "contracts": _contracts(),
        "capabilities": {
            "readonly": True,
            "client_registration_api": False,
            "request_id_persisted": True,
            "latency_available": False,
            "csv_export": True,
            "csv_export_audit_action": "integration_observability.export_csv",
        },
    }


@router.get("/export.csv", response_class=PlainTextResponse)
def export_integration_observability_csv(
    status_filter: str = Query(default="all", alias="status"),
    client_id: int | None = Query(default=None),
    endpoint: str | None = Query(default=None, max_length=160),
    error_code: str | None = Query(default=None, max_length=120),
    has_idempotency_key: bool | None = Query(default=None),
    retryable: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_runtime(current_user, db)
    query = _filtered_log_query(
        db,
        status_filter=status_filter,
        client_id=client_id,
        endpoint=endpoint,
        error_code=error_code,
        has_idempotency_key=has_idempotency_key,
        retryable=retryable,
        q=q,
    )
    rows = (
        query.options(joinedload(IntegrationRequestLog.client))
        .order_by(IntegrationRequestLog.created_at.desc(), IntegrationRequestLog.id.desc())
        .limit(limit)
        .all()
    )
    payload = [_serialize_log(row) for row in rows]
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "client_id",
            "client_name",
            "endpoint",
            "method",
            "scope",
            "status_code",
            "status_family",
            "error_code",
            "idempotency_key_present",
            "request_hash_present",
            "request_id",
            "retryable",
            "created_at",
        ],
    )
    writer.writeheader()
    for item in payload:
        writer.writerow({key: item.get(key) for key in writer.fieldnames})

    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="integration_observability.export_csv",
        target_type="integration_request_log",
        old_value=None,
        new_value={
            "filters": _filter_payload(
                status_filter=status_filter,
                client_id=client_id,
                endpoint=endpoint,
                error_code=error_code,
                has_idempotency_key=has_idempotency_key,
                retryable=retryable,
                q=q,
                limit=limit,
            ),
            "row_count": len(rows),
        },
    )
    db.commit()
    return PlainTextResponse(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="integration-observability.csv"'},
    )
