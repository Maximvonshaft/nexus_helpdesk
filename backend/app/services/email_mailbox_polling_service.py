from __future__ import annotations

import email.utils
import imaplib
import re
from dataclasses import dataclass
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from typing import Iterable, Protocol

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..enums import JobStatus, TicketStatus
from ..models import BackgroundJob, Customer, OutboundEmailAccount, Ticket
from ..schemas import (
    EmailMailboxSyncAccountStatus,
    EmailMailboxSyncEnqueueResponse,
    EmailMailboxSyncStatusRead,
    InboundEmailIngestRequest,
)
from ..settings import get_settings
from ..utils.normalize import normalize_email
from ..utils.time import utc_now
from .background_jobs import enqueue_background_job
from .email_inbound_service import ingest_ticket_inbound_email_system
from .email_mailbox_identity import normalize_mailbox_header_id, normalize_mailbox_references
from .permissions import ensure_can_manage_runtime
from .secret_crypto import SecretCryptoService

EMAIL_MAILBOX_SYNC_JOB = "email.mailbox_sync"
EMAIL_MAILBOX_SYNC_QUEUE = "email_mailbox_sync"
MAX_FETCH_PER_ACCOUNT = 20
TICKET_REF_RE = re.compile(r"nexusdesk-ticket-(\d+)", re.IGNORECASE)


class MailboxClient(Protocol):
    def select(self, mailbox: str) -> object: ...
    def uid(self, command: str, *args) -> tuple[object, list[bytes]]: ...
    def logout(self) -> object: ...


@dataclass(frozen=True)
class ParsedMailboxMessage:
    uid: str
    from_address: str
    from_name: str | None
    to_address: str | None
    cc: str | None
    subject: str | None
    body: str
    message_id: str | None
    references: str | None
    in_reply_to: str | None
    received_at: object | None
    raw_preview: str


@dataclass(frozen=True)
class MailboxSyncResult:
    account_id: int
    fetched: int
    ingested: int
    skipped: int
    cursor: str | None


def _crypto() -> SecretCryptoService:
    return SecretCryptoService.outbound_email()


def _configured(row: OutboundEmailAccount) -> bool:
    return bool(row.inbound_enabled and row.imap_host and row.imap_port and row.imap_username and row.imap_password_encrypted and row.imap_security_mode)


def _int_cursor(value: str | None) -> int:
    try:
        return int(value or "0")
    except Exception:
        return 0


def _safe_header(value: object | None, limit: int = 500) -> str | None:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] or None


def _body_from_message(message) -> str:
    if message.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            if content_type == "text/plain":
                plain_parts.append(str(part.get_content() or ""))
            elif content_type == "text/html":
                html_parts.append(str(part.get_content() or ""))
        body = "\n".join(part.strip() for part in plain_parts if part.strip())
        if body:
            return body
        return "\n".join(part.strip() for part in html_parts if part.strip())
    return str(message.get_content() or "")


def _parse_address(value: str | None) -> tuple[str | None, str | None]:
    name, address = email.utils.parseaddr(value or "")
    normalized = normalize_email(address)
    return (normalized, _safe_header(name, 160)) if normalized else (None, None)


def _parse_received_at(value: str | None):
    try:
        return email.utils.parsedate_to_datetime(value) if value else None
    except Exception:
        return None


def _parse_message(uid: str, raw: bytes) -> ParsedMailboxMessage | None:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    from_address, from_name = _parse_address(message.get("From"))
    if not from_address:
        return None
    body = _body_from_message(message).strip()
    if not body:
        return None
    to_address, _ = _parse_address(message.get("To"))
    return ParsedMailboxMessage(
        uid=uid,
        from_address=from_address,
        from_name=from_name,
        to_address=to_address,
        cc=_safe_header(message.get("Cc"), 2000),
        subject=_safe_header(message.get("Subject"), 255),
        body=body,
        message_id=normalize_mailbox_header_id(message.get("Message-ID")),
        references=normalize_mailbox_references(message.get("References")),
        in_reply_to=normalize_mailbox_header_id(message.get("In-Reply-To")),
        received_at=_parse_received_at(message.get("Date")),
        raw_preview=body[:500],
    )


def _extract_ticket_id(values: Iterable[str | None]) -> int | None:
    text = " ".join(value for value in values if value)
    match = TICKET_REF_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _find_open_ticket_by_sender(db: Session, from_address: str) -> Ticket | None:
    customer = db.query(Customer).filter(Customer.email_normalized == normalize_email(from_address)).first()
    if customer is None:
        return None
    return (
        db.query(Ticket)
        .filter(Ticket.customer_id == customer.id)
        .filter(Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]))
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .first()
    )


