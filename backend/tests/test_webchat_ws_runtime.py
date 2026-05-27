from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_ws_runtime_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")
os.environ.setdefault("WEBCHAT_ALLOW_NO_ORIGIN", "true")
os.environ.setdefault("WEBCHAT_WS_ENABLED", "true")
os.environ.setdefault("WEBCHAT_WS_ADMIN_ENABLED", "true")
os.environ.setdefault("WEBCHAT_WS_PUBLIC_ENABLED", "true")
os.environ.setdefault("WEBCHAT_WS_REPLAY_POLL_MS", "100")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, JobStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, Customer, Team, Ticket, User  # noqa: E402
from app.services.webchat_ai_turn_service import safe_write_webchat_event  # noqa: E402
from app.services.webchat_realtime_event_service import _hash_token  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def api_context(tmp_path):
    get_settings.cache_clear()
    db_file = tmp_path / "webchat_ws_runtime.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield session, client
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()


def make_team(db, name: str) -> Team:
    row = Team(name=name)
    db.add(row)
    db.flush()
    return row


def make_user(db, username: str, role: UserRole = UserRole.agent, team_id: int | None = None) -> User:
    row = User(
        username=username,
        display_name=username.title(),
        email=f"{username}@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_webchat(
    db,
    ticket_no: str,
    *,
    token: str = "visitor-token",
    assignee_id: int | None = None,
    team_id: int | None = None,
) -> tuple[Ticket, WebchatConversation, WebchatMessage]:
    customer = Customer(name=f"Visitor {ticket_no}", external_ref=ticket_no)
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=ticket_no,
        title=f"WebChat {ticket_no}",
        description="websocket test",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=f"wc-{ticket_no}",
        assignee_id=assignee_id,
        team_id=team_id,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wc_{ticket_no.lower()}",
        visitor_token_hash=_hash_token(token),
        tenant_key="pytest",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body="Hello from support",
        body_text="Hello from support",
        author_label="Support",
    )
    db.add(message)
    db.flush()
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload={"message_id": message.id, "direction": "agent", "author_user_id": 999},
    )
    db.flush()
    return ticket, conversation, message


def attach_open_ai_turn(db, conversation: WebchatConversation, ticket: Ticket, message: WebchatMessage) -> WebchatAITurn:
    job = BackgroundJob(
        queue_name="webchat_ai_reply",
        job_type="webchat.ai_reply",
        payload_json="{}",
        dedupe_key=f"webchat-ws-ai-turn:{message.id}",
        status=JobStatus.pending,
    )
    db.add(job)
    db.flush()
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=message.id,
        latest_visitor_message_id=message.id,
        job_id=job.id,
        status="queued",
        is_public_reply_allowed=True,
    )
    db.add(turn)
    db.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = "queued"
    conversation.active_ai_for_message_id = message.id
    db.flush()
    return turn


def drain_until(ws, event_type: str, *, max_messages: int = 12):
    seen = []
    for _ in range(max_messages):
        item = ws.receive_json()
        seen.append(item)
        if item.get("type") == event_type:
            return item, seen
    raise AssertionError(f"did not receive {event_type}; seen={seen!r}")


def test_public_visitor_websocket_replays_customer_safe_message(api_context):
    db, client = api_context
    _ticket, conversation, message = make_webchat(db, "WS-PUBLIC", token="public-token")
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({
            "type": "connection.hello",
            "client_type": "visitor",
            "conversation_id": conversation.public_id,
            "visitor_token": "public-token",
            "last_event_id": 0,
        })
        assert ws.receive_json()["type"] == "connection.ready"
        assert ws.receive_json()["type"] == "subscription.ready"
        event = ws.receive_json()

    assert event["type"] == "message.created"
    assert event["message"]["id"] == message.id
    assert event["message"]["body_text"] == "Hello from support"
    assert "author_user_id" not in event["message"]
    assert "author_user_id" not in event["payload"]


