from __future__ import annotations
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import get_db, Base
from app.models import Ticket, User, TicketEvent, UserCapabilityOverride
from app.tool_models import ToolCallLog, ToolCapability
from app.services.speedaf.action_service import SpeedafActionService
from app.services.speedaf.client import SpeedafMcpClient, SpeedafMcpResponse
from app.api.speedaf_cancel import generate_confirm_token
from app.api.deps import get_current_user

# Setup memory database
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="module")
def db_session():
    db = TestingSessionLocal()
    yield db
    db.close()

@pytest.fixture
def test_user(db_session):
    user = User( username=str(uuid.uuid4()), display_name="Admin", email="admin@example.com", is_active=True, role="admin", password_hash="test")
    db_session.add(user)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    return user

@pytest.fixture
def test_ticket(db_session):
    ticket = Ticket( ticket_no=str(uuid.uuid4()), title="test", description="test description", status="new", source="api", priority="low", conversation_state="ai_active", source_channel="internal")
    db_session.add(ticket)
    db_session.commit()
    return ticket

client = TestClient(app)

@pytest.fixture
def mock_speedaf_query(monkeypatch):
    def mock_post(self, path, payload):
        if path == "/open-api/mcp/order/query":
            return SpeedafMcpResponse(
                ok=True, data={"orderInfo": {"status": "in_transit"}}, raw={}, status_code=200, safe_summary={}
            )
        return SpeedafMcpResponse(ok=True, data={}, raw={}, status_code=200, safe_summary={})
    monkeypatch.setattr(SpeedafMcpClient, "post", mock_post)

@pytest.fixture
def mock_speedaf_terminal_query(monkeypatch):
    def mock_post(self, path, payload):
        return SpeedafMcpResponse(ok=True, data={"orderInfo": {"status": "delivered"}}, raw={}, status_code=200, safe_summary={})
    monkeypatch.setattr(SpeedafMcpClient, "post", mock_post)

def test_preview_cancel_no_capability(test_ticket, test_user, db_session, monkeypatch):

    import app.api.speedaf_cancel
    monkeypatch.setattr(app.api.speedaf_cancel, "resolve_capabilities", lambda u, db: [])

    
    res = client.post(f"/api/tickets/{test_ticket.id}/speedaf/cancel-preview", json={"waybill_code": "WB123"})
    assert res.status_code == 403

def test_preview_cancel_success(test_ticket, test_user, db_session, mock_speedaf_query, monkeypatch):

    import app.api.speedaf_cancel
    monkeypatch.setattr(app.api.speedaf_cancel, "resolve_capabilities", lambda u, db: ["tool:speedaf.order.cancel:write"])

    
    res = client.post(f"/api/tickets/{test_ticket.id}/speedaf/cancel-preview", json={"waybill_code": "WB123"})
    assert res.status_code == 200
    assert res.json()["cancel_allowed"] is True

def test_preview_cancel_terminal_status(test_ticket, test_user, db_session, mock_speedaf_terminal_query, monkeypatch):

    import app.api.speedaf_cancel
    monkeypatch.setattr(app.api.speedaf_cancel, "resolve_capabilities", lambda u, db: ["tool:speedaf.order.cancel:write"])

    res = client.post(f"/api/tickets/{test_ticket.id}/speedaf/cancel-preview", json={"waybill_code": "WB123"})
    assert res.status_code == 200
    assert res.json()["cancel_allowed"] is False

def test_confirm_cancel_success(test_ticket, test_user, db_session, monkeypatch):
    import app.services.speedaf.action_service
    monkeypatch.setattr(app.services.speedaf.action_service, "_enabled", lambda n, d=False: True)
    import app.api.speedaf_cancel
    monkeypatch.setattr(app.api.speedaf_cancel, "resolve_capabilities", lambda u, db: ["tool:speedaf.order.cancel:write"])
    
    def mock_post_action(self, atype, path, payload):
        return app.services.speedaf.action_service.SpeedafActionResult(ok=True, action_type=atype, status="success", safe_payload={})
    monkeypatch.setattr(SpeedafActionService, "_post_action", mock_post_action)
    
    token = generate_confirm_token(test_ticket.id, "WB123", "CC01", test_user.id)
    res = client.post(f"/api/tickets/{test_ticket.id}/speedaf/cancel", json={"waybill_code": "WB123", "reason_code": "CC01", "confirm_token": token})
    assert res.status_code == 200
    assert res.json()["ok"] is True

def test_confirm_cancel_dedupe(test_ticket, test_user, db_session, monkeypatch):

    import app.api.speedaf_cancel
    monkeypatch.setattr(app.api.speedaf_cancel, "resolve_capabilities", lambda u, db: ["tool:speedaf.order.cancel:write"])

    import hashlib
    waybill_hash = hashlib.sha256(b"WB123").hexdigest()[:8]
    dedupe_key = f"speedaf-cancel:ticket:{test_ticket.id}:waybill:{waybill_hash}:reason:CC01"
    
    db_session.add(ToolCallLog(tool_name="speedaf.order.cancel", provider="test", tool_type="write", request_id=dedupe_key, status="success"))
    db_session.commit()
    
    token = generate_confirm_token(test_ticket.id, "WB123", "CC01", test_user.id)
    res = client.post(f"/api/tickets/{test_ticket.id}/speedaf/cancel", json={"waybill_code": "WB123", "reason_code": "CC01", "confirm_token": token})
    assert res.status_code == 429
