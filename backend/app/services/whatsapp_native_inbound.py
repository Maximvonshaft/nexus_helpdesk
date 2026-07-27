from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..enums import JobStatus, SourceChannel
from ..models import BackgroundJob, ChannelAccount, WhatsAppInboundMessage
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .background_jobs import WEBCHAT_AI_REPLY_JOB, enqueue_background_job
from .channel_intake_service import (
    ChannelIntakeError,
    append_channel_customer_message,
    resolve_channel_intake_context,
)
from .webchat_ai_turn_service import (
    ai_snapshot,
    safe_write_webchat_event,
    schedule_webchat_ai_turn,
)


class WhatsAppNativeAuthError(ValueError):
    pass


class WhatsAppNativeInboundError(ValueError):
    pass


SELF_ECHO_TEST_SOURCE = "self_echo_test"
SELF_CHAT_SOURCE = "self_chat"
DEFAULT_SELF_ECHO_TEST_PREFIX = "NEXUS_SELF_INBOUND_TEST"
VALID_PROJECTION_MODES = {"visitor", "store_only", "test_visitor", "self_chat"}
NON_CUSTOMER_CHAT_ERROR = "ignored_whatsapp_non_customer_chat"


@dataclass(frozen=True)
class WhatsAppNativeInboundResult:
    ok: bool
    idempotent: bool
    inbound_message_id: int
    ticket_id: int | None
    conversation_id: int | None
    webchat_message_id: int | None
    ai_turn_id: int | None = None
    ai_status: str | None = None

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
        }


