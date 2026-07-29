from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..enums import SourceChannel
from ..models import BackgroundJob, ChannelAccount, Tenant
from ..models_agent_routing import ConversationControl
from ..models_whatsapp import WhatsAppConnection
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .data_subject_action_service import (
    DataProcessingRestricted,
    ensure_data_processing_allowed,
)
from .message_dispatch import ensure_external_dispatch_allowed
from .secret_crypto import SecretCryptoService
from .webchat_ai_turn_service import safe_write_webchat_event
from .whatsapp_baileys_sidecar import BaileysSidecarError, send_baileys_text
from .whatsapp_meta_cloud import MetaCloudTransportError, send_meta_cloud_text
from .whatsapp_runtime_settings import get_whatsapp_runtime_settings

WEBCHAT_WHATSAPP_DELIVERY_JOB = "webchat.whatsapp_delivery"


class TicketlessWhatsAppDeliveryError(RuntimeError):
    def __init__(self, code: str, reason: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class TicketlessWhatsAppRoute:
    channel_account_id: int
    chat_jid: str
    sender_phone: str | None


def queue_ticketless_whatsapp_delivery(
    db: Session,
    *,
    conversation: WebchatConversation,
    message: WebchatMessage,
) -> BackgroundJob:
    if conversation.id is None or message.id is None:
        raise RuntimeError("ticketless_whatsapp_delivery_requires_persisted_message")
    if message.conversation_id != conversation.id or message.ticket_id is not None:
        raise RuntimeError("ticketless_whatsapp_delivery_message_scope_mismatch")
    if str(conversation.channel_key or "").strip().lower() != SourceChannel.whatsapp.value:
        raise RuntimeError("ticketless_whatsapp_delivery_requires_whatsapp")
    if message.direction != "agent":
        raise RuntimeError("ticketless_whatsapp_delivery_requires_agent_message")

    metadata = _metadata(message)
    purpose = _processing_purpose(message, metadata=metadata)
    route = _latest_route(db, conversation=conversation)
    from .background_jobs import enqueue_background_job

    job = enqueue_background_job(
        db,
        queue_name="webchat_channel_delivery",
        job_type=WEBCHAT_WHATSAPP_DELIVERY_JOB,
        payload={
            "conversation_id": conversation.id,
            "webchat_message_id": message.id,
            "channel_account_id": route.channel_account_id,
        },
        dedupe_key=f"webchat-whatsapp-delivery:{message.id}",
    )
    metadata.update(
        {
            "external_send": True,
            "reply_channel": SourceChannel.whatsapp.value,
            "delivery_job_id": job.id,
            "delivery_job_type": WEBCHAT_WHATSAPP_DELIVERY_JOB,
            "processing_purpose": purpose,
        }
    )
    message.metadata_json = _json(metadata)
    message.delivery_status = "queued"
    db.flush()
    return job


def process_ticketless_whatsapp_delivery_job(
    db: Session,
    *,
    job: BackgroundJob,
    payload: dict[str, Any],
) -> None:
    conversation_id = _positive_int(payload.get("conversation_id"))
    message_id = _positive_int(payload.get("webchat_message_id"))
    account_id = _positive_int(payload.get("channel_account_id"))
    if not conversation_id or not message_id or not account_id:
        raise RuntimeError("ticketless_whatsapp_delivery_payload_invalid")

    conversation = db.get(WebchatConversation, conversation_id)
    message = db.get(WebchatMessage, message_id)
    account = db.get(ChannelAccount, account_id)
    if conversation is None or message is None or account is None:
        raise RuntimeError("ticketless_whatsapp_delivery_authority_missing")
    if message.conversation_id != conversation.id or message.ticket_id is not None:
        raise RuntimeError("ticketless_whatsapp_delivery_message_scope_mismatch")
    if str(conversation.channel_key or "").strip().lower() != SourceChannel.whatsapp.value:
        raise RuntimeError("ticketless_whatsapp_delivery_requires_whatsapp")
    if message.delivery_status == "sent" and _metadata(message).get("provider_message_id"):
        _scrub_job(job, message_id=message.id, outcome="already_sent")
        return

    metadata = _metadata(message)
    purpose = _processing_purpose(message, metadata=metadata)
    if metadata.get("processing_purpose") != purpose:
        raise RuntimeError("ticketless_whatsapp_delivery_purpose_mismatch")
    connection = (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.channel_account_id == account.id)
        .one_or_none()
    )
    try:
        _require_authority(
            db,
            conversation=conversation,
            account=account,
            connection=connection,
        )
        route = _latest_route(db, conversation=conversation)
        if route.channel_account_id != account.id:
            raise TicketlessWhatsAppDeliveryError(
                "ticketless_whatsapp_delivery_route_account_mismatch",
                "Conversation route and queued account differ",
                retryable=False,
            )
        body = str(message.body_text or message.body or "").strip()
        if not body:
            raise TicketlessWhatsAppDeliveryError(
                "ticketless_whatsapp_delivery_body_missing",
                "Customer-visible message body is missing",
                retryable=False,
            )
        control = (
            db.query(ConversationControl)
            .filter(ConversationControl.conversation_id == conversation.id)
            .one_or_none()
        )
        if control is None or control.customer_id is None:
            raise TicketlessWhatsAppDeliveryError(
                "ticketless_whatsapp_delivery_customer_scope_missing",
                "Conversation customer scope is missing",
                retryable=False,
            )
        if not get_whatsapp_runtime_settings().enabled:
            raise TicketlessWhatsAppDeliveryError(
                "whatsapp_disabled",
                "WhatsApp is disabled in this runtime",
                retryable=False,
            )
        try:
            ensure_external_dispatch_allowed()
        except RuntimeError as exc:
            raise TicketlessWhatsAppDeliveryError(
                "outbound_dispatch_disabled",
                str(exc),
                retryable=False,
            ) from exc

        # The canonical Job scope permits human support; this origin-specific
        # revalidation is the final guard immediately before Provider I/O.
        try:
            ensure_data_processing_allowed(
                db,
                customer_id=control.customer_id,
                purpose=purpose,
            )
        except DataProcessingRestricted as exc:
            raise TicketlessWhatsAppDeliveryError(
                "data_processing_restricted",
                str(exc),
                retryable=False,
            ) from exc
        result = _send(
            connection=connection,
            route=route,
            message=message,
            body=body,
            conversation_id=conversation.id,
            account_id=account.id,
        )
    except (BaileysSidecarError, MetaCloudTransportError) as exc:
        _handle_failure(
            db,
            job=job,
            conversation=conversation,
            message=message,
            code=exc.code,
            reason=exc.message,
            retryable=exc.retryable,
        )
        return
    except TicketlessWhatsAppDeliveryError as exc:
        _handle_failure(
            db,
            job=job,
            conversation=conversation,
            message=message,
            code=exc.code,
            reason=exc.reason,
            retryable=exc.retryable,
        )
        return

    sent_at = ensure_utc(result.sent_at) or utc_now()
    metadata.update(
        {
            "provider_status": "whatsapp_sent",
            "provider_message_id": result.provider_message_id,
            "provider_transport": connection.transport,
            "provider_sent_at": sent_at.isoformat(),
            "delivery_error_code": None,
            "delivery_error_message": None,
            "processing_purpose": purpose,
        }
    )
    message.delivery_status = "sent"
    message.metadata_json = _json(metadata)
    conversation.last_seen_at = sent_at
    conversation.updated_at = utc_now()
    connection.last_outbound_at = sent_at
    connection.last_error_code = None
    connection.last_error_message = None
    _scrub_job(job, message_id=message.id, outcome="sent")
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type="whatsapp.ticketless_delivery_sent",
        payload={
            "webchat_message_id": message.id,
            "channel_account_id": account.id,
            "connection_id": connection.id,
            "transport": connection.transport,
            "provider_message_id": result.provider_message_id,
            "sent_at": sent_at.isoformat(),
            "processing_purpose": purpose,
        },
    )
    db.flush()


