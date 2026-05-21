from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_service import ALGORITHM
from ..db import get_db
from ..enums import EventType
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from .deps import get_current_user
from ..services.admin_action_rate_limit import enforce_admin_action_rate_limit
from ..services.permissions import ensure_ticket_visible, resolve_capabilities
from ..services.speedaf.action_service import SpeedafActionDisabled, SpeedafActionService, _enabled as speedaf_action_enabled
from ..services.speedaf.adapter import SpeedafCoreAdapter
from ..services.speedaf.redactor import safe_caller_payload, safe_waybill_payload
from ..services.speedaf.status_map import (
    CANCEL_REASON_LABELS,
    is_cancel_reason_code_allowed,
    is_cancel_terminal_status,
    safe_cancel_reason_label,
    safe_order_status_label,
)
from ..services.tool_governance import record_tool_call
from ..utils.time import utc_now

router = APIRouter(prefix="/api/tickets", tags=["tickets", "speedaf"])

CAP_SPEEDAF_CANCEL_WRITE = "tool:speedaf.order.cancel:write"
CANCEL_PREVIEW_ACTION_KEY = "speedaf.cancel.preview"
CANCEL_CONFIRM_ACTION_KEY = "speedaf.cancel.confirm"
CANCEL_TOKEN_AUDIENCE = "speedaf-cancel"
CANCEL_TOKEN_ISSUER = "nexusdesk"
CANCEL_TOKEN_TTL_MINUTES = 15


class SpeedafCancelPreviewRequest(BaseModel):
    waybillCode: str
    callerID: str
    reasonCode: str


class SpeedafCancelPreviewResponse(BaseModel):
    ok: bool
    cancelAllowed: bool
    currentStatus: str | None = None
    currentStatusLabel: str | None = None
    reason: str | None = None
    reasonLabel: str | None = None
    confirmToken: str | None = None
    expiresInSeconds: int | None = None


class SpeedafCancelConfirmRequest(BaseModel):
    waybillCode: str
    callerID: str
    reasonCode: str
    confirmToken: str


class SpeedafCancelConfirmResponse(BaseModel):
    ok: bool
    status: str
    message: str
    dedupeKey: str | None = None


def _clean(value: str | None, *, limit: int = 160) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _hash_value(value: str | None) -> str:
    cleaned = _clean(value, limit=500).upper()
    return hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()


def _hash_short(value: str | None) -> str:
    return _hash_value(value)[:16]


def _request_id(request: Request | None) -> str | None:
    if request is None:
        return None
    settings = get_settings()
    return getattr(request.state, "request_id", None) or request.headers.get(settings.request_id_header)


def _token_secret() -> str:
    settings = get_settings()
    if settings.jwt_secret_key:
        return settings.jwt_secret_key
    if settings.app_env in {"development", "test", "local"}:
        return "dev-only-speedaf-cancel-confirm-token"
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="cancel_confirm_token_secret_missing")


def _cancel_enabled() -> bool:
    return speedaf_action_enabled("SPEEDAF_CANCEL_ENABLED", False)


def _require_cancel_enabled() -> None:
    if not _cancel_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="speedaf_cancel_disabled")


def _require_cancel_capability(user, db: Session) -> None:
    if CAP_SPEEDAF_CANCEL_WRITE not in resolve_capabilities(user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="speedaf_cancel_requires_capability")


def _validate_reason_code(reason_code: str) -> str:
    cleaned = _clean(reason_code, limit=16).upper()
    if not is_cancel_reason_code_allowed(cleaned):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_cancel_reason_code")
    return cleaned


def _validate_inputs(*, waybill_code: str, caller_id: str, reason_code: str) -> tuple[str, str, str]:
    waybill = _clean(waybill_code, limit=80).upper()
    caller = _clean(caller_id, limit=80)
    reason = _validate_reason_code(reason_code)
    if not waybill:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="waybill_code_required")
    if not caller:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="caller_id_required")
    return waybill, caller, reason


def _load_visible_ticket(db: Session, *, ticket_id: int, user) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket_not_found")
    ensure_ticket_visible(user, ticket, db)
    return ticket


def _build_confirm_token(*, ticket_id: int, waybill_code: str, caller_id: str, reason_code: str, user_id: int) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=CANCEL_TOKEN_TTL_MINUTES)
    payload = {
        "sub": f"speedaf-cancel:{ticket_id}:{_hash_short(waybill_code)}",
        "ticket_id": ticket_id,
        "waybill_hash": _hash_value(waybill_code),
        "caller_hash": _hash_value(caller_id),
        "reason_code": reason_code,
        "uid": user_id,
        "exp": expires_at,
        "iat": now,
        "nbf": now,
        "iss": CANCEL_TOKEN_ISSUER,
        "aud": CANCEL_TOKEN_AUDIENCE,
    }
    return jwt.encode(payload, _token_secret(), algorithm=ALGORITHM)


def _decode_confirm_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _token_secret(),
            algorithms=[ALGORITHM],
            audience=CANCEL_TOKEN_AUDIENCE,
            issuer=CANCEL_TOKEN_ISSUER,
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired_confirm_token")


