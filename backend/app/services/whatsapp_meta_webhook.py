from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator


@dataclass(frozen=True)
class MetaInboundMessage:
    phone_number_id: str
    display_phone_number: str | None
    external_message_id: str
    sender_phone: str
    sender_name: str | None
    message_type: str
    body_text: str
    received_at: datetime
    reply_to_message_id: str | None
    media_id: str | None
    media_mime_type: str | None
    raw_message: dict[str, Any]


@dataclass(frozen=True)
class MetaDeliveryEvent:
    phone_number_id: str
    provider_message_id: str
    status: str
    occurred_at: datetime
    recipient_phone: str | None
    conversation_id: str | None
    pricing_category: str | None
    error_code: str | None
    error_message: str | None
    raw_status: dict[str, Any]


def verify_meta_webhook_signature(
    *,
    raw_body: bytes,
    signature: str | None,
    app_secret: str,
) -> None:
    supplied = str(signature or "").strip()
    if not supplied.startswith("sha256="):
        raise ValueError("invalid_meta_webhook_signature")
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied[7:], expected):
        raise ValueError("invalid_meta_webhook_signature")


def iter_meta_inbound_messages(payload: dict[str, Any]) -> Iterator[MetaInboundMessage]:
    if payload.get("object") != "whatsapp_business_account":
        return
    for value in _iter_change_values(payload):
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        phone_number_id = _required(metadata.get("phone_number_id"), "meta_phone_number_id_missing")
        display_phone_number = _optional(metadata.get("display_phone_number"))
        contacts = value.get("contacts") if isinstance(value.get("contacts"), list) else []
        contact_by_phone: dict[str, dict[str, Any]] = {}
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            wa_id = _optional(contact.get("wa_id"))
            if wa_id:
                contact_by_phone[wa_id] = contact
        messages = value.get("messages") if isinstance(value.get("messages"), list) else []
        for message in messages:
            if not isinstance(message, dict):
                continue
            sender_phone = _required(message.get("from"), "meta_sender_phone_missing")
            contact = contact_by_phone.get(sender_phone, {})
            profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
            message_type = _optional(message.get("type")) or "unknown"
            body_text, media_id, media_mime_type = _message_content(message, message_type)
            context = message.get("context") if isinstance(message.get("context"), dict) else {}
            yield MetaInboundMessage(
                phone_number_id=phone_number_id,
                display_phone_number=display_phone_number,
                external_message_id=_required(message.get("id"), "meta_message_id_missing"),
                sender_phone=sender_phone,
                sender_name=_optional(profile.get("name")),
                message_type=message_type,
                body_text=body_text,
                received_at=_timestamp(message.get("timestamp")),
                reply_to_message_id=_optional(context.get("id")),
                media_id=media_id,
                media_mime_type=media_mime_type,
                raw_message=_bounded_raw_message(message),
            )


def iter_meta_delivery_events(payload: dict[str, Any]) -> Iterator[MetaDeliveryEvent]:
    if payload.get("object") != "whatsapp_business_account":
        return
    for value in _iter_change_values(payload):
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        phone_number_id = _required(metadata.get("phone_number_id"), "meta_phone_number_id_missing")
        statuses = value.get("statuses") if isinstance(value.get("statuses"), list) else []
        for status in statuses:
            if not isinstance(status, dict):
                continue
            conversation = status.get("conversation") if isinstance(status.get("conversation"), dict) else {}
            pricing = status.get("pricing") if isinstance(status.get("pricing"), dict) else {}
            errors = status.get("errors") if isinstance(status.get("errors"), list) else []
            first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
            error_data = first_error.get("error_data") if isinstance(first_error.get("error_data"), dict) else {}
            yield MetaDeliveryEvent(
                phone_number_id=phone_number_id,
                provider_message_id=_required(status.get("id"), "meta_status_message_id_missing"),
                status=_required(status.get("status"), "meta_delivery_status_missing").lower(),
                occurred_at=_timestamp(status.get("timestamp")),
                recipient_phone=_optional(status.get("recipient_id")),
                conversation_id=_optional(conversation.get("id")),
                pricing_category=_optional(pricing.get("category")),
                error_code=_optional(first_error.get("code")),
                error_message=(
                    _optional(error_data.get("details"))
                    or _optional(first_error.get("message"))
                    or _optional(first_error.get("title"))
                ),
                raw_status=_bounded_raw_status(status),
            )


def _iter_change_values(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes") if isinstance(entry.get("changes"), list) else []
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if isinstance(value, dict):
                yield value


def _message_content(
    message: dict[str, Any],
    message_type: str,
) -> tuple[str, str | None, str | None]:
    if message_type == "text":
        text = message.get("text") if isinstance(message.get("text"), dict) else {}
        return _optional(text.get("body")) or "", None, None
    if message_type == "button":
        button = message.get("button") if isinstance(message.get("button"), dict) else {}
        return _optional(button.get("text")) or "<button>", None, None
    if message_type == "interactive":
        interactive = message.get("interactive") if isinstance(message.get("interactive"), dict) else {}
        response = interactive.get("button_reply") or interactive.get("list_reply") or {}
        if not isinstance(response, dict):
            response = {}
        return _optional(response.get("title")) or _optional(response.get("id")) or "<interactive>", None, None
    if message_type == "location":
        location = message.get("location") if isinstance(message.get("location"), dict) else {}
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        label = _optional(location.get("name")) or _optional(location.get("address"))
        coordinates = f"{latitude},{longitude}" if latitude is not None and longitude is not None else "unknown"
        return f"<location:{coordinates}>{' ' + label if label else ''}", None, None
    if message_type == "contacts":
        contacts = message.get("contacts") if isinstance(message.get("contacts"), list) else []
        return f"<contacts:{len(contacts)}>", None, None
    if message_type in {"image", "video", "audio", "document", "sticker"}:
        media = message.get(message_type) if isinstance(message.get(message_type), dict) else {}
        caption = _optional(media.get("caption"))
        filename = _optional(media.get("filename"))
        placeholder = f"<media:{message_type}>"
        details = " ".join(item for item in (filename, caption) if item)
        return (
            f"{placeholder}{' ' + details if details else ''}",
            _optional(media.get("id")),
            _optional(media.get("mime_type")),
        )
    if message_type == "reaction":
        reaction = message.get("reaction") if isinstance(message.get("reaction"), dict) else {}
        emoji = _optional(reaction.get("emoji")) or "removed"
        target = _optional(reaction.get("message_id")) or "unknown"
        return f"<reaction:{emoji} to:{target}>", None, None
    return f"<unsupported:{message_type}>", None, None


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _required(value: Any, error: str) -> str:
    text = _optional(value)
    if not text:
        raise ValueError(error)
    return text


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bounded_raw_message(message: dict[str, Any]) -> dict[str, Any]:
    # Store only the provider message fragment needed for support evidence. The
    # complete webhook envelope can contain unrelated contacts and is deliberately
    # not persisted.
    allowed = {
        "id",
        "from",
        "timestamp",
        "type",
        "context",
        "text",
        "button",
        "interactive",
        "location",
        "contacts",
        "image",
        "video",
        "audio",
        "document",
        "sticker",
        "reaction",
        "errors",
    }
    return {key: value for key, value in message.items() if key in allowed}


def _bounded_raw_status(status: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "status",
        "timestamp",
        "recipient_id",
        "conversation",
        "pricing",
        "errors",
    }
    return {key: value for key, value in status.items() if key in allowed}
