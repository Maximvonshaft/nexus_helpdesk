from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import AdminAuditLog, Ticket, User  # noqa: E402
from app.services.operator_queue import (  # noqa: E402
    OperatorQueueError,
    create_operator_task,
    sanitize_operator_payload,
    serialize_operator_task,
    transition_operator_task,
)


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db, username="admin", role=UserRole.admin):
    row = User(username=username, display_name=username, email=f"{username}@example.test", password_hash="x", role=role, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_ticket(db):
    row = Ticket(
        ticket_no=f"T-{db.query(Ticket).count() + 1}",
        title="Need human review",
        description="Need human review",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )
    db.add(row)
    db.flush()
    return row


def test_sanitize_operator_payload_redacts_sensitive_fields():
    payload = sanitize_operator_payload({
        "session_key": "secret-session",
        "visitor_email": "customer@example.test",
        "visitor_phone": "+411234567",
        "last_error": "Provider error with raw payload and token abc123",
        "safe": "ok",
    })

    assert payload["session_key"]["redacted"] is True
    assert payload["visitor_email"]["redacted"] is True
    assert payload["visitor_phone"]["redacted"] is True
    assert payload["last_error"]["redacted"] is True
    assert payload["safe"] == "ok"
    assert "customer@example.test" not in json.dumps(payload)


def test_create_operator_task_stores_redacted_payload(db_session):
    row, created = create_operator_task(
        db_session,
        source_type="openclaw",
        task_type="bridge_unresolved",
        source_id="src-1",
        unresolved_event_id=1,
        payload={"session_key": "sess-secret", "recipient": "+411234567", "last_error": "raw secret error"},
    )

    assert created is True
    serialized = serialize_operator_task(row)
    rendered = json.dumps(serialized["payload_json"], ensure_ascii=False)
    assert "sess-secret" not in rendered
    assert "+411234567" not in rendered
    assert "raw secret error" not in rendered


def test_transition_operator_task_assigns_and_audits(db_session):
    admin = make_user(db_session)
    row, _ = create_operator_task(db_session, source_type="webchat", task_type="handoff", source_id="wc-1")
    db_session.commit()

    transitioned = transition_operator_task(db_session, task_id=row.id, action="assign", actor_id=admin.id, note="take ownership")

    assert transitioned.status == "assigned"
    assert transitioned.assignee_id == admin.id
    audit = db_session.query(AdminAuditLog).filter_by(action="operator_queue.assign").one()
    assert audit.actor_id == admin.id
    assert "take ownership" in (audit.new_value_json or "")


def test_transition_operator_task_rejects_unsupported_action(db_session):
    row, _ = create_operator_task(db_session, source_type="webchat", task_type="handoff", source_id="wc-1")

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(db_session, task_id=row.id, action="unsupported", actor_id=None)

    assert exc.value.status_code == 400
    assert exc.value.code == "unsupported_operator_task_action"