def _verify_confirm_token(
    *,
    token: str,
    ticket_id: int,
    waybill_code: str,
    caller_id: str,
    reason_code: str,
    user_id: int,
) -> None:
    payload = _decode_confirm_token(token)
    if (
        payload.get("ticket_id") != ticket_id
        or payload.get("waybill_hash") != _hash_value(waybill_code)
        or payload.get("caller_hash") != _hash_value(caller_id)
        or payload.get("reason_code") != reason_code
        or payload.get("uid") != user_id
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confirm_token_mismatch")


def _record_order_query_audit(
    db: Session,
    *,
    waybill_code: str,
    caller_id: str,
    ticket_id: int,
    request_id: str | None,
    status_value: str | None,
    status_label: str | None,
    tool_status: str,
    failure_reason: str | None = None,
) -> None:
    output_payload = {
        "tool_status": tool_status,
        "current_status": status_value,
        "current_status_label": status_label,
        "failure_reason": failure_reason,
    }
    record_tool_call(
        db=db,
        tool_name="speedaf.order.query",
        provider="speedaf_mcp",
        tool_type="read_only",
        input_payload={**safe_waybill_payload(waybill_code), **safe_caller_payload(caller_id)},
        output_payload=output_payload,
        status="success" if tool_status == "success" else "failed",
        error_code=failure_reason,
        ticket_id=ticket_id,
        request_id=request_id,
    )


def _query_order_status(
    db: Session,
    *,
    waybill_code: str,
    caller_id: str,
    ticket_id: int,
    request_id: str | None,
) -> tuple[str | None, str | None]:
    fact = SpeedafCoreAdapter().query_order_tracking_fact(waybill_code=waybill_code, caller_id=caller_id)
    status_value = fact.status
    status_label = fact.status_label or safe_order_status_label(status_value)
    _record_order_query_audit(
        db,
        waybill_code=waybill_code,
        caller_id=caller_id,
        ticket_id=ticket_id,
        request_id=request_id,
        status_value=status_value,
        status_label=status_label,
        tool_status=fact.tool_status or ("success" if fact.ok else "failed"),
        failure_reason=fact.failure_reason,
    )
    if not fact.ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=fact.failure_reason or "speedaf_order_query_failed")
    return status_value, status_label


def _dedupe_key(*, ticket_id: int, waybill_code: str, reason_code: str) -> str:
    return f"speedaf-cancel:ticket:{ticket_id}:waybill:{_hash_short(waybill_code)}:reason:{reason_code}"


def _ensure_sqlite_cancel_idempotency_table(db: Session) -> None:
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS speedaf_cancel_idempotency (
                id INTEGER PRIMARY KEY,
                dedupe_key VARCHAR(255) NOT NULL UNIQUE,
                ticket_id INTEGER NOT NULL,
                waybill_hash VARCHAR(64) NOT NULL,
                reason_code VARCHAR(16) NOT NULL,
                actor_id INTEGER NOT NULL,
                status VARCHAR(40) NOT NULL,
                request_id VARCHAR(160),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
    )
    db.flush()


