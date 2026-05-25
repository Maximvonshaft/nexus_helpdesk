from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from ...enums import MessageStatus, SourceChannel
from ...models import EmailChannelAccount, EmailOutboundMetadata, Ticket, TicketOutboundMessage
from ...settings import get_settings
from ...utils.time import utc_now
from ..channel_account_registry import EMAIL_PROVIDER, resolve_channel_account_for_provider
from ..email_providers.base import EmailProvider, EmailSendPayload
from ..email_providers.ses import SesEmailProvider
from ..email_security import is_email_suppressed, normalize_email_address, reject_header_injection, sanitize_email_body


def email_runtime_block_reason() -> str | None:
    settings = get_settings()
    if not settings.enable_outbound_dispatch:
        return "enable_outbound_dispatch"
    if not settings.outbound_email_enabled:
        return "outbound_email_enabled"
    if settings.email_provider != "ses":
        return "email_provider_ses"
    return None


def resolve_email_account(db: Session, *, ticket: Ticket | None, from_email: str | None = None) -> EmailChannelAccount | None:
    channel_account = None
    if from_email:
        row = (
            db.query(EmailChannelAccount)
            .filter(EmailChannelAccount.from_email == from_email.lower(), EmailChannelAccount.is_active.is_(True))
            .first()
        )
        if row is not None:
            return row
    if ticket is not None:
        channel_account = resolve_channel_account_for_provider(
            db,
            provider=EMAIL_PROVIDER,
            market_id=ticket.market_id,
            account_id=None,
        )
    else:
        channel_account = resolve_channel_account_for_provider(db, provider=EMAIL_PROVIDER)
    if channel_account is None:
        return None
    return (
        db.query(EmailChannelAccount)
        .filter(
            EmailChannelAccount.channel_account_id == channel_account.id,
            EmailChannelAccount.is_active.is_(True),
            EmailChannelAccount.verification_status == "verified",
        )
        .first()
    )


def build_or_get_email_metadata(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    subject: str | None = None,
    to_email: str | None = None,
    from_email: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> EmailOutboundMetadata:
    existing = db.query(EmailOutboundMetadata).filter(EmailOutboundMetadata.outbound_message_id == message.id).first()
    if existing is not None:
        return existing
    account = resolve_email_account(db, ticket=ticket, from_email=normalize_email_address(from_email))
    if account is None:
        raise ValueError("missing_verified_email_account")
    resolved_to = normalize_email_address(to_email)
    if resolved_to is None and ticket is not None:
        customer = getattr(ticket, "customer", None)
        resolved_to = normalize_email_address(ticket.preferred_reply_contact or ticket.source_chat_id or getattr(customer, "email", None))
    if not resolved_to:
        raise ValueError("missing_email_recipient")
    if is_email_suppressed(db, resolved_to):
        raise ValueError("email_recipient_suppressed")
    resolved_subject = (subject or (f"Re: {ticket.ticket_no}" if ticket is not None else "NexusDesk reply")).strip()
    reject_header_injection(account.from_email, resolved_to, resolved_subject)
    metadata = EmailOutboundMetadata(
        outbound_message_id=message.id,
        email_account_id=account.id,
        from_email=account.from_email,
        to_email=resolved_to,
        cc_json=cc or [],
        bcc_json=bcc or [],
        subject=resolved_subject[:255],
        reply_token=uuid.uuid4().hex,
    )
    db.add(metadata)
    db.flush()
    return metadata


def dispatch_email_outbound(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    idempotency_key: str,
    provider: EmailProvider | None = None,
    provider_factory: Callable[[], EmailProvider] | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict]:
    blocked = email_runtime_block_reason()
    if blocked:
        raise ValueError(blocked)
    metadata = build_or_get_email_metadata(db, message=message, ticket=ticket)
    account = metadata.email_account
    if account is None:
        raise ValueError("missing_verified_email_account")
    settings = get_settings()
    body = sanitize_email_body(message.body)
    reject_header_injection(metadata.subject, metadata.from_email, metadata.to_email)
    provider_impl = provider or (provider_factory() if provider_factory else SesEmailProvider())
    result = provider_impl.send_email(
        EmailSendPayload(
            from_email=metadata.from_email,
            from_name=account.from_name,
            to_email=metadata.to_email,
            cc=metadata.cc_json or [],
            bcc=metadata.bcc_json or [],
            subject=metadata.subject,
            body=body,
            reply_to=metadata.from_email,
            configuration_set=account.configuration_set or settings.email_ses_configuration_set,
            tags={"ticket_id": str(message.ticket_id), "outbound_message_id": str(message.id), "idempotency_key": idempotency_key},
        )
    )
    metadata.provider_message_id = result.provider_message_id
    message.provider_message_id = result.provider_message_id
    route = {
        "channel": SourceChannel.email.value,
        "adapter": "email_ses",
        "account_id": account.channel_account.account_id if account.channel_account else None,
        "from_email": metadata.from_email,
        "to_email": metadata.to_email,
        "provider_message_id": result.provider_message_id,
        "idempotency_key": idempotency_key,
    }
    return MessageStatus.sent, result.provider_status, utc_now(), route
