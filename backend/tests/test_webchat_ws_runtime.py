from __future__ import annotations

import asyncio
import os
import sys
from contextlib import contextmanager
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

from app.api import webchat_fast  # noqa: E402
from app import models, operator_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, JobStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, Customer, Team, Ticket, User  # noqa: E402
from app.api.webchat_ws import (  # noqa: E402
    ConnectionState,
    ConversationSubscription,
    QueueSubscription,
    _send_conversation_replay,
    _send_queue_updates,
)
from app.services import webchat_fast_rate_limit as fast_rate_limit  # noqa: E402
from app.services.webchat_ai_turn_service import safe_write_webchat_event  # noqa: E402
from app.services.webchat_realtime_event_service import _hash_token  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


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


@contextmanager
def fast_api_bound_to_session(db):
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


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


class CollectingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


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


def test_visitor_replay_advances_past_invisible_page_to_message_created(api_context):
    db, client = api_context
    ticket, conversation, _initial_message = make_webchat(db, "WS-PUBLIC-CURSOR", token="cursor-token")
    baseline_event_id = db.query(WebchatEvent.id).order_by(WebchatEvent.id.desc()).limit(1).scalar() or 0
    for index in range(55):
        safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="handoff.requested",
            payload={"handoff_request_id": 1000 + index},
        )
    visible_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="agent",
        body="Visible after invisible replay page",
        body_text="Visible after invisible replay page",
        author_label="Support",
    )
    db.add(visible_message)
    db.flush()
    visible_event = safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload={"message_id": visible_message.id, "direction": "agent"},
    )
    assert visible_event is not None
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({
            "type": "connection.hello",
            "client_type": "visitor",
            "conversation_id": conversation.public_id,
            "visitor_token": "cursor-token",
            "last_event_id": baseline_event_id,
        })
        assert ws.receive_json()["type"] == "connection.ready"
        assert ws.receive_json()["type"] == "subscription.ready"
        event, _seen = drain_until(ws, "message.created", max_messages=4)

    assert event["event_id"] == visible_event.id
    assert event["message"]["body_text"] == "Visible after invisible replay page"


def test_conversation_replay_cursor_advances_when_visible_events_empty(api_context):
    db, _client = api_context
    ticket, conversation, _initial_message = make_webchat(db, "WS-CONV-EMPTY-CURSOR", token="empty-cursor-token")
    baseline_event_id = db.query(WebchatEvent.id).order_by(WebchatEvent.id.desc()).limit(1).scalar() or 0
    last_invisible = None
    for index in range(3):
        last_invisible = safe_write_webchat_event(
            db,
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            event_type="handoff.requested",
            payload={"handoff_request_id": 2000 + index},
        )
    assert last_invisible is not None
    db.commit()

    websocket = CollectingWebSocket()
    state = ConnectionState(client_type="visitor")
    sub = ConversationSubscription(
        conversation_id=conversation.id,
        public_id=conversation.public_id,
        ticket_id=ticket.id,
        audience="visitor",
        last_event_id=baseline_event_id,
    )

    delivered = asyncio.run(_send_conversation_replay(websocket, db, state, sub))

    assert delivered is False
    assert websocket.sent == []
    assert sub.last_event_id == last_invisible.id


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


def test_admin_queue_replay_advances_past_invisible_page_to_visible_update(api_context):
    db, client = api_context
    visible_team = make_team(db, "queue-visible-team")
    hidden_team = make_team(db, "queue-hidden-team")
    agent = make_user(db, "queue-scoped-agent", UserRole.agent, team_id=visible_team.id)
    hidden_ticket, hidden_conversation, hidden_message = make_webchat(db, "WS-QUEUE-HIDDEN", team_id=hidden_team.id)
    for _index in range(105):
        safe_write_webchat_event(
            db,
            conversation_id=hidden_conversation.id,
            ticket_id=hidden_ticket.id,
            event_type="message.created",
            payload={"message_id": hidden_message.id, "direction": "agent"},
        )
    make_webchat(db, "WS-QUEUE-VISIBLE", assignee_id=agent.id, team_id=visible_team.id)
    visible_event_id = db.query(WebchatEvent.id).order_by(WebchatEvent.id.desc()).limit(1).scalar() or 0
    db.commit()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({"type": "connection.hello", "client_type": "agent", "access_token": create_access_token(agent.id)})
        assert ws.receive_json()["type"] == "connection.ready"
        ws.send_json({"type": "subscribe.handoff_queue", "view": "requested", "last_event_id": 0})
        assert ws.receive_json()["type"] == "queue.snapshot"
        update, _seen = drain_until(ws, "queue.updated", max_messages=4)

    assert update["event_id"] >= visible_event_id
    assert update["view"] == "requested"


def test_queue_replay_cursor_advances_when_visible_events_empty(api_context):
    db, _client = api_context
    visible_team = make_team(db, "queue-empty-visible-team")
    hidden_team = make_team(db, "queue-empty-hidden-team")
    agent = make_user(db, "queue-empty-agent", UserRole.agent, team_id=visible_team.id)
    make_webchat(db, "WS-QUEUE-EMPTY-HIDDEN", team_id=hidden_team.id)
    hidden_event_id = db.query(WebchatEvent.id).order_by(WebchatEvent.id.desc()).limit(1).scalar() or 0
    db.commit()

    websocket = CollectingWebSocket()
    state = ConnectionState(client_type="agent", current_user=agent)
    sub = QueueSubscription(view="requested", last_event_id=0)

    delivered = asyncio.run(_send_queue_updates(websocket, db, state, sub))

    assert delivered is False
    assert websocket.sent == []
    assert sub.last_event_id == hidden_event_id


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


