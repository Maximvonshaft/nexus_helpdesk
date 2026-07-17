from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/support_conversations_api_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.deps import get_current_user  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def api_context(tmp_path):
    db_file = tmp_path / "support_conversations.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    state = {"user": None, "Session": Session}

    def override_db():
        yield session

    def override_current_user():
        return state["user"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    client = TestClient(app)
    try:
        yield session, client, state
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db, username: str = "support-admin") -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@invalid.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_support_conversation(db, *, channel: SourceChannel, public_id: str, body: str, offset: int = 0) -> tuple[Ticket, WebchatConversation]:
    customer = Customer(
        name=f"{channel.value} customer",
        email=f"private-{offset}@invalid.test",
        phone=f"+4100000{offset}",
    )
    db.add(customer)
    db.flush()
    now = utc_now() - timedelta(minutes=offset)
    ticket = Ticket(
        ticket_no=f"SUP-{channel.value.upper()}-{offset}",
        title=f"{channel.value} conversation",
        description="support conversation fixture",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=channel,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.ai_active,
        customer_request=body,
        last_customer_message=body,
        tracking_number=f"CH02000012913{offset}",
        preferred_reply_channel=channel.value,
        preferred_reply_contact=customer.phone,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=hashlib.sha256(public_id.encode()).hexdigest(),
        tenant_key="default",
        channel_key="whatsapp" if channel == SourceChannel.whatsapp else "default",
        ticket_id=ticket.id,
        visitor_name=customer.name,
        visitor_email=customer.email,
        visitor_phone=customer.phone,
        origin="whatsapp-native" if channel == SourceChannel.whatsapp else "webchat-demo",
        status="open",
        active_ai_status="queued" if channel == SourceChannel.whatsapp else None,
        updated_at=now,
        last_seen_at=now,
    )
    db.add(conversation)
    db.flush()
    db.add(
        WebchatMessage(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            direction="visitor",
            body=body,
            body_text=body,
            message_type="text",
            delivery_status="sent",
            author_label=customer.name,
            created_at=now,
        )
    )
    db.flush()
    return ticket, conversation


def test_support_conversations_unifies_webchat_and_whatsapp(api_context):
    db, client, state = api_context
    state["user"] = make_user(db)
    _, webchat = make_support_conversation(db, channel=SourceChannel.web_chat, public_id="wc_support_1", body="hello from webchat", offset=1)
    whatsapp_ticket, whatsapp = make_support_conversation(db, channel=SourceChannel.whatsapp, public_id="wa_support_1", body="hello from whatsapp", offset=2)
    db.commit()

    listing = client.get("/api/support/conversations", params={"view": "all", "channel": "all"})
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["source"] == "nexus_support_conversations"
    by_key = {item["session_key"]: item for item in payload["items"]}
    webchat_item = by_key[f"webchat:{webchat.public_id}"]
    assert webchat_item["latest_message"] == "hello from webchat"
    assert "tracking_number" not in webchat_item
    assert webchat_item["tracking_reference"] == "parcel ending 129131"
    assert webchat_item["pii_minimized"] is True
    assert webchat_item["customer_contact"] == "phone ending 01"
    assert webchat_item["display_name"].endswith("•••")
    assert "private-1@invalid.test" not in listing.text
    assert "+41000001" not in listing.text
    assert "CH020000129131" not in listing.text
    assert by_key[f"whatsapp:{whatsapp.public_id}"]["channel"] == "whatsapp"
    assert by_key[f"whatsapp:{whatsapp.public_id}"]["ai_status"] == "queued"
    assert by_key[f"whatsapp:{whatsapp.public_id}"]["ai_pending"] is False
    assert by_key[f"whatsapp:{whatsapp.public_id}"]["can_reply"] is False
    assert by_key[f"whatsapp:{whatsapp.public_id}"]["can_force_takeover"] is True

    resolution = client.get("/api/support/conversations/resolve", params={"session_key": f"whatsapp:{whatsapp.public_id}"})
    assert resolution.status_code == 200
    resolved = resolution.json()
    assert resolved["source"] == "nexus_support_conversation_resolver"
    assert resolved["conversation"]["channel"] == "whatsapp"
    assert resolved["conversation"]["ticket_id"] == whatsapp_ticket.id
    assert resolved["conversation"]["pii_minimized"] is True
    assert "messages" not in resolved
    assert "support_memory" not in resolved
    assert "tracking_number" not in resolution.text
    assert "customer_contact" not in resolved["conversation"]

    metrics = client.get("/api/support/conversations/metrics", params={"since_hours": 24})
    assert metrics.status_code == 200
    assert metrics.json()["by_channel"]["whatsapp"] == 1
    assert metrics.json()["ai_active"] == 0
    assert metrics.json()["runtime_latency"]["sample_count"] == 0


