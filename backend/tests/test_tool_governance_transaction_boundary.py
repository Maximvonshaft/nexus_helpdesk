from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/tool_governance_tx_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.services import tool_governance  # noqa: E402
from app.tool_models import ToolCallLog, ToolRegistry  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "tool_governance_tx.db"
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


def make_business_user(db, username: str = "business-row") -> User:
    row = User(
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="x",
        role=UserRole.agent,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def test_borrowed_session_audit_insert_failure_does_not_rollback_business_row(db_session, monkeypatch):
    business = make_business_user(db_session)

    class BrokenToolCallLog:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("audit insert failed after business write")

    monkeypatch.setattr(tool_governance, "ToolCallLog", BrokenToolCallLog)

    tool_governance.record_tool_call(
        tool_name="messages_send",
        input_payload={"text": "customer text must be summarized", "session_key": "session-value"},
        status="failed",
        error_message="provider failure with customer message body",
        db=db_session,
    )

    db_session.commit()
    persisted = db_session.query(User).filter_by(id=business.id).one()
    assert persisted.username == "business-row"


def test_borrowed_session_registry_failure_does_not_rollback_business_row(db_session, monkeypatch):
    business = make_business_user(db_session, "business-row-registry")

    def broken_registry(*args, **kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(tool_governance, "get_or_create_tool_registry_entry", broken_registry)

    tool_governance.record_tool_call(
        tool_name="messages_read",
        input_payload={"status": "ok"},
        status="success",
        db=db_session,
    )

    db_session.commit()
    persisted = db_session.query(User).filter_by(id=business.id).one()
    assert persisted.username == "business-row-registry"


def test_owned_session_failure_rolls_back_own_audit_session_and_closes(monkeypatch):
    closed = {"value": False}
    rolled_back = {"value": False}

    class FakeOwnedSession:
        def query(self, *args, **kwargs):
            raise RuntimeError("schema unavailable")

        def rollback(self):
            rolled_back["value"] = True

        def close(self):
            closed["value"] = True

    monkeypatch.setattr(tool_governance, "SessionLocal", lambda: FakeOwnedSession())

    tool_governance.record_tool_call(tool_name="messages_read", input_payload={"status": "ok"})

    assert rolled_back["value"] is True
    assert closed["value"] is True


def test_successful_borrowed_session_audit_can_commit_with_business_row(db_session):
    business = make_business_user(db_session, "business-row-success")

    tool_governance.record_tool_call(
        tool_name="messages_read",
        input_payload={"limit": 1},
        output_payload={"ok": True},
        status="success",
        db=db_session,
    )

    db_session.commit()
    assert db_session.query(User).filter_by(id=business.id).one().username == "business-row-success"
    assert db_session.query(ToolRegistry).filter_by(tool_name="messages_read").count() == 1
    assert db_session.query(ToolCallLog).filter_by(tool_name="messages_read").count() == 1
