from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..enums import (
    ConversationState,
    EventType,
    JobStatus,
    NoteVisibility,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from ..models import (
    BackgroundJob,
    ChannelAccount,
    Customer,
    Ticket,
    TicketComment,
    WhatsAppInboundMessage,
)
from ..settings import get_settings
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .background_jobs import WEBCHAT_AI_REPLY_JOB, enqueue_background_job
from .ticket_event_writer import TicketEventClass, TicketEventWriter
from .ticket_service import generate_ticket_no
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
    if not connector_key or not hmac.compare_digest(connector_key, settings.whatsapp_connector_key):
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
    age = abs((utc_now().astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
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


def _public_id(account_id: str, chat_jid: str) -> str:
    digest = hashlib.sha256(f"{account_id}:{chat_jid}".encode("utf-8")).hexdigest()[:32]
    return f"wa_{digest}"


def _token_hash(account_id: str, chat_jid: str) -> str:
    return hashlib.sha256(f"whatsapp-native:{account_id}:{chat_jid}".encode("utf-8")).hexdigest()


def _metadata(**values: Any) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _projection_mode(payload: dict[str, Any], *, from_me: bool, body_text: str) -> str:
    raw_mode = _clip(payload.get("projection_mode"), 40) or "visitor"
    mode = raw_mode.lower()
    if mode not in VALID_PROJECTION_MODES:
        raise WhatsAppNativeInboundError("invalid_whatsapp_projection_mode")
    if not from_me:
        return "visitor"
    if mode == "store_only":
        return "store_only"
    if mode == "self_chat":
        return "store_only"
    if mode != "test_visitor":
        return "store_only"
    prefix = _clip(payload.get("self_echo_test_prefix"), 120) or DEFAULT_SELF_ECHO_TEST_PREFIX
    if not body_text.startswith(prefix):
        return "store_only"
    return "test_visitor"


def _strip_self_echo_test_prefix(payload: dict[str, Any], body_text: str, projection_mode: str) -> str:
    if projection_mode != "test_visitor":
        return body_text
    prefix = _clip(payload.get("self_echo_test_prefix"), 120) or DEFAULT_SELF_ECHO_TEST_PREFIX
    if not body_text.startswith(prefix):
        return body_text
    stripped = body_text[len(prefix):].strip()
    return stripped or body_text


def _active_whatsapp_account(db: Session, account_id: str) -> ChannelAccount:
    row = (
        db.query(ChannelAccount)
        .filter(ChannelAccount.account_id == account_id, ChannelAccount.provider == SourceChannel.whatsapp.value, ChannelAccount.is_active.is_(True))
        .first()
    )
    if row is None:
        raise WhatsAppNativeInboundError("unknown_whatsapp_channel_account")
    return row


def _customer_for_message(db: Session, *, chat_jid: str, sender_phone: str | None) -> Customer:
    external_ref = f"whatsapp:{chat_jid}"[:120]
    row = db.query(Customer).filter(Customer.external_ref == external_ref).first()
    if row is not None:
        if sender_phone and not row.phone:
            row.phone = sender_phone[:60]
        return row
    if sender_phone:
        row = db.query(Customer).filter(Customer.phone == sender_phone[:60]).first()
        if row is not None:
            row.external_ref = row.external_ref or external_ref
            return row
    row = Customer(
        name=sender_phone or f"WhatsApp {chat_jid.split('@')[0]}",
        phone=sender_phone[:60] if sender_phone else None,
        external_ref=external_ref,
    )
    db.add(row)
    db.flush()
    return row


def _conversation_for_message(db: Session, *, account: ChannelAccount, chat_jid: str, sender_phone: str | None, body: str) -> tuple[Ticket, WebchatConversation, Customer]:
    public_id = _public_id(account.account_id, chat_jid)
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if conversation is not None:
        ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
        if ticket is None:
            raise WhatsAppNativeInboundError("whatsapp_conversation_ticket_missing")
        customer = ticket.customer or _customer_for_message(db, chat_jid=chat_jid, sender_phone=sender_phone)
        return ticket, conversation, customer

    customer = _customer_for_message(db, chat_jid=chat_jid, sender_phone=sender_phone)
    ticket = Ticket(
        ticket_no=generate_ticket_no(),
        title=f"WhatsApp inquiry · {customer.name}"[:255],
        description="Native WhatsApp customer conversation.",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        market_id=account.market_id,
        channel_account_id=account.id,
        source_chat_id=chat_jid[:120],
        source_dedupe_key=f"whatsapp-native:{account.id}:{chat_jid}"[:300],
        preferred_reply_channel=SourceChannel.whatsapp.value,
        preferred_reply_contact=(sender_phone or chat_jid)[:160],
        customer_request=body,
        last_customer_message=body,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=_token_hash(account.account_id, chat_jid),
        visitor_token_expires_at=None,
        tenant_key="default",
        channel_key=SourceChannel.whatsapp.value,
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_phone=sender_phone,
        visitor_ref=chat_jid,
        origin="whatsapp-native",
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(conversation)
    db.flush()
    TicketEventWriter.add(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.ticket_created,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note="Native WhatsApp conversation created",
        payload={
            "public_conversation_id": conversation.public_id,
            "channel_account_id": account.id,
            "chat_jid": chat_jid,
        },
    )
    return ticket, conversation, customer


def _schedule_ai_turn(db: Session, *, conversation: WebchatConversation, visitor_message: WebchatMessage) -> dict[str, Any]:
    def create_job(payload: dict[str, Any], dedupe_key: str, scheduled_at) -> BackgroundJob:
        return enqueue_background_job(
            db,
            queue_name="webchat_ai_reply",
            job_type=WEBCHAT_AI_REPLY_JOB,
            payload=payload,
            dedupe_key=dedupe_key,
            next_run_at=scheduled_at,
        )

    legacy_job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.dedupe_key == f"webchat-ai-reply:{visitor_message.id}",
            BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing]),
        )
        .order_by(BackgroundJob.id.desc())
        .first()
    )
    if legacy_job is not None:
        legacy_job.status = JobStatus.done
        legacy_job.updated_at = utc_now()

    return schedule_webchat_ai_turn(
        db,
        conversation=conversation,
        ticket_id=conversation.ticket_id,
        visitor_message=visitor_message,
        create_job=create_job,
        debounce_seconds=float(getattr(get_settings(), "webchat_ai_turn_debounce_seconds", 0.15) or 0),
    )


