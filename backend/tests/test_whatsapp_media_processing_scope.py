from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-media-processing-scope.db",
)

from app.api.whatsapp_media_integration import receive_baileys_media
from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Customer, Tenant, Ticket
from app.models_agent_routing import ConversationControl
from app.services import whatsapp_media_processing_scope
from app.services.whatsapp_media_processing_scope import (
    enforce_whatsapp_media_processing_scope,
)
from app.services.whatsapp_media_worker import dispatch_pending_whatsapp_media
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation


register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'whatsapp-media-processing-scope.db'}",
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


def _fixture(db_session):
    tenant = Tenant(
        tenant_key="media-processing-scope",
        display_name="Media Processing Scope",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(
        tenant_id=tenant.id,
        name="Media Scope Customer",
        phone="+15551230001",
        phone_normalized="+15551230001",
    )
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        ticket_no="WA-MEDIA-SCOPE-1",
        title="Media processing scope",
        description="Scope gate regression",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
    )
    conversation = WebchatConversation(
        public_id="wa_media_processing_scope",
        visitor_token_hash="d" * 64,
        tenant_key=tenant.tenant_key,
        channel_key="whatsapp",
        status="open",
        last_seen_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add_all([ticket, conversation])
    db_session.flush()
    control = ConversationControl(
        conversation_id=conversation.id,
        customer_id=customer.id,
        tenant_key=tenant.tenant_key,
        country_code="US",
        channel_key="whatsapp",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(control)
    db_session.flush()
    return tenant, customer, ticket, conversation


def test_processing_scope_resolves_ticket_and_ticketless_customers(
    db_session,
    monkeypatch,
):
    tenant, customer, ticket, conversation = _fixture(db_session)
    observed: list[tuple[int, str]] = []
    monkeypatch.setattr(
        whatsapp_media_processing_scope,
        "ensure_data_processing_allowed",
        lambda _db, *, customer_id, purpose: observed.append(
            (customer_id, purpose)
        ),
    )

    enforce_whatsapp_media_processing_scope(
        db_session,
        SimpleNamespace(
            tenant_id=tenant.id,
            inbound_message=SimpleNamespace(
                ticket_id=ticket.id,
                conversation_id=conversation.id,
            ),
        ),
    )
    enforce_whatsapp_media_processing_scope(
        db_session,
        SimpleNamespace(
            tenant_id=tenant.id,
            inbound_message=SimpleNamespace(
                ticket_id=None,
                conversation_id=conversation.id,
            ),
        ),
    )

    assert observed == [
        (customer.id, "human_support"),
        (customer.id, "human_support"),
    ]


def test_meta_and_baileys_enter_the_same_processing_scope_authority():
    assert "enforce_whatsapp_media_processing_scope(db, asset)" in inspect.getsource(
        receive_baileys_media
    )
    assert "enforce_whatsapp_media_processing_scope(db, asset)" in inspect.getsource(
        dispatch_pending_whatsapp_media
    )