def test_agent_websocket_subscription_rejects_invisible_ticket(api_context):
    db, client = api_context
    team_a = make_team(db, "team-a")
    team_b = make_team(db, "team-b")
    agent = make_user(db, "agent", UserRole.agent, team_id=team_a.id)
    make_webchat(db, "WS-VISIBLE", assignee_id=agent.id, team_id=team_a.id)
    hidden_ticket, _hidden_conversation, _hidden_message = make_webchat(db, "WS-HIDDEN", team_id=team_b.id)
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({"type": "connection.hello", "client_type": "agent", "access_token": create_access_token(agent.id)})
        assert ws.receive_json()["type"] == "connection.ready"
        ws.send_json({"type": "subscribe.conversation", "ticket_id": hidden_ticket.id})
        event = ws.receive_json()

    assert event["type"] == "error"
    assert event["code"] == "request_failed"
    assert "hidden" not in str(event).lower()


def test_agent_queue_snapshot_exposes_real_force_takeover_capability(api_context):
    db, client = api_context
    team = make_team(db, "team")
    agent = make_user(db, "plain-agent", UserRole.agent, team_id=team.id)
    ticket, conversation, message = make_webchat(db, "WS-QUEUE", assignee_id=agent.id, team_id=team.id)
    attach_open_ai_turn(db, conversation, ticket, message)
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({"type": "connection.hello", "client_type": "agent", "access_token": create_access_token(agent.id)})
        assert ws.receive_json()["type"] == "connection.ready"
        ws.send_json({"type": "subscribe.handoff_queue", "view": "ai_active"})
        snapshot = ws.receive_json()

    assert snapshot["type"] == "queue.snapshot"
    assert snapshot["data"]["permissions"]["can_force_takeover"] is False
    assert snapshot["data"]["items"][0]["can_force_takeover"] is False


def test_supervisor_force_takeover_command_replays_handoff_and_ai_cancelled(api_context):
    db, client = api_context
    manager = make_user(db, "manager", UserRole.manager)
    ticket, conversation, message = make_webchat(db, "WS-FORCE")
    turn = attach_open_ai_turn(db, conversation, ticket, message)
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({"type": "connection.hello", "client_type": "agent", "access_token": create_access_token(manager.id)})
        assert ws.receive_json()["type"] == "connection.ready"
        ws.send_json({"type": "subscribe.conversation", "ticket_id": ticket.id, "last_event_id": 0})
        drain_until(ws, "message.created")
        ws.send_json({
            "type": "handoff.force_takeover",
            "request_id": "force-1",
            "ticket_id": ticket.id,
            "reason_code": "operator_forced_takeover",
        })
        command, seen = drain_until(ws, "command.ok")
        cancelled, more = drain_until(ws, "ai_turn.cancelled_by_handoff")
        force, final = drain_until(ws, "handoff.force_takeover")

    assert command["result"]["status"] == "accepted"
    assert cancelled["payload"]["ai_turn_id"] == turn.id
    assert force["handoff"]["can_reply"] is True
    assert {item["type"] for item in [*seen, *more, *final]} >= {"handoff.accepted", "handoff.force_takeover"}


def test_agent_reply_command_uses_existing_handoff_ownership_gate(api_context):
    db, client = api_context
    manager = make_user(db, "reply-manager", UserRole.manager)
    ticket, conversation, message = make_webchat(db, "WS-REPLY-GATE")
    attach_open_ai_turn(db, conversation, ticket, message)
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({"type": "connection.hello", "client_type": "agent", "access_token": create_access_token(manager.id)})
        assert ws.receive_json()["type"] == "connection.ready"
        ws.send_json({"type": "agent.reply", "request_id": "reply-1", "ticket_id": ticket.id, "body": "Reply before takeover"})
        event = ws.receive_json()

    assert event["type"] == "error"
    assert event["request_id"] == "reply-1"
    assert "force takeover" in event["message"]
