from __future__ import annotations

import socket
import smtplib
import ssl
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Callable

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.orm import Session

from ...enums import MessageStatus, SourceChannel
from ...models import OutboundEmailAccount, Ticket, TicketAttachment, TicketOutboundMessage
from ...utils.time import utc_now
from ..outbound_email_account_service import resolve_outbound_email_account
from ..secret_crypto import SecretCryptoService

SMTP_TIMEOUT_SECONDS = 20
SMTP_RATE_LIMIT_CODES = frozenset({421, 450, 451, 452})
MAX_SMTP_ERROR_LENGTH = 500

SMTP_CONFIGURATION_MISSING = "smtp_configuration_missing"
SMTP_AUTH_FAILED = "smtp_auth_failed"
SMTP_TLS_FAILED = "smtp_tls_failed"
SMTP_CONNECT_TIMEOUT = "smtp_connect_timeout"
SMTP_CONNECT_FAILED = "smtp_connect_failed"
SMTP_SENDER_REJECTED = "smtp_sender_rejected"
SMTP_RECIPIENT_REJECTED = "smtp_recipient_rejected"
SMTP_RATE_LIMITED = "smtp_rate_limited"
SMTP_MESSAGE_REJECTED = "smtp_message_rejected"
SMTP_UNEXPECTED_ERROR = "smtp_unexpected_error"
SMTP_ATTACHMENT_UNAVAILABLE = "smtp_attachment_unavailable"

SMTPClientFactory = Callable[..., Any]


class EmailOutboundError(ValueError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.message = message


@dataclass(frozen=True)
class EmailOutboundRoute:
    account_id: int
    host: str
    port: int
    username: str
    password: str
    from_address: str
    reply_to: str | None
    to_address: str
    subject: str
    security_mode: str
    source: str

    def to_context(self, *, idempotency_key: str) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("password", None)
        payload.pop("username", None)
        payload["to_address"] = mask_email_address(self.to_address)
        payload["from_address"] = mask_email_address(self.from_address)
        payload["reply_to"] = mask_email_address(self.reply_to)
        payload["idempotency_key"] = idempotency_key
        payload["adapter"] = "smtp"
        payload["channel"] = SourceChannel.email.value
        return payload


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def clean_email_subject(value: Any) -> str | None:
    text = " ".join(str(value or "").splitlines()).strip()
    return text[:255] if text else None


def normalize_email_address(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        result = validate_email(cleaned, check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized.lower()


def mask_email_address(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is None or "@" not in cleaned:
        return None
    local, domain = cleaned.rsplit("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _ticket_email_target(ticket: Ticket | None) -> str | None:
    if ticket is None:
        return None
    customer = getattr(ticket, "customer", None)
    candidates = [
        getattr(ticket, "preferred_reply_contact", None),
        getattr(ticket, "source_chat_id", None),
        getattr(customer, "email", None),
    ]
    for candidate in candidates:
        normalized = normalize_email_address(candidate)
        if normalized:
            return normalized
    return None


def _decrypt_password(account: OutboundEmailAccount) -> str:
    try:
        password = SecretCryptoService.outbound_email().decrypt(account.password_encrypted)
    except Exception as exc:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "SMTP credential could not be decrypted") from exc
    if not password:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "SMTP credential is missing")
    return password


def _route_from_account(
    account: OutboundEmailAccount,
    *,
    to_address: str,
    subject: str,
    source: str,
) -> EmailOutboundRoute:
    normalized_to = normalize_email_address(to_address)
    if normalized_to is None:
        raise EmailOutboundError(SMTP_RECIPIENT_REJECTED, "Recipient email address is invalid")
    cleaned_subject = clean_email_subject(subject)
    if cleaned_subject is None:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "Email subject is required")
    security_mode = str(account.security_mode or "starttls").strip().lower()
    if security_mode not in {"starttls", "ssl", "plain"}:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "SMTP security mode is invalid")
    return EmailOutboundRoute(
        account_id=account.id,
        host=account.host,
        port=account.port,
        username=account.username,
        password=_decrypt_password(account),
        from_address=account.from_address,
        reply_to=account.reply_to,
        to_address=normalized_to,
        subject=cleaned_subject,
        security_mode=security_mode,
        source=source,
    )


def resolve_email_outbound_route(db: Session, *, message: TicketOutboundMessage, ticket: Ticket | None) -> EmailOutboundRoute:
    if message.channel != SourceChannel.email:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "Email adapter received a non-email message")

    market_id = getattr(ticket, "market_id", None) if ticket is not None else None
    account = resolve_outbound_email_account(db, market_id=market_id)
    if account is None:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "No active outbound email account is configured")

    target = _ticket_email_target(ticket)
    if target is None:
        raise EmailOutboundError(SMTP_RECIPIENT_REJECTED, "No valid recipient email address is available")

    subject = clean_email_subject(getattr(message, "subject", None))
    if subject is None and ticket is not None:
        subject = clean_email_subject(getattr(ticket, "title", None))
    if subject is None:
        raise EmailOutboundError(SMTP_CONFIGURATION_MISSING, "Email subject is required")

    return _route_from_account(account, to_address=target, subject=subject, source="ticket_market_or_global_account")


