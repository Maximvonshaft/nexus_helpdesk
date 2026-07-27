from __future__ import annotations

import email.utils
import imaplib
import re
from dataclasses import dataclass
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from typing import Iterable, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..enums import JobStatus, TicketStatus
from ..models import BackgroundJob, Market, OutboundEmailAccount, Ticket
from ..models_channel_intake import (
    CustomerIdentityBinding,
    EmailIntakeQuarantine,
)
from ..models_job_scope import BackgroundJobScope
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
from .email_mailbox_identity import (
    normalize_mailbox_header_id,
    normalize_mailbox_references,
)
from .identity_tenant_scope import actor_tenant_id
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


class EmailMailboxScopeError(RuntimeError):
    pass


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
    quarantined: int = 0


def _crypto() -> SecretCryptoService:
    return SecretCryptoService.outbound_email()


def _configured(row: OutboundEmailAccount) -> bool:
    return bool(
        row.inbound_enabled
        and row.imap_host
        and row.imap_port
        and row.imap_username
        and row.imap_password_encrypted
        and row.imap_security_mode
    )


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


def _account_market(db: Session, account: OutboundEmailAccount) -> Market:
    if account.market_id is None:
        raise EmailMailboxScopeError("email_mailbox_market_required")
    market = db.get(Market, int(account.market_id))
    if market is None or not market.is_active or market.tenant_id is None:
        raise EmailMailboxScopeError("email_mailbox_tenant_scope_missing")
    return market


def _account_tenant_id(db: Session, account: OutboundEmailAccount) -> int:
    return int(_account_market(db, account).tenant_id)


def _find_open_ticket_by_sender(
    db: Session,
    account: OutboundEmailAccount,
    from_address: str,
) -> Ticket | None:
    tenant_id = _account_tenant_id(db, account)
    normalized = normalize_email(from_address)
    if not normalized:
        return None
    customer_ids = select(CustomerIdentityBinding.customer_id).where(
        CustomerIdentityBinding.tenant_id == tenant_id,
        CustomerIdentityBinding.identity_type == "email",
        CustomerIdentityBinding.normalized_value == normalized,
    )
    return (
        db.query(Ticket)
        .filter(
            Ticket.tenant_id == tenant_id,
            Ticket.customer_id.in_(customer_ids),
            Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]),
        )
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .first()
    )


def _resolve_ticket(
    db: Session,
    account: OutboundEmailAccount,
    message: ParsedMailboxMessage,
) -> Ticket | None:
    tenant_id = _account_tenant_id(db, account)
    ticket_id = _extract_ticket_id(
        [
            message.references,
            message.in_reply_to,
            message.message_id,
            message.subject,
            message.body[:1000],
        ]
    )
    if ticket_id is not None:
        return (
            db.query(Ticket)
            .filter(Ticket.id == ticket_id, Ticket.tenant_id == tenant_id)
            .first()
        )
    return _find_open_ticket_by_sender(db, account, message.from_address)


def _connect_imap(row: OutboundEmailAccount) -> MailboxClient:
    if not _configured(row):
        raise RuntimeError("imap_account_not_configured")
    password = _crypto().decrypt(row.imap_password_encrypted or "")
    if row.imap_security_mode == "ssl":
        client = imaplib.IMAP4_SSL(
            str(row.imap_host),
            int(row.imap_port or 993),
        )
    else:
        client = imaplib.IMAP4(
            str(row.imap_host),
            int(row.imap_port or 143),
        )
        if row.imap_security_mode == "starttls":
            client.starttls()
    client.login(str(row.imap_username), password)
    return client


def _extract_fetch_body(fetch_item) -> bytes | None:
    if (
        isinstance(fetch_item, tuple)
        and len(fetch_item) >= 2
        and isinstance(fetch_item[1], bytes)
    ):
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


def _provider_message_id(account: OutboundEmailAccount, uid: str) -> str:
    return f"imap:{account.id}:{uid}"[:255]


