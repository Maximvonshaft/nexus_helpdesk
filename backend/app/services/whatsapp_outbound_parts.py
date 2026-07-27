from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import TicketOutboundMessage
from ..models_whatsapp import WhatsAppConnection
from ..models_whatsapp_outbound import WhatsAppOutboundPart
from ..utils.time import utc_now
from .secret_crypto import SecretCryptoService
from .whatsapp_attachment_bytes import (
    WhatsAppAttachmentError,
    load_whatsapp_attachment,
)
from .whatsapp_baileys_sidecar import (
    BaileysSidecarError,
    send_baileys_media,
    send_baileys_text,
)
from .whatsapp_meta_cloud import (
    MetaCloudTransportError,
    send_meta_cloud_text,
)
from .whatsapp_meta_media import send_meta_cloud_media


_SUCCESS_STATES = {"sent", "delivered", "read"}


@dataclass(frozen=True)
class WhatsAppPartsDispatchResult:
    ok: bool
    provider_message_ids: tuple[str, ...]
    sent_at: datetime | None
    failure_code: str | None = None
    failure_reason: str | None = None
    retryable: bool = False


def dispatch_whatsapp_parts(
    db: Session,
    *,
    connection: WhatsAppConnection,
    message: TicketOutboundMessage,
    target: str,
) -> WhatsAppPartsDispatchResult:
    ticket = message.ticket
    if (
        ticket is None
        or ticket.tenant_id != connection.tenant_id
        or ticket.channel_account_id != connection.channel_account_id
    ):
        return WhatsAppPartsDispatchResult(
            ok=False,
            provider_message_ids=(),
            sent_at=None,
            failure_code="whatsapp_outbound_part_scope_mismatch",
            failure_reason="WhatsApp outbound message does not belong to the selected connection",
            retryable=False,
        )
    parts = _ensure_parts(db, connection=connection, message=message)
    provider_ids: list[str] = []
    sent_at_values: list[datetime] = []
    access_token: str | None = None
    for part in parts:
        if part.status in _SUCCESS_STATES and part.provider_message_id:
            provider_ids.append(part.provider_message_id)
            if part.sent_at:
                sent_at_values.append(part.sent_at)
            continue
        try:
            if part.part_type == "text":
                result = _send_text_part(
                    connection,
                    message=message,
                    part=part,
                    target=target,
                    access_token=access_token,
                )
            else:
                attachment = part.attachment
                if attachment is None:
                    raise WhatsAppAttachmentError("whatsapp_outbound_attachment_missing")
                loaded = load_whatsapp_attachment(attachment)
                if connection.transport == "baileys_sidecar":
                    result = send_baileys_media(
                        connection,
                        target=target,
                        content=loaded.content,
                        media_kind=loaded.media_kind,
                        media_type=loaded.media_type,
                        filename=loaded.filename,
                        caption=None,
                        idempotency_key=part.idempotency_key,
                        metadata=_metadata(connection, message, part),
                    )
                    provider_media_id = None
                elif connection.transport == "meta_cloud_api":
                    if access_token is None:
                        access_token = _meta_access_token(connection)
                    meta_result = send_meta_cloud_media(
                        connection,
                        access_token=access_token,
                        target=target,
                        content=loaded.content,
                        media_kind=loaded.media_kind,
                        media_type=loaded.media_type,
                        filename=loaded.filename,
                        caption=None,
                    )
                    result = meta_result
                    provider_media_id = meta_result.provider_media_id
                else:
                    raise ValueError("unsupported_whatsapp_transport")
                part.media_kind = loaded.media_kind
                part.media_type = loaded.media_type
                part.file_name = loaded.filename
                part.provider_media_id = provider_media_id
            part.status = "sent"
            part.provider_message_id = result.provider_message_id
            part.sent_at = result.sent_at
            part.receipt_at = result.sent_at
            part.failure_code = None
            part.failure_reason = None
            part.updated_at = utc_now()
            db.flush()
            provider_ids.append(result.provider_message_id)
            sent_at_values.append(result.sent_at)
        except (BaileysSidecarError, MetaCloudTransportError) as exc:
            return _failed_part(
                db,
                part=part,
                provider_ids=provider_ids,
                sent_at_values=sent_at_values,
                code=exc.code,
                reason=exc.message,
                retryable=exc.retryable,
            )
        except WhatsAppAttachmentError as exc:
            code = exc.args[0] if exc.args else "whatsapp_attachment_failed"
            return _failed_part(
                db,
                part=part,
                provider_ids=provider_ids,
                sent_at_values=sent_at_values,
                code=str(code),
                reason=str(code),
                retryable=str(code).endswith("storage_read_failed"),
            )
        except (RuntimeError, ValueError) as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else "whatsapp_part_dispatch_failed"
            return _failed_part(
                db,
                part=part,
                provider_ids=provider_ids,
                sent_at_values=sent_at_values,
                code=code,
                reason=code,
                retryable=False,
            )
    return WhatsAppPartsDispatchResult(
        ok=True,
        provider_message_ids=tuple(provider_ids),
        sent_at=max(sent_at_values) if sent_at_values else utc_now(),
    )


