from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChannelAccount, WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppConnection
from ..schemas_whatsapp import (
    WhatsAppBindingStatus,
    WhatsAppConnectionCreate,
    WhatsAppConnectionRead,
    WhatsAppConnectionUpdate,
    WhatsAppDesiredStateUpdate,
    WhatsAppMetaSubscriptionRequest,
    WhatsAppPairingCodeRead,
    WhatsAppPairingCodeRequest,
    WhatsAppTestInboundRequest,
    WhatsAppTestOutboundRequest,
    WhatsAppTestResult,
)
from ..services.audit_service import log_admin_audit
from ..services.identity_tenant_scope import active_market_for_actor, actor_tenant_id
from ..services.permissions import ensure_can_manage_channel_accounts
from ..services.secret_crypto import SecretCryptoService
from ..services.whatsapp_baileys_sidecar import (
    BaileysSidecarError,
    call_baileys_account_action,
    request_baileys_pairing_code,
    send_baileys_text,
)
from ..services.whatsapp_connection_service import (
    WhatsAppActivationError,
    WhatsAppConnectionError,
    apply_observed_snapshot,
    connection_audit_snapshot,
    get_whatsapp_connection,
    record_verification_evidence,
    reset_verification,
    set_desired_state,
    validate_whatsapp_connection_configuration,
)
from ..services.whatsapp_meta_cloud import (
    MetaCloudTransportError,
    probe_meta_cloud_connection,
    send_meta_cloud_text,
    subscribe_meta_waba,
)
from ..services.whatsapp_runtime_settings import get_whatsapp_runtime_settings
from ..services.whatsapp_transport_registry import (
    BAILEYS_SIDECAR_TRANSPORT,
    META_CLOUD_API_TRANSPORT,
    normalize_whatsapp_transport,
)
from ..unit_of_work import managed_session
from ..utils.time import utc_now
from .deps import get_current_user


router = APIRouter(
    prefix="/api/admin/whatsapp/connections",
    tags=["admin-whatsapp"],
)


def _crypto() -> SecretCryptoService:
    try:
        return SecretCryptoService.whatsapp()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="whatsapp_secret_runtime_unavailable",
        ) from exc


def _safe_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:120]
    if exc.args and isinstance(exc.args[0], str):
        value = exc.args[0]
        if value and all(char.isalnum() or char in "_.:-," for char in value):
            return value[:120]
    return "whatsapp_operation_failed"


def _http_error(exc: Exception) -> HTTPException:
    code = _safe_code(exc)
    if isinstance(exc, WhatsAppActivationError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": code, "retryable": False},
        )
    if isinstance(exc, WhatsAppConnectionError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": code, "retryable": False},
        )
    if isinstance(exc, (BaileysSidecarError, MetaCloudTransportError)):
        return HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.retryable
                else status.HTTP_400_BAD_REQUEST
            ),
            detail={"error_code": exc.code, "retryable": exc.retryable},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error_code": "whatsapp_operation_failed", "retryable": False},
    )


def _masked_phone(value: str | None) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return f"•••• {digits[-4:]}" if digits else None