def test_list_search_does_not_use_raw_customer_or_tracking_fields(api_context):
    db, client, state = api_context
    state["user"] = make_user(db, "search-admin")
    ticket, _ = make_support_conversation(
        db,
        channel=SourceChannel.web_chat,
        public_id="wc_search_private",
        body="private search fixture",
        offset=7,
    )
    db.commit()

    for raw_value in (
        "+41000007",
        "private-7@invalid.test",
        "web_chat customer",
        "CH020000129137",
    ):
        response = client.get(
            "/api/support/conversations",
            params={"view": "all", "channel": "all", "q": raw_value},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    by_ticket = client.get(
        "/api/support/conversations",
        params={"view": "all", "channel": "all", "q": ticket.ticket_no},
    )
    assert by_ticket.status_code == 200
    assert [item["ticket_id"] for item in by_ticket.json()["items"]] == [ticket.id]


def test_support_conversation_ai_active_requires_live_turn(api_context):
    db, client, state = api_context
    state["user"] = make_user(db)
    ticket, conversation = make_support_conversation(db, channel=SourceChannel.web_chat, public_id="wc_ai_active", body="hello", offset=1)
    message = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id).one()
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=message.id,
        latest_visitor_message_id=message.id,
        status="queued",
    )
    db.add(turn)
    db.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = "queued"
    db.commit()

    listing = client.get("/api/support/conversations", params={"view": "ai_active", "channel": "all"})
    assert listing.status_code == 200
    assert [item["session_key"] for item in listing.json()["items"]] == [f"webchat:{conversation.public_id}"]
    assert listing.json()["items"][0]["ai_pending"] is True
    assert listing.json()["items"][0]["can_reply"] is False
    assert listing.json()["items"][0]["can_force_takeover"] is True

    resolution = client.get("/api/support/conversations/resolve", params={"session_key": f"webchat:{conversation.public_id}"})
    assert resolution.status_code == 200
    assert resolution.json()["conversation"]["ticket_id"] == ticket.id
    assert resolution.json()["conversation"]["pii_minimized"] is True

    metrics = client.get("/api/support/conversations/metrics", params={"since_hours": 24})
    assert metrics.status_code == 200
    assert metrics.json()["ai_active"] == 1
    assert metrics.json()["runtime_latency"]["sample_count"] == 1


def test_support_conversation_metrics_include_runtime_latency(api_context):
    db, client, state = api_context
    state["user"] = make_user(db)
    ticket, conversation = make_support_conversation(db, channel=SourceChannel.web_chat, public_id="wc_latency", body="hello", offset=1)
    message = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id).one()
    now = utc_now()
    db.add(
        WebchatAITurn(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            trigger_message_id=message.id,
            latest_visitor_message_id=message.id,
            status="completed",
            reply_source="private_ai_runtime",
            bridge_elapsed_ms=900,
            runtime_trace_json=json.dumps(
                {
                    "latency_class": "short_general_support",
                    "runtime_usage": {
                        "total_duration_ms": 850,
                        "load_duration_ms": 120,
                        "prompt_eval_duration_ms": 80,
                        "eval_duration_ms": 600,
                    },
                }
            ),
            created_at=now - timedelta(seconds=2),
            started_at=now - timedelta(seconds=1),
            completed_at=now,
        )
    )
    db.commit()

    response = client.get("/api/support/conversations/metrics", params={"since_hours": 24})
    assert response.status_code == 200
    latency = response.json()["runtime_latency"]
    assert latency["sample_count"] == 1
    assert latency["bridge"]["p50_ms"] == 900
    assert latency["runtime_total"]["p50_ms"] == 850
    assert latency["runtime_eval"]["p90_ms"] == 600
    assert latency["by_latency_class"]["short_general_support"] == 1


def test_support_conversation_reply_uses_session_channel(api_context):
    db, client, state = api_context
    state["user"] = make_user(db)
    _, webchat = make_support_conversation(db, channel=SourceChannel.web_chat, public_id="wc_reply_1", body="webchat inbound", offset=1)
    whatsapp_ticket, whatsapp = make_support_conversation(db, channel=SourceChannel.whatsapp, public_id="wa_reply_1", body="whatsapp inbound", offset=2)
    whatsapp.active_ai_status = None
    db.commit()

    response = client.post(
        "/api/support/conversations/reply",
        json={
            "session_key": f"whatsapp:{whatsapp.public_id}",
            "body": "Hello from the support console.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["channel"] == "whatsapp"
    assert payload["session_key"] == f"whatsapp:{whatsapp.public_id}"
    assert payload["message_id"] == payload["message"]["id"]
    assert isinstance(payload["outbound_message_id"], int)

    verify_db = state["Session"]()
    try:
        outbound = verify_db.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == whatsapp_ticket.id).one()
        assert outbound.channel == SourceChannel.whatsapp
        assert outbound.body == "Hello from the support console."
        assert payload["outbound_message_id"] == outbound.id
    finally:
        verify_db.close()

    webchat_messages = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == webchat.id, WebchatMessage.direction == "agent").all()
    assert webchat_messages == []
