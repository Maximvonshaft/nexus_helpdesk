from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Ticket, TicketAIIntake, User  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session():
    db_file = ROOT / f".webchat_ai_suggestions_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        db_file.unlink(missing_ok=True)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _admin(db_session) -> User:
    suffix = uuid.uuid4().hex[:8]
    row = User(
        username=f"webchat-ai-suggestions-{suffix}",
        display_name="WebChat AI Suggestions Admin",
        email=f"webchat-ai-suggestions-{suffix}@example.test",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_webchat_thread_returns_structured_ai_suggestions_from_real_ai_intake(client: TestClient, db_session):
    admin = _admin(db_session)
    customer = Customer(name="AI Suggestion Visitor", email="visitor@example.test")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"WCAI-{uuid.uuid4().hex[:8]}",
        title="WebChat delivery address question",
        description="Customer asks if the address can be updated.",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        required_action="Verify the delivery postcode before replying.",
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="wc-ai-suggestions",
    )
    db_session.add(ticket)
    db_session.flush()
    conversation = WebchatConversation(
        public_id=f"wc_ai_suggestions_{ticket.id}",
        visitor_token_hash="hash",
        tenant_key="pytest",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="AI Suggestion Visitor",
        visitor_email="visitor@example.test",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(WebchatMessage(conversation_id=conversation.id, ticket_id=ticket.id, direction="visitor", body="Can I change my address?", body_text="Can I change my address?"))
    db_session.add(
        TicketAIIntake(
            ticket_id=ticket.id,
            summary="Customer wants to change delivery address after dispatch.",
            classification="address_change",
            confidence=0.82,
            missing_fields_json='["postcode", "new delivery address"]',
            recommended_action="Verify customer identity and postcode before offering address update actions.",
            suggested_reply="I can help check that. Please confirm the postcode and the new delivery address.",
            created_by=admin.id,
        )
    )
    db_session.commit()

    response = client.get(f"/api/webchat/admin/tickets/{ticket.id}/thread", headers=_headers(admin))

    assert response.status_code == 200, response.text
    payload = response.json()
    suggestions = {item["key"]: item for item in payload["ai_suggestions"]}
    assert suggestions["ai-summary"]["source_type"] == "ticket_ai_intake"
    assert suggestions["ai-summary"]["confidence"] == 0.82
    assert "address after dispatch" in suggestions["ai-summary"]["body"]
    assert suggestions["recommended-action"]["action"] == "agent_next_step"
    assert "Verify customer identity" in suggestions["recommended-action"]["body"]
    assert suggestions["suggested-reply"]["action"] == "insert_reply"
    assert suggestions["suggested-reply"]["insertable_reply"].startswith("I can help check")
    assert "postcode" in suggestions["missing-fields"]["body"]
    assert "raw_payload" not in str(payload["ai_suggestions"])
