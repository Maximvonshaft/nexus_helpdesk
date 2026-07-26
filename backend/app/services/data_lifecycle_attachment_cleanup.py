from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Customer, Ticket, TicketAttachment
from .data_lifecycle_service import DataLifecycleError
from .storage import get_storage_backend
from .tenant_authority import resolve_actor_tenant_id


@dataclass(frozen=True)
class AttachmentCleanupReceipt:
    customer_id: int
    attachment_count: int
    deleted_count: int
    already_absent_count: int
    key_hashes: tuple[str, ...]


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def delete_subject_attachment_blobs(
    db: Session,
    *,
    actor,
    customer_id: int,
) -> AttachmentCleanupReceipt:
    """Delete every subject attachment through the canonical storage backend.

    External deletion is verified before attachment metadata is cleared. The
    returned receipt contains hashes only; raw storage keys never enter audit or
    DSAR result payloads. Repeated execution is idempotent because absent objects
    are accepted only after the backend verifies absence.
    """

    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        raise DataLifecycleError("privacy_requires_tenant_authority", status_code=403)
    customer = db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise DataLifecycleError("data_subject_not_found", status_code=404)
    ticket_ids = [
        int(value)
        for (value,) in db.query(Ticket.id)
        .filter(Ticket.tenant_id == tenant_id, Ticket.customer_id == customer_id)
        .order_by(Ticket.id.asc())
        .all()
    ]
    attachments = (
        db.query(TicketAttachment)
        .filter(TicketAttachment.ticket_id.in_(ticket_ids))
        .order_by(TicketAttachment.id.asc())
        .all()
        if ticket_ids
        else []
    )
    storage = get_storage_backend()
    deleted = 0
    absent = 0
    hashes: list[str] = []
    for row in attachments:
        key = str(row.storage_key or "").strip()
        if not key:
            # Legacy local-path metadata cannot be represented as a verified
            # storage deletion. Fail closed rather than deleting a guessed path.
            if row.file_path or row.file_url:
                raise DataLifecycleError("privacy_attachment_storage_key_required")
            db.delete(row)
            continue
        try:
            receipt = storage.delete(key)
        except Exception as exc:
            raise DataLifecycleError("privacy_attachment_delete_not_verified") from exc
        hashes.append(_key_hash(key))
        deleted += int(receipt.deleted)
        absent += int(receipt.already_absent)
        row.storage_key = None
        row.file_path = None
        row.file_url = None
        row.file_name = "erased-attachment"
        row.mime_type = "application/octet-stream"
        row.file_size = 0
        db.delete(row)
    db.flush()
    return AttachmentCleanupReceipt(
        customer_id=customer_id,
        attachment_count=len(attachments),
        deleted_count=deleted,
        already_absent_count=absent,
        key_hashes=tuple(hashes),
    )