def _ensure_parts(
    db: Session,
    *,
    connection: WhatsAppConnection,
    message: TicketOutboundMessage,
) -> list[WhatsAppOutboundPart]:
    existing = (
        db.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id == message.id)
        .order_by(WhatsAppOutboundPart.sequence.asc())
        .all()
    )
    if existing:
        if any(part.connection_id != connection.id for part in existing):
            raise ValueError("whatsapp_outbound_part_connection_mismatch")
        return existing
    sequence = 0
    body = str(message.body or "").strip()
    if body:
        db.add(
            WhatsAppOutboundPart(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                outbound_message_id=message.id,
                attachment_id=None,
                sequence=sequence,
                part_type="text",
                idempotency_key=f"nexusdesk-wa-part-{message.id}-{sequence}",
                status="queued",
            )
        )
        sequence += 1
    links = sorted(
        (link for link in message.attachment_links if link.attachment is not None),
        key=lambda link: (link.id or 0, link.attachment_id),
    )
    for link in links:
        db.add(
            WhatsAppOutboundPart(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                outbound_message_id=message.id,
                attachment_id=link.attachment_id,
                sequence=sequence,
                part_type="media",
                media_type=link.attachment.mime_type,
                file_name=link.attachment.file_name,
                idempotency_key=f"nexusdesk-wa-part-{message.id}-{sequence}",
                status="queued",
            )
        )
        sequence += 1
    if sequence == 0:
        raise ValueError("whatsapp_outbound_content_missing")
    db.flush()
    return (
        db.query(WhatsAppOutboundPart)
        .filter(WhatsAppOutboundPart.outbound_message_id == message.id)
        .order_by(WhatsAppOutboundPart.sequence.asc())
        .all()
    )


def _send_text_part(
    connection: WhatsAppConnection,
    *,
    message: TicketOutboundMessage,
    part: WhatsAppOutboundPart,
    target: str,
    access_token: str | None,
):
    if connection.transport == "baileys_sidecar":
        return send_baileys_text(
            connection,
            target=target,
            body=message.body,
            idempotency_key=part.idempotency_key,
            metadata=_metadata(connection, message, part),
        )
    if connection.transport == "meta_cloud_api":
        token = access_token or _meta_access_token(connection)
        return send_meta_cloud_text(
            connection,
            access_token=token,
            target=target,
            body=message.body,
        )
    raise ValueError("unsupported_whatsapp_transport")


def _meta_access_token(connection: WhatsAppConnection) -> str:
    token = SecretCryptoService.whatsapp().decrypt(connection.access_token_encrypted)
    if not token:
        raise ValueError("meta_access_token_missing")
    return token


def _metadata(
    connection: WhatsAppConnection,
    message: TicketOutboundMessage,
    part: WhatsAppOutboundPart,
) -> dict[str, int]:
    return {
        "tenant_id": connection.tenant_id,
        "ticket_id": message.ticket_id,
        "outbound_message_id": message.id,
        "connection_id": connection.id,
        "outbound_part_id": part.id,
        "sequence": part.sequence,
    }


def _failed_part(
    db: Session,
    *,
    part: WhatsAppOutboundPart,
    provider_ids: list[str],
    sent_at_values: list[datetime],
    code: str,
    reason: str,
    retryable: bool,
) -> WhatsAppPartsDispatchResult:
    part.status = "failed"
    part.failure_code = code[:120]
    part.failure_reason = reason[:1000]
    part.receipt_at = utc_now()
    part.updated_at = utc_now()
    db.flush()
    return WhatsAppPartsDispatchResult(
        ok=False,
        provider_message_ids=tuple(provider_ids),
        sent_at=max(sent_at_values) if sent_at_values else None,
        failure_code=code[:120],
        failure_reason=reason[:1000],
        retryable=retryable,
    )
