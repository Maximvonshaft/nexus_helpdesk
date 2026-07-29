from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppConnection, WhatsAppMediaAsset
from ..models_whatsapp_outbound import WhatsAppOutboundPart
from .whatsapp_inbound import WhatsAppInboundError, ingest_whatsapp_inbound


class WhatsAppUatEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class WhatsAppUatSelection:
    inbound_provider_message_id: str
    outbound_provider_message_id: str
    media_inbound_provider_message_id: str | None = None
    media_outbound_provider_message_id: str | None = None


def replay_whatsapp_uat_inbound(
    db: Session,
    *,
    connection: WhatsAppConnection,
    provider_message_id: str,
) -> dict[str, Any]:
    provider_id = _safe_provider_id(
        provider_message_id,
        "whatsapp_uat_inbound_provider_id_invalid",
    )
    inbound = (
        db.query(WhatsAppInboundMessage)
        .filter(
            WhatsAppInboundMessage.channel_account_id
            == connection.channel_account_id,
            WhatsAppInboundMessage.external_message_id == provider_id,
            WhatsAppInboundMessage.processed_at.isnot(None),
        )
        .first()
    )
    if inbound is None:
        raise WhatsAppUatEvidenceError("whatsapp_uat_inbound_not_found")
    raw = (
        dict(inbound.raw_payload_json)
        if isinstance(inbound.raw_payload_json, dict)
        else {}
    )
    raw.update(
        {
            "transport": connection.transport,
            "account_id": connection.channel_account.account_id,
            "external_message_id": inbound.external_message_id,
            "chat_jid": inbound.chat_jid,
            "sender_jid": inbound.sender_jid,
            "sender_phone": inbound.sender_phone,
            "message_type": inbound.message_type,
            "body_text": inbound.body_text,
            "received_at": _timestamp(inbound.received_at),
        }
    )
    try:
        result = ingest_whatsapp_inbound(db, raw)
    except WhatsAppInboundError as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else "whatsapp_uat_replay_failed"
        raise WhatsAppUatEvidenceError(code) from exc
    if (
        result.idempotent is not True
        or result.inbound_message_id != inbound.id
        or result.ticket_id != inbound.ticket_id
        or result.conversation_id != inbound.conversation_id
        or result.webchat_message_id != inbound.webchat_message_id
    ):
        raise WhatsAppUatEvidenceError("whatsapp_uat_idempotent_replay_failed")
    db.flush()
    return {
        "ok": True,
        "idempotent": True,
        "connection_id": connection.id,
        "transport": connection.transport,
        "provider_message_id": inbound.external_message_id,
        "inbound_message_id": inbound.id,
        "ticket_id": inbound.ticket_id,
        "conversation_id": inbound.conversation_id,
        "webchat_message_id": inbound.webchat_message_id,
        "contains_secrets": False,
        "contains_full_phone_numbers": False,
    }