def verify_whatsapp_connector_headers(
    *,
    raw_body: bytes,
    connector_key: str | None,
    account_id: str | None,
    timestamp: str | None,
    signature: str | None,
) -> None:
    settings = get_settings()
    if not settings.whatsapp_connector_key or not settings.whatsapp_connector_hmac_secret:
        raise WhatsAppNativeAuthError("whatsapp_connector_secret_missing")
    if not connector_key or not hmac.compare_digest(
        connector_key,
        settings.whatsapp_connector_key,
    ):
        raise WhatsAppNativeAuthError("invalid_connector_key")
    if not account_id:
        raise WhatsAppNativeAuthError("missing_account_id")
    if not timestamp:
        raise WhatsAppNativeAuthError("missing_timestamp")
    if not signature:
        raise WhatsAppNativeAuthError("missing_signature")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WhatsAppNativeAuthError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = abs(
        (
            utc_now().astimezone(timezone.utc)
            - parsed.astimezone(timezone.utc)
        ).total_seconds()
    )
    if age > settings.whatsapp_connector_timestamp_tolerance_seconds:
        raise WhatsAppNativeAuthError("stale_timestamp")
    expected = hmac.new(
        settings.whatsapp_connector_hmac_secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise WhatsAppNativeAuthError("invalid_signature")


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _is_customer_chat_jid(value: str | None) -> bool:
    jid = (value or "").strip()
    if not jid:
        return False
    if jid == "status@broadcast":
        return False
    if jid.endswith("@broadcast"):
        return False
    if jid.endswith("@g.us"):
        return False
    if jid.endswith("@newsletter"):
        return False
    return True


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
    raw_mode = _clip(payload.get("projection_mode"), 40) or "visitor"
    mode = raw_mode.lower()
    if mode not in VALID_PROJECTION_MODES:
        raise WhatsAppNativeInboundError("invalid_whatsapp_projection_mode")
    if not from_me:
        return "visitor"
    if mode in {"store_only", "self_chat"}:
        return "store_only"
    if mode != "test_visitor":
        return "store_only"
    prefix = (
        _clip(payload.get("self_echo_test_prefix"), 120)
        or DEFAULT_SELF_ECHO_TEST_PREFIX
    )
    if not body_text.startswith(prefix):
        return "store_only"
    return "test_visitor"


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


def _active_whatsapp_account(db: Session, account_id: str) -> ChannelAccount:
    row = (
        db.query(ChannelAccount)
        .filter(
            ChannelAccount.account_id == account_id,
            ChannelAccount.provider == SourceChannel.whatsapp.value,
            ChannelAccount.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        raise WhatsAppNativeInboundError("unknown_whatsapp_channel_account")
    if row.tenant_id is None:
        raise WhatsAppNativeInboundError("whatsapp_channel_account_tenant_missing")
    return row


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
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def ingest_whatsapp_native_inbound(
    db: Session,
    payload: dict[str, Any],
) -> WhatsAppNativeInboundResult:
    account_id = _clip(payload.get("account_id"), 160)
    external_message_id = _clip(payload.get("external_message_id"), 180)
    chat_jid = _clip(payload.get("chat_jid"), 180)
    sender_jid = _clip(payload.get("sender_jid"), 180) or chat_jid
    raw_body_text = _clip(payload.get("body_text"), 4000)
    if not account_id or not external_message_id or not chat_jid or not raw_body_text:
        raise WhatsAppNativeInboundError("invalid_whatsapp_inbound_payload")
    if not _is_customer_chat_jid(chat_jid):
        raise WhatsAppNativeInboundError(NON_CUSTOMER_CHAT_ERROR)

    account = _active_whatsapp_account(db, account_id)
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
        return WhatsAppNativeInboundResult(
            ok=True,
            idempotent=True,
            inbound_message_id=existing.id,
            ticket_id=existing.ticket_id,
            conversation_id=existing.conversation_id,
            webchat_message_id=existing.webchat_message_id,
            ai_turn_id=snapshot.get("ai_turn_id"),
            ai_status=snapshot.get("ai_status"),
        )

    sender_phone = _clip(payload.get("sender_phone"), 80)
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

    inbound = WhatsAppInboundMessage(
        channel_account_id=account.id,
        account_id=account.account_id,
        external_message_id=external_message_id,
        chat_jid=chat_jid,
        sender_jid=sender_jid or chat_jid,
        sender_phone=sender_phone,
        message_type=_clip(payload.get("message_type"), 80) or "text",
        body_text=body_text,
        raw_payload_json=payload,
        received_at=received_at,
        created_at=utc_now(),
    )
    db.add(inbound)
    db.flush()

    if from_me and projection_mode == "store_only":
        inbound.processed_at = utc_now()
        db.flush()
        return WhatsAppNativeInboundResult(
            ok=True,
            idempotent=False,
            inbound_message_id=inbound.id,
            ticket_id=None,
            conversation_id=None,
            webchat_message_id=None,
            ai_turn_id=None,
            ai_status=None,
        )

    external_ref = f"whatsapp:{chat_jid}"[:160]
    try:
        context = resolve_channel_intake_context(
            db,
            account=account,
            channel_key=SourceChannel.whatsapp.value,
            external_conversation_key=chat_jid,
            identity_type="phone" if sender_phone else "external_ref",
            identity_value=sender_phone or external_ref,
            display_name=sender_phone or f"WhatsApp {chat_jid.split('@')[0]}",
            visitor_phone=sender_phone,
            visitor_ref=external_ref,
            origin="whatsapp-native",
        )
        projected = append_channel_customer_message(
            db,
            context=context,
            external_message_id=external_message_id,
            body=body_text,
            created_at=received_at,
            author_label=sender_phone or "WhatsApp Customer",
            metadata={
                "generated_by": "whatsapp_native_inbound",
                "source": (
                    SELF_ECHO_TEST_SOURCE
                    if from_me and projection_mode == "test_visitor"
                    else SELF_CHAT_SOURCE
                    if from_me and projection_mode == "self_chat"
                    else "whatsapp_native"
                ),
                "from_me": from_me,
                "projection_mode": projection_mode,
                "account_id": account.account_id,
                "channel_account_id": account.id,
                "external_message_id": external_message_id,
                "chat_jid": chat_jid,
                "sender_jid": sender_jid,
                "sender_phone": sender_phone,
            },
        )
    except ChannelIntakeError as exc:
        raise WhatsAppNativeInboundError(str(exc)) from exc

    inbound.ticket_id = projected.ticket.id if projected.ticket is not None else None
    inbound.conversation_id = context.conversation.id
    inbound.webchat_message_id = projected.message.id
    inbound.processed_at = utc_now()
    safe_write_webchat_event(
        db,
        conversation_id=context.conversation.id,
        ticket_id=inbound.ticket_id,
        event_type="whatsapp_native.inbound_projected",
        payload={
            "whatsapp_inbound_message_id": inbound.id,
            "message_id": projected.message.id,
            "external_message_id": external_message_id,
            "ticketless": projected.ticket is None,
        },
    )
    snapshot = _schedule_ai_turn(
        db,
        conversation=context.conversation,
        visitor_message=projected.message,
    )
    db.flush()
    return WhatsAppNativeInboundResult(
        ok=True,
        idempotent=False,
        inbound_message_id=inbound.id,
        ticket_id=inbound.ticket_id,
        conversation_id=context.conversation.id,
        webchat_message_id=projected.message.id,
        ai_turn_id=snapshot.get("ai_turn_id"),
        ai_status=snapshot.get("ai_status"),
    )