def _resolve_ticket(db: Session, message: ParsedMailboxMessage) -> Ticket | None:
    ticket_id = _extract_ticket_id([message.references, message.in_reply_to, message.message_id, message.subject, message.body[:1000]])
    if ticket_id is not None:
        return db.query(Ticket).filter(Ticket.id == ticket_id).first()
    return _find_open_ticket_by_sender(db, message.from_address)


def _connect_imap(row: OutboundEmailAccount) -> MailboxClient:
    if not _configured(row):
        raise RuntimeError("imap_account_not_configured")
    password = _crypto().decrypt(row.imap_password_encrypted or "")
    if row.imap_security_mode == "ssl":
        client = imaplib.IMAP4_SSL(str(row.imap_host), int(row.imap_port or 993))
    else:
        client = imaplib.IMAP4(str(row.imap_host), int(row.imap_port or 143))
        if row.imap_security_mode == "starttls":
            client.starttls()
    client.login(str(row.imap_username), password)
    return client


def _extract_fetch_body(fetch_item) -> bytes | None:
    if isinstance(fetch_item, tuple) and len(fetch_item) >= 2 and isinstance(fetch_item[1], bytes):
        return fetch_item[1]
    return None


def _imap_status_ok(value: object) -> bool:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="ignore")
    return str(value).upper() == "OK"


def _imap_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value or "").encode("ascii", errors="ignore")


def poll_imap_account(
    db: Session,
    account: OutboundEmailAccount,
    *,
    client: MailboxClient | None = None,
    limit: int = MAX_FETCH_PER_ACCOUNT,
) -> MailboxSyncResult:
    if not _configured(account):
        account.imap_last_status = "not_configured"
        account.imap_last_error = "IMAP inbound sync is not configured"
        account.imap_last_seen_at = utc_now()
        return MailboxSyncResult(account_id=account.id, fetched=0, ingested=0, skipped=0, cursor=account.imap_sync_cursor)

    owns_client = client is None
    mailbox_client = client or _connect_imap(account)
    fetched = 0
    ingested = 0
    skipped = 0
    last_uid = _int_cursor(account.imap_sync_cursor)
    try:
        mailbox_client.select(account.imap_mailbox or "INBOX")
        status_value, data = mailbox_client.uid("search", None, "ALL")
        if not _imap_status_ok(status_value):
            raise RuntimeError("imap_search_failed")
        uids = []
        for chunk in data or []:
            for value in _imap_bytes(chunk).split():
                try:
                    uid_value = int(value)
                except ValueError:
                    continue
                if uid_value > last_uid:
                    uids.append(uid_value)
        for uid_value in sorted(uids)[: max(1, min(limit, MAX_FETCH_PER_ACCOUNT))]:
            fetch_status, fetch_data = mailbox_client.uid("fetch", str(uid_value), "(RFC822)")
            if not _imap_status_ok(fetch_status):
                skipped += 1
                last_uid = max(last_uid, uid_value)
                continue
            raw = next((_extract_fetch_body(item) for item in fetch_data or [] if _extract_fetch_body(item)), None)
            if raw is None:
                skipped += 1
                last_uid = max(last_uid, uid_value)
                continue
            fetched += 1
            parsed = _parse_message(str(uid_value), raw)
            if parsed is None:
                skipped += 1
                last_uid = max(last_uid, uid_value)
                continue
            ticket = _resolve_ticket(db, parsed)
            if ticket is None:
                skipped += 1
                last_uid = max(last_uid, uid_value)
                continue
            result = ingest_ticket_inbound_email_system(
                db,
                ticket_id=ticket.id,
                actor_id=account.updated_by or account.created_by,
                source="imap_poll",
                payload=InboundEmailIngestRequest(
                    from_address=parsed.from_address,
                    from_name=parsed.from_name,
                    to_address=parsed.to_address,
                    cc=parsed.cc,
                    subject=parsed.subject,
                    body=parsed.body,
                    provider="imap",
                    provider_message_id=f"imap:{account.id}:{parsed.uid}",
                    mailbox_message_id=parsed.message_id,
                    mailbox_references=parsed.references,
                    in_reply_to=parsed.in_reply_to,
                    received_at=parsed.received_at,
                ),
            )
            if result.created:
                ingested += 1
            else:
                skipped += 1
            last_uid = max(last_uid, uid_value)
        account.imap_sync_cursor = str(last_uid) if last_uid else account.imap_sync_cursor
        account.imap_last_seen_at = utc_now()
        account.imap_last_status = "ok"
        account.imap_last_error = None
        return MailboxSyncResult(account_id=account.id, fetched=fetched, ingested=ingested, skipped=skipped, cursor=account.imap_sync_cursor)
    except Exception as exc:
        account.imap_last_seen_at = utc_now()
        account.imap_last_status = "error"
        account.imap_last_error = str(exc)[:1000]
        raise
    finally:
        if owns_client:
            try:
                mailbox_client.logout()
            except Exception:
                pass


