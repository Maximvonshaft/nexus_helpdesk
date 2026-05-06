from __future__ import annotations

import json
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.api import operator_queue as operator_queue_api
from app.db import Base, SessionLocal, engine
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.main import app
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, Ticket, User, UserCapabilityOverride
from app.operator_models import OperatorTask
from app.services.permissions import CAP_RUNTIME_MANAGE
from app.webchat_models import WebchatConversation, WebchatEvent

ADMIN_ID = 50050
AGENT_ID = 50051
RUNTIME_MANAGER_ID = 50052
PREFIX = "oq-api-"


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _reset_data() -> None:
    _ensure_schema()
    db = SessionLocal()
    try:
        db.query(AdminAuditLog).filter(AdminAuditLog.target_type == "operator_task").delete(synchronize_session=False)
        db.query(OperatorTask).filter(
            (OperatorTask.source_id.like(f"{PREFIX}%"))
            | (OperatorTask.reason_code.like(f"{PREFIX}%"))
            | (OperatorTask.payload_json.like(f"%{PREFIX}%"))
        ).delete(synchronize_session=False)
        db.query(WebchatEvent).filter(WebchatEvent.payload_json.like(f"%{PREFIX}%")).delete(synchronize_session=False)
        db.query(WebchatConversation).filter(WebchatConversation.public_id.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.session_key.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(Ticket).filter(Ticket.ticket_no.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id.in_([ADMIN_ID, AGENT_ID, RUNTIME_MANAGER_ID])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ADMIN_ID, AGENT_ID, RUNTIME_MANAGER_ID])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _user(user_id: int, role: UserRole, username: str) -> User:
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user_id).first()
        if row is None:
            row = User(id=user_id, username=username, display_name=username, password_hash="test", role=role, is_active=True)
            db.add(row)
        else:
            row.role = role
            row.is_active = True
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def _admin() -> User:
    return _user(ADMIN_ID, UserRole.admin, "oq_api_admin")


def _agent() -> User:
    return _user(AGENT_ID, UserRole.agent, "oq_api_agent")


def _runtime_manager() -> User:
    manager = _user(RUNTIME_MANAGER_ID, UserRole.manager, "oq_api_runtime_manager")
    db = SessionLocal()
    try:
        db.add(UserCapabilityOverride(user_id=RUNTIME_MANAGER_ID, capability=CAP_RUNTIME_MANAGE, allowed=True))
        db.commit()
    finally:
        db.close()
    return manager


@contextmanager
def _client_as(user: User | None):
    app.dependency_overrides.clear()
    if user is not None:
        app.dependency_overrides[operator_queue_api.get_current_user] = lambda: user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _counts() -> tuple[int, int]:
    db = SessionLocal()
    try:
        return db.query(OperatorTask).count(), db.query(WebchatEvent).count()
    finally:
        db.close()


