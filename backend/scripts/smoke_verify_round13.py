from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

smoke_dir = ROOT / ".smoke_tmp"
smoke_dir.mkdir(parents=True, exist_ok=True)
db_path = smoke_dir / "helpdesk_round13_smoke.db"
if db_path.exists():
    db_path.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
os.environ["SECRET_KEY"] = "round13-secret-000000000000000000000000"
os.environ["OPENCLAW_SYNC_ENABLED"] = "true"
os.environ["OPENCLAW_EVENT_DRIVER_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import ConversationState, JobStatus, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import ChannelAccount, Market, Team, User, OpenClawAttachmentReference, TicketAttachment  # noqa: E402
from backend.app.schemas import CustomerInput, TicketCreate  # noqa: E402
from backend.app.services.background_jobs import dispatch_pending_background_jobs  # noqa: E402
from backend.app.services.openclaw_bridge import consume_openclaw_events_once, ensure_openclaw_conversation_link  # noqa: E402
from backend.app.services.heartbeat_service import update_service_heartbeat  # noqa: E402
import backend.app.services.openclaw_bridge as bridge_mod  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

market = Market(code="PH", name="Philippines", country_code="PH", language_code="en")
team = Team(name="PH Support", team_type="support", market=market)
admin = User(username="admin", display_name="Admin", email="admin@test.local", password_hash=hash_password("pw"), role=UserRole.admin, team=team)
db.add_all([market, team, admin]); db.commit()
db.refresh(market); db.refresh(team); db.refresh(admin)

channel_account = ChannelAccount(provider="whatsapp", account_id="wa-main-ph", display_name="PH Main", market_id=market.id, priority=10)
db.add(channel_account); db.commit(); db.refresh(channel_account)

from backend.app.services.ticket_service import create_ticket  # noqa: E402
ticket = create_ticket(
    db,
    TicketCreate(
        title="Delivery issue",
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
    route={"channel": "whatsapp", "recipient": "+10000000001", "accountId": "wa-main-ph"},
)
db.commit(); db.refresh(link); db.refresh(ticket)

class FakeMCP:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def conversation_get(self, session_key):
        return {"sessionKey": session_key, "channel": "whatsapp", "recipient": "+10000000001", "accountId": "wa-main-ph"}
    def messages_read(self, session_key, limit=50):
        return [{"id": "m1", "role": "user", "text": "where is my parcel?"}]
    def attachments_fetch(self, message_id):
        return {"attachments": [{"attachmentId": "att-1", "contentType": "image/jpeg", "filename": "proof.jpg"}]}
    def events_wait(self, cursor=None, timeout_seconds=None):
        return {"cursor": "2", "events": [{"type": "message", "sessionKey": "agent:support:whatsapp:dm:+10000000001"}]}
    def messages_send(self, session_key, text):
        return {"ok": True}

bridge_mod.OpenClawMCPClient = FakeMCP

processed = consume_openclaw_events_once(db)
assert processed == 1, processed
db.commit()

refs = db.query(OpenClawAttachmentReference).filter(OpenClawAttachmentReference.ticket_id == ticket.id).all()
assert len(refs) == 1, refs

processed_jobs = dispatch_pending_background_jobs(db, worker_id="smoke-worker")
db.commit()
ref = db.query(OpenClawAttachmentReference).filter(OpenClawAttachmentReference.ticket_id == ticket.id).first()
assert ref.storage_status == "captured", ref.storage_status
ta = db.query(TicketAttachment).filter(TicketAttachment.ticket_id == ticket.id).first()
assert ta is not None, ta

update_service_heartbeat(db, service_name="openclaw_event_daemon", instance_id="smoke-daemon", status="ok", details={"processed": 1})
db.commit()

client = TestClient(app)
res = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
assert res.status_code == 200, res.text
headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

res = client.get("/api/admin/channel-accounts", headers=headers)
assert res.status_code == 200, res.text
assert len(res.json()) == 1

res = client.get("/api/admin/openclaw/runtime-health", headers=headers)
assert res.status_code == 200, res.text
body = res.json()
assert body["sync_daemon_status"] == "ok", body
assert body["pending_attachment_jobs"] == 0, body

res = client.get(f"/api/tickets/{ticket.id}", headers=headers)
assert res.status_code == 200, res.text
ticket_body = res.json()
assert ticket_body["conversation_state"] == "ai_active", ticket_body
assert len(ticket_body["openclaw_attachment_references"]) == 1, ticket_body

print("ROUND13_SMOKE_PASSED")