def enqueue_email_mailbox_sync_jobs(
    db: Session,
    *,
    current_user=None,
    account_id: int | None = None,
) -> EmailMailboxSyncEnqueueResponse:
    if current_user is not None:
        ensure_can_manage_runtime(current_user, db)
    query = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.inbound_enabled.is_(True), OutboundEmailAccount.is_active.is_(True))
    if account_id is not None:
        query = query.filter(OutboundEmailAccount.id == account_id)
    accounts = query.order_by(OutboundEmailAccount.priority.asc(), OutboundEmailAccount.id.asc()).all()
    jobs: list[BackgroundJob] = []
    for account in accounts:
        job = enqueue_background_job(
            db,
            queue_name=EMAIL_MAILBOX_SYNC_QUEUE,
            job_type=EMAIL_MAILBOX_SYNC_JOB,
            payload={"account_id": account.id},
            dedupe_key=f"email-mailbox-sync:{account.id}",
        )
        account.imap_last_sync_job_id = job.id
        jobs.append(job)
    return EmailMailboxSyncEnqueueResponse(enqueued=len(jobs), job_ids=[job.id for job in jobs])


def enqueue_due_email_mailbox_sync_jobs(
    db: Session,
    *,
    interval_seconds: int,
    limit: int | None = None,
) -> list[BackgroundJob]:
    cutoff = utc_now() - timedelta(seconds=interval_seconds)
    rows = (
        db.query(OutboundEmailAccount)
        .filter(OutboundEmailAccount.inbound_enabled.is_(True), OutboundEmailAccount.is_active.is_(True))
        .filter(OutboundEmailAccount.imap_host.is_not(None))
        .filter(OutboundEmailAccount.imap_port.is_not(None))
        .filter(OutboundEmailAccount.imap_username.is_not(None))
        .filter(OutboundEmailAccount.imap_password_encrypted.is_not(None))
        .filter(OutboundEmailAccount.imap_security_mode.is_not(None))
        .filter(or_(OutboundEmailAccount.imap_last_seen_at.is_(None), OutboundEmailAccount.imap_last_seen_at < cutoff))
        .order_by(OutboundEmailAccount.imap_last_seen_at.asc().nullsfirst(), OutboundEmailAccount.priority.asc(), OutboundEmailAccount.id.asc())
        .limit(limit or MAX_FETCH_PER_ACCOUNT)
        .all()
    )
    jobs: list[BackgroundJob] = []
    for account in rows:
        job = enqueue_background_job(
            db,
            queue_name=EMAIL_MAILBOX_SYNC_QUEUE,
            job_type=EMAIL_MAILBOX_SYNC_JOB,
            payload={"account_id": account.id},
            dedupe_key=f"email-mailbox-sync:{account.id}",
        )
        account.imap_last_sync_job_id = job.id
        jobs.append(job)
    return jobs


def process_email_mailbox_sync_job(db: Session, *, account_id: int) -> MailboxSyncResult:
    account = db.query(OutboundEmailAccount).filter(OutboundEmailAccount.id == account_id).first()
    if account is None:
        raise RuntimeError("email_mailbox_account_not_found")
    return poll_imap_account(db, account)


def build_email_mailbox_sync_status(db: Session, current_user) -> EmailMailboxSyncStatusRead:
    ensure_can_manage_runtime(current_user, db)
    settings = get_settings()
    rows = db.query(OutboundEmailAccount).order_by(OutboundEmailAccount.priority.asc(), OutboundEmailAccount.id.asc()).all()
    return EmailMailboxSyncStatusRead(
        generated_at=utc_now(),
        daemon_enabled=settings.email_mailbox_sync_enabled,
        interval_seconds=settings.email_mailbox_sync_interval_seconds,
        enabled_accounts=sum(1 for row in rows if row.inbound_enabled),
        configured_accounts=sum(1 for row in rows if _configured(row)),
        pending_jobs=db.query(BackgroundJob).filter(BackgroundJob.job_type == EMAIL_MAILBOX_SYNC_JOB, BackgroundJob.status == JobStatus.pending).count(),
        dead_jobs=db.query(BackgroundJob).filter(BackgroundJob.job_type == EMAIL_MAILBOX_SYNC_JOB, BackgroundJob.status == JobStatus.dead).count(),
        accounts=[
            EmailMailboxSyncAccountStatus(
                account_id=row.id,
                display_name=row.display_name,
                from_address=row.from_address,
                inbound_enabled=bool(row.inbound_enabled),
                configured=_configured(row),
                imap_host=row.imap_host,
                imap_mailbox=row.imap_mailbox,
                imap_sync_cursor=row.imap_sync_cursor,
                imap_last_seen_at=row.imap_last_seen_at,
                imap_last_status=row.imap_last_status,
                imap_last_error=row.imap_last_error,
                imap_last_sync_job_id=row.imap_last_sync_job_id,
            )
            for row in rows
        ],
    )
