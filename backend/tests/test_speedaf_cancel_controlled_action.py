from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import speedaf_cancel
from app.api.deps import get_current_user
from app.db import Base, get_db
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.main import app
from app.models import Ticket, TicketEvent, User
from app.services.speedaf.action_service import SpeedafActionResult, SpeedafActionService
from app.services.tracking_fact_schema import TrackingFactResult
from app.tool_models import ToolCallLog  # noqa: F401


@dataclass
class Harness:
    client: TestClient
    SessionLocal: sessionmaker
    user: User
    ticket: Ticket


@pytest.fixture
def harness(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = User(username="cancel-admin", display_name="Cancel Admin", email="cancel@example.com", password_hash="x", role=UserRole.admin, is_active=True)
        db.add(user)
        db.flush()
        ticket = Ticket(ticket_no="T-CANCEL-1", title="Cancel", description="Cancel", source=TicketSource.manual, source_channel=SourceChannel.web_chat, priority=TicketPriority.medium, status=TicketStatus.in_progress, conversation_state=ConversationState.human_owned, created_by=user.id)
        db.add(ticket)
        db.commit()
        user_id, ticket_id = user.id, ticket.id

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def override_user():
        with SessionLocal() as db:
            return db.get(User, user_id)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_user
    monkeypatch.setattr(speedaf_cancel, "resolve_capabilities", lambda user, db: {"ticket.read", speedaf_cancel.CAP_SPEEDAF_CANCEL_WRITE})
    client = TestClient(app)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        ticket = db.get(Ticket, ticket_id)
    try:
        yield Harness(client, SessionLocal, user, ticket)
    finally:
        app.dependency_overrides.clear()


class FakeAdapter:
    def __init__(self, statuses, calls):
        self.statuses = statuses
        self.calls = calls

    def query_order_tracking_fact(self, *, waybill_code: str, caller_id: str | None = None):
        self.calls.append((waybill_code, caller_id or ""))
        return TrackingFactResult(ok=True, tracking_number=waybill_code, status=self.statuses.pop(0), tool_status="success", pii_redacted=True, fact_evidence_present=True, source="speedaf_api.order_query", tool_name="speedaf.order.query")


def install_adapter(monkeypatch, statuses):
    calls = []
    monkeypatch.setattr(speedaf_cancel, "SpeedafCoreAdapter", lambda: FakeAdapter(statuses, calls))
    return calls


def preview(client, ticket_id, reason="CC01", caller="41000000000"):
    return client.post(f"/api/tickets/{ticket_id}/speedaf/cancel-preview", json={"waybillCode": "WB123", "callerID": caller, "reasonCode": reason})


def test_preview_requires_enabled_flag(harness):
    res = preview(harness.client, harness.ticket.id)
    assert res.status_code == 403
    assert res.json()["detail"] == "speedaf_cancel_disabled"


def test_preview_requires_caller_and_valid_reason(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_CANCEL_ENABLED", "true")
    assert preview(harness.client, harness.ticket.id, caller="").json()["detail"] == "caller_id_required"
    assert preview(harness.client, harness.ticket.id, reason="BAD").json()["detail"] == "invalid_cancel_reason_code"


def test_preview_blocks_terminal_code(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_CANCEL_ENABLED", "true")
    calls = install_adapter(monkeypatch, ["5"])
    res = preview(harness.client, harness.ticket.id)
    assert res.status_code == 200
    assert res.json()["cancelAllowed"] is False
    assert res.json()["currentStatusLabel"] == "delivered"
    assert calls == [("WB123", "41000000000")]


def test_confirm_rechecks_status_and_does_not_call_cancel_when_terminal(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_CANCEL_ENABLED", "true")
    install_adapter(monkeypatch, ["2", "5"])
    action_calls = []
    monkeypatch.setattr(SpeedafActionService, "cancel_order", lambda self, **kwargs: action_calls.append(kwargs) or SpeedafActionResult(ok=True, action_type="cancel_order", status="success", safe_payload={}))
    token = preview(harness.client, harness.ticket.id).json()["confirmToken"]
    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/cancel", json={"waybillCode": "WB123", "callerID": "41000000000", "reasonCode": "CC01", "confirmToken": token})
    assert res.status_code == 409
    assert res.json()["detail"] == "terminal_status_blocks_cancel"
    assert action_calls == []


def test_confirm_success_uses_customer_caller_and_dedupes(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_CANCEL_ENABLED", "true")
    install_adapter(monkeypatch, ["2", "2", "2"])
    action_calls = []
    monkeypatch.setattr(SpeedafActionService, "cancel_order", lambda self, **kwargs: action_calls.append(kwargs) or SpeedafActionResult(ok=True, action_type="cancel_order", status="success", safe_payload={"ok": True}))
    token = preview(harness.client, harness.ticket.id).json()["confirmToken"]
    body = {"waybillCode": "WB123", "callerID": "41000000000", "reasonCode": "CC01", "confirmToken": token}
    first = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/cancel", json=body)
    second = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/cancel", json=body)
    assert first.status_code == 200
    assert second.status_code == 409
    assert action_calls == [{"waybill_code": "WB123", "reason_code": "CC01", "caller_id": "41000000000"}]
    with harness.SessionLocal() as db:
        assert db.query(TicketEvent).filter(TicketEvent.field_name == "speedaf_cancel").count() == 1