def _serialize(connection: WhatsAppConnection) -> WhatsAppConnectionRead:
    account = connection.channel_account
    return WhatsAppConnectionRead(
        id=connection.id,
        tenant_id=connection.tenant_id,
        channel_account_id=connection.channel_account_id,
        account_id=account.account_id,
        display_name=account.display_name,
        market_id=account.market_id,
        priority=account.priority,
        channel_active=account.is_active,
        transport=connection.transport,
        desired_state=connection.desired_state,
        observed_state=connection.observed_state,
        authentication_state=connection.authentication_state,
        listener_state=connection.listener_state,
        verification_state=connection.verification_state,
        desired_generation=connection.desired_generation,
        observed_generation=connection.observed_generation,
        phone_number_mask=_masked_phone(connection.phone_number),
        business_account_id=connection.business_account_id,
        waba_id=connection.waba_id,
        phone_number_id=connection.phone_number_id,
        graph_api_version=connection.graph_api_version,
        sidecar_session_key=connection.sidecar_session_key,
        session_generation=connection.session_generation,
        access_token_configured=bool(connection.access_token_encrypted),
        app_secret_configured=bool(connection.app_secret_encrypted),
        verify_token_configured=bool(connection.verify_token_encrypted),
        last_qr_generated_at=connection.last_qr_generated_at,
        qr_expires_at=connection.qr_expires_at,
        last_connected_at=connection.last_connected_at,
        last_disconnected_at=connection.last_disconnected_at,
        last_inbound_at=connection.last_inbound_at,
        last_outbound_at=connection.last_outbound_at,
        last_probe_at=connection.last_probe_at,
        last_probe_status=connection.last_probe_status,
        reconnect_count=connection.reconnect_count,
        last_error_code=connection.last_error_code,
        last_error_message=connection.last_error_message,
        inbound_tested_at=connection.inbound_tested_at,
        outbound_tested_at=connection.outbound_tested_at,
        verified_at=connection.verified_at,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _binding_status(
    connection: WhatsAppConnection,
    *,
    qr_status: str | None = None,
    qr_data_url: str | None = None,
) -> WhatsAppBindingStatus:
    return WhatsAppBindingStatus(
        connection_id=connection.id,
        channel_account_id=connection.channel_account_id,
        transport=connection.transport,
        observed_state=connection.observed_state,
        authentication_state=connection.authentication_state,
        listener_state=connection.listener_state,
        verification_state=connection.verification_state,
        desired_generation=connection.desired_generation,
        observed_generation=connection.observed_generation,
        qr_status=qr_status,
        qr_data_url=qr_data_url,
        qr_expires_at=connection.qr_expires_at,
        phone_number_mask=_masked_phone(connection.phone_number),
        last_connected_at=connection.last_connected_at,
        last_disconnected_at=connection.last_disconnected_at,
        last_inbound_at=connection.last_inbound_at,
        last_outbound_at=connection.last_outbound_at,
        last_probe_at=connection.last_probe_at,
        reconnect_count=connection.reconnect_count,
        last_error_code=connection.last_error_code,
        last_error_message=connection.last_error_message,
    )


def _connection(db: Session, connection_id: int) -> WhatsAppConnection:
    try:
        return get_whatsapp_connection(db, connection_id)
    except WhatsAppConnectionError as exc:
        raise _http_error(exc) from exc


def _tenant_id(db: Session, current_user) -> int:
    value = actor_tenant_id(db, current_user)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="whatsapp_connection_requires_tenant",
        )
    return value


def _validate_market(
    db: Session,
    *,
    tenant_id: int,
    market_id: int | None,
) -> None:
    if market_id is None:
        return
    if active_market_for_actor(db, tenant_id, market_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="market_not_found_or_inactive",
        )


def _meta_secrets(connection: WhatsAppConnection) -> tuple[str, str, str]:
    crypto = _crypto()
    access_token = crypto.decrypt(connection.access_token_encrypted)
    app_secret = crypto.decrypt(connection.app_secret_encrypted)
    verify_token = crypto.decrypt(connection.verify_token_encrypted)
    if not access_token or not app_secret or not verify_token:
        raise WhatsAppConnectionError("meta_cloud_credentials_missing")
    return access_token, app_secret, verify_token


def _probe(connection: WhatsAppConnection) -> dict[str, Any]:
    if connection.transport == BAILEYS_SIDECAR_TRANSPORT:
        return call_baileys_account_action(
            connection,
            "status",
            method="GET",
        ).as_dict()
    return probe_meta_cloud_connection(
        connection,
        access_token=_meta_secrets(connection)[0],
    ).as_dict()