def test_fast_ai_response_credentials_authenticate_public_visitor_websocket(api_context, monkeypatch):
    db, client = api_context
    monkeypatch.setattr(webchat_fast, "db_context", lambda: fast_api_bound_to_session(db))
    monkeypatch.setattr(fast_rate_limit, "db_context", lambda: fast_api_bound_to_session(db))

    response = client.post(
        "/api/webchat/fast-reply",
        json={
            "tenant_key": "pytest",
            "channel_key": "website",
            "session_id": "fast-ws-auth-session",
            "client_message_id": "fast-ws-auth-msg",
            "body": "I want a human support agent for a complaint",
            "recent_context": [],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["handoff_required"] is True
    assert payload["conversation_id"].startswith("wcf_")
    assert payload["visitor_token"].startswith("fast-visitor:")
    assert payload["webchat_session"]["conversation_id"] == payload["conversation_id"]

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({
            "type": "connection.hello",
            "client_type": "visitor",
            "conversation_id": payload["conversation_id"],
            "visitor_token": payload["visitor_token"],
            "last_event_id": payload["last_event_id"],
        })
        assert ws.receive_json()["type"] == "connection.ready"
        subscription = ws.receive_json()

    assert subscription["type"] == "subscription.ready"
    assert subscription["conversation_id"] == payload["conversation_id"]


def test_fast_ai_handoff_agent_reply_reaches_public_visitor_websocket(api_context, monkeypatch):
    db, client = api_context
    monkeypatch.setattr(webchat_fast, "db_context", lambda: fast_api_bound_to_session(db))
    monkeypatch.setattr(fast_rate_limit, "db_context", lambda: fast_api_bound_to_session(db))
    manager = make_user(db, "fast-ws-manager", UserRole.manager)
    db.commit()

    fast_response = client.post(
        "/api/webchat/fast-reply",
        json={
            "tenant_key": "pytest",
            "channel_key": "website",
            "session_id": "fast-ws-e2e-session",
            "client_message_id": "fast-ws-e2e-msg",
            "body": "I want a human support agent for a complaint",
            "recent_context": [],
        },
    )
    assert fast_response.status_code == 200
    fast_payload = fast_response.json()
    assert fast_payload["handoff_required"] is True
    assert fast_payload["handoff_request_id"]
    auth = {"Authorization": f"Bearer {create_access_token(manager.id)}"}

    accept_response = client.post(
        f"/api/webchat/admin/handoff/{fast_payload['handoff_request_id']}/accept",
        headers=auth,
        json={"note": "taking over"},
    )
    assert accept_response.status_code == 200
    accepted_event_id = db.query(WebchatEvent.id).order_by(WebchatEvent.id.desc()).limit(1).scalar() or fast_payload["last_event_id"]

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({
            "type": "connection.hello",
            "client_type": "visitor",
            "conversation_id": fast_payload["conversation_id"],
            "visitor_token": fast_payload["visitor_token"],
            "last_event_id": accepted_event_id,
        })
        assert ws.receive_json()["type"] == "connection.ready"
        assert ws.receive_json()["type"] == "subscription.ready"
        reply_response = client.post(
            f"/api/webchat/admin/tickets/{fast_payload['ticket_id']}/reply",
            headers=auth,
            json={
                "body": "Thanks, a support specialist has taken over and will help from here.",
                "has_fact_evidence": True,
                "confirm_review": True,
            },
        )
        assert reply_response.status_code == 200
        event = ws.receive_json()

    assert event["conversation_id"] == fast_payload["conversation_id"]
    assert event["type"] == "message.created"
    assert event["message"]["direction"] == "agent"
    assert event["message"]["body_text"] == "Thanks, a support specialist has taken over and will help from here."
    assert "author_user_id" not in event["message"]


def test_public_ws_disabled_keeps_fast_reply_and_polling_fallback_usable(api_context, monkeypatch):
    db, client = api_context
    monkeypatch.setattr(webchat_fast, "db_context", lambda: fast_api_bound_to_session(db))
    monkeypatch.setattr(fast_rate_limit, "db_context", lambda: fast_api_bound_to_session(db))
    monkeypatch.setattr(get_settings(), "webchat_ws_public_enabled", False)

    fast_response = client.post(
        "/api/webchat/fast-reply",
        json={
            "tenant_key": "pytest",
            "channel_key": "website",
            "session_id": "fast-ws-disabled-session",
            "client_message_id": "fast-ws-disabled-msg",
            "body": "I want a human support agent for a complaint",
            "recent_context": [],
        },
    )
    assert fast_response.status_code == 200
    payload = fast_response.json()

    with client.websocket_connect("/api/webchat/ws") as ws:
        ws.send_json({
            "type": "connection.hello",
            "client_type": "visitor",
            "conversation_id": payload["conversation_id"],
            "visitor_token": payload["visitor_token"],
        })
        event = ws.receive_json()

    assert event["type"] == "error"
    assert event["code"] == "webchat_ws_public_disabled"

    poll_response = client.get(
        f"/api/webchat/conversations/{payload['conversation_id']}/messages?limit=10",
        headers={
            "X-Webchat-Visitor-Token": payload["visitor_token"],
            "X-Webchat-WS-Fallback": "true",
        },
    )
    assert poll_response.status_code == 200
    assert poll_response.json()["conversation_id"] == payload["conversation_id"]
