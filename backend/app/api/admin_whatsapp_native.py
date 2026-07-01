from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount
from ..services.permissions import ensure_can_manage_channel_accounts
from ..services.whatsapp_native_admin import (
    call_whatsapp_sidecar_account_action,
    request_whatsapp_sidecar_pairing_code,
    whatsapp_health_from_native_status,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/whatsapp/accounts", tags=["admin-whatsapp-native"])


class WhatsAppNativeAccountStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    status: str
    qr_status: str
    qr: str | None = None
    qr_data_url: str | None = None
    phone_number: str | None = None
    jid: str | None = None
    last_qr_generated_at: str | None = None
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_transport_at: str | None = None
    last_qr_expires_at: str | None = None
    session_state: str | None = None
    browser: list[str] | None = None
    reconnect_count: int = 0
    recovery_action: str | None = None
    recovery_reason: str | None = None
    channel_account_id: int
    channel_health_status: str


class WhatsAppNativePairingCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str


class WhatsAppNativePairingCodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    account_id: str
    pairing_code: str | None = None
    phone_number_suffix: str | None = None
    error_code: str | None = None
    retryable: bool | None = None


def _whatsapp_account_or_404(db: Session, account_id: str) -> ChannelAccount:
    row = (
        db.query(ChannelAccount)
        .filter(ChannelAccount.account_id == account_id, ChannelAccount.provider == "whatsapp")
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp channel account not found")
    return row


def _sidecar_error(exc: Exception) -> HTTPException:
    message = str(exc) or type(exc).__name__
    if message in {"whatsapp_native_disabled", "whatsapp_sidecar_token_missing"}:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message[:240])


def _snapshot_response(db: Session, account: ChannelAccount, snapshot: Any) -> WhatsAppNativeAccountStatus:
    health = whatsapp_health_from_native_status(snapshot.status)
    with managed_session(db):
        account.health_status = health
        account.last_health_check_at = utc_now()
        db.flush()
    data = snapshot.as_dict()
    data["channel_account_id"] = account.id
    data["channel_health_status"] = account.health_status
    return WhatsAppNativeAccountStatus.model_validate(data)


def _call(
    action: Literal["start", "status", "qr", "logout", "restart"],
    account_id: str,
    *,
    method: Literal["GET", "POST"],
    db: Session,
    current_user,
) -> WhatsAppNativeAccountStatus:
    ensure_can_manage_channel_accounts(current_user, db)
    account = _whatsapp_account_or_404(db, account_id)
    try:
        snapshot = call_whatsapp_sidecar_account_action(account.account_id, action, method=method)
    except Exception as exc:
        raise _sidecar_error(exc) from exc
    return _snapshot_response(db, account, snapshot)


@router.post("/{account_id}/login/start", response_model=WhatsAppNativeAccountStatus)
def start_whatsapp_native_login(account_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _call("start", account_id, method="POST", db=db, current_user=current_user)


@router.get("/{account_id}/login/qr", response_model=WhatsAppNativeAccountStatus)
def get_whatsapp_native_qr(account_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _call("qr", account_id, method="GET", db=db, current_user=current_user)


@router.post("/{account_id}/login/pairing-code", response_model=WhatsAppNativePairingCodeResponse)
def request_whatsapp_native_pairing_code(
    account_id: str,
    payload: WhatsAppNativePairingCodeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    account = _whatsapp_account_or_404(db, account_id)
    try:
        result = request_whatsapp_sidecar_pairing_code(account.account_id, payload.phone_number)
    except Exception as exc:
        raise _sidecar_error(exc) from exc
    return WhatsAppNativePairingCodeResponse.model_validate(result.as_dict())


@router.get("/{account_id}/status", response_model=WhatsAppNativeAccountStatus)
def get_whatsapp_native_status(account_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _call("status", account_id, method="GET", db=db, current_user=current_user)


@router.post("/{account_id}/logout", response_model=WhatsAppNativeAccountStatus)
def logout_whatsapp_native(account_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _call("logout", account_id, method="POST", db=db, current_user=current_user)


@router.post("/{account_id}/session/reset", response_model=WhatsAppNativeAccountStatus)
def reset_whatsapp_native_session(account_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _call("logout", account_id, method="POST", db=db, current_user=current_user)


@router.post("/{account_id}/restart", response_model=WhatsAppNativeAccountStatus)
def restart_whatsapp_native(account_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _call("restart", account_id, method="POST", db=db, current_user=current_user)
