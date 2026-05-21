from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.deps import get_current_user
from ..db import get_db
from ..enums import EventType, UserRole
from ..models import Ticket, TicketEvent
from ..settings import get_settings
from ..services.admin_action_rate_limit import enforce_admin_action_rate_limit
from ..services.background_jobs import enqueue_speedaf_address_update_job, enqueue_speedaf_work_order_create_job
from ..services.permissions import ensure_ticket_visible, resolve_capabilities
from ..services.speedaf.redactor import safe_caller_payload, safe_waybill_payload
from ..services.speedaf.status_map import is_auto_work_order_type_allowed
from ..utils.time import utc_now

router = APIRouter(prefix="/api/tickets", tags=["tickets", "speedaf"])

CAP_SPEEDAF_WORK_ORDER_WRITE = "tool:speedaf.work_order.create:write"
CAP_SPEEDAF_ADDRESS_UPDATE_WRITE = "tool:speedaf.order.update_address:write"
WORK_ORDER_ACTION_KEY = "speedaf.work_order.create"
ADDRESS_UPDATE_ACTION_KEY = "speedaf.address_update.submit"


class SpeedafWorkOrderRequest(BaseModel):
    waybillCode: str = Field(min_length=1, max_length=80)
    callerID: str = Field(min_length=1, max_length=80)
    workOrderType: str = Field(default="WT0103-05", max_length=32)
    description: str = Field(min_length=1, max_length=200)


class SpeedafAddressUpdateRequest(BaseModel):
    waybillCode: str = Field(min_length=1, max_length=80)
    callerID: str = Field(min_length=1, max_length=80)
    whatsAppPhone: str = Field(min_length=4, max_length=80)