def collect_whatsapp_uat_facts(
    db: Session,
    *,
    connection: WhatsAppConnection,
    selection: WhatsAppUatSelection,
) -> dict[str, Any]:
    _validate_selection(selection)
    account = connection.channel_account
    if (
        account is None
        or account.provider != "whatsapp"
        or account.tenant_id != connection.tenant_id
    ):
        raise WhatsAppUatEvidenceError("whatsapp_uat_connection_scope_invalid")
    inbound = (
        db.query(WhatsAppInboundMessage)
        .filter(
            WhatsAppInboundMessage.channel_account_id
            == connection.channel_account_id,
            WhatsAppInboundMessage.external_message_id
            == selection.inbound_provider_message_id,
            WhatsAppInboundMessage.processed_at.isnot(None),
        )
        .first()
    )
    if inbound is None:
        raise WhatsAppUatEvidenceError("whatsapp_uat_inbound_not_found")
    outbound = _outbound_part(
        db,
        connection=connection,
        provider_message_id=selection.outbound_provider_message_id,
    )
    payload: dict[str, Any] = {
        "transport": connection.transport,
        "connection_id": connection.id,
        "account_id": account.account_id,
        "phone_suffix": _phone_suffix(connection.phone_number),
        "binding": {
            "observed_state": connection.observed_state,
            "authentication_state": connection.authentication_state,
            "listener_state": connection.listener_state,
            "desired_generation": connection.desired_generation,
            "observed_generation": connection.observed_generation,
            "session_generation": connection.session_generation,
        },
        "inbound": {
            "provider_message_id": inbound.external_message_id,
            "received_at": _timestamp(inbound.received_at),
            "stored": True,
            "inbound_message_id": inbound.id,
        },
        "outbound": _part_facts(outbound),
        "contains_secrets": False,
        "contains_full_phone_numbers": False,
    }
    media_requested = bool(
        selection.media_inbound_provider_message_id
        or selection.media_outbound_provider_message_id
    )
    if media_requested:
        if not (
            selection.media_inbound_provider_message_id
            and selection.media_outbound_provider_message_id
        ):
            raise WhatsAppUatEvidenceError(
                "whatsapp_uat_media_selection_incomplete"
            )
        media_inbound = (
            db.query(WhatsAppInboundMessage)
            .filter(
                WhatsAppInboundMessage.channel_account_id
                == connection.channel_account_id,
                WhatsAppInboundMessage.external_message_id
                == selection.media_inbound_provider_message_id,
                WhatsAppInboundMessage.processed_at.isnot(None),
            )
            .first()
        )
        if media_inbound is None:
            raise WhatsAppUatEvidenceError(
                "whatsapp_uat_media_inbound_not_found"
            )
        asset = (
            db.query(WhatsAppMediaAsset)
            .filter(
                WhatsAppMediaAsset.connection_id == connection.id,
                WhatsAppMediaAsset.inbound_message_id == media_inbound.id,
            )
            .order_by(WhatsAppMediaAsset.id.desc())
            .first()
        )
        if asset is None:
            raise WhatsAppUatEvidenceError(
                "whatsapp_uat_media_asset_not_found"
            )
        media_outbound = _outbound_part(
            db,
            connection=connection,
            provider_message_id=selection.media_outbound_provider_message_id,
            require_media=True,
        )
        payload["media"] = {
            "inbound": {
                "provider_message_id": media_inbound.external_message_id,
                "asset_id": asset.id,
                "attachment_id": asset.ticket_attachment_id,
                "scan_status": asset.scan_status,
                "storage_status": asset.storage_status,
                "sha256": asset.sha256,
                "byte_size": asset.byte_size,
            },
            "outbound": _part_facts(media_outbound),
        }
    return payload


def _outbound_part(
    db: Session,
    *,
    connection: WhatsAppConnection,
    provider_message_id: str,
    require_media: bool = False,
) -> WhatsAppOutboundPart:
    query = db.query(WhatsAppOutboundPart).filter(
        WhatsAppOutboundPart.connection_id == connection.id,
        WhatsAppOutboundPart.tenant_id == connection.tenant_id,
        WhatsAppOutboundPart.provider_message_id == provider_message_id,
    )
    if require_media:
        query = query.filter(WhatsAppOutboundPart.part_type == "media")
    row = query.first()
    if row is None:
        raise WhatsAppUatEvidenceError(
            "whatsapp_uat_media_outbound_not_found"
            if require_media
            else "whatsapp_uat_outbound_not_found"
        )
    parent = row.outbound_message
    ticket = parent.ticket if parent is not None else None
    if (
        parent is None
        or ticket is None
        or ticket.tenant_id != connection.tenant_id
        or ticket.channel_account_id != connection.channel_account_id
    ):
        raise WhatsAppUatEvidenceError(
            "whatsapp_uat_outbound_scope_invalid"
        )
    return row


def _part_facts(part: WhatsAppOutboundPart) -> dict[str, Any]:
    return {
        "provider_message_id": part.provider_message_id,
        "status": part.status,
        "sent_at": _timestamp(part.sent_at),
        "delivered_at": _timestamp(part.delivered_at),
        "read_at": _timestamp(part.read_at),
        "outbound_message_id": part.outbound_message_id,
        "outbound_part_id": part.id,
        "part_type": part.part_type,
    }


def _validate_selection(selection: WhatsAppUatSelection) -> None:
    for value, code in (
        (
            selection.inbound_provider_message_id,
            "whatsapp_uat_inbound_provider_id_invalid",
        ),
        (
            selection.outbound_provider_message_id,
            "whatsapp_uat_outbound_provider_id_invalid",
        ),
    ):
        _safe_provider_id(value, code)
    for value, code in (
        (
            selection.media_inbound_provider_message_id,
            "whatsapp_uat_media_inbound_provider_id_invalid",
        ),
        (
            selection.media_outbound_provider_message_id,
            "whatsapp_uat_media_outbound_provider_id_invalid",
        ),
    ):
        if value is not None:
            _safe_provider_id(value, code)


def _safe_provider_id(value: str, code: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 255
        or any(char in normalized for char in "\r\n\x00")
    ):
        raise WhatsAppUatEvidenceError(code)
    return normalized


def _phone_suffix(value: str | None) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 4:
        raise WhatsAppUatEvidenceError("whatsapp_uat_phone_suffix_missing")
    return digits[-4:]


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
