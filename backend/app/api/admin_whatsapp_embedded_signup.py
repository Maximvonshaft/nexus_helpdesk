from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount
from ..models_whatsapp import WhatsAppConnection, WhatsAppEmbeddedSignupSession
from ..schemas_whatsapp import WhatsAppConnectionCreate
from ..schemas_whatsapp_signup import (
    EmbeddedSignupCompleteRead,
    EmbeddedSignupCompleteRequest,
    EmbeddedSignupSessionCreate,
    EmbeddedSignupSessionRead,
)
from ..services.identity_tenant_scope import actor_tenant_id
from ..services.permissions import ensure_can_manage_channel_accounts
from ..services.whatsapp_connection_service import (
    WhatsAppConnectionError,
    get_whatsapp_connection,
)
from ..services.whatsapp_embedded_signup import (
    EmbeddedSignupAccountIntent,
    EmbeddedSignupError,
    clear_signup_exchange_checkpoint,
    exchange_and_validate_signup,
    load_signup_exchange_checkpoint,
    mark_signup_completed,
    mark_signup_exchanging,
    mark_signup_failed,
    persist_signup_exchange_checkpoint,
    require_pending_signup_session,
    start_embedded_signup_session,
)
from ..services.whatsapp_embedded_signup_settings import (
    get_whatsapp_embedded_signup_settings,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .admin_whatsapp import (
    create_whatsapp_connection,
    start_whatsapp_binding,
)
from .deps import get_current_user


router = APIRouter(
    prefix="/api/admin/whatsapp/embedded-signup",
    tags=["admin-whatsapp-embedded-signup"],
)


_RETRYABLE_BINDING_CODES = {
    "baileys_sidecar_timeout",
    "baileys_sidecar_transport_error",
    "meta_cloud_timeout",
    "meta_cloud_transport_error",
    "meta_waba_subscription_failed",
}
_EXCHANGING_RECOVERY_AFTER = timedelta(minutes=5)


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
    for_update: bool = False,
) -> WhatsAppEmbeddedSignupSession | None:
    query = db.query(WhatsAppEmbeddedSignupSession).filter(
        WhatsAppEmbeddedSignupSession.id == session_id,
        WhatsAppEmbeddedSignupSession.tenant_id == tenant_id,
        WhatsAppEmbeddedSignupSession.requested_by == requested_by,
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def _completed_connection(
    db: Session,
    *,
    signup: WhatsAppEmbeddedSignupSession,
) -> WhatsAppConnection:
    if signup.connection_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="embedded_signup_completed_connection_missing",
        )
    try:
        connection = get_whatsapp_connection(db, signup.connection_id)
    except WhatsAppConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="embedded_signup_completed_connection_missing",
        ) from exc
    if connection.tenant_id != signup.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="embedded_signup_connection_scope_mismatch",
        )
    return connection


def _verify_idempotent_state(
    signup: WhatsAppEmbeddedSignupSession,
    state: str,
) -> None:
    supplied = hashlib.sha256(state.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied, signup.state_digest):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "embedded_signup_state_invalid",
                "retryable": False,
            },
        )


def _binding_error(exc: HTTPException) -> tuple[str, bool]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("error_code") or "embedded_signup_binding_failed")[:120]
    retryable = bool(detail.get("retryable")) or code in _RETRYABLE_BINDING_CODES
    return code, retryable


def _restore_retryable_signup(
    signup: WhatsAppEmbeddedSignupSession,
    *,
    code: str,
) -> None:
    """Return a transiently failed validation to its resumable pending state."""

    signup.status = "pending"
    signup.last_error_code = code[:120]
    signup.updated_at = utc_now()


