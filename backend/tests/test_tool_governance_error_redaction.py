from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/tool_governance_redaction_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.services import openclaw_mcp_client, tool_governance  # noqa: E402
from app.services.error_sanitizer import redact_sensitive_error_text  # noqa: E402
from app.tool_models import ToolCallLog  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "tool_governance_redaction.db"
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


def test_record_tool_call_redacts_authorization_from_db_error_message(db_session):
    tool_governance.record_tool_call(
        tool_name="messages_send",
        status="failed",
        error_code="ProviderError",
        error_message="Authorization: Bearer SECRET_TOKEN should never persist",
        db=db_session,
    )
    db_session.commit()

    row = db_session.query(ToolCallLog).one()
    rendered = json.dumps({"error_message": row.error_message, "input_summary": row.input_summary}, ensure_ascii=False)
    assert "SECRET_TOKEN" not in rendered
    assert "Bearer SECRET_TOKEN" not in rendered
    assert "sha256_prefix" in rendered


def test_record_tool_call_redacts_api_key_from_db_error_message(db_session):
    tool_governance.record_tool_call(
        tool_name="messages_read",
        status="failed",
        error_code="ProviderError",
        error_message="upstream rejected api_key=abc123 during request",
        db=db_session,
    )
    db_session.commit()

    row = db_session.query(ToolCallLog).one()
    assert "abc123" not in (row.error_message or "")
    assert "sha256_prefix" in (row.error_message or "")


def test_mcp_stderr_redaction_removes_cookie_password_and_session_key(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(openclaw_mcp_client, "log_event", lambda level, message, **payload: events.append({"message": message, **payload}))

    class FakeStderr:
        def __iter__(self):
            return iter(["cookie=sessionid; password=hunter2 session_key=raw-session-key\n"])

    class FakeProcess:
        stderr = FakeStderr()

    client = openclaw_mcp_client.OpenClawMCPClient()
    client.process = FakeProcess()
    client._stderr_loop()

    rendered = json.dumps(events, ensure_ascii=False)
    assert "hunter2" not in rendered
    assert "raw-session-key" not in rendered
    assert "sessionid" not in rendered
    assert "sha256_prefix" in rendered


def test_input_output_summary_redaction_does_not_regress(db_session):
    tool_governance.record_tool_call(
        tool_name="messages_send",
        input_payload={"text": "full customer message", "session_key": "raw-session"},
        output_payload={"body": "provider response body", "ok": True},
        status="success",
        db=db_session,
    )
    db_session.commit()

    row = db_session.query(ToolCallLog).one()
    rendered = json.dumps({"input": row.input_summary, "output": row.output_summary}, ensure_ascii=False)
    assert "full customer message" not in rendered
    assert "raw-session" not in rendered
    assert "provider response body" not in rendered
    assert "sha256_prefix" in rendered


def test_error_redaction_preserves_safe_diagnostics():
    summary = redact_sensitive_error_text("Timeout waiting for upstream response", error_code="TimeoutError", error_class="TimeoutError")
    parsed = json.loads(summary or "{}")

    assert parsed["redacted"] is True
    assert parsed["error_code"] == "TimeoutError"
    assert parsed["length"] > 0
    assert parsed["sha256_prefix"]


def test_governance_modes_still_behave(monkeypatch):
    monkeypatch.setenv("TOOL_GOVERNANCE_ENFORCEMENT_MODE", "off")
    assert tool_governance.enforce_tool_policy(tool_name="messages_send").allowed is True

    monkeypatch.setenv("TOOL_GOVERNANCE_ENFORCEMENT_MODE", "audit_only")
    audit_decision = tool_governance.evaluate_tool_call_policy(tool_name="messages_send", actor_capabilities=[])
    assert audit_decision.allowed is True
    assert audit_decision.reason_code.startswith("would_block")

    monkeypatch.setenv("TOOL_GOVERNANCE_ENFORCEMENT_MODE", "enforce")
    with pytest.raises(tool_governance.ToolPolicyBlocked):
        tool_governance.enforce_tool_policy(tool_name="messages_send", actor_capabilities=[])
