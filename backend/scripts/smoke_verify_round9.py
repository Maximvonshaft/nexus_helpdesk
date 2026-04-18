from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

db_path = Path(tempfile.gettempdir()) / "helpdesk_round9_smoke.db"
if db_path.exists():
    db_path.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
os.environ["SECRET_KEY"] = "round9-secret"
os.environ["OPENCLAW_SYNC_ENABLED"] = "true"
os.environ["METRICS_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import JobStatus, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import Market, OpenClawConversationLink, Team, User  # noqa: E402
from backend.app.schemas import CustomerInput, TicketCreate  # noqa: E402
from backend.app.services.background_jobs import dispatch_pending_background_jobs, enqueue_openclaw_sync_job  # noqa: E402
from backend.app.services.openclaw_bridge import ensure_openclaw_conversation_link  # noqa: E402
from backend.app.services.ticket_service import create_ticket  # noqa: E402
from backend.app.settings import get_settings  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

market = Market(code="PH", name="Philippines", country_code="PH", language_code="en")
team = Team(name="PH Support", team_type="support", market=market)
admin = User(username="admin", display_name="Admin", email="admin@test.local", password_hash=hash_password("pw"), role=UserRole.admin, team=team)
db.add_all([market, team, admin])
db.commit()
db.refresh(team)
db.refresh(admin)
db.refresh(market)

ticket = create_ticket(
    db,
    TicketCreate(
        title="Parcel issue",
        description="Need help",
        source=TicketSource.ai_intake,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        customer=CustomerInput(name="Alice", phone="+10000000001"),
        team_id=team.id,
        market_id=market.id,
        country_code="PH",
    ),
    admin,
)
link = ensure_openclaw_conversation_link(
    db,
    ticket=ticket,
    session_key="agent:support:whatsapp:dm:+10000000001",
    route={"channel": "whatsapp", "recipient": "+10000000001"},
)
db.commit()
db.refresh(link)

client = TestClient(app)
res = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
assert res.status_code == 200, res.text
headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

res = client.get("/api/admin/queues/summary", headers=headers)
assert res.status_code == 200, res.text
assert "pending_jobs" in res.json(), res.text

res = client.post("/api/admin/openclaw/sync/enqueue", headers=headers, json={"ticket_id": ticket.id, "session_key": link.session_key})
assert res.status_code == 200, res.text
job = res.json()
assert job["job_type"] == "openclaw.sync_session", res.text

# dedupe should return same pending job
res2 = client.post("/api/admin/openclaw/sync/enqueue", headers=headers, json={"ticket_id": ticket.id, "session_key": link.session_key})
assert res2.status_code == 200, res2.text
assert res2.json()["id"] == job["id"], (res.text, res2.text)

# processing unsupported MCP should not crash the worker path; it should move job state away from processing
with SessionLocal() as db2:
    processed = dispatch_pending_background_jobs(db2, worker_id="smoke-worker")
    assert len(processed) >= 1
with SessionLocal() as db3:
    row = db3.query(type(processed[0])).filter(type(processed[0]).id == processed[0].id).first()
    assert row.status in {JobStatus.pending, JobStatus.dead, JobStatus.done}, row.status

res = client.get("/api/admin/jobs?limit=10", headers=headers)
assert res.status_code == 200, res.text
assert isinstance(res.json(), list), res.text

res = client.get("/api/admin/production-readiness", headers=headers)
assert res.status_code == 200, res.text
body = res.json()
assert body["openclaw_sync_enabled"] is True, res.text
assert body["metrics_enabled"] is True, res.text
assert body["openclaw_transport"] in {"mcp", "cli"}, res.text

res = client.get("/metrics")
assert res.status_code == 200, res.text

print("ROUND9_SMOKE_PASSED")