@router.get("", response_model=list[WhatsAppConnectionRead])
def list_whatsapp_connections(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    rows = (
        db.query(WhatsAppConnection)
        .join(ChannelAccount, ChannelAccount.id == WhatsAppConnection.channel_account_id)
        .order_by(ChannelAccount.priority.asc(), WhatsAppConnection.id.asc())
        .all()
    )
    return [_serialize(row) for row in rows]


@router.get("/{connection_id}", response_model=WhatsAppConnectionRead)
def get_whatsapp_connection_detail(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    return _serialize(_connection(db, connection_id))


@router.post("", response_model=WhatsAppConnectionRead, status_code=status.HTTP_201_CREATED)
def create_whatsapp_connection(
    payload: WhatsAppConnectionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    tenant_id = _tenant_id(db, current_user)
    _validate_market(db, tenant_id=tenant_id, market_id=payload.market_id)
    if (
        db.query(ChannelAccount)
        .filter(ChannelAccount.account_id == payload.account_id)
        .first()
        is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="channel_account_already_exists",
        )
    transport = normalize_whatsapp_transport(payload.transport)
    crypto = _crypto() if transport == META_CLOUD_API_TRANSPORT else None
    with managed_session(db):
        account = ChannelAccount(
            tenant_id=tenant_id,
            provider="whatsapp",
            account_id=payload.account_id,
            display_name=payload.display_name,
            market_id=payload.market_id,
            is_active=False,
            priority=payload.priority,
            health_status="unknown",
        )
        db.add(account)
        db.flush()
        connection = WhatsAppConnection(
            tenant_id=tenant_id,
            channel_account_id=account.id,
            transport=transport,
            desired_state="disabled",
            observed_state=(
                "auth_required"
                if transport == BAILEYS_SIDECAR_TRANSPORT
                else "unconfigured"
            ),
            authentication_state="unconfigured",
            listener_state="stopped",
            verification_state="pending",
            desired_generation=0,
            observed_generation=0,
            sidecar_session_key=(
                payload.sidecar_session_key
                if transport == BAILEYS_SIDECAR_TRANSPORT
                else None
            ),
            business_account_id=payload.business_account_id,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
            graph_api_version=(
                payload.graph_api_version
                if transport == META_CLOUD_API_TRANSPORT
                else None
            ),
            access_token_encrypted=(
                crypto.encrypt(payload.access_token) if crypto else None
            ),
            access_token_fingerprint=(
                crypto.fingerprint(payload.access_token)
                if crypto and payload.access_token
                else None
            ),
            app_secret_encrypted=(
                crypto.encrypt(payload.app_secret) if crypto else None
            ),
            verify_token_encrypted=(
                crypto.encrypt(payload.verify_token) if crypto else None
            ),
            session_generation=0,
            reconnect_count=0,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(connection)
        db.flush()
        validate_whatsapp_connection_configuration(connection)
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="whatsapp_connection.create",
            target_type="whatsapp_connection",
            target_id=connection.id,
            old_value=None,
            new_value=connection_audit_snapshot(connection),
        )
    db.refresh(connection)
    return _serialize(connection)


@router.patch("/{connection_id}", response_model=WhatsAppConnectionRead)
def update_whatsapp_connection(
    connection_id: int,
    payload: WhatsAppConnectionUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    tenant_id = _tenant_id(db, current_user)
    if connection.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    data = payload.model_dump(exclude_unset=True)
    target_market = data.get("market_id", connection.channel_account.market_id)
    _validate_market(db, tenant_id=tenant_id, market_id=target_market)
    crypto = _crypto() if connection.transport == META_CLOUD_API_TRANSPORT else None
    with managed_session(db):
        before = connection_audit_snapshot(connection)
        if "display_name" in data:
            connection.channel_account.display_name = data["display_name"]
        if "market_id" in data:
            connection.channel_account.market_id = data["market_id"]
        if "priority" in data:
            connection.channel_account.priority = data["priority"]
        for field in (
            "sidecar_session_key",
            "business_account_id",
            "waba_id",
            "phone_number_id",
            "graph_api_version",
        ):
            if field in data:
                setattr(connection, field, data[field])
        for secret_field, model_field in (
            ("access_token", "access_token_encrypted"),
            ("app_secret", "app_secret_encrypted"),
            ("verify_token", "verify_token_encrypted"),
        ):
            if secret_field in data:
                if not crypto:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"{secret_field}_meta_only",
                    )
                setattr(connection, model_field, crypto.encrypt(data[secret_field]))
                if secret_field == "access_token":
                    connection.access_token_fingerprint = crypto.fingerprint(
                        data[secret_field]
                    )
        connection.updated_by = current_user.id
        connection.desired_generation += 1
        connection.desired_state = "disabled"
        connection.channel_account.is_active = False
        reset_verification(connection)
        validate_whatsapp_connection_configuration(connection)
        db.flush()
        log_admin_audit(
            db,
            actor_id=current_user.id,
            action="whatsapp_connection.update",
            target_type="whatsapp_connection",
            target_id=connection.id,
            old_value=before,
            new_value=connection_audit_snapshot(connection),
        )
    db.refresh(connection)
    return _serialize(connection)


@router.post("/{connection_id}/binding/start", response_model=WhatsAppBindingStatus)
def start_whatsapp_binding(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    try:
        with managed_session(db):
            before = connection_audit_snapshot(connection)
            connection.desired_state = "binding"
            connection.channel_account.is_active = False
            connection.desired_generation += 1
            connection.updated_by = current_user.id
            reset_verification(connection)
            db.flush()
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action="whatsapp_connection.binding_requested",
                target_type="whatsapp_connection",
                target_id=connection.id,
                old_value=before,
                new_value=connection_audit_snapshot(connection),
            )
        if connection.transport == BAILEYS_SIDECAR_TRANSPORT:
            snapshot = call_baileys_account_action(
                connection,
                "start",
                method="POST",
            )
            qr_status = snapshot.qr_status
            qr_data_url = snapshot.qr_data_url
        else:
            access_token, _app_secret, verify_token = _meta_secrets(connection)
            settings = get_whatsapp_runtime_settings()
            callback_url = (
                f"{settings.meta_webhook_public_url.rstrip('/')}/"
                f"api/integrations/whatsapp/meta/{connection.id}/webhook"
                if settings.meta_webhook_public_url
                else None
            )
            subscribe_meta_waba(
                connection,
                access_token=access_token,
                callback_url=callback_url,
                verify_token=verify_token if callback_url else None,
            )
            snapshot = probe_meta_cloud_connection(
                connection,
                access_token=access_token,
            )
            qr_status = None
            qr_data_url = None
        with managed_session(db):
            before_observed = connection_audit_snapshot(connection)
            if connection.transport == BAILEYS_SIDECAR_TRANSPORT:
                connection.session_generation = max(
                    connection.session_generation,
                    snapshot.generation,
                )
                apply_observed_snapshot(connection, snapshot.as_dict())
            else:
                apply_observed_snapshot(connection, snapshot.as_dict())
            db.flush()
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action="whatsapp_connection.binding_observed",
                target_type="whatsapp_connection",
                target_id=connection.id,
                old_value=before_observed,
                new_value=connection_audit_snapshot(connection),
            )
    except Exception as exc:
        with managed_session(db):
            connection.last_error_code = _safe_code(exc)
            connection.last_error_message = _safe_code(exc)
            connection.last_probe_status = "failed"
            connection.last_probe_at = utc_now()
            db.flush()
        raise _http_error(exc) from exc
    return _binding_status(
        connection,
        qr_status=qr_status,
        qr_data_url=qr_data_url,
    )


@router.get("/{connection_id}/binding/qr", response_model=WhatsAppBindingStatus)
def get_whatsapp_binding_qr(
    connection_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    if connection.transport != BAILEYS_SIDECAR_TRANSPORT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="baileys_qr_required",
        )
    try:
        snapshot = call_baileys_account_action(
            connection,
            "qr",
            method="GET",
        )
        with managed_session(db):
            apply_observed_snapshot(connection, snapshot.as_dict())
            db.flush()
    except Exception as exc:
        raise _http_error(exc) from exc
    response.headers["Cache-Control"] = "no-store, private, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return _binding_status(
        connection,
        qr_status=snapshot.qr_status,
        qr_data_url=snapshot.qr_data_url,
    )


@router.post("/{connection_id}/binding/pairing-code", response_model=WhatsAppPairingCodeRead)
def request_whatsapp_pairing_code(
    connection_id: int,
    payload: WhatsAppPairingCodeRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    if connection.transport != BAILEYS_SIDECAR_TRANSPORT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="baileys_pairing_code_required",
        )
    try:
        result = request_baileys_pairing_code(
            connection,
            phone_number=payload.phone_number,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    return WhatsAppPairingCodeRead(**asdict(result))


@router.post("/{connection_id}/logout", response_model=WhatsAppBindingStatus)
def logout_whatsapp_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    try:
        snapshot = None
        if connection.transport == BAILEYS_SIDECAR_TRANSPORT:
            snapshot = call_baileys_account_action(
                connection,
                "logout",
                method="POST",
            )
        with managed_session(db):
            before = connection_audit_snapshot(connection)
            if snapshot is not None:
                apply_observed_snapshot(connection, snapshot.as_dict())
                connection.session_generation += 1
            else:
                connection.observed_state = "logged_out"
                connection.authentication_state = "revoked"
                connection.listener_state = "stopped"
            connection.desired_state = "disabled"
            connection.channel_account.is_active = False
            connection.desired_generation += 1
            reset_verification(connection)
            db.flush()
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action="whatsapp_connection.logout",
                target_type="whatsapp_connection",
                target_id=connection.id,
                old_value=before,
                new_value=connection_audit_snapshot(connection),
            )
    except Exception as exc:
        raise _http_error(exc) from exc
    return _binding_status(connection)


@router.post("/{connection_id}/restart", response_model=WhatsAppBindingStatus)
def restart_whatsapp_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    try:
        snapshot = (
            call_baileys_account_action(
                connection,
                "restart",
                method="POST",
            ).as_dict()
            if connection.transport == BAILEYS_SIDECAR_TRANSPORT
            else probe_meta_cloud_connection(
                connection,
                access_token=_meta_secrets(connection)[0],
            ).as_dict()
        )
        with managed_session(db):
            apply_observed_snapshot(connection, snapshot)
            db.flush()
    except Exception as exc:
        raise _http_error(exc) from exc
    return _binding_status(connection)


@router.post("/{connection_id}/probe", response_model=WhatsAppBindingStatus)
def probe_whatsapp_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    try:
        snapshot = _probe(connection)
        with managed_session(db):
            apply_observed_snapshot(connection, snapshot)
            db.flush()
    except Exception as exc:
        with managed_session(db):
            connection.last_probe_at = utc_now()
            connection.last_probe_status = "failed"
            connection.last_error_code = _safe_code(exc)
            connection.last_error_message = _safe_code(exc)
            db.flush()
        raise _http_error(exc) from exc
    return _binding_status(connection)


@router.post("/{connection_id}/desired-state", response_model=WhatsAppConnectionRead)
def update_whatsapp_desired_state(
    connection_id: int,
    payload: WhatsAppDesiredStateUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    try:
        with managed_session(db):
            before = connection_audit_snapshot(connection)
            set_desired_state(
                connection,
                payload.desired_state,
                actor_id=current_user.id,
            )
            connection.channel_account.is_active = payload.desired_state == "active"
            db.flush()
            log_admin_audit(
                db,
                actor_id=current_user.id,
                action="whatsapp_connection.desired_state",
                target_type="whatsapp_connection",
                target_id=connection.id,
                old_value=before,
                new_value=connection_audit_snapshot(connection),
            )
    except Exception as exc:
        raise _http_error(exc) from exc
    return _serialize(connection)


@router.post("/{connection_id}/meta/subscribe", response_model=WhatsAppBindingStatus)
def subscribe_whatsapp_meta_webhook(
    connection_id: int,
    payload: WhatsAppMetaSubscriptionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    if connection.transport != META_CLOUD_API_TRANSPORT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="meta_transport_required",
        )
    try:
        access_token, _app_secret, verify_token = _meta_secrets(connection)
        subscribe_meta_waba(
            connection,
            access_token=access_token,
            callback_url=payload.callback_url,
            verify_token=verify_token if payload.callback_url else None,
        )
        snapshot = probe_meta_cloud_connection(
            connection,
            access_token=access_token,
        )
        with managed_session(db):
            apply_observed_snapshot(connection, snapshot.as_dict())
            db.flush()
    except Exception as exc:
        raise _http_error(exc) from exc
    return _binding_status(connection)


@router.post("/{connection_id}/test-inbound", response_model=WhatsAppTestResult)
def test_whatsapp_inbound(
    connection_id: int,
    payload: WhatsAppTestInboundRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    message = (
        db.query(WhatsAppInboundMessage)
        .filter(
            WhatsAppInboundMessage.channel_account_id
            == connection.channel_account_id,
            WhatsAppInboundMessage.external_message_id
            == payload.provider_message_id,
            WhatsAppInboundMessage.processed_at.isnot(None),
        )
        .first()
    )
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="verified_inbound_message_not_found",
        )
    with managed_session(db):
        record_verification_evidence(connection, inbound=True)
        db.flush()
    return WhatsAppTestResult(
        ok=True,
        connection_id=connection.id,
        transport=connection.transport,
        provider_message_id=message.external_message_id,
        verification_state=connection.verification_state,
        occurred_at=connection.inbound_tested_at or utc_now(),
    )


@router.post("/{connection_id}/test-outbound", response_model=WhatsAppTestResult)
def test_whatsapp_outbound(
    connection_id: int,
    payload: WhatsAppTestOutboundRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_channel_accounts(current_user, db)
    connection = _connection(db, connection_id)
    try:
        if connection.transport == BAILEYS_SIDECAR_TRANSPORT:
            result = send_baileys_text(
                connection,
                target=payload.target,
                body=payload.body,
                idempotency_key=(
                    f"whatsapp-connection-test:{connection.id}:"
                    f"{connection.desired_generation}"
                ),
                metadata={
                    "purpose": "whatsapp_connection_test",
                    "connection_id": connection.id,
                },
            )
        else:
            result = send_meta_cloud_text(
                connection,
                access_token=_meta_secrets(connection)[0],
                target=payload.target,
                body=payload.body,
            )
        with managed_session(db):
            connection.last_outbound_at = result.sent_at
            record_verification_evidence(connection, outbound=True)
            db.flush()
    except Exception as exc:
        raise _http_error(exc) from exc
    return WhatsAppTestResult(
        ok=True,
        connection_id=connection.id,
        transport=connection.transport,
        provider_message_id=result.provider_message_id,
        verification_state=connection.verification_state,
        occurred_at=result.sent_at,
    )