def _smtp_response_text(exc: BaseException) -> str:
    code = getattr(exc, "smtp_code", None)
    error = getattr(exc, "smtp_error", None)
    if isinstance(error, bytes):
        error_text = error.decode("utf-8", errors="replace")
    else:
        error_text = str(error or exc)
    if code:
        error_text = f"{code} {error_text}"
    return error_text.strip()[:MAX_SMTP_ERROR_LENGTH] or exc.__class__.__name__


def _response_failure_code(exc: smtplib.SMTPResponseException) -> str:
    return SMTP_RATE_LIMITED if int(getattr(exc, "smtp_code", 0) or 0) in SMTP_RATE_LIMIT_CODES else SMTP_MESSAGE_REJECTED


def _connect_smtp(route: EmailOutboundRoute, *, smtp_factory: SMTPClientFactory | None = None):
    context = ssl.create_default_context()
    if smtp_factory is not None:
        return smtp_factory(
            host=route.host,
            port=route.port,
            timeout=SMTP_TIMEOUT_SECONDS,
            security_mode=route.security_mode,
            context=context,
        )
    if route.security_mode == "ssl":
        return smtplib.SMTP_SSL(route.host, route.port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
    client = smtplib.SMTP(route.host, route.port, timeout=SMTP_TIMEOUT_SECONDS)
    if route.security_mode == "starttls":
        try:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            try:
                client.close()
            finally:
                raise EmailOutboundError(SMTP_TLS_FAILED, _smtp_response_text(exc)) from exc
    return client


def _attachment_mime_parts(attachment: TicketAttachment) -> tuple[str, str]:
    raw = str(attachment.mime_type or "application/octet-stream").strip().lower()
    if "/" not in raw:
        return "application", "octet-stream"
    maintype, subtype = raw.split("/", 1)
    return maintype or "application", subtype or "octet-stream"


def _read_attachment_bytes(attachment: TicketAttachment) -> bytes:
    if not attachment.file_path:
        raise EmailOutboundError(SMTP_ATTACHMENT_UNAVAILABLE, f"Attachment {attachment.id} has no stored file")
    path = Path(attachment.file_path)
    if not path.is_file():
        raise EmailOutboundError(SMTP_ATTACHMENT_UNAVAILABLE, f"Attachment {attachment.id} file is not available")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EmailOutboundError(SMTP_ATTACHMENT_UNAVAILABLE, f"Attachment {attachment.id} could not be read") from exc


def _build_message(route: EmailOutboundRoute, *, body: str, idempotency_key: str, attachments: list[TicketAttachment] | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = route.from_address
    message["To"] = route.to_address
    if route.reply_to:
        message["Reply-To"] = route.reply_to
    message["Subject"] = route.subject
    message["Message-ID"] = make_msgid(domain=route.from_address.rsplit("@", 1)[-1])
    message["X-NexusDesk-Idempotency-Key"] = idempotency_key
    message.set_content(body or "")
    for attachment in attachments or []:
        maintype, subtype = _attachment_mime_parts(attachment)
        message.add_attachment(
            _read_attachment_bytes(attachment),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.file_name,
        )
    return message


def _message_attachments(message: TicketOutboundMessage) -> list[TicketAttachment]:
    return [attachment for attachment in getattr(message, "attachments", []) if attachment is not None]


def send_email_route(
    route: EmailOutboundRoute,
    *,
    body: str,
    idempotency_key: str,
    attachments: list[TicketAttachment] | None = None,
    smtp_factory: SMTPClientFactory | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    context = route.to_context(idempotency_key=idempotency_key)
    context["attachment_count"] = len(attachments or [])
    context["attachment_filenames"] = [attachment.file_name for attachment in attachments or []]
    client = None
    try:
        client = _connect_smtp(route, smtp_factory=smtp_factory)
        if route.username or route.password:
            client.login(route.username, route.password)
        refused = client.send_message(_build_message(route, body=body, idempotency_key=idempotency_key, attachments=attachments))
        if refused:
            return _failed(SMTP_RECIPIENT_REJECTED, "SMTP server rejected one or more recipients", context)
        return MessageStatus.sent, "smtp_sent", utc_now(), context
    except EmailOutboundError as exc:
        return _failed(exc.failure_code, exc.message, context)
    except smtplib.SMTPAuthenticationError as exc:
        return _failed(SMTP_AUTH_FAILED, _smtp_response_text(exc), context)
    except smtplib.SMTPRecipientsRefused:
        return _failed(SMTP_RECIPIENT_REJECTED, "SMTP server rejected all recipients", context)
    except smtplib.SMTPSenderRefused as exc:
        return _failed(SMTP_SENDER_REJECTED, _smtp_response_text(exc), context)
    except smtplib.SMTPResponseException as exc:
        return _failed(_response_failure_code(exc), _smtp_response_text(exc), context)
    except (socket.timeout, TimeoutError) as exc:
        return _failed(SMTP_CONNECT_TIMEOUT, _smtp_response_text(exc), context)
    except (ConnectionError, OSError) as exc:
        return _failed(SMTP_CONNECT_FAILED, _smtp_response_text(exc), context)
    except smtplib.SMTPException as exc:
        return _failed(SMTP_MESSAGE_REJECTED, _smtp_response_text(exc), context)
    except Exception as exc:
        return _failed(SMTP_UNEXPECTED_ERROR, _smtp_response_text(exc), context)
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass


def _failed(
    failure_code: str,
    error_message: str,
    context: dict[str, Any],
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    safe_context = dict(context)
    safe_context["failure_code"] = failure_code
    safe_context["error"] = error_message[:MAX_SMTP_ERROR_LENGTH]
    return MessageStatus.failed, failure_code, None, safe_context


def dispatch_email_outbound(
    db: Session,
    *,
    message: TicketOutboundMessage,
    ticket: Ticket | None,
    idempotency_key: str,
    smtp_factory: SMTPClientFactory | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    try:
        route = resolve_email_outbound_route(db, message=message, ticket=ticket)
    except EmailOutboundError as exc:
        return _failed(exc.failure_code, exc.message, {
            "adapter": "smtp",
            "channel": SourceChannel.email.value,
            "idempotency_key": idempotency_key,
        })
    return send_email_route(route, body=message.body, idempotency_key=idempotency_key, attachments=_message_attachments(message), smtp_factory=smtp_factory)


def send_outbound_email_test(
    account: OutboundEmailAccount,
    *,
    to_address: str,
    subject: str | None = None,
    body: str | None = None,
    smtp_factory: SMTPClientFactory | None = None,
) -> tuple[MessageStatus, str | None, object | None, dict[str, Any]]:
    idempotency_key = f"nexusdesk-email-test-{account.id}-{utc_now().timestamp()}"
    try:
        route = _route_from_account(
            account,
            to_address=to_address,
            subject=subject or "NexusDesk outbound email test",
            source="admin_test_send",
        )
    except EmailOutboundError as exc:
        return _failed(exc.failure_code, exc.message, {
            "adapter": "smtp",
            "channel": SourceChannel.email.value,
            "account_id": account.id,
            "idempotency_key": idempotency_key,
        })
    return send_email_route(
        route,
        body=body or "This is a NexusDesk outbound email test message.",
        idempotency_key=idempotency_key,
        smtp_factory=smtp_factory,
    )