class SpeedafActionResponse(BaseModel):
    ok: bool
    status: str
    message: str
    jobId: int | None = None
    dedupeKey: str | None = None


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def _require_feature(name: str, detail: str) -> None:
    if not _enabled(name, False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_capability_or_admin(user, db: Session, capability: str, detail: str) -> None:
    if getattr(user, "role", None) == UserRole.admin:
        return
    if capability not in resolve_capabilities(user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _load_visible_ticket(db: Session, *, ticket_id: int, user) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket_not_found")
    ensure_ticket_visible(user, ticket, db)
    return ticket


def _append_event(db: Session, *, ticket_id: int, actor_id: int | None, field_name: str, new_value: str, note: str, payload: dict[str, Any]) -> None:
    db.add(
        TicketEvent(
            ticket_id=ticket_id,
            actor_id=actor_id,
            event_type=EventType.field_updated,
            field_name=field_name,
            new_value=new_value,
            note=note,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            created_at=utc_now(),
        )
    )


def _ensure_sqlite_address_table(db: Session) -> None:
    if db.bind is None or db.bind.dialect.name != "sqlite":
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS speedaf_address_update_idempotency (
            id INTEGER PRIMARY KEY,
            dedupe_key VARCHAR(255) NOT NULL UNIQUE,
            ticket_id INTEGER NOT NULL,
            waybill_hash VARCHAR(64) NOT NULL,
            phone_hash VARCHAR(64) NOT NULL,
            actor_id INTEGER NOT NULL,
            status VARCHAR(40) NOT NULL,
            request_id VARCHAR(160),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """))
    db.flush()


def _address_dedupe_key(*, ticket_id: int, waybill_code: str, whatsapp_phone: str) -> str:
    return f"speedaf-update-address:ticket:{ticket_id}:waybill:{_hash_short(waybill_code)}:phone:{_hash_short(whatsapp_phone)}"


def _reserve_address_update(db: Session, *, dedupe_key: str, ticket_id: int, waybill_code: str, whatsapp_phone: str, actor_id: int, request_id: str | None) -> None:
    _ensure_sqlite_address_table(db)
    now = utc_now()
    try:
        with db.begin_nested():
            db.execute(
                text("""
                    INSERT INTO speedaf_address_update_idempotency
                        (dedupe_key, ticket_id, waybill_hash, phone_hash, actor_id, status, request_id, created_at, updated_at)
                    VALUES
                        (:dedupe_key, :ticket_id, :waybill_hash, :phone_hash, :actor_id, 'queued', :request_id, :now, :now)
                """),
                {
                    "dedupe_key": dedupe_key,
                    "ticket_id": ticket_id,
                    "waybill_hash": _hash_value(waybill_code),
                    "phone_hash": _hash_value(whatsapp_phone),
                    "actor_id": actor_id,
                    "request_id": request_id,
                    "now": now,
                },
            )
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="speedaf_address_update_already_requested")


@router.post("/{ticket_id}/speedaf/work-orders", response_model=SpeedafActionResponse)
def create_speedaf_work_order(ticket_id: int, payload: SpeedafWorkOrderRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    _require_feature("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "speedaf_work_order_create_disabled")
    _require_capability_or_admin(current_user, db, CAP_SPEEDAF_WORK_ORDER_WRITE, "speedaf_work_order_requires_capability")
    enforce_admin_action_rate_limit(db, actor_id=current_user.id, action_key=WORK_ORDER_ACTION_KEY, max_requests=get_settings().admin_action_rate_limit_single_max, request_id=_request_id(request))
    _load_visible_ticket(db, ticket_id=ticket_id, user=current_user)
    work_order_type = _clean(payload.workOrderType, limit=32)
    if not is_auto_work_order_type_allowed(work_order_type):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="speedaf_work_order_type_not_allowed")
    job = enqueue_speedaf_work_order_create_job(
        db,
        ticket_id=ticket_id,
        waybill_code=_clean(payload.waybillCode, limit=80).upper(),
        caller_id=_clean(payload.callerID, limit=80),
        description=_clean(payload.description, limit=200),
        work_order_type=work_order_type,
    )
    _append_event(
        db,
        ticket_id=ticket_id,
        actor_id=current_user.id,
        field_name="speedaf_work_order",
        new_value="queued",
        note="Speedaf delivery follow-up work order queued.",
        payload={"job_id": job.id, "workOrderType": work_order_type, **safe_waybill_payload(payload.waybillCode), **safe_caller_payload(payload.callerID)},
    )
    db.commit()
    return SpeedafActionResponse(ok=True, status="queued", message="Speedaf work order queued.", jobId=job.id, dedupeKey=job.dedupe_key)


@router.post("/{ticket_id}/speedaf/address-update", response_model=SpeedafActionResponse)
def submit_speedaf_address_update(ticket_id: int, payload: SpeedafAddressUpdateRequest, request: Request, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    _require_feature("SPEEDAF_UPDATE_ADDRESS_ENABLED", "speedaf_update_address_disabled")
    _require_capability_or_admin(current_user, db, CAP_SPEEDAF_ADDRESS_UPDATE_WRITE, "speedaf_address_update_requires_capability")
    enforce_admin_action_rate_limit(db, actor_id=current_user.id, action_key=ADDRESS_UPDATE_ACTION_KEY, max_requests=get_settings().admin_action_rate_limit_batch_max, request_id=_request_id(request))
    _load_visible_ticket(db, ticket_id=ticket_id, user=current_user)
    waybill = _clean(payload.waybillCode, limit=80).upper()
    caller = _clean(payload.callerID, limit=80)
    phone = _clean(payload.whatsAppPhone, limit=80)
    dedupe_key = _address_dedupe_key(ticket_id=ticket_id, waybill_code=waybill, whatsapp_phone=phone)
    request_id = _request_id(request)
    _reserve_address_update(db, dedupe_key=dedupe_key, ticket_id=ticket_id, waybill_code=waybill, whatsapp_phone=phone, actor_id=current_user.id, request_id=request_id)
    job = enqueue_speedaf_address_update_job(db, ticket_id=ticket_id, waybill_code=waybill, caller_id=caller, whatsapp_phone=phone, dedupe_key=dedupe_key, request_id=request_id)
    _append_event(
        db,
        ticket_id=ticket_id,
        actor_id=current_user.id,
        field_name="speedaf_address_update",
        new_value="queued",
        note="Speedaf address update confirmation request queued. This does not mean the address has already changed.",
        payload={"job_id": job.id, "dedupe_key": dedupe_key, **safe_waybill_payload(waybill), "whatsapp_phone": {"redacted": True, "suffix": phone[-4:]}},
    )
    db.commit()
    return SpeedafActionResponse(ok=True, status="queued", message="Address update confirmation request queued. This does not mean the address has already changed.", jobId=job.id, dedupeKey=dedupe_key)
