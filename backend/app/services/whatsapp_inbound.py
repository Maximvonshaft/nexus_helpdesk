from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..enums import JobStatus, SourceChannel
from ..models import BackgroundJob, ChannelAccount, WhatsAppInboundMessage
from ..models_whatsapp import WhatsAppConnection
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .background_jobs import WEBCHAT_AI_REPLY_JOB, enqueue_background_job
from .channel_intake_service import (
    ChannelIntakeError,
    append_channel_customer_message,
    resolve_channel_intake_context,
)
from .whatsapp_runtime_settings import get_whatsapp_runtime_settings
from .webchat_ai_turn_service import (
    ai_snapshot,
    safe_write_webchat_event,
    schedule_webchat_ai_turn,
)


class WhatsAppConnectorAuthError(ValueError):
    pass


class WhatsAppInboundError(ValueError):
    pass


SELF_ECHO_TEST_SOURCE = "self_echo_test"
DEFAULT_SELF_ECHO_TEST_PREFIX = "NEXUS_SELF_INBOUND_TEST"
VALID_PROJECTION_MODES = {"visitor", "store_only", "test_visitor"}
NON_CUSTOMER_CHAT_ERROR = "ignored_whatsapp_non_customer_chat"


@dataclass(frozen=True)
class WhatsAppInboundResult:
    ok: bool
    idempotent: bool
    inbound_message_id: int
    ticket_id: int | None
    conversation_id: int | None
    webchat_message_id: int | None
    ai_turn_id: int | None = None
    ai_status: str | None = None
    pre_activation: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "idempotent": self.idempotent,
            "inbound_message_id": self.inbound_message_id,
            "ticket_id": self.ticket_id,
            "conversation_id": self.conversation_id,
            "webchat_message_id": self.webchat_message_id,
            "ai_turn_id": self.ai_turn_id,
            "ai_status": self.ai_status,
            "pre_activation": self.pre_activation,
        }


def verify_whatsapp_connector_headers(
    *,
    raw_body: bytes,
    connector_key: str | None,
    account_id: str | None,
    timestamp: str | None,
    signature: str | None,
) -> None:
    settings = get_whatsapp_runtime_settings()
    if not settings.connector_key or not settings.connector_hmac_secret:
        raise WhatsAppConnectorAuthError("whatsapp_connector_secret_missing")
    if not connector_key or not hmac.compare_digest(
        connector_key,
        settings.connector_key,
    ):
        raise WhatsAppConnectorAuthError("invalid_connector_key")
    if not account_id:
        raise WhatsAppConnectorAuthError("missing_account_id")
    if not timestamp:
        raise WhatsAppConnectorAuthError("missing_timestamp")
    if not signature:
        raise WhatsAppConnectorAuthError("missing_signature")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WhatsAppConnectorAuthError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = abs(
        (
            utc_now().astimezone(timezone.utc)
            - parsed.astimezone(timezone.utc)
        ).total_seconds()
    )
    if age > settings.connector_timestamp_tolerance_seconds:
        raise WhatsAppConnectorAuthError("stale_timestamp")
    expected = hmac.new(
        settings.connector_hmac_secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise WhatsAppConnectorAuthError("invalid_signature")


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _is_customer_chat_jid(value: str | None) -> bool:
    jid = (value or "").strip()
    if not jid or jid == "status@broadcast":
        return False
    return not (
        jid.endswith("@broadcast")
        or jid.endswith("@g.us")
        or jid.endswith("@newsletter")
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _projection_mode(
    payload: dict[str, Any],
    *,
    from_me: bool,
    body_text: str,
) -> str:
    mode = (_clip(payload.get("projection_mode"), 40) or "visitor").lower()
    if mode not in VALID_PROJECTION_MODES:
        raise WhatsAppInboundError("invalid_whatsapp_projection_mode")
    if not from_me:
        return "visitor"
    if mode == "store_only":
        return "store_only"
    if mode != "test_visitor":
        return "store_only"
    prefix = (
        _clip(payload.get("self_echo_test_prefix"), 120)
        or DEFAULT_SELF_ECHO_TEST_PREFIX
    )
    return "test_visitor" if body_text.startswith(prefix) else "store_only"


def _strip_self_echo_test_prefix(
    payload: dict[str, Any],
    body_text: str,
    projection_mode: str,
) -> str:
    if projection_mode != "test_visitor":
        return body_text
    prefix = (
        _clip(payload.get("self_echo_test_prefix"), 120)
        or DEFAULT_SELF_ECHO_TEST_PREFIX
    )
    if not body_text.startswith(prefix):
        return body_text
    stripped = body_text[len(prefix) :].strip()
    return stripped or body_text


def _account_context(
    db: Session,
    account_id: str,
) -> tuple[ChannelAccount, WhatsAppConnection, bool]:
    account = (
        db.query(ChannelAccount)
        .filter(
            ChannelAccount.account_id == account_id,
            ChannelAccount.provider == SourceChannel.whatsapp.value,
        )
        .first()
    )
    if account is None:
        raise WhatsAppInboundError("unknown_whatsapp_channel_account")
    if account.tenant_id is None:
        raise WhatsAppInboundError("whatsapp_channel_account_tenant_missing")
    connection = (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.channel_account_id == account.id)
        .first()
    )
    if connection is None or connection.tenant_id != account.tenant_id:
        raise WhatsAppInboundError("whatsapp_connection_scope_missing")
    pre_activation = not account.is_active or connection.desired_state != "active"
    if pre_activation and not (
        connection.observed_state == "connected"
        and connection.authentication_state == "linked"
        and connection.listener_state == "active"
    ):
        raise WhatsAppInboundError("whatsapp_connection_not_ready")
    return account, connection, pre_activation


def _schedule_ai_turn(
    db: Session,
    *,
    conversation: WebchatConversation,
    visitor_message: WebchatMessage,
) -> dict[str, Any]:
    def create_job(payload: dict[str, Any], dedupe_key: str, scheduled_at) -> BackgroundJob:
        return enqueue_background_job(
            db,
            queue_name="webchat_ai_reply",
            job_type=WEBCHAT_AI_REPLY_JOB,
            payload=payload,
            dedupe_key=dedupe_key,
            next_run_at=scheduled_at,
        )

    stale_pre_turn_job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.dedupe_key == f"webchat-ai-reply:{visitor_message.id}",
            BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]),
        )
        .order_by(BackgroundJob.id.desc())
        .first()
    )
    if stale_pre_turn_job is not None:
        stale_pre_turn_job.status = JobStatus.done
        stale_pre_turn_job.updated_at = utc_now()

    return schedule_webchat_ai_turn(
        db,
        conversation=conversation,
        ticket_id=conversation.ticket_id,
        visitor_message=visitor_message,
        create_job=create_job,
        debounce_seconds=float(
            getattr(get_settings(), "webchat_ai_turn_debounce_seconds", 0.15)
            or 0
        ),
    )