def _completion_result(
    *,
    session_id: str,
    connection: WhatsAppConnection,
    binding_state: str,
    binding_error_code: str | None = None,
    binding_retryable: bool = False,
    idempotent: bool = False,
) -> EmbeddedSignupCompleteRead:
    account = connection.channel_account
    return EmbeddedSignupCompleteRead(
        ok=True,
        session_id=session_id,
        connection_id=connection.id,
        account_id=account.account_id,
        waba_id=str(connection.waba_id or ""),
        phone_number_id=str(connection.phone_number_id or ""),
        desired_state=connection.desired_state,
        verification_state=connection.verification_state,
        binding_state=binding_state,
        binding_error_code=binding_error_code,
        binding_retryable=binding_retryable,
        idempotent=idempotent,
    )


def _binding_was_never_started(connection: WhatsAppConnection) -> bool:
    return bool(
        connection.transport == "meta_cloud_api"
        and connection.desired_state == "disabled"
        and connection.observed_state == "unconfigured"
        and connection.authentication_state == "unconfigured"
        and connection.listener_state == "stopped"
        and connection.last_probe_at is None
    )


def _finish_idempotent_completion(
    *,
    session_id: str,
    connection: WhatsAppConnection,
    db: Session,
    current_user,
) -> EmbeddedSignupCompleteRead:
    if _binding_was_never_started(connection):
        try:
            start_whatsapp_binding(connection.id, db, current_user)
        except HTTPException as exc:
            db.expire_all()
            refreshed = get_whatsapp_connection(db, connection.id)
            error_code, retryable = _binding_error(exc)
            return _completion_result(
                session_id=session_id,
                connection=refreshed,
                binding_state="attention_required",
                binding_error_code=error_code,
                binding_retryable=retryable,
                idempotent=True,
            )
        db.expire_all()
        connection = get_whatsapp_connection(db, connection.id)
    error_code = connection.last_error_code
    return _completion_result(
        session_id=session_id,
        connection=connection,
        binding_state=("attention_required" if error_code else "started"),
        binding_error_code=error_code,
        binding_retryable=bool(error_code in _RETRYABLE_BINDING_CODES),
        idempotent=True,
    )


