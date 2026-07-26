from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/data_subject_action_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    ConversationState,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import Customer, Tenant, Ticket, User  # noqa: E402
from app.models_case_governance import DataSubjectRequest  # noqa: E402
from app.models_privacy_runtime import DataProcessingRestriction  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.data_lifecycle_service import (  # noqa: E402
    DataLifecycleError,
    create_data_subject_request,
    qualify_data_subject_request,
)
from app.services.data_subject_action_service import (  # noqa: E402
    activate_data_processing_restriction,
    ensure_data_processing_allowed,
    execute_data_subject_correction,
    release_data_processing_restriction,
    DataProcessingRestricted,
)
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import (  # noqa: E402
    WebchatAITurn,
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'subject-actions.db'}",
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


def make_scope(db):
    tenant = Tenant(
        tenant_key="privacy-actions",
        display_name="Privacy Actions",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        username="privacy-admin",
        display_name="Privacy Admin",
        email="privacy-admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    customer = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        name="Original Customer",
        email="original@example.test",
        email_normalized="original@example.test",
        phone="+410000001",
        phone_normalized="+410000001",
        external_ref="original-ref",
    )
    db.add_all([admin, customer])
    db.flush()
    return tenant, admin, customer


def qualified_request(db, *, admin, customer, request_type, key):
    row, created = create_data_subject_request(
        db,
        actor=admin,
        customer_id=customer.id,
        request_key=key,
        request_type=request_type,
    )
    assert created is True
    return qualify_data_subject_request(
        db,
        actor=admin,
        request_id=row.id,
        identity_evidence=customer.email,
    )


def make_open_conversation(db, *, tenant, customer):
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no="PRIV-ACTION-1",
        title="Privacy restricted conversation",
        description="Customer requested processing restriction",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.ai_active,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id="privacy-restricted-conversation",
        visitor_token_hash="hash",
        tenant_key=tenant.tenant_key,
        channel_key=SourceChannel.web_chat.value,
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_email=customer.email,
        status="open",
        active_ai_status="processing",
        ai_suspended=False,
        handoff_status="none",
    )
    db.add(conversation)
    db.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="Please restrict automated processing.",
        body_text="Please restrict automated processing.",
        message_type="text",
        delivery_status="sent",
    )
    db.add(message)
    db.flush()
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=message.id,
        latest_visitor_message_id=message.id,
        status="processing",
        is_public_reply_allowed=True,
        started_at=utc_now(),
    )
    db.add(turn)
    db.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_for_message_id = message.id
    db.flush()
    return ticket, conversation, turn


def test_correction_is_idempotent_and_does_not_persist_raw_values_in_manifest(db_session):
    _, admin, customer = make_scope(db_session)
    request = qualified_request(
        db_session,
        admin=admin,
        customer=customer,
        request_type="correct",
        key="correct-1",
    )

    first = execute_data_subject_correction(
        db_session,
        actor=admin,
        request_id=request.id,
        name="Corrected Customer",
        email="corrected@example.test",
        phone="+410000099",
    )
    second = execute_data_subject_correction(
        db_session,
        actor=admin,
        request_id=request.id,
        name="Corrected Customer",
        email="corrected@example.test",
        phone="+410000099",
    )

    assert first.fields == second.fields == ("email", "name", "phone")
    assert customer.name == "Corrected Customer"
    assert customer.email_normalized == "corrected@example.test"
    assert customer.phone_normalized == "+410000099"
    assert request.status == "completed"
    persisted = str(request.result_manifest_json)
    assert "corrected@example.test" not in persisted
    assert "+410000099" not in persisted

    with pytest.raises(DataLifecycleError, match="dsar_correction_idempotency_conflict"):
        execute_data_subject_correction(
            db_session,
            actor=admin,
            request_id=request.id,
            email="different@example.test",
        )


def test_processing_restriction_routes_open_conversation_to_human_only_once(db_session):
    tenant, admin, customer = make_scope(db_session)
    ticket, conversation, turn = make_open_conversation(
        db_session,
        tenant=tenant,
        customer=customer,
    )
    request = qualified_request(
        db_session,
        admin=admin,
        customer=customer,
        request_type="restrict",
        key="restrict-1",
    )

    restriction = activate_data_processing_restriction(
        db_session,
        actor=admin,
        request_id=request.id,
    )
    same = activate_data_processing_restriction(
        db_session,
        actor=admin,
        request_id=request.id,
    )
    db_session.refresh(conversation)
    db_session.refresh(turn)
    db_session.refresh(ticket)

    assert same.id == restriction.id
    assert request.status == "completed"
    assert request.result_manifest_json["human_only_conversation_count"] == 1
    assert conversation.ai_suspended is True
    assert conversation.handoff_status == "requested"
    assert conversation.current_handoff_request_id is not None
    assert turn.status == "cancelled"
    assert turn.is_public_reply_allowed is False
    assert ticket.conversation_state == ConversationState.human_review_required
    assert db_session.query(WebchatHandoffRequest).count() == 1
    assert db_session.query(OperatorTask).filter_by(task_type="handoff").count() == 1
    assert db_session.query(DataProcessingRestriction).count() == 1

    with pytest.raises(DataProcessingRestricted):
        ensure_data_processing_allowed(
            db_session,
            customer_id=customer.id,
            purpose="automated_ai",
        )
    ensure_data_processing_allowed(
        db_session,
        customer_id=customer.id,
        purpose="human_support",
    )

    released = release_data_processing_restriction(
        db_session,
        actor=admin,
        restriction_id=restriction.id,
    )
    db_session.refresh(conversation)
    assert released.status == "released"
    assert conversation.ai_suspended is True
    assert conversation.handoff_status == "requested"
    ensure_data_processing_allowed(
        db_session,
        customer_id=customer.id,
        purpose="automated_ai",
    )

    with pytest.raises(
        DataLifecycleError,
        match="processing_restriction_request_already_released",
    ):
        activate_data_processing_restriction(
            db_session,
            actor=admin,
            request_id=request.id,
        )