def _quarantine_unmatched_message(
    db: Session,
    *,
    account: OutboundEmailAccount,
    message: ParsedMailboxMessage,
) -> tuple[EmailIntakeQuarantine, bool]:
    provider_message_id = _provider_message_id(account, message.uid)
    existing = (
        db.query(EmailIntakeQuarantine)
        .filter(
            EmailIntakeQuarantine.account_id == account.id,
            EmailIntakeQuarantine.provider_message_id == provider_message_id,
        )
        .first()
    )
    if existing is not None:
        return existing, False
    row = EmailIntakeQuarantine(
        tenant_id=_account_tenant_id(db, account),
        account_id=account.id,
        provider_message_id=provider_message_id,
        mailbox_uid=message.uid[:80],
        from_address=message.from_address[:320],
        from_name=message.from_name,
        to_address=message.to_address,
        cc=message.cc,
        subject=message.subject,
        body=message.body,
        mailbox_message_id=message.message_id,
        mailbox_references=message.references,
        in_reply_to=message.in_reply_to,
        received_at=message.received_at,
        status="pending_intake",
        reason_code="ticket_not_resolved",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row, True


def poll_imap_account(
    db: Session,
    account: OutboundEmailAccount,
    *,
    client: MailboxClient | None = None,
    limit: int = MAX_FETCH_PER_ACCOUNT,
) -> MailboxSyncResult:
    tenant_id = _account_tenant_id(db, account)
    if not _configured(account):
        account.imap_last_status = "not_configured"
        account.imap_last_error = "IMAP inbound sync is not configured"
        account.imap_last_seen_at = utc_now()
        return MailboxSyncResult(
            account_id=account.id,
            fetched=0,
            ingested=0,
            skipped=0,
            cursor=account.imap_sync_cursor,
            quarantined=0,
        )

    owns_client = client is None
    mailbox_client = client or _connect_imap(account)
    fetched = 0
    ingested = 0
    skipped = 0
    quarantined = 0
    last_uid = _int_cursor(account.imap_sync_cursor)
    try:
        mailbox_client.select(account.imap_mailbox or "INBOX")
        status_value, data = mailbox_client.uid("search", None, "ALL")
        if not _imap_status_ok(status_value):
            raise RuntimeError("imap_search_failed")
        uids: list[int] = []
        for chunk in data or []:
            for value in _imap_bytes(chunk).split():
                try:
                    uid_value = int(value)
                except ValueError:
                    continue
                if uid_value > last_uid:
                    uids.append(uid_value)
        bounded_limit = max(1, min(limit, MAX_FETCH_PER_ACCOUNT))
        for uid_value in sorted(uids)[:bounded_limit]:
            fetch_status, fetch_data = mailbox_client.uid(
                "fetch",
                str(uid_value),
                "(RFC822)",
            )
            if not _imap_status_ok(fetch_status):
                raise RuntimeError(f"imap_fetch_failed:{uid_value}")
            raw = next(
                (
                    body
                    for item in fetch_data or []
                    for body in [_extract_fetch_body(item)]
                    if body is not None
                ),
                None,
            )
            if raw is None:
                raise RuntimeError(f"imap_fetch_body_missing:{uid_value}")
            fetched += 1
            parsed = _parse_message(str(uid_value), raw)
            if parsed is None:
                raise RuntimeError(f"imap_message_unprocessable:{uid_value}")
            ticket = _resolve_ticket(db, account, parsed)
            if ticket is None:
                _row, created = _quarantine_unmatched_message(
                    db,
                    account=account,
                    message=parsed,
                )
                if created:
                    quarantined += 1
                else:
                    skipped += 1
                last_uid = uid_value
                continue
            result = ingest_ticket_inbound_email_system(
                db,
                ticket_id=ticket.id,
                actor_id=account.updated_by or account.created_by,
                source="imap_poll",
                expected_tenant_id=tenant_id,
                payload=InboundEmailIngestRequest(
                    from_address=parsed.from_address,
                    from_name=parsed.from_name,
                    to_address=parsed.to_address,
                    cc=parsed.cc,
                    subject=parsed.subject,
                    body=parsed.body,
                    provider="imap",
                    provider_message_id=_provider_message_id(account, parsed.uid),
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
            last_uid = uid_value

        account.imap_sync_cursor = (
            str(last_uid) if last_uid else account.imap_sync_cursor
        )
        account.imap_last_seen_at = utc_now()
        account.imap_last_status = "ok"
        account.imap_last_error = None
        return MailboxSyncResult(
            account_id=account.id,
            fetched=fetched,
            ingested=ingested,
            skipped=skipped,
            cursor=account.imap_sync_cursor,
            quarantined=quarantined,
        )
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


def _tenant_email_account_query(
    db: Session,
    *,
    tenant_id: int,
):
    market_ids = select(Market.id).where(
        Market.tenant_id == tenant_id,
        Market.is_active.is_(True),
    )
    return db.query(OutboundEmailAccount).filter(
        OutboundEmailAccount.market_id.in_(market_ids)
    )


def enqueue_email_mailbox_sync_jobs(
    db: Session,
    *,
    current_user=None,
    account_id: int | None = None,
) -> EmailMailboxSyncEnqueueResponse:
    if current_user is None:
        raise EmailMailboxScopeError("email_mailbox_actor_required")
    ensure_can_manage_runtime(current_user, db)
    tenant_id = actor_tenant_id(db, current_user)
    if tenant_id is None:
        raise EmailMailboxScopeError("email_mailbox_actor_tenant_required")
    query = _tenant_email_account_query(db, tenant_id=tenant_id).filter(
        OutboundEmailAccount.inbound_enabled.is_(True),
        OutboundEmailAccount.is_active.is_(True),
    )
    if account_id is not None:
        query = query.filter(OutboundEmailAccount.id == account_id)
    accounts = query.order_by(
        OutboundEmailAccount.priority.asc(),
        OutboundEmailAccount.id.asc(),
    ).all()
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
    return EmailMailboxSyncEnqueueResponse(
        enqueued=len(jobs),
        job_ids=[job.id for job in jobs],
    )


def enqueue_due_email_mailbox_sync_jobs(
    db: Session,
    *,
    interval_seconds: int,
    limit: int | None = None,
) -> list[BackgroundJob]:
    cutoff = utc_now() - timedelta(seconds=interval_seconds)
    tenant_market_ids = select(Market.id).where(
        Market.tenant_id.is_not(None),
        Market.is_active.is_(True),
    )
    rows = (
        db.query(OutboundEmailAccount)
        .filter(
            OutboundEmailAccount.market_id.in_(tenant_market_ids),
            OutboundEmailAccount.inbound_enabled.is_(True),
            OutboundEmailAccount.is_active.is_(True),
            OutboundEmailAccount.imap_host.is_not(None),
            OutboundEmailAccount.imap_port.is_not(None),
            OutboundEmailAccount.imap_username.is_not(None),
            OutboundEmailAccount.imap_password_encrypted.is_not(None),
            OutboundEmailAccount.imap_security_mode.is_not(None),
            or_(
                OutboundEmailAccount.imap_last_seen_at.is_(None),
                OutboundEmailAccount.imap_last_seen_at < cutoff,
            ),
        )
        .order_by(
            OutboundEmailAccount.imap_last_seen_at.asc().nullsfirst(),
            OutboundEmailAccount.priority.asc(),
            OutboundEmailAccount.id.asc(),
        )
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


def process_email_mailbox_sync_job(
    db: Session,
    *,
    account_id: int,
) -> MailboxSyncResult:
    account = db.get(OutboundEmailAccount, account_id)
    if account is None:
        raise RuntimeError("email_mailbox_account_not_found")
    _account_tenant_id(db, account)
    return poll_imap_account(db, account)


def build_email_mailbox_sync_status(
    db: Session,
    current_user,
) -> EmailMailboxSyncStatusRead:
    ensure_can_manage_runtime(current_user, db)
    tenant_id = actor_tenant_id(db, current_user)
    if tenant_id is None:
        raise EmailMailboxScopeError("email_mailbox_actor_tenant_required")
    settings = get_settings()
    rows = _tenant_email_account_query(db, tenant_id=tenant_id).order_by(
        OutboundEmailAccount.priority.asc(),
        OutboundEmailAccount.id.asc(),
    ).all()
    job_ids = select(BackgroundJobScope.job_id).where(
        BackgroundJobScope.scope_type == "tenant",
        BackgroundJobScope.tenant_id == tenant_id,
        BackgroundJobScope.purpose == "human_support",
    )
    return EmailMailboxSyncStatusRead(
        generated_at=utc_now(),
        daemon_enabled=settings.email_mailbox_sync_enabled,
        interval_seconds=settings.email_mailbox_sync_interval_seconds,
        enabled_accounts=sum(1 for row in rows if row.inbound_enabled),
        configured_accounts=sum(1 for row in rows if _configured(row)),
        pending_jobs=(
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id.in_(job_ids),
                BackgroundJob.job_type == EMAIL_MAILBOX_SYNC_JOB,
                BackgroundJob.status == JobStatus.pending,
            )
            .count()
        ),
        dead_jobs=(
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id.in_(job_ids),
                BackgroundJob.job_type == EMAIL_MAILBOX_SYNC_JOB,
                BackgroundJob.status == JobStatus.dead,
            )
            .count()
        ),
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
