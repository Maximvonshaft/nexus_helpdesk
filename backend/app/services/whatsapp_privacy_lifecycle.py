from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import TicketOutboundMessage, WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppMediaAsset
from ..models_whatsapp_outbound import WhatsAppOutboundPart
from ..utils.time import utc_now


class WhatsAppPrivacyLifecycleError(RuntimeError):
    pass


def collect_whatsapp_subject_export(
    db: Session,
    *,
    ticket_ids: list[int],
    max_rows: int,
) -> dict[str, list[dict[str, Any]]]:
    if not ticket_ids:
        return {
            "whatsapp_inbound_messages": [],
            "whatsapp_media_assets": [],
            "whatsapp_outbound_parts": [],
        }
    inbound_rows = (
        db.query(WhatsAppInboundMessage)
        .filter(WhatsAppInboundMessage.ticket_id.in_(ticket_ids))
        .order_by(WhatsAppInboundMessage.id.asc())
        .limit(max_rows + 1)
        .all()
    )
    outbound_ids = [
        int(value)
        for (value,) in db.query(TicketOutboundMessage.id)
        .filter(TicketOutboundMessage.ticket_id.in_(ticket_ids))
        .order_by(TicketOutboundMessage.id.asc())
        .all()
    ]
    inbound_ids = [int(row.id) for row in inbound_rows]
    media_rows = []
    if inbound_ids or outbound_ids:
        filters = []
        if inbound_ids:
            filters.append(WhatsAppMediaAsset.inbound_message_id.in_(inbound_ids))
        if outbound_ids:
            filters.append(WhatsAppMediaAsset.outbound_message_id.in_(outbound_ids))
        media_rows = (
            db.query(WhatsAppMediaAsset)
            .filter(or_(*filters))
            .order_by(WhatsAppMediaAsset.id.asc())
            .limit(max_rows + 1)
            .all()
        )
    part_rows = (
        db.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id.in_(outbound_ids))
        .order_by(
            WhatsAppOutboundPart.outbound_message_id.asc(),
            WhatsAppOutboundPart.sequence.asc(),
        )
        .limit(max_rows + 1)
        .all()
        if outbound_ids
        else []
    )
    for label, rows in (
        ("whatsapp_inbound_messages", inbound_rows),
        ("whatsapp_media_assets", media_rows),
        ("whatsapp_outbound_parts", part_rows),
    ):
        if len(rows) > max_rows:
            raise WhatsAppPrivacyLifecycleError(
                f"dsar_export_{label}_requires_storage_job"
            )
    return {
        "whatsapp_inbound_messages": [
            {
                "id": row.id,
                "ticket_id": row.ticket_id,
                "external_message_id": row.external_message_id,
                "chat_jid": row.chat_jid,
                "sender_jid": row.sender_jid,
                "sender_phone": row.sender_phone,
                "message_type": row.message_type,
                "body_text": row.body_text,
                "received_at": row.received_at.isoformat(),
                "processed_at": (
                    row.processed_at.isoformat() if row.processed_at else None
                ),
            }
            for row in inbound_rows
        ],
        "whatsapp_media_assets": [
            {
                "id": row.id,
                "inbound_message_id": row.inbound_message_id,
                "outbound_message_id": row.outbound_message_id,
                "provider": row.provider,
                "provider_media_id": row.provider_media_id,
                "media_kind": row.media_kind,
                "file_name": row.file_name,
                "declared_mime_type": row.declared_mime_type,
                "detected_mime_type": row.detected_mime_type,
                "byte_size": row.byte_size,
                "sha256": row.sha256,
                "storage_status": row.storage_status,
                "scan_status": row.scan_status,
                "ticket_attachment_id": row.ticket_attachment_id,
                "created_at": row.created_at.isoformat(),
                "available_at": (
                    row.available_at.isoformat() if row.available_at else None
                ),
            }
            for row in media_rows
        ],
        "whatsapp_outbound_parts": [
            {
                "id": row.id,
                "outbound_message_id": row.outbound_message_id,
                "attachment_id": row.attachment_id,
                "sequence": row.sequence,
                "part_type": row.part_type,
                "media_kind": row.media_kind,
                "media_type": row.media_type,
                "file_name": row.file_name,
                "status": row.status,
                "provider_media_id": row.provider_media_id,
                "provider_message_id": row.provider_message_id,
                "failure_code": row.failure_code,
                "failure_reason": row.failure_reason,
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                "delivered_at": (
                    row.delivered_at.isoformat() if row.delivered_at else None
                ),
                "read_at": row.read_at.isoformat() if row.read_at else None,
            }
            for row in part_rows
        ],
    }


def redact_whatsapp_subject_records(
    db: Session,
    *,
    ticket_ids: list[int],
    anonymize: Callable[..., str],
) -> int:
    if not ticket_ids:
        return 0
    inbound_ids = [
        int(value)
        for (value,) in db.query(WhatsAppInboundMessage.id)
        .filter(WhatsAppInboundMessage.ticket_id.in_(ticket_ids))
        .all()
    ]
    outbound_ids = [
        int(value)
        for (value,) in db.query(TicketOutboundMessage.id)
        .filter(TicketOutboundMessage.ticket_id.in_(ticket_ids))
        .all()
    ]
    media_rows = []
    if inbound_ids or outbound_ids:
        filters = []
        if inbound_ids:
            filters.append(WhatsAppMediaAsset.inbound_message_id.in_(inbound_ids))
        if outbound_ids:
            filters.append(WhatsAppMediaAsset.outbound_message_id.in_(outbound_ids))
        media_rows = db.query(WhatsAppMediaAsset).filter(or_(*filters)).all()
    for row in media_rows:
        row.provider_media_id = anonymize(
            row.provider_media_id,
            namespace="whatsapp-media",
            record_id=row.id,
        )
        row.file_name = "[redacted by privacy request]"
        row.storage_key = None
        row.ticket_attachment_id = None
        row.sha256 = None
        row.provider_url_expires_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.storage_status = "deleted"
        row.updated_at = utc_now()
    part_rows = (
        db.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id.in_(outbound_ids))
        .all()
        if outbound_ids
        else []
    )
    for row in part_rows:
        row.provider_media_id = None
        row.provider_message_id = None
        row.file_name = (
            "[redacted by privacy request]" if row.file_name else None
        )
        row.failure_code = None
        row.failure_reason = None
        row.updated_at = utc_now()
    db.flush()
    return len(media_rows) + len(part_rows)
