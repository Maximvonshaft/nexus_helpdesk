from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Customer, Ticket, TicketAttachment
from ..models_case_governance import DataSubjectRequest
from .data_lifecycle_service import AnonymizationReceipt, DataLifecycleError
from .storage import get_storage_backend
from .tenant_authority import resolve_actor_tenant_id


@dataclass(frozen=True)
class AttachmentCleanupReceipt:
    customer_id: int
    attachment_count: int
    deleted_count: int
    already_absent_count: int
    key_hashes: tuple[str, ...]


@dataclass(frozen=True)
class SubjectDeletionReceipt:
    customer_id: int
    ticket_count: int
    conversation_count: int
    message_count: int
    related_row_count: int
    attachment_count: int
    attachment_deleted_count: int
    attachment_already_absent_count: int
    receipt_sha256: str


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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
            # Legacy path/URL metadata cannot prove external deletion. Never
            # guess a filesystem or object-store identity.
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


def bind_attachment_cleanup_receipt(
    db: Session,
    *,
    request_id: int,
    database_receipt: AnonymizationReceipt,
    attachment_receipt: AttachmentCleanupReceipt,
) -> SubjectDeletionReceipt:
    request = db.get(DataSubjectRequest, request_id)
    if request is None:
        raise DataLifecycleError("dsar_not_found", status_code=404)
    existing = request.result_manifest_json or {}
    if existing.get("schema") == "nexus.subject-deletion-manifest.v1":
        cleanup = existing.get("attachment_cleanup") or {}
        return SubjectDeletionReceipt(
            customer_id=request.customer_id,
            ticket_count=int(existing.get("ticket_count") or 0),
            conversation_count=int(existing.get("conversation_count") or 0),
            message_count=int(existing.get("message_count") or 0),
            related_row_count=int(existing.get("related_row_count") or 0),
            attachment_count=int(cleanup.get("attachment_count") or 0),
            attachment_deleted_count=int(cleanup.get("deleted_count") or 0),
            attachment_already_absent_count=int(
                cleanup.get("already_absent_count") or 0
            ),
            receipt_sha256=str(request.result_sha256 or ""),
        )
    manifest = {
        "schema": "nexus.subject-deletion-manifest.v1",
        "ticket_count": database_receipt.ticket_count,
        "conversation_count": database_receipt.conversation_count,
        "message_count": database_receipt.message_count,
        "related_row_count": database_receipt.related_row_count,
        "database_receipt_sha256": database_receipt.receipt_sha256,
        "attachment_cleanup": {
            "attachment_count": attachment_receipt.attachment_count,
            "deleted_count": attachment_receipt.deleted_count,
            "already_absent_count": attachment_receipt.already_absent_count,
            "storage_key_hashes": list(attachment_receipt.key_hashes),
            "raw_storage_keys_persisted": False,
        },
        "raw_values_persisted": False,
    }
    digest = _digest(manifest)
    request.result_manifest_json = manifest
    request.result_sha256 = digest
    db.flush()
    return SubjectDeletionReceipt(
        customer_id=request.customer_id,
        ticket_count=database_receipt.ticket_count,
        conversation_count=database_receipt.conversation_count,
        message_count=database_receipt.message_count,
        related_row_count=database_receipt.related_row_count,
        attachment_count=attachment_receipt.attachment_count,
        attachment_deleted_count=attachment_receipt.deleted_count,
        attachment_already_absent_count=attachment_receipt.already_absent_count,
        receipt_sha256=digest,
    )
