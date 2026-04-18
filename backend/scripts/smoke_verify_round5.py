from __future__ import annotations

import os
import tempfile
from pathlib import Path

db_path = Path(tempfile.gettempdir()) / "helpdesk_round5_smoke.db"
if db_path.exists():
    db_path.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
os.environ["SECRET_KEY"] = "round5-secret"
os.environ["INTEGRATION_API_KEY"] = "round5-integration-key"
os.environ["MAX_UPLOAD_BYTES"] = "2048"
os.environ["METRICS_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import SourceChannel, TicketPriority, TicketSource, UserRole, TicketStatus  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import Team, User, UserCapabilityOverride  # noqa: E402
from backend.app.schemas import CustomerInput, TicketCreate  # noqa: E402
from backend.app.services.ticket_service import create_ticket  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()
support = Team(name="Support", team_type="support")
ops = Team(name="Operations", team_type="ops")
db.add_all([support, ops]); db.commit(); db.refresh(support); db.refresh(ops)

lead = User(username="lead", display_name="Lead", email="lead@test.local", password_hash=hash_password("pw"), role=UserRole.lead, team_id=support.id)
agent = User(username="agent", display_name="Agent", email="agent@test.local", password_hash=hash_password("pw"), role=UserRole.agent, team_id=support.id)
auditor = User(username="auditor", display_name="Auditor", email="audit@test.local", password_hash=hash_password("pw"), role=UserRole.auditor, team_id=ops.id)
db.add_all([lead, agent, auditor]); db.commit()
for user in [lead, agent, auditor]: db.refresh(user)

ticket = create_ticket(
    db,
    TicketCreate(
        title="Need help",
        description="Package delayed",
        source=TicketSource.ai_intake,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.high,
        customer=CustomerInput(name="Alice", phone="+10000000001"),
        team_id=support.id,
        assignee_id=agent.id,
    ),
    lead,
)

client = TestClient(app)

def login(username: str):
    res = client.post("/api/auth/login", json={"username": username, "password": "pw"})
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

agent_headers = login("agent")
lead_headers = login("lead")
auditor_headers = login("auditor")

# agent cannot close by default
res = client.post(f"/api/tickets/{ticket.id}/status", json={"new_status": "closed", "note": "done"}, headers=agent_headers)
assert res.status_code == 403, res.text

# grant override then allow cancel/terminal status
db.add(UserCapabilityOverride(user_id=agent.id, capability="ticket.close", allowed=True))
db.commit()
res = client.post(f"/api/tickets/{ticket.id}/status", json={"new_status": "canceled", "note": "done"}, headers=agent_headers)
assert res.status_code == 200, res.text
assert res.json()["status"] == "canceled", res.text

# agent cannot update core fields
res = client.patch(f"/api/tickets/{ticket.id}", json={"title": "changed"}, headers=agent_headers)
assert res.status_code == 403, res.text

# lead can update core fields
res = client.patch(f"/api/tickets/{ticket.id}", json={"title": "changed-by-lead"}, headers=lead_headers)
assert res.status_code == 200, res.text

# metrics endpoint
res = client.get("/metrics")
assert res.status_code == 200, res.text
assert "nexusdesk_http_requests_total" in res.text, res.text

# readyz endpoint
res = client.get("/readyz")
assert res.status_code == 200, res.text

# customer profile permission: auditor allowed globally
res = client.get(f"/api/customers/{ticket.customer_id}/history", headers=auditor_headers)
assert res.status_code == 200, res.text

print("ROUND5_SMOKE_PASSED")
