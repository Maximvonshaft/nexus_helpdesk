from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Customer
from ..models_channel_intake import CustomerIdentityBinding, EmailIntakeQuarantine
from ..utils.time import utc_now


class ChannelIdentityPrivacyLifecycleError(RuntimeError):
    pass


def collect_customer_identity_values(
    db: Session,
    *,
    tenant_id: int,
    customer_id: int,
) -> set[str]:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise ChannelIdentityPrivacyLifecycleError(
            "privacy_customer_identity_scope_conflict"
        )
    values = {
        str(customer.email or "").strip().lower(),
        str(customer.phone or "").strip(),
        str(customer.external_ref or "").strip(),
    }
    values.update(
        str(value or "").strip().lower() if identity_type == "email" else str(value or "").strip()
        for identity_type, value in db.query(
            CustomerIdentityBinding.identity_type,
            CustomerIdentityBinding.normalized_value,
        )
        .filter(
            CustomerIdentityBinding.tenant_id == tenant_id,
            CustomerIdentityBinding.customer_id == customer_id,
        )
        .all()
    )
    return {value for value in values if value}


def collect_channel_identity_subject_export(
    db: Session,
    *,
    tenant_id: int,
    customer_id: int,
    max_rows: int,
) -> dict[str, list[dict[str, Any]]]:
    bindings = (
        db.query(CustomerIdentityBinding)
        .filter(
            CustomerIdentityBinding.tenant_id == tenant_id,
            CustomerIdentityBinding.customer_id == customer_id,
        )
        .order_by(CustomerIdentityBinding.id.asc())
        .limit(max_rows + 1)
        .all()
    )
    if len(bindings) > max_rows:
        raise ChannelIdentityPrivacyLifecycleError(
            "dsar_export_customer_identity_bindings_requires_storage_job"
        )

    email_values = {
        str(row.normalized_value or "").strip().lower()
        for row in bindings
        if row.identity_type == "email" and str(row.normalized_value or "").strip()
    }
    customer = db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise ChannelIdentityPrivacyLifecycleError(
            "privacy_customer_identity_scope_conflict"
        )
    if customer.email:
        email_values.add(str(customer.email).strip().lower())

    quarantine = []
    if email_values:
        quarantine = (
            db.query(EmailIntakeQuarantine)
            .filter(
                EmailIntakeQuarantine.tenant_id == tenant_id,
                func.lower(func.trim(EmailIntakeQuarantine.from_address)).in_(
                    sorted(email_values)
                ),
            )
            .order_by(EmailIntakeQuarantine.id.asc())
            .limit(max_rows + 1)
            .all()
        )
    if len(quarantine) > max_rows:
        raise ChannelIdentityPrivacyLifecycleError(
            "dsar_export_email_intake_quarantine_requires_storage_job"
        )

    return {
        "customer_identity_bindings": [
            {
                "id": row.id,
                "identity_type": row.identity_type,
                "normalized_value": row.normalized_value,
                "source": row.source,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in bindings
        ],
        "email_intake_quarantine": [
            {
                "id": row.id,
                "account_id": row.account_id,
                "provider_message_id": row.provider_message_id,
                "mailbox_uid": row.mailbox_uid,
                "from_address": row.from_address,
                "from_name": row.from_name,
                "to_address": row.to_address,
                "cc": row.cc,
                "subject": row.subject,
                "body": row.body,
                "mailbox_message_id": row.mailbox_message_id,
                "mailbox_references": row.mailbox_references,
                "in_reply_to": row.in_reply_to,
                "received_at": row.received_at.isoformat() if row.received_at else None,
                "status": row.status,
                "reason_code": row.reason_code,
                "conversation_id": row.conversation_id,
                "ticket_id": row.ticket_id,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in quarantine
        ],
    }


def redact_channel_identity_subject_records(
    db: Session,
    *,
    tenant_id: int,
    customer_id: int,
    anonymize: Callable[..., str],
) -> int:
    customer = db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise ChannelIdentityPrivacyLifecycleError(
            "privacy_customer_identity_scope_conflict"
        )
    bindings = (
        db.query(CustomerIdentityBinding)
        .filter(
            CustomerIdentityBinding.tenant_id == tenant_id,
            CustomerIdentityBinding.customer_id == customer_id,
        )
        .order_by(CustomerIdentityBinding.id.asc())
        .all()
    )
    email_values = {
        str(row.normalized_value or "").strip().lower()
        for row in bindings
        if row.identity_type == "email" and str(row.normalized_value or "").strip()
    }
    if customer.email:
        email_values.add(str(customer.email).strip().lower())

    quarantine = []
    if email_values:
        quarantine = (
            db.query(EmailIntakeQuarantine)
            .filter(
                EmailIntakeQuarantine.tenant_id == tenant_id,
                func.lower(func.trim(EmailIntakeQuarantine.from_address)).in_(
                    sorted(email_values)
                ),
            )
            .order_by(EmailIntakeQuarantine.id.asc())
            .all()
        )

    for row in quarantine:
        row.from_address = (
            anonymize(
                row.from_address,
                namespace="email-quarantine",
                record_id=row.id,
            )
            + "@invalid"
        )
        row.from_name = None
        row.to_address = None
        row.cc = None
        row.subject = None
        row.body = "[redacted by privacy request]"
        row.mailbox_message_id = None
        row.mailbox_references = None
        row.in_reply_to = None
        row.status = "rejected"
        row.reason_code = "privacy_redacted"
        row.conversation_id = None
        row.ticket_id = None
        row.updated_at = utc_now()

    for row in bindings:
        replacement = anonymize(
            row.normalized_value,
            namespace=f"identity-{row.identity_type}",
            record_id=row.id,
        )
        row.normalized_value = (
            replacement + "@invalid"
            if row.identity_type == "email"
            else replacement
        )
        row.source = "privacy_erasure"
        row.updated_at = utc_now()

    db.flush()
    return len(bindings) + len(quarantine)
