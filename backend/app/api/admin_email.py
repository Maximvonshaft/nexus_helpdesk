from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount, EmailChannelAccount
from ..schemas import EmailAccountCreate, EmailAccountRead, EmailAccountUpdate, EmailReadinessRead, EmailTestSendRequest
from ..services.email_security import normalize_email_address
from ..services.permissions import ensure_can_manage_runtime
from ..settings import get_settings
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user

router = APIRouter(prefix="/api/admin/email-accounts", tags=["admin-email"])


def _serialize(row: EmailChannelAccount) -> EmailAccountRead:
    account = row.channel_account
    return EmailAccountRead(
        id=row.id,
        channel_account_id=row.channel_account_id,
        account_id=account.account_id,
        from_email=row.from_email,
        from_name=row.from_name,
        market_id=account.market_id,
        provider=row.provider,
        region=row.region,
        configuration_set=row.configuration_set,
        verification_status=row.verification_status,
        inbound_domain=row.inbound_domain,
        plus_address_tag=row.plus_address_tag,
        is_active=row.is_active and account.is_active,
        health_status=account.health_status,
        last_test_send_at=row.last_test_send_at,
        last_readiness_check_at=row.last_readiness_check_at,
        updated_at=row.updated_at,
    )


def _readiness(row: EmailChannelAccount) -> EmailReadinessRead:
    settings = get_settings()
    missing: list[str] = []
    if not settings.enable_outbound_dispatch:
        missing.append("enable_outbound_dispatch")
    if not settings.outbound_email_enabled:
        missing.append("outbound_email_enabled")
    if settings.email_provider != "ses":
        missing.append("email_provider_ses")
    if row.verification_status != "verified":
        missing.append("verified_identity")
    if not row.is_active or not row.channel_account.is_active:
        missing.append("active_email_account")
    return EmailReadinessRead(ready=not missing, missing=missing)


@router.get("", response_model=list[EmailAccountRead])
def list_email_accounts(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    rows = db.query(EmailChannelAccount).join(ChannelAccount).order_by(ChannelAccount.priority.asc(), EmailChannelAccount.id.asc()).all()
    return [_serialize(row) for row in rows]


@router.post("", response_model=EmailAccountRead)
def create_email_account(payload: EmailAccountCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    from_email = normalize_email_address(payload.from_email)
    if not from_email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_from_email")
    if db.query(ChannelAccount).filter(ChannelAccount.account_id == payload.account_id).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="channel_account_id_exists")
    with managed_session(db):
        account = ChannelAccount(provider="email", account_id=payload.account_id, display_name=payload.from_name or from_email, market_id=payload.market_id, is_active=payload.is_active, priority=100, health_status="unknown")
        db.add(account)
        db.flush()
        row = EmailChannelAccount(
            channel_account_id=account.id,
            from_email=from_email,
            from_name=payload.from_name,
            provider="ses",
            region=payload.region,
            configuration_set=payload.configuration_set,
            verification_status=payload.verification_status,
            inbound_domain=payload.inbound_domain,
            plus_address_tag=payload.plus_address_tag,
            is_active=payload.is_active,
        )
        db.add(row)
        db.flush()
    return _serialize(row)


@router.patch("/{email_account_id}", response_model=EmailAccountRead)
def update_email_account(email_account_id: int, payload: EmailAccountUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    row = db.query(EmailChannelAccount).filter(EmailChannelAccount.id == email_account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="email_account_not_found")
    with managed_session(db):
        for field in ("from_name", "region", "configuration_set", "inbound_domain", "plus_address_tag", "verification_status", "is_active"):
            value = getattr(payload, field)
            if value is not None:
                setattr(row, field, value)
        if payload.market_id is not None:
            row.channel_account.market_id = payload.market_id
        if payload.is_active is not None:
            row.channel_account.is_active = payload.is_active
        row.updated_at = utc_now()
        row.channel_account.updated_at = utc_now()
        db.flush()
    return _serialize(row)


@router.post("/{email_account_id}/check-readiness", response_model=EmailReadinessRead)
def check_readiness(email_account_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    row = db.query(EmailChannelAccount).filter(EmailChannelAccount.id == email_account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="email_account_not_found")
    row.last_readiness_check_at = utc_now()
    db.commit()
    return _readiness(row)


@router.post("/{email_account_id}/test-send")
def test_send(email_account_id: int, payload: EmailTestSendRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_runtime(current_user, db)
    row = db.query(EmailChannelAccount).filter(EmailChannelAccount.id == email_account_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="email_account_not_found")
    readiness = _readiness(row)
    if not readiness.ready:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "email_account_not_ready", "missing": readiness.missing})
    row.last_test_send_at = utc_now()
    row.channel_account.health_status = "test_send_queued"
    db.commit()
    return {"ok": True, "status": "test_send_queued", "to_email": payload.to_email}
