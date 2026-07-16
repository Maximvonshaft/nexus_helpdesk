from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_thread_pagination_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Ticket, User  # noqa: E402
from app.services.webchat_service import admin_get_thread  # noqa: E402
from app.webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def thread_context(tmp_path):
    db_file = tmp_path / "webchat_thread_pagination.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()

    admin = User(
        username="thread-pagination-admin",
        display_name="Thread Pagination Admin",
        email="thread-pagination-admin@example.test",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    customer = Customer(name="Thread Customer", email="thread@example.test")
    session.add_all([admin, customer])
    session.flush()
    ticket = Ticket(
        ticket_no="THREAD-PAGE-001",
        title="Long canonical thread",
        description="Pagination fixture",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.human_owned,
    )
    session.add(ticket)
    session.flush()
    conversation = WebchatConversation(
        public_id="wc_thread_page_001",
        visitor_token_hash=hashlib.sha256(b"thread-token").hexdigest(),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_email=customer.email,
        status="open",
    )
    session.add(conversation)
    session.flush()

    base_time = datetime(2026, 7, 16, 10, 0, 0)
    messages: list[WebchatMessage] = []
    for index in range(250):
        row = WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction="visitor" if index % 2 == 0 else "agent",
            body=f"message-{index + 1:03d}",
            body_text=f"message-{index + 1:03d}",
            message_type="text",
            delivery_status="sent",
            author_label="Thread Customer" if index % 2 == 0 else "Thread Agent",
            created_at=base_time + timedelta(seconds=index),
        )
        session.add(row)
        messages.append(row)
    session.flush()

    for index in range(75):
        action_id = f"action-{index + 1:03d}"
        session.add(
            WebchatCardAction(
                conversation_id=conversation.id,
                ticket_id=ticket.id,
                message_id=messages[index].id,
                action_id=action_id,
                action_type="test_action",
                action_payload_json=json.dumps({"action_id": action_id}),
                submitted_by="visitor",
                status="submitted",
                created_at=base_time + timedelta(seconds=index),
            )
        )
    session.commit()

    state = {"user": admin}

    def override_db():
        yield session

    def override_current_user():
        return state["user"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    client = TestClient(app)
    try:
        yield session, client, admin, ticket, conversation
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_admin_thread_latest_page_is_bounded_and_actions_are_bounded(thread_context):
    db, _, admin, ticket, _ = thread_context

    payload = admin_get_thread(db, ticket.id, admin, message_limit=100)

    assert len(payload["messages"]) == 100
    assert payload["messages"][0]["body"] == "message-151"
    assert payload["messages"][-1]["body"] == "message-250"
    assert payload["message_page"] == {
        "before_id": payload["messages"][0]["id"],
        "has_more": True,
        "limit": 100,
    }
    assert len(payload["actions"]) == 50
    assert payload["actions"][0]["payload"]["action_id"] == "action-026"
    assert payload["actions"][-1]["payload"]["action_id"] == "action-075"


def test_message_cursor_remains_stable_when_newer_message_is_inserted(thread_context):
    db, _, admin, ticket, conversation = thread_context

    latest = admin_get_thread(db, ticket.id, admin, message_limit=100)
    before_id = latest["message_page"]["before_id"]

    db.add(
        WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction="visitor",
            body="message-251-newer",
            body_text="message-251-newer",
            message_type="text",
            delivery_status="sent",
            author_label="Thread Customer",
        )
    )
    db.commit()

    older = admin_get_thread(
        db,
        ticket.id,
        admin,
        before_message_id=before_id,
        message_limit=100,
    )

    assert len(older["messages"]) == 100
    assert older["messages"][0]["body"] == "message-051"
    assert older["messages"][-1]["body"] == "message-150"
    assert all(message["body"] != "message-251-newer" for message in older["messages"])
    assert older["message_page"]["has_more"] is True


def test_existing_thread_route_pages_history_without_parallel_endpoint(thread_context):
    _, client, _, ticket, _ = thread_context

    latest_response = client.get(
        f"/api/webchat/admin/tickets/{ticket.id}/thread",
        params={"message_limit": 60},
    )
    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert len(latest["messages"]) == 60
    assert latest["message_page"]["has_more"] is True
    assert "support_memory" in latest

    historical_response = client.get(
        f"/api/webchat/admin/tickets/{ticket.id}/thread",
        params={
            "before_message_id": latest["message_page"]["before_id"],
            "message_limit": 60,
        },
    )
    assert historical_response.status_code == 200
    historical = historical_response.json()
    assert len(historical["messages"]) == 60
    assert "support_memory" not in historical
    assert historical["messages"][-1]["id"] < latest["messages"][0]["id"]


def test_retired_n_plus_one_list_implementation_is_removed() -> None:
    source = (ROOT / "app" / "services" / "webchat_service.py").read_text(encoding="utf-8")
    api_source = (ROOT / "app" / "api" / "webchat.py").read_text(encoding="utf-8")

    assert "def admin_list_conversations(" not in source
    assert "admin_list_conversations_optimized" in api_source
    assert "@router.get(\"/admin/tickets/{ticket_id}/thread\")" in api_source
    assert "thread-v2" not in api_source
