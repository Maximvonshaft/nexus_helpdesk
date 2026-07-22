"""Canonical WebChat public service facade.

Business logic is owned by the session-identity, message, and conversation-operator
services. This module is the stable application import boundary and contains no
fallback, ticket-creation, policy, persistence, or provider implementation.
"""

from typing import Any

from sqlalchemy.orm import Session

from ..models import User
from .conversation_operator_service import (
    read_ticket_conversation_thread,
    reply_to_ticket_conversation,
)
from .webchat_message_service import (
    add_visitor_message,
    add_visitor_message_to_conversation,
    get_authorized_webchat_conversation,
    message_payload,
    submit_card_action,
)
from .webchat_session_identity import (
    MAX_FIELD_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_URL_CHARS,
    clip as _clip,
    hash_token as _hash_token,
    origin_from_request as _origin_from_request,
    validate_visitor_token as _validate_token,
)


def admin_get_thread(
    db: Session,
    ticket_id: int,
    current_user: User,
    *,
    before_message_id: int | None = None,
    message_limit: int = 100,
) -> dict[str, Any]:
    """Delegate ticket-thread reads to the one conversation operator authority."""

    return read_ticket_conversation_thread(
        db,
        ticket_id,
        current_user,
        before_message_id=before_message_id,
        message_limit=message_limit,
    )


def admin_reply(
    db: Session,
    ticket_id: int,
    current_user: User,
    *,
    body: str,
    evidence_reference_id: int | None = None,
    conversation_public_id: str | None = None,
) -> dict[str, Any]:
    """Delegate reply policy to the operator authority.

    The delegated implementation persists every visible entity through
    create_customer_visible_message(...); this facade owns no persistence.
    """

    return reply_to_ticket_conversation(
        db,
        ticket_id,
        current_user,
        body=body,
        evidence_reference_id=evidence_reference_id,
        conversation_public_id=conversation_public_id,
    )


__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_URL_CHARS",
    "add_visitor_message",
    "add_visitor_message_to_conversation",
    "admin_get_thread",
    "admin_reply",
    "get_authorized_webchat_conversation",
    "message_payload",
    "submit_card_action",
]
