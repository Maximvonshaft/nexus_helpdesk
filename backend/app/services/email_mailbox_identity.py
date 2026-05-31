from __future__ import annotations

from typing import Any

MAILBOX_ID_DOMAIN = "nexusdesk.local"


def _header_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or "\r" in text or "\n" in text:
        return None
    bare = text[1:-1] if text.startswith("<") and text.endswith(">") else text.strip("<>")
    if not bare or "@" not in bare or any(ch.isspace() for ch in bare):
        return None
    return f"<{bare}>"


def _references(value: Any) -> str | None:
    ids = [_header_id(token) for token in str(value or "").split()]
    cleaned = [token for token in ids if token]
    return " ".join(cleaned) if cleaned else None


def normalize_mailbox_header_id(value: Any) -> str | None:
    return _header_id(value)


def normalize_mailbox_references(value: Any) -> str | None:
    return _references(value)


def _ticket_id(message, ticket=None) -> int | None:
    return getattr(ticket, "id", None) or getattr(message, "ticket_id", None)


def build_mailbox_thread_id(ticket_id: int) -> str:
    return f"<nexusdesk-ticket-{ticket_id}@{MAILBOX_ID_DOMAIN}>"


def build_mailbox_message_id(ticket_id: int, outbound_message_id: int) -> str:
    return f"<nexusdesk-ticket-{ticket_id}-outbound-{outbound_message_id}@{MAILBOX_ID_DOMAIN}>"


def build_inbound_mailbox_message_id(ticket_id: int, inbound_message_id: int) -> str:
    return f"<nexusdesk-ticket-{ticket_id}-inbound-{inbound_message_id}@{MAILBOX_ID_DOMAIN}>"


def ensure_outbound_mailbox_identity(message, *, ticket=None, include_message_id: bool = True) -> None:
    ticket_id = _ticket_id(message, ticket)
    if ticket_id is None:
        return

    thread_id = _header_id(getattr(message, "mailbox_thread_id", None)) or build_mailbox_thread_id(int(ticket_id))
    message.mailbox_thread_id = thread_id

    references = _references(getattr(message, "mailbox_references", None)) or thread_id
    message.mailbox_references = references

    if include_message_id and getattr(message, "id", None):
        message_id = _header_id(getattr(message, "mailbox_message_id", None)) or build_mailbox_message_id(int(ticket_id), int(message.id))
        message.mailbox_message_id = message_id
