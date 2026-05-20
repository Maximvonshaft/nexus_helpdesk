from __future__ import annotations
from pydantic import BaseModel

import time
import hashlib
from typing import Any
import jwt
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import User, Ticket, TicketEvent, EventType
from .deps import get_current_user
from ..services.permissions import resolve_capabilities
from ..services.speedaf.action_service import SpeedafActionService
from ..services.speedaf.client import SpeedafMcpClient
from ..services.speedaf.status_map import is_terminal_status, safe_order_status_label
from ..auth_service import SECRET_KEY, ALGORITHM
from ..tool_models import ToolCallLog

router = APIRouter(prefix='/api/tickets', tags=['tickets', 'speedaf'])

class PreviewCancelRequest(BaseModel):
    waybill_code: str

class PreviewCancelResponse(BaseModel):
    current_status: str | None
    current_status_label: str | None
    cancel_allowed: bool
    reason: str | None
    confirm_tokens: dict[str, str]

class ConfirmCancelRequest(BaseModel):
    waybill_code: str
    reason_code: str
    confirm_token: str

class ConfirmCancelResponse(BaseModel):
    ok: bool
    status: str
    message: str | None


def generate_confirm_token(ticket_id: int, waybill_code: str, reason_code: str, user_id: int) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=15)
    payload = {
        "sub": f"speedaf-cancel:{ticket_id}:{waybill_code}",
        "ticket_id": ticket_id,
        "waybill": waybill_code,
        "reason": reason_code,
        "uid": user_id,
        "exp": expire,
        "iat": now,
        "iss": "nexusdesk",
        "aud": "speedaf-cancel",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_confirm_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience="speedaf-cancel",
            issuer="nexusdesk"
        )
    except Exception:
        return None

@router.post("/{ticket_id}/speedaf/cancel-preview", response_model=PreviewCancelResponse)
def speedaf_cancel_preview(
    ticket_id: int,
    payload: PreviewCancelRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    capabilities = resolve_capabilities(current_user, db)
    if "tool:speedaf.order.cancel:write" not in capabilities:
        raise HTTPException(status_code=403, detail="speedaf_cancel_requires_capability")
    
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket_not_found")
        
    client = SpeedafMcpClient()
    try:
        res = client.post("/open-api/mcp/order/query", {"waybillCode": payload.waybill_code})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
        
    if not res.ok:
        raise HTTPException(status_code=400, detail="speedaf_query_failed")
        
    data = res.data or {}
    order_info = data.get("orderInfo") or {}
    current_status = order_info.get("status")
    current_status_label = safe_order_status_label(current_status)
    
    if is_terminal_status(current_status) or is_terminal_status(current_status_label) or is_terminal_status(current_status_label.split(":")[-1] if current_status_label else None):
        return PreviewCancelResponse(
            current_status=current_status,
            current_status_label=current_status_label,
            cancel_allowed=False,
            reason="terminal_status_blocks_cancel",
            confirm_tokens={}
        )
        
    confirm_tokens = {}
    for rcode in ["CC01", "CC02", "CC03", "CC04", "CC05"]:
        confirm_tokens[rcode] = generate_confirm_token(ticket_id, payload.waybill_code, rcode, current_user.id)
        
    return PreviewCancelResponse(
        current_status=current_status,
        current_status_label=current_status_label,
        cancel_allowed=True,
        reason=None,
        confirm_tokens=confirm_tokens
    )

@router.post("/{ticket_id}/speedaf/cancel", response_model=ConfirmCancelResponse)
def speedaf_cancel_confirm(
    ticket_id: int,
    payload: ConfirmCancelRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    capabilities = resolve_capabilities(current_user, db)
    if "tool:speedaf.order.cancel:write" not in capabilities:
        raise HTTPException(status_code=403, detail="speedaf_cancel_requires_capability")
        
    token_data = decode_confirm_token(payload.confirm_token)
    if not token_data:
        raise HTTPException(status_code=400, detail="invalid_or_expired_confirm_token")
        
    if token_data.get("ticket_id") != ticket_id or \
       token_data.get("waybill") != payload.waybill_code or \
       token_data.get("reason") != payload.reason_code or \
       token_data.get("uid") != current_user.id:
        raise HTTPException(status_code=400, detail="token_mismatch")
        
    waybill_hash = hashlib.sha256(payload.waybill_code.encode()).hexdigest()[:8]
    dedupe_key = f"speedaf-cancel:ticket:{ticket_id}:waybill:{waybill_hash}:reason:{payload.reason_code}"
    
    recent = db.query(ToolCallLog).filter(
        ToolCallLog.request_id == dedupe_key,
        ToolCallLog.status == "success"
    ).first()
    if recent:
        raise HTTPException(status_code=429, detail="cancel_already_processed")
        
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket_not_found")
        
    action_service = SpeedafActionService(ticket_id=ticket_id, request_id=dedupe_key)
    try:
        res = action_service.cancel_order(
            waybill_code=payload.waybill_code,
            reason_code=payload.reason_code,
            caller_id=f"user_{current_user.id}"
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
        
    if not res.ok:
        raise HTTPException(status_code=400, detail=res.error_message or "cancel_failed")
        
    ev = TicketEvent(
        ticket_id=ticket_id,
        actor_id=current_user.id,
        event_type=EventType.speedaf_cancel if hasattr(EventType, "speedaf_cancel") else EventType.internal_note,
        new_value=f"Cancelled {payload.waybill_code} ({payload.reason_code})",
        note="Waiting for manual confirmation"
    )
    db.add(ev)
    db.commit()
    
    return ConfirmCancelResponse(
        ok=True,
        status="success",
        message="Cancel requested successfully. Pending manual closure."
    )
