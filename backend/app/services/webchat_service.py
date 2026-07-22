"""Canonical WebChat public service facade.

Business logic is owned by the session-identity, message, and conversation-operator
services. This module is the stable application import boundary and contains no
fallback, ticket-creation, policy, persistence, or provider implementation.
"""

from .conversation_operator_service import (
    read_ticket_conversation_thread as admin_get_thread,
    reply_to_ticket_conversation as admin_reply,
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