def _openclaw_event(suffix: str = "one") -> int:
    db = SessionLocal()
    try:
        row = OpenClawUnresolvedEvent(
            source="pytest",
            session_key=f"{PREFIX}{suffix}",
            event_type="message",
            recipient="customer@example.test",
            source_chat_id="customer@example.test",
            preferred_reply_contact="customer@example.test",
            payload_json=json.dumps({"type": "message", "sessionKey": f"{PREFIX}{suffix}", "body": "Need support"}),
            status="pending",
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _webchat_ticket_and_conversation(*, suffix: str, required_action: str | None = None, state: ConversationState = ConversationState.ai_active) -> tuple[int, int]:
    db = SessionLocal()
    try:
        ticket = Ticket(
            ticket_no=f"{PREFIX}{suffix}",
            title="Operator queue handoff",
            description="pytest handoff",
            source=TicketSource.user_message,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.medium,
            status=TicketStatus.new,
            conversation_state=state,
            required_action=required_action,
        )
        db.add(ticket)
        db.flush()
        conversation = WebchatConversation(
            public_id=f"{PREFIX}{suffix}",
            visitor_token_hash="hash",
            tenant_key="pytest",
            channel_key="website",
            ticket_id=ticket.id,
            visitor_name="Queue Visitor",
            visitor_email="visitor@example.test",
            origin="https://example.test/operator-queue",
        )
        db.add(conversation)
        db.commit()
        return ticket.id, conversation.id
    finally:
        db.close()


def _task(*, suffix: str, status: str = "pending", source_type: str = "webchat", task_type: str = "handoff", ticket_id: int | None = None, conversation_id: int | None = None, unresolved_event_id: int | None = None) -> int:
    db = SessionLocal()
    try:
        row = OperatorTask(
            source_type=source_type,
            source_id=f"{PREFIX}{suffix}",
            ticket_id=ticket_id,
            webchat_conversation_id=conversation_id,
            unresolved_event_id=unresolved_event_id,
            task_type=task_type,
            status=status,
            priority=40,
            reason_code=f"{PREFIX}reason",
            payload_json=json.dumps({"note_marker": f"{PREFIX}{suffix}"}),
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def test_operator_queue_authentication_and_runtime_permission():
    _reset_data()

    with _client_as(None) as client:
        unauth = client.get("/api/admin/operator-queue")
    assert unauth.status_code == 401

    with _client_as(_agent()) as client:
        forbidden = client.get("/api/admin/operator-queue")
    assert forbidden.status_code == 403

    with _client_as(_runtime_manager()) as client:
        allowed = client.get("/api/admin/operator-queue")
    assert allowed.status_code == 200, allowed.text


def test_get_operator_queue_is_pure_read_and_does_not_project_sources():
    _reset_data()
    _openclaw_event("get-pure-openclaw")
    _webchat_ticket_and_conversation(suffix="get-pure-webchat", required_action="check_customer_request")
    before = _counts()

    with _client_as(_admin()) as client:
        res = client.get("/api/admin/operator-queue")

    assert res.status_code == 200, res.text
    after = _counts()
    assert after == before
    assert res.json().get("projected_openclaw_unresolved", 0) == 0
    assert res.json().get("projected_webchat_handoff", 0) == 0


def test_post_project_projects_openclaw_and_webchat_once():
    _reset_data()
    _openclaw_event("project-openclaw")
    _webchat_ticket_and_conversation(suffix="project-required-action", required_action="collect_tracking_number")
    _webchat_ticket_and_conversation(suffix="project-human-review", state=ConversationState.human_review_required)

    with _client_as(_admin()) as client:
        first = client.post("/api/admin/operator-queue/project")
        second = client.post("/api/admin/operator-queue/project")

    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["projected_openclaw_unresolved"] == 1
    assert payload["projected_webchat_handoff"] == 2
    assert second.status_code == 200, second.text
    assert second.json()["projected_openclaw_unresolved"] == 0
    assert second.json()["projected_webchat_handoff"] == 0

    db = SessionLocal()
    try:
        assert db.query(OperatorTask).filter(OperatorTask.source_id.like(f"{PREFIX}project-%"), OperatorTask.status.notin_(["resolved", "dropped", "replayed"])).count() == 3
    finally:
        db.close()


def test_transition_endpoints_update_state_note_and_404_contract():
    _reset_data()
    ticket_id, conversation_id = _webchat_ticket_and_conversation(suffix="transition-webchat", required_action="human_needed")
    task_id = _task(suffix="transition-webchat", ticket_id=ticket_id, conversation_id=conversation_id)

    with _client_as(_admin()) as client:
        assigned = client.post(f"/api/admin/operator-queue/{task_id}/assign", json={"note": "take ownership"})
        missing = client.post("/api/admin/operator-queue/999999999/assign", json={"note": "missing"})

    assert assigned.status_code == 200, assigned.text
    assigned_payload = assigned.json()
    assert assigned_payload["status"] == "assigned"
    assert assigned_payload["assignee_id"] == ADMIN_ID
    assert missing.status_code == 404

    with _client_as(_admin()) as client:
        resolved = client.post(f"/api/admin/operator-queue/{task_id}/resolve", json={"note": "customer confirmed"})

    assert resolved.status_code == 200, resolved.text
    resolved_payload = resolved.json()
    assert resolved_payload["status"] == "resolved"
    assert resolved_payload["resolved_at"] is not None

    db = SessionLocal()
    try:
        events = db.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation_id).order_by(WebchatEvent.id.asc()).all()
        assert events
        assert any("customer confirmed" in (event.payload_json or "") for event in events)
    finally:
        db.close()


def test_drop_and_replay_transition_endpoints_are_terminal_and_source_aware(monkeypatch):
    _reset_data()
    event_id = _openclaw_event("transition-replay")
    replay_task_id = _task(
        suffix="transition-replay",
        source_type="openclaw",
        task_type="bridge_unresolved",
        unresolved_event_id=event_id,
    )
    drop_task_id = _task(suffix="transition-drop")

    def fake_replay(db, *, row):
        row.status = "resolved"
        return True

    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event_payload", fake_replay, raising=False)
    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event", fake_replay, raising=False)

    with _client_as(_admin()) as client:
        dropped = client.post(f"/api/admin/operator-queue/{drop_task_id}/drop", json={"note": "not actionable"})
        replayed = client.post(f"/api/admin/operator-queue/{replay_task_id}/replay", json={"note": "safe replay"})

    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["status"] == "dropped"
    assert dropped.json()["resolved_at"] is not None
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["status"] == "replayed"
    assert replayed.json()["replay_result"] is True

    db = SessionLocal()
    try:
        source = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == event_id).first()
        assert source is not None
        assert source.status in {"resolved", "replayed"}
    finally:
        db.close()


def test_invalid_terminal_transition_returns_409_or_400():
    _reset_data()
    task_id = _task(suffix="invalid-terminal", status="resolved")

    with _client_as(_admin()) as client:
        res = client.post(f"/api/admin/operator-queue/{task_id}/assign", json={"note": "should not reopen"})

    assert res.status_code in {400, 409}