def _processing_purpose(
    message: WebchatMessage,
    *,
    metadata: dict[str, Any],
) -> str:
    provider_status = str(metadata.get("provider_status") or "").strip()
    generated_by = str(metadata.get("generated_by") or "").strip()
    if (
        provider_status == "whatsapp_ai_reply_queued"
        or generated_by == "agent_runtime"
        or message.ai_turn_id is not None
    ):
        return "automated_ai"
    if (
        provider_status == "whatsapp_agent_reply_queued"
        or message.author_user_id is not None
        or str(message.author_label or "").strip()
    ):
        return "human_support"
    raise RuntimeError("ticketless_whatsapp_delivery_origin_unsupported")


def _send(
    *,
    connection: WhatsAppConnection,
    route: TicketlessWhatsAppRoute,
    message: WebchatMessage,
    body: str,
    conversation_id: int,
    account_id: int,
):
    idempotency_key = f"nexusdesk-webchat-message-{message.id}"
    if connection.transport == "baileys_sidecar":
        return send_baileys_text(
            connection,
            target=route.chat_jid,
            body=body,
            idempotency_key=idempotency_key,
            metadata={
                "conversation_id": conversation_id,
                "webchat_message_id": message.id,
                "connection_id": connection.id,
                "channel_account_id": account_id,
            },
        )
    if connection.transport == "meta_cloud_api":
        token = SecretCryptoService.whatsapp().decrypt(
            connection.access_token_encrypted
        )
        if not token:
            raise TicketlessWhatsAppDeliveryError(
                "meta_access_token_missing",
                "Meta access token is not configured",
                retryable=False,
            )
        return send_meta_cloud_text(
            connection,
            access_token=token,
            target=route.sender_phone or route.chat_jid,
            body=body,
        )
    raise TicketlessWhatsAppDeliveryError(
        "unsupported_whatsapp_transport",
        "WhatsApp connection transport is unsupported",
        retryable=False,
    )