def _reserve_cancel_idempotency(
    db: Session,
    *,
    dedupe_key: str,
    ticket_id: int,
    waybill_code: str,
    reason_code: str,
    actor_id: int,
    request_id: str | None,
) -> None:
    _ensure_sqlite_cancel_idempotency_table(db)
    now = utc_now()
    try:
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO speedaf_cancel_idempotency
                        (dedupe_key, ticket_id, waybill_hash, reason_code, actor_id, status, request_id, created_at, updated_at)
                    VALUES
                        (:dedupe_key, :ticket_id, :waybill_hash, :reason_code, :actor_id, 'processing', :request_id, :now, :now)
                    """
                ),
                {
                    "dedupe_key": dedupe_key,
                    "ticket_id": ticket_id,
                    "waybill_hash": _hash_value(waybill_code),
                    "reason_code": reason_code,
                    "actor_id": actor_id,
                    "request_id": request_id,
                    "now": now,
                },
            )
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="speedaf_cancel_already_requested")


def _update_cancel_idempotency_status(db: Session, *, dedupe_key: str, status_value: str) -> None:
    db.execute(
        text(
            """
            UPDATE speedaf_cancel_idempotency
            SET status = :status, updated_at = :now
            WHERE dedupe_key = :dedupe_key
            """
        ),
        {"status": status_value, "now": utc_now(), "dedupe_key": dedupe_key},
    )


def _append_cancel_event(
    db: Session,
    *,
    ticket_id: int,
    actor_id: int,
    waybill_code: str,
    reason_code: str,
    dedupe_key: str,
    safe_payload: dict[str, Any],
) -> None:
    db.add(
        TicketEvent(
            ticket_id=ticket_id,
            actor_id=actor_id,
            event_type=EventType.field_updated,
            field_name="speedaf_cancel",
            new_value="cancel_requested",
            note="Speedaf cancel request submitted; Nexus ticket remains open for human confirmation.",
            payload_json=json.dumps(
                {
                    "dedupe_key": dedupe_key,
                    "reason_code": reason_code,
                    "reason_label": safe_cancel_reason_label(reason_code),
                    **safe_waybill_payload(waybill_code),
                    "speedaf_safe_payload": safe_payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        )
    )


@router.post("/{ticket_id}/speedaf/cancel-preview", response_model=SpeedafCancelPreviewResponse)
def speedaf_cancel_preview(
    ticket_id: int,
    payload: SpeedafCancelPreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    _require_cancel_enabled()
    _require_cancel_capability(current_user, db)
    enforce_admin_action_rate_limit(
        db,
        actor_id=current_user.id,
        action_key=CANCEL_PREVIEW_ACTION_KEY,
        max_requests=get_settings().admin_action_rate_limit_single_max,
        request_id=_request_id(request),
    )
    _load_visible_ticket(db, ticket_id=ticket_id, user=current_user)
    waybill, caller, reason = _validate_inputs(
        waybill_code=payload.waybillCode,
        caller_id=payload.callerID,
        reason_code=payload.reasonCode,
    )
    status_value, status_label = _query_order_status(
        db,
        waybill_code=waybill,
        caller_id=caller,
        ticket_id=ticket_id,
        request_id=_request_id(request),
    )
    if is_cancel_terminal_status(status_value, status_label):
        db.commit()
        return SpeedafCancelPreviewResponse(
            ok=True,
            cancelAllowed=False,
            currentStatus=status_value,
            currentStatusLabel=status_label,
            reason="terminal_status_blocks_cancel",
            reasonLabel=safe_cancel_reason_label(reason),
            confirmToken=None,
            expiresInSeconds=None,
        )
    token = _build_confirm_token(
        ticket_id=ticket_id,
        waybill_code=waybill,
        caller_id=caller,
        reason_code=reason,
        user_id=current_user.id,
    )
    db.commit()
    return SpeedafCancelPreviewResponse(
        ok=True,
        cancelAllowed=True,
        currentStatus=status_value,
        currentStatusLabel=status_label,
        reason=None,
        reasonLabel=safe_cancel_reason_label(reason),
        confirmToken=token,
        expiresInSeconds=CANCEL_TOKEN_TTL_MINUTES * 60,
    )


@router.post("/{ticket_id}/speedaf/cancel", response_model=SpeedafCancelConfirmResponse)
def speedaf_cancel_confirm(
    ticket_id: int,
    payload: SpeedafCancelConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    _require_cancel_enabled()
    _require_cancel_capability(current_user, db)
    enforce_admin_action_rate_limit(
        db,
        actor_id=current_user.id,
        action_key=CANCEL_CONFIRM_ACTION_KEY,
        max_requests=get_settings().admin_action_rate_limit_batch_max,
        request_id=_request_id(request),
    )
    _load_visible_ticket(db, ticket_id=ticket_id, user=current_user)
    waybill, caller, reason = _validate_inputs(
        waybill_code=payload.waybillCode,
        caller_id=payload.callerID,
        reason_code=payload.reasonCode,
    )
    _verify_confirm_token(
        token=payload.confirmToken,
        ticket_id=ticket_id,
        waybill_code=waybill,
        caller_id=caller,
        reason_code=reason,
        user_id=current_user.id,
    )
    status_value, status_label = _query_order_status(
        db,
        waybill_code=waybill,
        caller_id=caller,
        ticket_id=ticket_id,
        request_id=_request_id(request),
    )
    if is_cancel_terminal_status(status_value, status_label):
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="terminal_status_blocks_cancel")

    dedupe_key = _dedupe_key(ticket_id=ticket_id, waybill_code=waybill, reason_code=reason)
    _reserve_cancel_idempotency(
        db,
        dedupe_key=dedupe_key,
        ticket_id=ticket_id,
        waybill_code=waybill,
        reason_code=reason,
        actor_id=current_user.id,
        request_id=_request_id(request),
    )
    service = SpeedafActionService(ticket_id=ticket_id, request_id=dedupe_key)
    try:
        result = service.cancel_order(waybill_code=waybill, reason_code=reason, caller_id=caller)
    except SpeedafActionDisabled:
        _update_cancel_idempotency_status(db, dedupe_key=dedupe_key, status_value="failed")
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="speedaf_cancel_disabled")
    if not result.ok:
        _update_cancel_idempotency_status(db, dedupe_key=dedupe_key, status_value="failed")
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error_code or "speedaf_cancel_failed")
    _update_cancel_idempotency_status(db, dedupe_key=dedupe_key, status_value="success")
    _append_cancel_event(
        db,
        ticket_id=ticket_id,
        actor_id=current_user.id,
        waybill_code=waybill,
        reason_code=reason,
        dedupe_key=dedupe_key,
        safe_payload=result.safe_payload,
    )
    db.commit()
    return SpeedafCancelConfirmResponse(
        ok=True,
        status="submitted",
        message="Speedaf cancel request submitted. Nexus ticket remains open for human confirmation.",
        dedupeKey=dedupe_key,
    )
