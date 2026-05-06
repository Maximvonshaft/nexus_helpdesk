from __future__ import annotations

import json

from app.db import Base, SessionLocal, engine
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.models import OpenClawUnresolvedEvent, Ticket
from app.operator_models import OperatorTask
from app.services.operator_queue import project_openclaw_unresolved_events, project_webchat_handoff_tasks, transition_operator_task
from app.webchat_models import WebchatConversation, WebchatEvent

PREFIX = "oq-proj-"


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _reset_data() -> None:
    _ensure_schema()
    db = SessionLocal()
    try:
        db.query(OperatorTask).filter(
            (OperatorTask.source_id.like(f"{PREFIX}%"))
            | (OperatorTask.reason_code.like(f"{PREFIX}%"))
            | (OperatorTask.payload_json.like(f"%{PREFIX}%"))
        ).delete(synchronize_session=False)
        db.query(WebchatEvent).filter(WebchatEvent.payload_json.like(f"%{PREFIX}%")).delete(synchronize_session=False)
        db.query(WebchatConversation).filter(WebchatConversation.public_id.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.session_key.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(Ticket).filter(Ticket.ticket_no.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _openclaw_event(suffix: str, *, status: str = "pending") -> int:
    db = SessionLocal()
    try:
        row = OpenClawUnresolvedEvent(
            source="pytest",
            session_key=f"{PREFIX}{suffix}",
            event_type="message",
            recipient="recipient@example.test",
            source_chat_id="recipient@example.test",
            preferred_reply_contact="recipient@example.test",
            payload_json=json.dumps(
                {
                    "type": "message",
                    "sessionKey": f"{PREFIX}{suffix}",
                    "body": "Customer needs help",
                    "secret_token": "must-not-be-copied-to-task-payload",
                }
            ),
            status=status,
            last_error="No unique ticket match for auto-link",
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
            title="Projection handoff",
            description="pytest projection",
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
            visitor_name="Projection Visitor",
            visitor_email="projection@example.test",
            origin="https://example.test/projection",
        )
        db.add(conversation)
        db.commit()
        return ticket.id, conversation.id
    finally:
        db.close()


def test_project_openclaw_unresolved_event_creates_one_bridge_task_with_bounded_payload():
    _reset_data()
    event_id = _openclaw_event("openclaw")

    db = SessionLocal()
    try:
        created = project_openclaw_unresolved_events(db, limit=100)
        db.commit()
        assert created == 1

        task = db.query(OperatorTask).filter(OperatorTask.unresolved_event_id == event_id).first()
        assert task is not None
        assert task.source_type == "openclaw"
        assert task.task_type == "bridge_unresolved"
        assert task.unresolved_event_id == event_id
        assert task.status == "pending"
        payload = json.loads(task.payload_json or "{}")
        assert payload["session_key"] == "oq-proj-openclaw"
        assert payload["last_error"] == "No unique ticket match for auto-link"
        assert "secret_token" not in payload

        again = project_openclaw_unresolved_events(db, limit=100)
        db.commit()
        assert again == 0
        assert db.query(OperatorTask).filter(OperatorTask.unresolved_event_id == event_id).count() == 1
    finally:
        db.close()


def test_project_webchat_required_action_and_human_review_creates_events_once():
    _reset_data()
    _, required_conversation_id = _webchat_ticket_and_conversation(suffix="required-action", required_action="collect_tracking")
    _, review_conversation_id = _webchat_ticket_and_conversation(suffix="human-review", state=ConversationState.human_review_required)

    db = SessionLocal()
    try:
        created = project_webchat_handoff_tasks(db, limit=100)
        db.commit()
        assert created == 2

        tasks = db.query(OperatorTask).filter(OperatorTask.source_id.like(f"{PREFIX}%"), OperatorTask.task_type == "handoff").all()
        assert len(tasks) == 2
        assert {task.webchat_conversation_id for task in tasks} == {required_conversation_id, review_conversation_id}
        assert all(task.source_type == "webchat" for task in tasks)
        assert {task.reason_code for task in tasks} == {"ticket_required_action", "human_review_required"}

        events = db.query(WebchatEvent).filter(WebchatEvent.conversation_id.in_([required_conversation_id, review_conversation_id])).all()
        assert len(events) == 2
        assert {event.event_type for event in events} <= {"handoff.requested", "operator_task.created"}

        again = project_webchat_handoff_tasks(db, limit=100)
        db.commit()
        assert again == 0
        assert db.query(OperatorTask).filter(OperatorTask.source_id.like(f"{PREFIX}%"), OperatorTask.task_type == "handoff").count() == 2
    finally:
        db.close()


def test_terminal_source_is_closed_and_not_reprojected_for_openclaw_resolve_drop_replay():
    _reset_data()
    event_ids = [_openclaw_event(f"terminal-{action}") for action in ("resolve", "drop", "replay")]

    db = SessionLocal()
    try:
        assert project_openclaw_unresolved_events(db, limit=100) == 3
        db.commit()
        tasks = db.query(OperatorTask).filter(OperatorTask.unresolved_event_id.in_(event_ids)).order_by(OperatorTask.id.asc()).all()
        assert len(tasks) == 3
        for task, action in zip(tasks, ["resolve", "drop", "replay"]):
            transition_operator_task(db, task_id=task.id, action=action, actor_id=123, note=f"close via {action}")
        db.commit()

        created_again = project_openclaw_unresolved_events(db, limit=100)
        db.commit()
        assert created_again == 0
        assert db.query(OperatorTask).filter(OperatorTask.unresolved_event_id.in_(event_ids)).count() == 3
        statuses = {row.status for row in db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id.in_(event_ids)).all()}
        assert statuses <= {"resolved", "dropped", "replayed"}
    finally:
        db.close()


def test_terminal_webchat_source_is_closed_and_not_reprojected_after_drop():
    _reset_data()
    _, conversation_id = _webchat_ticket_and_conversation(suffix="terminal-webchat", required_action="human_needed")

    db = SessionLocal()
    try:
        assert project_webchat_handoff_tasks(db, limit=100) == 1
        db.commit()
        task = db.query(OperatorTask).filter(OperatorTask.webchat_conversation_id == conversation_id).first()
        assert task is not None
        transition_operator_task(db, task_id=task.id, action="drop", actor_id=123, note="closed source")
        db.commit()

        created_again = project_webchat_handoff_tasks(db, limit=100)
        db.commit()
        assert created_again == 0
        assert db.query(OperatorTask).filter(OperatorTask.webchat_conversation_id == conversation_id).count() == 1
    finally:
        db.close()