def _received_at(value: str | None) -> datetime:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if value
            else utc_now()
        )
    except ValueError:
        parsed = utc_now()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ingest_whatsapp_inbound(
    db: Session,
    payload: dict[str, Any],
) -> WhatsAppInboundResult:
    account_id = _clip(payload.get("account_id"), 160)
    external_message_id = _clip(payload.get("external_message_id"), 180)
    chat_jid = _clip(payload.get("chat_jid"), 180)
    sender_jid = _clip(payload.get("sender_jid"), 180) or chat_jid
    raw_body_text = _clip(payload.get("body_text"), 4000)
    if not account_id or not external_message_id or not chat_jid or not raw_body_text:
        raise WhatsAppInboundError("invalid_whatsapp_inbound_payload")
    if not _is_customer_chat_jid(chat_jid):
        raise WhatsAppInboundError(NON_CUSTOMER_CHAT_ERROR)

    account, connection, pre_activation = _account_context(db, account_id)
    existing = (
        db.query(WhatsAppInboundMessage)
        .filter(
            WhatsAppInboundMessage.channel_account_id == account.id,
            WhatsAppInboundMessage.external_message_id == external_message_id,
        )
        .first()
    )
    if existing is not None:
        conversation = (
            db.get(WebchatConversation, existing.conversation_id)
            if existing.conversation_id
            else None
        )
        snapshot = ai_snapshot(conversation) if conversation else {}
        return WhatsAppInboundResult(
            ok=True,
            idempotent=True,
            inbound_message_id=existing.id,
            ticket_id=existing.ticket_id,
            conversation_id=existing.conversation_id,
            webchat_message_id=existing.webchat_message_id,
            ai_turn_id=snapshot.get("ai_turn_id"),
            ai_status=snapshot.get("ai_status"),
            pre_activation=pre_activation,
        )

    sender_phone = _clip(payload.get("sender_phone"), 80)
    sender_name = _clip(payload.get("sender_name"), 160)
    from_me = _truthy(payload.get("from_me"))
    projection_mode = _projection_mode(
        payload,
        from_me=from_me,
        body_text=raw_body_text,
    )
    body_text = _strip_self_echo_test_prefix(
        payload,
        raw_body_text,
        projection_mode,
    )
    received_at = _received_at(_clip(payload.get("received_at"), 80))
    transport = _clip(payload.get("transport"), 40) or connection.transport

    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id=external_message_id,
        chat_jid=chat_jid,
        sender_jid=sender_jid or chat_jid,
        sender_phone=sender_phone,
        message_type=_clip(payload.get("message_type"), 80) or "text",
        body_text=body_text,
        raw_payload_json=_bounded_payload(payload),
        received_at=received_at,
        created_at=utc_now(),
    )
    db.add(inbound)
    db.flush()
    connection.last_inbound_at = received_at

    # Pre-activation inbound is durable provider evidence only. It must not enter
    # the customer inbox, trigger AI or create responsibility before the account
    # has passed both real-direction tests and been explicitly activated.
    if pre_activation or (from_me and projection_mode == "store_only"):
        inbound.processed_at = utc_now()
        db.flush()
        return WhatsAppInboundResult(
            ok=True,
            idempotent=False,
            inbound_message_id=inbound.id,
            ticket_id=None,
            conversation_id=None,
            webchat_message_id=None,
            ai_turn_id=None,
            ai_status=None,
            pre_activation=pre_activation,
        )

    external_ref = f"whatsapp:{chat_jid}"[:160]
    display_name = sender_name or sender_phone or f"WhatsApp {chat_jid.split('@')[0]}"
    try:
        context = resolve_channel_intake_context(
            db,
            account=account,
            channel_key=SourceChannel.whatsapp.value,
            external_conversation_key=chat_jid,
            identity_type="phone" if sender_phone else "external_ref",
            identity_value=sender_phone or external_ref,
            display_name=display_name,
            visitor_phone=sender_phone,
            visitor_ref=external_ref,
            origin=f"whatsapp-{transport}",
        )
        projected = append_channel_customer_message(
            db,
            context=context,
            external_message_id=external_message_id,
            body=body_text,
            created_at=received_at,
            author_label=display_name,
            metadata={
                "generated_by": "whatsapp_inbound",
                "source": (
                    SELF_ECHO_TEST_SOURCE
                    if from_me and projection_mode == "test_visitor"
                    else f"whatsapp_{transport}"
                ),
                "transport": transport,
                "from_me": from_me,
                "projection_mode": projection_mode,
                "account_id": account.account_id,
                "channel_account_id": account.id,
                "external_message_id": external_message_id,
                "chat_jid": chat_jid,
                "sender_jid": sender_jid,
                "sender_phone": sender_phone,
                "reply_to_message_id": _clip(
                    payload.get("reply_to_message_id"),
                    180,
                ),
                "media_id": _clip(payload.get("media_id"), 255),
                "media_mime_type": _clip(
                    payload.get("media_mime_type"),
                    160,
                ),
            },
        )
    except ChannelIntakeError as exc:
        raise WhatsAppInboundError(str(exc)) from exc

    inbound.ticket_id = projected.ticket.id if projected.ticket is not None else None
    inbound.conversation_id = context.conversation.id
    inbound.webchat_message_id = projected.message.id
    inbound.processed_at = utc_now()
    safe_write_webchat_event(
        db,
        conversation_id=context.conversation.id,
        ticket_id=inbound.ticket_id,
        event_type="whatsapp.inbound_projected",
        payload={
            "whatsapp_inbound_message_id": inbound.id,
            "message_id": projected.message.id,
            "external_message_id": external_message_id,
            "transport": transport,
            "ticketless": projected.ticket is None,
        },
    )
    snapshot = _schedule_ai_turn(
        db,
        conversation=context.conversation,
        visitor_message=projected.message,
    )
    db.flush()
    return WhatsAppInboundResult(
        ok=True,
        idempotent=False,
        inbound_message_id=inbound.id,
        ticket_id=inbound.ticket_id,
        conversation_id=context.conversation.id,
        webchat_message_id=projected.message.id,
        ai_turn_id=snapshot.get("ai_turn_id"),
        ai_status=snapshot.get("ai_status"),
        pre_activation=False,
    )


def _bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Avoid persisting full Baileys/Meta envelopes. Keep only the normalized
    # support evidence and bounded provider fragment required for diagnostics.
    allowed = {
        "transport",
        "account_id",
        "external_message_id",
        "chat_jid",
        "sender_jid",
        "sender_phone",
        "sender_name",
        "message_type",
        "body_text",
        "received_at",
        "from_me",
        "projection_mode",
        "reply_to_message_id",
        "media_id",
        "media_mime_type",
        "raw_message",
    }
    return {key: value for key, value in payload.items() if key in allowed}
