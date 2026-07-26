from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/data_lifecycle_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    NoteVisibility,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import (  # noqa: E402
    Customer,
    Tenant,
    Ticket,
    TicketAttachment,
    TicketComment,
    User,
)
from app.services.data_lifecycle_service import (  # noqa: E402
    DataLifecycleError,
    apply_retention_execution,
    build_data_subject_export,
    create_data_subject_request,
    create_retention_policy,
    execute_data_subject_deletion,
    place_legal_hold,
    plan_retention_execution,
    qualify_data_subject_request,
    release_legal_hold,
)
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'privacy.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_tenant(db, key: str) -> Tenant:
    row = Tenant(
        tenant_key=key,
        display_name=key,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_admin(db, tenant: Tenant, username: str) -> User:
    row = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_customer(db, tenant: Tenant, suffix: str = "1") -> Customer:
    row = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        name=f"Customer {suffix}",
        email=f"customer-{suffix}@example.test",
        email_normalized=f"customer-{suffix}@example.test",
        phone=f"+4100000{suffix}",
        phone_normalized=f"+4100000{suffix}",
        external_ref=f"external-{suffix}",
        created_at=utc_now() - timedelta(days=800),
        updated_at=utc_now() - timedelta(days=800),
    )
    db.add(row)
    db.flush()
    return row


def make_ticket(
    db,
    tenant: Tenant,
    customer: Customer,
    *,
    status: TicketStatus = TicketStatus.closed,
    suffix: str = "1",
) -> Ticket:
    row = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no=f"PRIV-{suffix}",
        title=f"Private title {suffix}",
        description=f"Private description {suffix}",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=status,
        tracking_number=f"TRACK-{suffix}",
        issue_summary=f"Issue {suffix}",
        customer_request=f"Request {suffix}",
        preferred_reply_contact=customer.email,
        created_at=utc_now() - timedelta(days=700),
        updated_at=utc_now() - timedelta(days=700),
        closed_at=(utc_now() - timedelta(days=600) if status == TicketStatus.closed else None),
    )
    db.add(row)
    db.flush()
    return row


def qualify_request(db, admin, customer, *, request_type="export", key="req-1"):
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
        identity_evidence=customer.email,
    )
    assert qualified.status == "qualified"
    assert qualified.identity_evidence_hash
    assert customer.email not in qualified.identity_evidence_hash
    return qualified


def test_identity_qualification_and_cross_tenant_concealment(db_session):
    tenant = make_tenant(db_session, "privacy-one")
    other = make_tenant(db_session, "privacy-two")
    admin = make_admin(db_session, tenant, "admin-one")
    other_admin = make_admin(db_session, other, "admin-two")
    customer = make_customer(db_session, tenant)

    request, _ = create_data_subject_request(
        db_session,
        actor=admin,
        customer_id=customer.id,
        request_key="access-1",
        request_type="access",
    )
    with pytest.raises(DataLifecycleError, match="dsar_identity_verification_failed"):
        qualify_data_subject_request(
            db_session,
            actor=admin,
            request_id=request.id,
            identity_evidence="wrong@example.test",
        )
    qualified = qualify_data_subject_request(
        db_session,
        actor=admin,
        request_id=request.id,
        identity_evidence=customer.email,
    )
    assert qualified.status == "qualified"
    assert qualified.identity_evidence_hash != customer.email

    with pytest.raises(DataLifecycleError, match="dsar_not_found"):
        build_data_subject_export(
            db_session,
            actor=other_admin,
            request_id=request.id,
        )