def _require_authority(
    db: Session,
    *,
    conversation: WebchatConversation,
    account: ChannelAccount,
    connection: WhatsAppConnection | None,
) -> None:
    if account.provider != SourceChannel.whatsapp.value or not account.is_active:
        raise TicketlessWhatsAppDeliveryError(
            "ticketless_whatsapp_delivery_account_inactive",
            "WhatsApp channel account is inactive",
            retryable=False,
        )
    tenant_key = str(conversation.tenant_key or "").strip().lower()
    tenant = (
        db.query(Tenant)
        .filter(Tenant.tenant_key == tenant_key, Tenant.is_active.is_(True))
        .one_or_none()
    )
    if tenant is None or account.tenant_id != tenant.id:
        raise TicketlessWhatsAppDeliveryError(
            "ticketless_whatsapp_delivery_tenant_mismatch",
            "Conversation and account tenant authority differ",
            retryable=False,
        )
    if connection is None or connection.tenant_id != tenant.id:
        raise TicketlessWhatsAppDeliveryError(
            "ticketless_whatsapp_delivery_connection_missing",
            "WhatsApp connection authority is missing",
            retryable=False,
        )
    if (
        connection.desired_state != "active"
        or connection.observed_state != "connected"
        or connection.authentication_state != "linked"
        or connection.listener_state != "active"
        or connection.verification_state != "verified"
        or connection.observed_generation != connection.desired_generation
    ):
        raise TicketlessWhatsAppDeliveryError(
            "ticketless_whatsapp_delivery_connection_not_ready",
            "WhatsApp connection is not ready",
            retryable=True,
        )


def _latest_route(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> TicketlessWhatsAppRoute:
    rows = (
        db.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "visitor",
        )
        .order_by(WebchatMessage.id.desc())
        .limit(50)
        .all()
    )
    for row in rows:
        metadata = _metadata(row)
        account_id = _positive_int(metadata.get("channel_account_id"))
        chat_jid = str(metadata.get("chat_jid") or "").strip()
        if account_id and chat_jid:
            return TicketlessWhatsAppRoute(
                channel_account_id=account_id,
                chat_jid=chat_jid,
                sender_phone=str(metadata.get("sender_phone") or "").strip() or None,
            )
    raise RuntimeError("ticketless_whatsapp_delivery_route_missing")


def _handle_failure(
    db: Session,
    *,
    job: BackgroundJob,
    conversation: WebchatConversation,
    message: WebchatMessage,
    code: str,
    reason: str,
    retryable: bool,
) -> None:
    attempts_remaining = int(job.attempt_count or 0) + 1 < int(job.max_attempts or 1)
    if retryable and attempts_remaining:
        metadata = _metadata(message)
        metadata.update(
            {
                "provider_status": "whatsapp_retry_scheduled",
                "delivery_error_code": code[:120],
                "delivery_error_message": reason[:500],
            }
        )
        message.delivery_status = "queued"
        message.metadata_json = _json(metadata)
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            event_type="whatsapp.ticketless_delivery_retry_scheduled",
            payload={"webchat_message_id": message.id, "error_code": code[:120]},
        )
        db.flush()
        raise RuntimeError(code)

    terminal_code = f"{code}_retry_exhausted" if retryable else code
    metadata = _metadata(message)
    metadata.update(
        {
            "provider_status": "whatsapp_failed",
            "delivery_error_code": terminal_code[:120],
            "delivery_error_message": reason[:500],
        }
    )
    message.delivery_status = "failed"
    message.metadata_json = _json(metadata)
    _scrub_job(
        job,
        message_id=message.id,
        outcome="failed",
        code=terminal_code,
    )
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        event_type="whatsapp.ticketless_delivery_failed",
        payload={
            "webchat_message_id": message.id,
            "error_code": terminal_code[:120],
        },
    )
    db.flush()


def _scrub_job(
    job: BackgroundJob,
    *,
    message_id: int,
    outcome: str,
    code: str | None = None,
) -> None:
    job.payload_json = _json(
        {
            "scrubbed": True,
            "job_type": job.job_type,
            "webchat_message_id": message_id,
            "outcome": outcome,
            "error_code": code[:120] if code else None,
        }
    )


def _metadata(message: WebchatMessage) -> dict[str, Any]:
    try:
        value = json.loads(message.metadata_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "WEBCHAT_WHATSAPP_DELIVERY_JOB",
    "process_ticketless_whatsapp_delivery_job",
    "queue_ticketless_whatsapp_delivery",
]
