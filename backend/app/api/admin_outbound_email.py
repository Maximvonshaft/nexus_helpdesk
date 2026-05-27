from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import OutboundEmailAccount
from ..enums import MessageStatus
from ..schemas import (
    OutboundEmailAccountCreate,
    OutboundEmailAccountRead,
    OutboundEmailAccountUpdate,
    OutboundEmailTestSendRead,
    OutboundEmailTestSendRequest,
)
from ..services.audit_service import log_admin_audit
from ..services.outbound_adapters.email import send_outbound_email_test
from ..services.outbound_email_account_service import (
    account_audit_snapshot,
    clean_optional_text,
    find_duplicate_account,
    normalize_email,
    normalize_host,
    validate_active_market,
)
from ..services.permissions import ensure_can_manage_channel_accounts
from ..services.secret_crypto import SecretCryptoService, mask_secret
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/outbound-email", tags=["admin-outbound-email"])


def _crypto() -> SecretCryptoService:
    try:
        return SecretCryptoService.outbound_email()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def _serialize(row: OutboundEmailAccount) -> OutboundEmailAccountRead:
    return OutboundEmailAccountRead(
        id=row.id,
        display_name=row.display_name,
        host=row.host,
        port=row.port,
        username=row.username,
        from_address=row.from_address,
        reply_to=row.reply_to,
        security_mode=row.security_mode,
        market_id=row.market_id,
        is_active=row.is_active,
        priority=row.priority,
        health_status=row.health_status,
        last_test_status=row.last_test_status,
        last_test_error=row.last_test_error,
        last_test_at=row.last_test_at,
        password_configured=bool(row.password_encrypted),
        password_mask=mask_secret(row.password_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ensure_valid_unique_route(
    db: Session,
    *,
    host: str,
    port: int,
    username: str,
    from_address: str,
    market_id: int | None,
    exclude_id: int | None = None,
) -> None:
    try:
        validate_active_market(db, market_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if find_duplicate_account(
        db,
        host=host,
        port=port,
        username=username,
        from_address=from_address,
        market_id=market_id,
        exclude_id=exclude_id,
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Outbound Email account already exists")


@router.get("/accounts", response_model=list[OutboundEmailAccountRead])
def list_outbound_email_accounts(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    rows = db.query(OutboundEmailAccount).order_by(
        OutboundEmailAccount.is_active.desc(),
        OutboundEmailAccount.priority.asc(),
        OutboundEmailAccount.id.asc(),
    ).all()
    return [_serialize(row) for row in rows]


@router.get("/accounts/{account_id}", response_model=OutboundEmailAccountRead)
def get_outbound_email_account(account_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound Email account not found")
    return _serialize(row)


@router.post("/accounts", response_model=OutboundEmailAccountRead)
def create_outbound_email_account(
    payload: OutboundEmailAccountCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    host = normalize_host(payload.host)
    username = payload.username.strip()
    from_address = normalize_email(str(payload.from_address))
    reply_to = normalize_email(str(payload.reply_to)) if payload.reply_to is not None else None
    _ensure_valid_unique_route(
        db,
        host=host,
        port=payload.port,
        username=username,
        from_address=from_address or "",
        market_id=payload.market_id,
    )
    encrypted_password = _crypto().encrypt(payload.password)
    if not encrypted_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="password is required")

    with managed_session(db):
        row = OutboundEmailAccount(
            display_name=clean_optional_text(payload.display_name),
            host=host,
            port=payload.port,
            username=username,
            password_encrypted=encrypted_password,
            from_address=from_address or "",
            reply_to=reply_to,
            security_mode=payload.security_mode,
            market_id=payload.market_id,
            is_active=payload.is_active,
            priority=payload.priority,
            health_status="unknown",
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(row)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="outbound_email_account.create",
            target_type="outbound_email_account",
            target_id=row.id,
            old_value=None,
            new_value=account_audit_snapshot(row),
        )
    db.refresh(row)
    return _serialize(row)


@router.patch("/accounts/{account_id}", response_model=OutboundEmailAccountRead)
def update_outbound_email_account(
    account_id: int,
    payload: OutboundEmailAccountUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound Email account not found")

    data = payload.model_dump(exclude_unset=True)
    for required_field in ("host", "port", "username", "from_address", "security_mode", "priority", "is_active"):
        if required_field in data and data[required_field] is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{required_field} cannot be null")
    host = normalize_host(data.get("host", row.host))
    port = data.get("port", row.port)
    username = data.get("username", row.username).strip()
    from_address = normalize_email(str(data.get("from_address", row.from_address))) or ""
    reply_to = normalize_email(str(data["reply_to"])) if "reply_to" in data and data["reply_to"] is not None else (None if data.get("reply_to") is None and "reply_to" in data else row.reply_to)
    market_id = data.get("market_id", row.market_id)
    _ensure_valid_unique_route(
        db,
        host=host,
        port=port,
        username=username,
        from_address=from_address,
        market_id=market_id,
        exclude_id=row.id,
    )

    encrypted_password = None
    if "password" in data:
        encrypted_password = _crypto().encrypt(data["password"])
        if not encrypted_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="password is required")

    with managed_session(db):
        before = account_audit_snapshot(row)
        row.display_name = clean_optional_text(data["display_name"]) if "display_name" in data else row.display_name
        row.host = host
        row.port = port
        row.username = username
        row.from_address = from_address
        row.reply_to = reply_to
        row.security_mode = data.get("security_mode", row.security_mode)
        row.market_id = market_id
        row.priority = data.get("priority", row.priority)
        row.is_active = data.get("is_active", row.is_active)
        row.updated_by = current_user.id
        if encrypted_password is not None:
            row.password_encrypted = encrypted_password
            row.health_status = "unknown"
            row.last_test_status = None
            row.last_test_error = None
            row.last_test_at = None
        db.flush()
        after = account_audit_snapshot(row)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="outbound_email_account.update",
            target_type="outbound_email_account",
            target_id=row.id,
            old_value=before,
            new_value=after,
        )
        if encrypted_password is not None:
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action="outbound_email_account.password_change",
                target_type="outbound_email_account",
                target_id=row.id,
                old_value={"password": {"redacted": True, "configured": bool(before["password"]["configured"])}},
                new_value={"password": {"redacted": True, "configured": True}},
            )
    db.refresh(row)
    return _serialize(row)


@router.post("/accounts/{account_id}/enable", response_model=OutboundEmailAccountRead)
def enable_outbound_email_account(account_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _set_outbound_email_account_active(account_id, True, db, current_user)


@router.post("/accounts/{account_id}/disable", response_model=OutboundEmailAccountRead)
def disable_outbound_email_account(account_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _set_outbound_email_account_active(account_id, False, db, current_user)


@router.post("/accounts/{account_id}/test-send", response_model=OutboundEmailTestSendRead)
def send_outbound_email_account_test(
    account_id: int,
    payload: OutboundEmailTestSendRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound Email account not found")

    status_value, provider_status, sent_at, route_context = send_outbound_email_test(
        row,
        to_address=str(payload.to_address),
        subject=payload.subject,
        body=payload.body,
    )
    ok = status_value == MessageStatus.sent
    failure_code = route_context.get("failure_code") if isinstance(route_context, dict) else None
    error_message = route_context.get("error") if isinstance(route_context, dict) else None
    health_status = "ok" if ok else "error"
    last_test_status = "success" if ok else str(failure_code or provider_status or "failed")

    with managed_session(db):
        row.health_status = health_status
        row.last_test_status = last_test_status[:40]
        row.last_test_error = None if ok else str(error_message or provider_status or "SMTP test-send failed")
        row.last_test_at = utc_now()
        row.updated_by = current_user.id
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="outbound_email_account.test_send",
            target_type="outbound_email_account",
            target_id=row.id,
            old_value=None,
            new_value={
                "id": row.id,
                "health_status": row.health_status,
                "last_test_status": row.last_test_status,
                "last_test_error": row.last_test_error,
                "route": route_context,
            },
        )

    return OutboundEmailTestSendRead(
        ok=ok,
        account_id=row.id,
        provider_status=provider_status or ("smtp_sent" if ok else "failed"),
        failure_code=str(failure_code) if failure_code else None,
        error_message=None if ok else str(error_message or provider_status or "SMTP test-send failed"),
        sent_at=sent_at,
        health_status=health_status,
    )


def _set_outbound_email_account_active(
    account_id: int,
    active: bool,
    db: Session,
    current_user,
) -> OutboundEmailAccountRead:
    ensure_can_manage_channel_accounts(current_user, db)
    row = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.id == account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbound Email account not found")
    with managed_session(db):
        before = account_audit_snapshot(row)
        row.is_active = active
        row.updated_by = current_user.id
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="outbound_email_account.enable" if active else "outbound_email_account.disable",
            target_type="outbound_email_account",
            target_id=row.id,
            old_value=before,
            new_value=account_audit_snapshot(row),
        )
    db.refresh(row)
    return _serialize(row)
