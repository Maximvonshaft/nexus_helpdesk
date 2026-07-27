from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..models import ChannelAccount
from ..models_whatsapp import WhatsAppConnection
from ..utils.time import utc_now
from .whatsapp_transport_registry import (
    BAILEYS_SIDECAR_TRANSPORT,
    META_CLOUD_API_TRANSPORT,
    normalize_whatsapp_transport,
)


_GRAPH_VERSION = re.compile(r"^v\d{1,3}\.\d{1,3}$")


class WhatsAppConnectionError(ValueError):
    pass


class WhatsAppActivationError(WhatsAppConnectionError):
    pass


def get_whatsapp_channel_account(
    db: Session,
    channel_account_id: int,
) -> ChannelAccount:
    row = (
        db.query(ChannelAccount)
        .filter(
            ChannelAccount.id == channel_account_id,
            ChannelAccount.provider == "whatsapp",
        )
        .first()
    )
    if row is None:
        raise WhatsAppConnectionError("whatsapp_channel_account_not_found")
    if row.tenant_id is None:
        raise WhatsAppConnectionError("whatsapp_channel_account_tenant_missing")
    return row


def get_whatsapp_connection(
    db: Session,
    connection_id: int,
) -> WhatsAppConnection:
    row = db.get(WhatsAppConnection, connection_id)
    if row is None:
        raise WhatsAppConnectionError("whatsapp_connection_not_found")
    return row


def get_whatsapp_connection_for_channel_account(
    db: Session,
    channel_account_id: int,
) -> WhatsAppConnection | None:
    return (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.channel_account_id == channel_account_id)
        .first()
    )


def validate_whatsapp_connection_configuration(
    connection: WhatsAppConnection,
) -> None:
    transport = normalize_whatsapp_transport(connection.transport)
    if connection.tenant_id is None or connection.channel_account_id is None:
        raise WhatsAppConnectionError("whatsapp_connection_scope_missing")

    if transport == BAILEYS_SIDECAR_TRANSPORT:
        if not str(connection.sidecar_session_key or "").strip():
            raise WhatsAppConnectionError("baileys_session_key_required")
        return

    missing = []
    if not str(connection.waba_id or "").strip():
        missing.append("waba_id")
    if not str(connection.phone_number_id or "").strip():
        missing.append("phone_number_id")
    if not str(connection.graph_api_version or "").strip():
        missing.append("graph_api_version")
    if not connection.access_token_encrypted:
        missing.append("access_token")
    if not connection.app_secret_encrypted:
        missing.append("app_secret")
    if not connection.verify_token_encrypted:
        missing.append("verify_token")
    if missing:
        raise WhatsAppConnectionError(
            "meta_cloud_configuration_missing:" + ",".join(missing)
        )
    if not _GRAPH_VERSION.fullmatch(str(connection.graph_api_version)):
        raise WhatsAppConnectionError("invalid_meta_graph_api_version")


def assert_connection_can_activate(connection: WhatsAppConnection) -> None:
    validate_whatsapp_connection_configuration(connection)
    if connection.verification_state != "verified":
        raise WhatsAppActivationError("verification_required")
    if connection.authentication_state != "linked":
        raise WhatsAppActivationError("authentication_not_linked")
    if connection.listener_state != "active":
        raise WhatsAppActivationError("listener_not_active")
    if connection.observed_state != "connected":
        raise WhatsAppActivationError("transport_not_connected")
    if connection.observed_generation != connection.desired_generation:
        raise WhatsAppActivationError("observed_generation_stale")


def set_desired_state(
    connection: WhatsAppConnection,
    desired_state: str,
    *,
    actor_id: int | None,
) -> None:
    normalized = str(desired_state or "").strip().lower()
    if normalized not in {"disabled", "active"}:
        raise WhatsAppConnectionError("invalid_whatsapp_desired_state")
    if normalized == "active":
        assert_connection_can_activate(connection)
    if connection.desired_state != normalized:
        connection.desired_generation += 1
    connection.desired_state = normalized
    connection.updated_by = actor_id
    connection.updated_at = utc_now()


