from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_whatsapp import WhatsAppEmbeddedSignupSession
from ..schemas_whatsapp import WhatsAppConnectionCreate
from ..schemas_whatsapp_signup import (
    EmbeddedSignupCompleteRead,
    EmbeddedSignupCompleteRequest,
    EmbeddedSignupSessionCreate,
    EmbeddedSignupSessionRead,
)
from ..services.identity_tenant_scope import actor_tenant_id
from ..services.permissions import ensure_can_manage_channel_accounts
from ..services.whatsapp_embedded_signup import (
    EmbeddedSignupAccountIntent,
    EmbeddedSignupError,
    exchange_and_validate_signup,
    mark_signup_completed,
    mark_signup_exchanging,
    mark_signup_failed,
    require_pending_signup_session,
    start_embedded_signup_session,
)
from ..services.whatsapp_embedded_signup_settings import (
    get_whatsapp_embedded_signup_settings,
)
from ..unit_of_work import managed_session
from .admin_whatsapp import (
    create_whatsapp_connection,
    start_whatsapp_binding,
)
from .deps import get_current_user


router = APIRouter(
    prefix="/api/admin/whatsapp/embedded-signup",
    tags=["admin-whatsapp-embedded-signup"],
)


def _tenant_id(db: Session, current_user) -> int:
    value = actor_tenant_id(db, current_user)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="embedded_signup_requires_tenant",
        )
    return value


def _intent(payload) -> EmbeddedSignupAccountIntent:
    return EmbeddedSignupAccountIntent(
        display_name=payload.display_name,
        account_id=payload.account_id,
        market_id=payload.market_id,
        priority=payload.priority,
    )


def _http_error(exc: EmbeddedSignupError) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.retryable
            else status.HTTP_409_CONFLICT
        ),
        detail={
            "error_code": exc.code,
            "retryable": exc.retryable,
        },
    )


def _signup_session(
    db: Session,
    *,
    session_id: str,
    tenant_id: int,
    requested_by: int,
) -> WhatsAppEmbeddedSignupSession | None:
    return (
        db.query(WhatsAppEmbeddedSignupSession)
        .filter(
            WhatsAppEmbeddedSignupSession.id == session_id,
            WhatsAppEmbeddedSignupSession.tenant_id == tenant_id,
            WhatsAppEmbeddedSignupSession.requested_by == requested_by,
        )
        .first()
    )


@router.post(
    "/sessions",
    response_model=EmbeddedSignupSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_embedded_signup_session(
    payload: EmbeddedSignupSessionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    tenant_id = _tenant_id(db, current_user)
    try:
        with managed_session(db):
            session = start_embedded_signup_session(
                db,
                tenant_id=tenant_id,
                requested_by=current_user.id,
                intent=_intent(payload),
            )
    except EmbeddedSignupError as exc:
        raise _http_error(exc) from exc
    return EmbeddedSignupSessionRead(**session.__dict__)


@router.post(
    "/sessions/{session_id}/complete",
    response_model=EmbeddedSignupCompleteRead,
)
def complete_embedded_signup_session(
    session_id: str,
    payload: EmbeddedSignupCompleteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    tenant_id = _tenant_id(db, current_user)
    intent = _intent(payload)
    signup: WhatsAppEmbeddedSignupSession | None = None
    try:
        with managed_session(db):
            signup = require_pending_signup_session(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                requested_by=current_user.id,
                state=payload.state,
                intent=intent,
            )
            mark_signup_exchanging(
                signup,
                code=payload.code,
                business_account_id=payload.business_account_id,
                waba_id=payload.waba_id,
                phone_number_id=payload.phone_number_id,
            )
            db.flush()
        assets = exchange_and_validate_signup(
            code=payload.code,
            business_account_id=payload.business_account_id,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
        )
    except EmbeddedSignupError as exc:
        signup = _signup_session(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            requested_by=current_user.id,
        )
        if signup is not None and signup.status != "completed":
            with managed_session(db):
                mark_signup_failed(signup, code=exc.code)
                db.flush()
        raise _http_error(exc) from exc

    settings = get_whatsapp_embedded_signup_settings()
    if not settings.graph_api_version or not settings.app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="embedded_signup_runtime_invalid",
        )
    verify_token = secrets.token_urlsafe(48)
    try:
        connection = create_whatsapp_connection(
            WhatsAppConnectionCreate(
                display_name=payload.display_name,
                account_id=payload.account_id,
                market_id=payload.market_id,
                priority=payload.priority,
                transport="meta_cloud_api",
                business_account_id=assets.business_account_id,
                waba_id=assets.waba_id,
                phone_number_id=assets.phone_number_id,
                graph_api_version=settings.graph_api_version,
                access_token=assets.access_token,
                app_secret=settings.app_secret,
                verify_token=verify_token,
            ),
            db,
            current_user,
        )
    except HTTPException:
        signup = _signup_session(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            requested_by=current_user.id,
        )
        if signup is not None:
            with managed_session(db):
                mark_signup_failed(signup, code="embedded_signup_connection_create_failed")
                db.flush()
        raise

    signup = _signup_session(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        requested_by=current_user.id,
    )
    if signup is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="embedded_signup_session_lost",
        )
    with managed_session(db):
        mark_signup_completed(signup, connection_id=connection.id)
        db.flush()

    binding = start_whatsapp_binding(connection.id, db, current_user)
    return EmbeddedSignupCompleteRead(
        ok=True,
        session_id=session_id,
        connection_id=connection.id,
        account_id=connection.account_id,
        waba_id=assets.waba_id,
        phone_number_id=assets.phone_number_id,
        desired_state="binding",
        verification_state=binding.verification_state,
    )
