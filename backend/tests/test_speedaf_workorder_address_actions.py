from __future__ import annotations

from dataclasses import dataclass

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.db import Base, get_db
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.main import app
from app.models import BackgroundJob, Ticket, TicketEvent, User
from app.services.background_jobs import SPEEDAF_ADDRESS_UPDATE_JOB, process_background_job
from app.services.speedaf.action_service import SpeedafActionResult, SpeedafActionService
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
        user = User(username="speedaf-admin", display_name="Speedaf Admin", email="speedaf-admin@example.com", password_hash="x", role=UserRole.admin, is_active=True)
        db.add(user)
        db.flush()
        ticket = Ticket(ticket_no="T-SPEEDAF-1", title="Speedaf", description="Speedaf", source=TicketSource.manual, source_channel=SourceChannel.web_chat, priority=TicketPriority.medium, status=TicketStatus.in_progress, conversation_state=ConversationState.human_owned, created_by=user.id)
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
    client = TestClient(app)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        ticket = db.get(Ticket, ticket_id)
    try:
        yield Harness(client, SessionLocal, user, ticket)
    finally:
        app.dependency_overrides.clear()


def work_order_payload(description: str = "Please follow up delivery"):
    return {"waybillCode": "WB123", "callerID": "41000000000", "workOrderType": "WT0103-05", "description": description}


def address_payload():
    return {"waybillCode": "WB123", "callerID": "41000000000", "whatsAppPhone": "41790000000"}


def test_work_order_disabled_by_default(harness):
    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/work-orders", json=work_order_payload())
    assert res.status_code == 403
    assert res.json()["detail"] == "speedaf_work_order_create_disabled"


def test_work_order_enabled_queues_job_and_truncates_description(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "true")
    long_description = "x" * 260
    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/work-orders", json=work_order_payload(long_description))
    assert res.status_code == 200
    assert res.json()["status"] == "queued"
    with harness.SessionLocal() as db:
        job = db.query(BackgroundJob).one()
        assert job.job_type == "speedaf.work_order.create"
        assert len(json.loads(job.payload_json)["description"]) == 200
        assert db.query(TicketEvent).filter(TicketEvent.field_name == "speedaf_work_order").count() == 1


def test_work_order_enabled_requires_tool_capability_for_visible_agent(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "true")
    with harness.SessionLocal() as db:
        user = db.get(User, harness.user.id)
        ticket = db.get(Ticket, harness.ticket.id)
        user.role = UserRole.agent
        ticket.assignee_id = user.id
        db.commit()

    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/work-orders", json=work_order_payload())

    assert res.status_code == 403
    assert res.json()["detail"] == "speedaf_work_order_requires_capability"


def test_address_update_disabled_by_default(harness):
    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/address-update", json=address_payload())
    assert res.status_code == 403
    assert res.json()["detail"] == "speedaf_update_address_disabled"


def test_address_update_enabled_queues_job_without_synchronous_submit(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_UPDATE_ADDRESS_ENABLED", "true")
    calls = []
    monkeypatch.setattr(SpeedafActionService, "submit_update_address_flow", lambda self, **kwargs: calls.append(kwargs) or SpeedafActionResult(ok=True, action_type="update_address_flow", status="success", safe_payload={"ok": True}))
    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/address-update", json=address_payload())
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "queued"
    assert "already changed" not in body["message"].lower()
    assert calls == []
    with harness.SessionLocal() as db:
        job = db.query(BackgroundJob).one()
        assert job.job_type == SPEEDAF_ADDRESS_UPDATE_JOB
        assert json.loads(job.payload_json)["addressUpdateDedupeKey"] == body["dedupeKey"]
        assert db.query(TicketEvent).filter(TicketEvent.field_name == "speedaf_address_update", TicketEvent.new_value == "queued").count() == 1


def test_address_update_enabled_requires_tool_capability_for_visible_agent(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_UPDATE_ADDRESS_ENABLED", "true")
    with harness.SessionLocal() as db:
        user = db.get(User, harness.user.id)
        ticket = db.get(Ticket, harness.ticket.id)
        user.role = UserRole.agent
        ticket.assignee_id = user.id
        db.commit()

    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/address-update", json=address_payload())

    assert res.status_code == 403
    assert res.json()["detail"] == "speedaf_address_update_requires_capability"


def test_address_update_worker_executes_speedaf_action_and_writes_completion(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_UPDATE_ADDRESS_ENABLED", "true")
    calls = []

    def fake_submit(self, **kwargs):
        calls.append(kwargs)
        return SpeedafActionResult(ok=True, action_type="update_address_flow", status="success", safe_payload={"ok": True})

    monkeypatch.setattr(SpeedafActionService, "submit_update_address_flow", fake_submit)
    res = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/address-update", json=address_payload())
    assert res.status_code == 200
    with harness.SessionLocal() as db:
        job = db.query(BackgroundJob).filter(BackgroundJob.job_type == SPEEDAF_ADDRESS_UPDATE_JOB).one()
        process_background_job(db, job)
        db.commit()
    assert calls == [{"waybill_code": "WB123", "whatsapp_phone": "41790000000", "caller_id": "41000000000"}]
    with harness.SessionLocal() as db:
        assert db.query(TicketEvent).filter(TicketEvent.field_name == "speedaf_address_update", TicketEvent.new_value == "completed").count() == 1


def test_address_update_dedupe_blocks_duplicate(harness, monkeypatch):
    monkeypatch.setenv("SPEEDAF_UPDATE_ADDRESS_ENABLED", "true")
    first = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/address-update", json=address_payload())
    second = harness.client.post(f"/api/tickets/{harness.ticket.id}/speedaf/address-update", json=address_payload())
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "speedaf_address_update_already_requested"