def _recover_interrupted_signup(
    db: Session,
    *,
    session_id: str,
    tenant_id: int,
    requested_by: int,
    state: str,
    intent: EmbeddedSignupAccountIntent,
) -> WhatsAppConnection | None:
    """Recover a process crash after the connection commit or release a stale claim."""

    recovered_connection_id: int | None = None
    with managed_session(db):
        signup = _signup_session(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            for_update=True,
        )
        if signup is None or signup.status != "exchanging":
            return None
        _verify_idempotent_state(signup, state)

        connection = None
        if signup.phone_number_id:
            connection = (
                db.query(WhatsAppConnection)
                .join(
                    ChannelAccount,
                    ChannelAccount.id == WhatsAppConnection.channel_account_id,
                )
                .filter(
                    WhatsAppConnection.tenant_id == tenant_id,
                    WhatsAppConnection.transport == "meta_cloud_api",
                    WhatsAppConnection.phone_number_id == signup.phone_number_id,
                    WhatsAppConnection.created_by == requested_by,
                    ChannelAccount.tenant_id == tenant_id,
                    ChannelAccount.provider == "whatsapp",
                    ChannelAccount.account_id == intent.account_id,
                )
                .first()
            )
        if connection is not None:
            if signup.waba_id and connection.waba_id != signup.waba_id:
                raise EmbeddedSignupError(
                    "embedded_signup_recovery_waba_mismatch"
                )
            if (
                signup.business_account_id
                and connection.business_account_id
                and connection.business_account_id != signup.business_account_id
            ):
                raise EmbeddedSignupError(
                    "embedded_signup_recovery_business_account_mismatch"
                )
            clear_signup_exchange_checkpoint(db, session_id=signup.id)
            mark_signup_completed(signup, connection_id=connection.id)
            recovered_connection_id = connection.id
            db.flush()
        else:
            stale_before = utc_now() - _EXCHANGING_RECOVERY_AFTER
            if signup.updated_at > stale_before:
                raise EmbeddedSignupError(
                    "embedded_signup_exchange_in_progress",
                    retryable=True,
                )
            _restore_retryable_signup(
                signup,
                code="embedded_signup_interrupted_before_connection",
            )
            db.flush()

    if recovered_connection_id is None:
        return None
    return get_whatsapp_connection(db, recovered_connection_id)


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
    existing = _signup_session(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        requested_by=current_user.id,
    )
    if existing is not None and existing.status == "exchanging":
        try:
            recovered = _recover_interrupted_signup(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                requested_by=current_user.id,
                state=payload.state,
                intent=intent,
            )
        except EmbeddedSignupError as exc:
            raise _http_error(exc) from exc
        if recovered is not None:
            return _finish_idempotent_completion(
                session_id=session_id,
                connection=recovered,
                db=db,
                current_user=current_user,
            )
        existing = _signup_session(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            requested_by=current_user.id,
        )
    if existing is not None and existing.status == "completed":
        _verify_idempotent_state(existing, payload.state)
        connection = _completed_connection(db, signup=existing)
        return _finish_idempotent_completion(
            session_id=session_id,
            connection=connection,
            db=db,
            current_user=current_user,
        )

    resume_access_token: str | None = None
    claim_acquired = False
    try:
        with managed_session(db):
            _signup_session(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                requested_by=current_user.id,
                for_update=True,
            )
            signup = require_pending_signup_session(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                requested_by=current_user.id,
                state=payload.state,
                intent=intent,
            )
            resume_access_token = load_signup_exchange_checkpoint(
                db,
                session=signup,
                code=payload.code,
            )
            mark_signup_exchanging(
                signup,
                code=payload.code,
                business_account_id=payload.business_account_id,
                waba_id=payload.waba_id,
                phone_number_id=payload.phone_number_id,
            )
            db.flush()
        claim_acquired = True
        assets = exchange_and_validate_signup(
            code=payload.code,
            business_account_id=payload.business_account_id,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
            access_token=resume_access_token,
        )
    except EmbeddedSignupError as exc:
        if claim_acquired:
            with managed_session(db):
                signup = _signup_session(
                    db,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    requested_by=current_user.id,
                    for_update=True,
                )
                if signup is not None and signup.status == "exchanging":
                    if exc.retryable:
                        if exc.resume_access_token:
                            persist_signup_exchange_checkpoint(
                                db,
                                session=signup,
                                access_token=exc.resume_access_token,
                            )
                        _restore_retryable_signup(signup, code=exc.code)
                    else:
                        clear_signup_exchange_checkpoint(
                            db,
                            session_id=signup.id,
                        )
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
        created = create_whatsapp_connection(
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
        with managed_session(db):
            signup = _signup_session(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                requested_by=current_user.id,
                for_update=True,
            )
            if signup is not None:
                clear_signup_exchange_checkpoint(db, session_id=signup.id)
                mark_signup_failed(
                    signup,
                    code="embedded_signup_connection_create_failed",
                )
                db.flush()
        raise

    with managed_session(db):
        signup = _signup_session(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            requested_by=current_user.id,
            for_update=True,
        )
        if signup is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="embedded_signup_session_lost",
            )
        clear_signup_exchange_checkpoint(db, session_id=signup.id)
        mark_signup_completed(signup, connection_id=created.id)
        db.flush()

    connection = _completed_connection(db, signup=signup)
    try:
        start_whatsapp_binding(connection.id, db, current_user)
    except HTTPException as exc:
        db.expire_all()
        connection = _completed_connection(db, signup=signup)
        error_code, retryable = _binding_error(exc)
        return _completion_result(
            session_id=session_id,
            connection=connection,
            binding_state="attention_required",
            binding_error_code=error_code,
            binding_retryable=retryable,
        )

    db.expire_all()
    connection = _completed_connection(db, signup=signup)
    return _completion_result(
        session_id=session_id,
        connection=connection,
        binding_state="started",
    )