def apply_observed_snapshot(
    connection: WhatsAppConnection,
    snapshot: dict[str, Any],
) -> None:
    observed_state = str(snapshot.get("status") or snapshot.get("observed_state") or "").strip().lower()
    state_map = {
        "idle": "auth_required",
        "qr_pending": "qr_pending",
        "pairing": "auth_persisting",
        "connecting": "connecting",
        "connected": "connected",
        "reconnecting": "degraded",
        "disconnected": "logged_out",
        "logged_out": "logged_out",
        "error": "error",
        "disabled": "disabled",
    }
    if observed_state:
        connection.observed_state = state_map.get(observed_state, "error")

    auth_state = str(snapshot.get("authentication_state") or "").strip().lower()
    if auth_state in {"unconfigured", "pending", "linked", "unstable", "revoked", "error"}:
        connection.authentication_state = auth_state
    elif connection.observed_state == "connected":
        connection.authentication_state = "linked"
    elif connection.observed_state in {"qr_pending", "auth_persisting", "connecting"}:
        connection.authentication_state = "pending"
    elif connection.observed_state == "logged_out":
        connection.authentication_state = "revoked"

    listener_state = str(snapshot.get("listener_state") or "").strip().lower()
    if listener_state in {"stopped", "starting", "active", "reconnecting", "error"}:
        connection.listener_state = listener_state
    elif connection.observed_state == "connected":
        connection.listener_state = "active"
    elif connection.observed_state in {"connecting", "qr_pending", "auth_persisting"}:
        connection.listener_state = "starting"
    elif connection.observed_state == "degraded":
        connection.listener_state = "reconnecting"
    elif connection.observed_state in {"logged_out", "disabled"}:
        connection.listener_state = "stopped"
    elif connection.observed_state == "error":
        connection.listener_state = "error"

    connection.phone_number = _optional(snapshot.get("phone_number")) or connection.phone_number
    connection.jid = _optional(snapshot.get("jid")) or connection.jid
    connection.last_error_code = _optional(snapshot.get("last_error_code"))
    connection.last_error_message = _optional(snapshot.get("last_error_message"))
    connection.reconnect_count = max(
        0,
        int(snapshot.get("reconnect_count") or connection.reconnect_count or 0),
    )
    generation = snapshot.get("generation")
    if generation is not None:
        connection.observed_generation = max(0, int(generation))
    elif connection.observed_state == "connected":
        connection.observed_generation = connection.desired_generation

    for field in (
        "last_qr_generated_at",
        "qr_expires_at",
        "last_connected_at",
        "last_disconnected_at",
        "last_inbound_at",
        "last_outbound_at",
    ):
        value = snapshot.get(field)
        if value is not None:
            setattr(connection, field, value)
    connection.last_probe_at = utc_now()
    connection.last_probe_status = (
        "success" if connection.observed_state == "connected" else connection.observed_state
    )
    connection.updated_at = utc_now()


def record_verification_evidence(
    connection: WhatsAppConnection,
    *,
    inbound: bool = False,
    outbound: bool = False,
) -> None:
    now = utc_now()
    if inbound:
        connection.inbound_tested_at = now
    if outbound:
        connection.outbound_tested_at = now
    if connection.inbound_tested_at and connection.outbound_tested_at:
        connection.verification_state = "verified"
        connection.verified_at = now
    elif connection.inbound_tested_at:
        connection.verification_state = "inbound_verified"
    elif connection.outbound_tested_at:
        connection.verification_state = "outbound_verified"
    else:
        connection.verification_state = "pending"
    connection.updated_at = now


def reset_verification(connection: WhatsAppConnection) -> None:
    connection.verification_state = "pending"
    connection.inbound_tested_at = None
    connection.outbound_tested_at = None
    connection.verified_at = None
    connection.updated_at = utc_now()


def connection_audit_snapshot(connection: WhatsAppConnection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "tenant_id": connection.tenant_id,
        "channel_account_id": connection.channel_account_id,
        "transport": connection.transport,
        "desired_state": connection.desired_state,
        "observed_state": connection.observed_state,
        "authentication_state": connection.authentication_state,
        "listener_state": connection.listener_state,
        "verification_state": connection.verification_state,
        "desired_generation": connection.desired_generation,
        "observed_generation": connection.observed_generation,
        "phone_number": _masked_phone(connection.phone_number),
        "jid": {"configured": bool(connection.jid), "redacted": True},
        "business_account_id": connection.business_account_id,
        "waba_id": connection.waba_id,
        "phone_number_id": connection.phone_number_id,
        "graph_api_version": connection.graph_api_version,
        "access_token": {
            "configured": bool(connection.access_token_encrypted),
            "redacted": True,
        },
        "app_secret": {
            "configured": bool(connection.app_secret_encrypted),
            "redacted": True,
        },
        "verify_token": {
            "configured": bool(connection.verify_token_encrypted),
            "redacted": True,
        },
        "sidecar_session_key": connection.sidecar_session_key,
        "session_generation": connection.session_generation,
        "last_probe_status": connection.last_probe_status,
        "last_error_code": connection.last_error_code,
    }


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _masked_phone(value: str | None) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return None
    return f"•••• {digits[-4:]}"
