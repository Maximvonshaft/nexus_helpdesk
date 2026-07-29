from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/channel_identity_privacy.db",
)
os.environ.setdefault(
    "OUTBOUND_EMAIL_ENCRYPTION_KEY",
    Fernet.generate_key().decode("ascii"),
)

from app.db import Base
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Customer, OutboundEmailAccount, Tenant, User
from app.models_channel_intake import CustomerIdentityBinding, EmailIntakeQuarantine
from app.services.data_lifecycle_service import (
    build_data_subject_export,
    create_data_subject_request,
    execute_data_subject_deletion,
    qualify_data_subject_request,
)
from app.services.secret_crypto import SecretCryptoService
from app.utils.time import utc_now


register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'channel-privacy.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _subject(db):
    tenant = Tenant(
        tenant_key="channel-privacy",
        display_name="Channel Privacy",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        username="channel-privacy-admin",
        display_name="Channel Privacy Admin",
        email="admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    customer = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        name="Ticketless Customer",
        email="primary@example.test",
        email_normalized="primary@example.test",
        phone=None,
        phone_normalized=None,
        external_ref=None,
    )
    db.add_all([admin, customer])
    db.flush()
    binding = CustomerIdentityBinding(
        tenant_id=tenant.id,
        customer_id=customer.id,
        identity_type="email",
        normalized_value="alternate@example.test",
        source="email_intake",
    )
    account = OutboundEmailAccount(
        display_name="Support Mailbox",
        host="smtp.example.test",
        port=587,
        username="support@example.test",
        password_encrypted=SecretCryptoService.outbound_email().encrypt(
            "smtp-secret"
        ),
        from_address="support@example.test",
        reply_to=None,
        security_mode="starttls",
        inbound_enabled=True,
        imap_host="imap.example.test",
        imap_port=993,
        imap_username="support@example.test",
        imap_password_encrypted=SecretCryptoService.outbound_email().encrypt(
            "imap-secret"
        ),
        imap_security_mode="ssl",
        imap_mailbox="INBOX",
        is_active=True,
        priority=10,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add_all([binding, account])
    db.flush()
    quarantine = EmailIntakeQuarantine(
        tenant_id=tenant.id,
        account_id=account.id,
        provider_message_id="imap:ticketless:1",
        mailbox_uid="1",
        from_address="Alternate@Example.Test",
        from_name="Ticketless Customer",
        to_address="support@example.test",
        cc="copy@example.test",
        subject="Private unmatched email",
        body="Full private email body that never created a ticket.",
        mailbox_message_id="<ticketless-1@example.test>",
        mailbox_references="<earlier@example.test>",
        in_reply_to="<earlier@example.test>",
        received_at=utc_now(),
        status="pending_intake",
        reason_code="ticket_not_resolved",
    )
    db.add(quarantine)
    db.commit()
    return tenant, admin, customer, binding, quarantine


def _qualified_request(db, *, admin, customer, key: str, request_type: str):
    request, created = create_data_subject_request(
        db,
        actor=admin,
        customer_id=customer.id,
        request_key=key,
        request_type=request_type,
    )
    assert created is True
    qualified = qualify_data_subject_request(
        db,
        actor=admin,
        request_id=request.id,
        identity_evidence="alternate@example.test",
    )
    assert qualified.status == "qualified"
    return qualified


def test_ticketless_bound_identity_qualifies_and_exports_quarantined_email(
    db_session,
) -> None:
    _tenant, admin, customer, binding, quarantine = _subject(db_session)
    request = _qualified_request(
        db_session,
        admin=admin,
        customer=customer,
        key="ticketless-export",
        request_type="export",
    )

    payload = build_data_subject_export(
        db_session,
        actor=admin,
        request_id=request.id,
    )

    assert payload["tickets"] == []
    assert payload["conversations"] == []
    assert payload["customer_identity_bindings"] == [
        {
            "id": binding.id,
            "identity_type": "email",
            "normalized_value": "alternate@example.test",
            "source": "email_intake",
            "created_at": binding.created_at.isoformat(),
            "updated_at": binding.updated_at.isoformat(),
        }
    ]
    assert len(payload["email_intake_quarantine"]) == 1
    exported = payload["email_intake_quarantine"][0]
    assert exported["id"] == quarantine.id
    assert exported["from_address"] == "Alternate@Example.Test"
    assert exported["subject"] == "Private unmatched email"
    assert exported["body"] == (
        "Full private email body that never created a ticket."
    )


def test_ticketless_deletion_anonymizes_binding_and_quarantine_in_one_receipt(
    db_session,
) -> None:
    _tenant, admin, customer, binding, quarantine = _subject(db_session)
    request = _qualified_request(
        db_session,
        admin=admin,
        customer=customer,
        key="ticketless-delete",
        request_type="delete",
    )

    receipt = execute_data_subject_deletion(
        db_session,
        actor=admin,
        request_id=request.id,
    )
    db_session.refresh(customer)
    db_session.refresh(binding)
    db_session.refresh(quarantine)

    assert receipt.ticket_count == 0
    assert receipt.conversation_count == 0
    assert receipt.related_row_count == 2
    assert customer.email is None
    assert binding.normalized_value.endswith("@invalid")
    assert binding.normalized_value != "alternate@example.test"
    assert binding.source == "privacy_erasure"
    assert quarantine.from_address.endswith("@invalid")
    assert quarantine.from_address.lower() != "alternate@example.test"
    assert quarantine.from_name is None
    assert quarantine.to_address is None
    assert quarantine.cc is None
    assert quarantine.subject is None
    assert quarantine.body == "[redacted by privacy request]"
    assert quarantine.mailbox_message_id is None
    assert quarantine.mailbox_references is None
    assert quarantine.in_reply_to is None
    assert quarantine.status == "rejected"
    assert quarantine.reason_code == "privacy_redacted"