def ingest_whatsapp_native_inbound(db: Session, payload: dict[str, Any]) -> WhatsAppNativeInboundResult:
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
        .filter(WhatsAppInboundMessage.channel_account_id == account.id, WhatsAppInboundMessage.external_message_id == external_message_id)
        .first()
    )
    if existing is not None:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.id == existing.conversation_id).first() if existing.conversation_id else None
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
    projection_mode = _projection_mode(payload, from_me=from_me, body_text=raw_body_text)
    body_text = _strip_self_echo_test_prefix(payload, raw_body_text, projection_mode)
    received_at_text = _clip(payload.get("received_at"), 80)
    try:
        received_at = datetime.fromisoformat((received_at_text or "").replace("Z", "+00:00")) if received_at_text else utc_now()
    except ValueError:
        received_at = utc_now()
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

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

    ticket, conversation, _ = _conversation_for_message(db, account=account, chat_jid=chat_jid, sender_phone=sender_phone, body=body_text)
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body=body_text,
        body_text=body_text,
        message_type="text",
        client_message_id=external_message_id,
        delivery_status="sent",
        metadata_json=_metadata(
            generated_by="whatsapp_native_inbound",
            source=(
                SELF_ECHO_TEST_SOURCE
                if from_me and projection_mode == "test_visitor"
                else SELF_CHAT_SOURCE
                if from_me and projection_mode == "self_chat"
                else "whatsapp_native"
            ),
            from_me=from_me,
            projection_mode=projection_mode,
            account_id=account.account_id,
            channel_account_id=account.id,
            external_message_id=external_message_id,
            chat_jid=chat_jid,
            sender_jid=sender_jid,
            sender_phone=sender_phone,
        ),
        author_label=sender_phone or "WhatsApp Customer",
        created_at=received_at,
    )
    db.add(message)
    db.flush()

    inbound.ticket_id = ticket.id
    inbound.conversation_id = conversation.id
    inbound.webchat_message_id = message.id
    inbound.processed_at = utc_now()
    ticket.last_customer_message = body_text
    ticket.customer_request = body_text
    ticket.source_chat_id = chat_jid[:120]
    ticket.preferred_reply_channel = SourceChannel.whatsapp.value
    ticket.preferred_reply_contact = (sender_phone or chat_jid)[:160]
    if ticket.status in {TicketStatus.resolved, TicketStatus.closed}:
        ticket.status = TicketStatus.pending_assignment
        ticket.conversation_state = ConversationState.reopened_by_customer
    ticket.updated_at = utc_now()
    conversation.last_seen_at = utc_now()
    conversation.updated_at = utc_now()
    db.add(TicketComment(ticket_id=ticket.id, author_id=None, body=body_text, visibility=NoteVisibility.external))
    TicketEventWriter.add(
        db,
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.comment_added,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note="Native WhatsApp inbound message received",
        payload={
            "whatsapp_inbound_message_id": inbound.id,
            "webchat_message_id": message.id,
            "public_conversation_id": conversation.public_id,
            "chat_jid": chat_jid,
        },
    )
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="whatsapp_native.inbound_projected",
        payload={"whatsapp_inbound_message_id": inbound.id, "message_id": message.id, "external_message_id": external_message_id},
    )
    snapshot = _schedule_ai_turn(db, conversation=conversation, visitor_message=message)
    db.flush()
    return WhatsAppNativeInboundResult(
        ok=True,
        idempotent=False,
        inbound_message_id=inbound.id,
        ticket_id=ticket.id,
        conversation_id=conversation.id,
        webchat_message_id=message.id,
        ai_turn_id=snapshot.get("ai_turn_id"),
        ai_status=snapshot.get("ai_status"),
    )