def test_export_returns_subject_data_but_persists_only_manifest(db_session):
    tenant = make_tenant(db_session, "privacy-export")
    admin = make_admin(db_session, tenant, "export-admin")
    customer = make_customer(db_session, tenant)
    ticket = make_ticket(db_session, tenant, customer)
    db_session.add(
        TicketComment(
            ticket_id=ticket.id,
            author_id=admin.id,
            body="Customer private comment",
            visibility=NoteVisibility.external,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    conversation = WebchatConversation(
        public_id="privacy-conversation",
        visitor_token_hash="token-hash",
        tenant_key=tenant.tenant_key,
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_email=customer.email,
        visitor_phone=customer.phone,
        status="closed",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction="visitor",
            body="Customer private webchat text",
            body_text="Customer private webchat text",
            message_type="text",
            delivery_status="sent",
            created_at=utc_now(),
        )
    )
    db_session.flush()

    request = qualify_request(db_session, admin, customer)
    payload = build_data_subject_export(
        db_session,
        actor=admin,
        request_id=request.id,
    )

    assert payload["customer"]["email"] == customer.email
    assert payload["tickets"][0]["tracking_number"] == "TRACK-1"
    assert payload["webchat_messages"][0]["body"] == "Customer private webchat text"
    db_session.refresh(request)
    persisted = str(request.result_manifest_json)
    assert request.status == "completed"
    assert request.result_sha256
    assert customer.email not in persisted
    assert "Customer private webchat text" not in persisted
    assert request.result_manifest_json["persisted_raw_export"] is False


def test_delete_is_blocked_by_active_case_legal_hold_and_attachment(db_session):
    tenant = make_tenant(db_session, "privacy-blocks")
    admin = make_admin(db_session, tenant, "block-admin")
    customer = make_customer(db_session, tenant)
    active_ticket = make_ticket(
        db_session,
        tenant,
        customer,
        status=TicketStatus.in_progress,
    )
    request = qualify_request(
        db_session,
        admin,
        customer,
        request_type="delete",
        key="delete-active",
    )

    with pytest.raises(DataLifecycleError, match="privacy_active_case_blocks_deletion"):
        execute_data_subject_deletion(
            db_session,
            actor=admin,
            request_id=request.id,
        )

    active_ticket.status = TicketStatus.closed
    active_ticket.closed_at = utc_now()
    hold = place_legal_hold(
        db_session,
        actor=admin,
        customer_id=customer.id,
        ticket_id=None,
        reason_code="litigation",
    )
    with pytest.raises(DataLifecycleError, match="privacy_legal_hold_blocks_deletion"):
        execute_data_subject_deletion(
            db_session,
            actor=admin,
            request_id=request.id,
        )

    release_legal_hold(db_session, actor=admin, hold_id=hold.id)
    db_session.add(
        TicketAttachment(
            ticket_id=active_ticket.id,
            uploaded_by=admin.id,
            file_name="private.pdf",
            storage_key="private/blob",
            file_path=None,
            file_url=None,
            mime_type="application/pdf",
            file_size=10,
            visibility=NoteVisibility.external,
            created_at=utc_now(),
        )
    )
    db_session.flush()
    with pytest.raises(DataLifecycleError, match="privacy_attachment_blob_receipt_required"):
        execute_data_subject_deletion(
            db_session,
            actor=admin,
            request_id=request.id,
        )


def test_successful_delete_anonymizes_once_and_returns_receipt(db_session):
    tenant = make_tenant(db_session, "privacy-delete")
    admin = make_admin(db_session, tenant, "delete-admin")
    customer = make_customer(db_session, tenant)
    ticket = make_ticket(db_session, tenant, customer)
    conversation = WebchatConversation(
        public_id="delete-conversation",
        visitor_token_hash="token-hash",
        tenant_key=tenant.tenant_key,
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_email=customer.email,
        visitor_phone=customer.phone,
        status="closed",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(conversation)
    db_session.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="Delete this private text",
        body_text="Delete this private text",
        message_type="text",
        delivery_status="sent",
        created_at=utc_now(),
    )
    db_session.add(message)
    db_session.flush()
    request = qualify_request(
        db_session,
        admin,
        customer,
        request_type="delete",
        key="delete-success",
    )

    receipt = execute_data_subject_deletion(
        db_session,
        actor=admin,
        request_id=request.id,
    )
    same = execute_data_subject_deletion(
        db_session,
        actor=admin,
        request_id=request.id,
    )
    db_session.refresh(customer)
    db_session.refresh(ticket)
    db_session.refresh(conversation)
    db_session.refresh(message)

    assert receipt.receipt_sha256 == same.receipt_sha256
    assert customer.email is None
    assert customer.phone is None
    assert customer.name.startswith("erased-customer-")
    assert ticket.tracking_number is None
    assert ticket.description == "[redacted by privacy request]"
    assert conversation.visitor_email is None
    assert message.body == "[redacted by privacy request]"
    assert "Delete this private text" not in str(request.result_manifest_json)


def test_retention_requires_dry_run_then_explicit_apply(db_session):
    tenant = make_tenant(db_session, "privacy-retention")
    admin = make_admin(db_session, tenant, "retention-admin")
    customer = make_customer(db_session, tenant, suffix="retention")
    make_ticket(db_session, tenant, customer, suffix="retention")
    policy = create_retention_policy(
        db_session,
        actor=admin,
        resource_type="customer_profile",
        retention_days=365,
        legal_basis="contract retention expired",
    )
    execution = plan_retention_execution(
        db_session,
        actor=admin,
        policy_id=policy.id,
        execution_key="retention-2026-07",
    )

    assert execution.status == "dry_run"
    assert execution.affected_count == 0
    assert customer.id in execution.receipt_json["candidate_ids"]
    assert customer.email is not None

    applied = apply_retention_execution(
        db_session,
        actor=admin,
        execution_id=execution.id,
    )
    db_session.refresh(customer)

    assert applied.status == "applied"
    assert applied.affected_count == 1
    assert customer.email is None
    assert applied.receipt_sha256
