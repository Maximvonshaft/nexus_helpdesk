from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/deletion_preflight_order.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api import data_lifecycle as api  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import Customer, Tenant, Ticket, User  # noqa: E402
from app.services.data_lifecycle_service import (  # noqa: E402
    create_data_subject_request,
    place_legal_hold,
    qualify_data_subject_request,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preflight.db'}",
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


def make_request(db, *, ticket_status: TicketStatus):
    tenant = Tenant(
        tenant_key="deletion-preflight",
        display_name="Deletion Preflight",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        username="privacy-preflight-admin",
        display_name="Privacy Preflight Admin",
        email="privacy-preflight-admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    customer = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        name="Deletion Subject",
        email="deletion-subject@example.test",
        email_normalized="deletion-subject@example.test",
        phone="+410000088",
        phone_normalized="+410000088",
    )
    db.add_all([admin, customer])
    db.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no="DELETE-PREFLIGHT-1",
        title="Deletion preflight case",
        description="Deletion preflight case",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=ticket_status,
    )
    db.add(ticket)
    db.flush()
    request, _ = create_data_subject_request(
        db,
        actor=admin,
        customer_id=customer.id,
        request_key=f"delete-{ticket_status.value}",
        request_type="delete",
    )
    qualify_data_subject_request(
        db,
        actor=admin,
        request_id=request.id,
        identity_evidence=customer.email,
    )
    db.commit()
    return tenant, admin, customer, ticket, request


def assert_storage_not_called(monkeypatch):
    calls = {"count": 0}

    def forbidden_storage_delete(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("storage must not run before deletion preflight")

    monkeypatch.setattr(
        api,
        "delete_subject_attachment_blobs",
        forbidden_storage_delete,
    )
    return calls


def test_active_case_blocks_before_storage(db_session, monkeypatch):
    _, admin, _, _, request = make_request(
        db_session,
        ticket_status=TicketStatus.in_progress,
    )
    calls = assert_storage_not_called(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        api.delete_dsar_subject(
            request.id,
            db=db_session,
            current_user=admin,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "privacy_active_case_blocks_deletion"
    assert calls["count"] == 0


def test_legal_hold_blocks_before_storage(db_session, monkeypatch):
    _, admin, customer, ticket, request = make_request(
        db_session,
        ticket_status=TicketStatus.closed,
    )
    place_legal_hold(
        db_session,
        actor=admin,
        customer_id=customer.id,
        ticket_id=ticket.id,
        reason_code="litigation",
    )
    db_session.commit()
    calls = assert_storage_not_called(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        api.delete_dsar_subject(
            request.id,
            db=db_session,
            current_user=admin,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "privacy_legal_hold_blocks_deletion"
    assert calls["count"] == 0
