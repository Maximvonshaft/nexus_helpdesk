from __future__ import annotations

import secrets
from typing import Any

from ..webchat_schemas import WebChatCardAction, WebChatCardPayload


def _card_id(prefix: str) -> str:
    return f"card_{prefix}_{secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:10]}"


def build_quick_replies_card(*, title: str = "How can we help you?", body: str = "Choose one option below.", actions: list[dict[str, Any]] | None = None, intent: str = "unknown", generated_by: str = "system") -> WebChatCardPayload:
    safe_actions = actions or [
        {"id": "track_parcel", "label": "Track my parcel", "value": "track_parcel", "action_type": "quick_reply", "payload": {"intent": "tracking"}},
        {"id": "change_address", "label": "Change delivery address", "value": "change_address", "action_type": "quick_reply", "payload": {"intent": "address_change"}},
        {"id": "talk_to_human", "label": "Talk to support", "value": "talk_to_human", "action_type": "handoff_request", "payload": {"intent": "handoff"}},
    ]
    return WebChatCardPayload(
        card_id=_card_id("quick"),
        card_type="quick_replies",
        version=1,
        title=title,
        body=body,
        actions=[WebChatCardAction(**item) for item in safe_actions],
        metadata={"intent": intent, "generated_by": generated_by, "requires_audit": True},
    )


def build_handoff_card(*, reason: str = "Customer requested human support", generated_by: str = "system") -> WebChatCardPayload:
    return WebChatCardPayload(
        card_id=_card_id("handoff"),
        card_type="handoff",
        version=1,
        title="Would you like to talk to a support specialist?",
        body="We can bring a human support specialist into this conversation. They will review your request and reply here.",
        actions=[WebChatCardAction(id="request_handoff", label="Request human support", value="request_handoff", action_type="handoff_request", payload={"reason": reason})],
        metadata={"intent": "handoff", "generated_by": generated_by, "requires_audit": True, "reason": reason},
    )


def build_tracking_status_card_schema_only_or_safe() -> WebChatCardPayload:
    return build_quick_replies_card(title="Please share your tracking number", body="We need a tracking number before showing any shipment status.", intent="tracking")


def build_address_confirmation_card_schema_only_or_safe() -> WebChatCardPayload:
    return build_handoff_card(reason="Address changes require manual verification")


def build_reschedule_picker_card_schema_only_or_safe() -> WebChatCardPayload:
    return build_handoff_card(reason="Delivery rescheduling requires manual verification")


def build_photo_upload_request_card_schema_only_or_safe() -> WebChatCardPayload:
    return build_handoff_card(reason="Photo upload is not enabled in this WebChat runtime yet")


def build_csat_card_schema_only_or_safe() -> WebChatCardPayload:
    return WebChatCardPayload(
        card_id=_card_id("csat"),
        card_type="csat",
        version=1,
        title="How was this support experience?",
        body="Your feedback helps us improve.",
        actions=[
            WebChatCardAction(id="csat_good", label="Good", value="good", action_type="csat_submit", payload={"score": 5}),
            WebChatCardAction(id="csat_needs_work", label="Needs work", value="needs_work", action_type="csat_submit", payload={"score": 2}),
        ],
        metadata={"intent": "csat", "generated_by": "system", "requires_audit": True},
    )
