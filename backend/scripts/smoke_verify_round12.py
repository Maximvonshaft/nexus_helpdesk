from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

smoke_dir = ROOT / ".smoke_tmp"
smoke_dir.mkdir(parents=True, exist_ok=True)
db_path = smoke_dir / "helpdesk_round12_smoke.db"
if db_path.exists():
    db_path.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
os.environ["SECRET_KEY"] = "round12-secret"
os.environ["OPENCLAW_SYNC_ENABLED"] = "true"
os.environ["OPENCLAW_EVENT_DRIVER_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import ChannelAccount, Market, Team, User  # noqa: E402
from backend.app.schemas import ChannelAccountCreate, CustomerInput, TicketCreate  # noqa: E402
from backend.app.services.openclaw_bridge import ensure_openclaw_conversation_link  # noqa: E402
from backend.app.services.ticket_service import create_ticket  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

market = Market(code="PH", name="Philippines", country_code="PH", language_code="en")
team = Team(name="PH Support", team_type="support", market=market)
admin = User(username="admin", display_name="Admin", email="admin@test.local", password_hash=hash_password("pw"), role=UserRole.admin, team=team)
db.add_all([market, team, admin])
db.commit()
db.refresh(market); db.refresh(team); db.refresh(admin)

channel_account = ChannelAccount(provider="whatsapp", account_id="wa-main-ph", display_name="PH Main", market_id=market.id, priority=10)
db.add(channel_account); db.commit(); db.refresh(channel_account)

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
assert ticket.channel_account_id == channel_account.id
assert ticket.conversation_state == ConversationState.ai_active

client = TestClient(app)
res = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
assert res.status_code == 200, res.text
headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

res = client.get("/api/admin/channel-accounts", headers=headers)
assert res.status_code == 200, res.text
assert len(res.json()) == 1, res.text

res = client.get("/api/admin/openclaw/runtime-health", headers=headers)
assert res.status_code == 200, res.text
assert "stale_link_count" in res.json(), res.text

res = client.get("/api/admin/signoff-checklist", headers=headers)
assert res.status_code == 200, res.text
assert "checks" in res.json(), res.text

res = client.get(f"/api/tickets/{ticket.id}", headers=headers)
assert res.status_code == 200, res.text
body = res.json()
assert body["country_code"] == "PH", body
assert body["market_code"] == "PH", body
assert body["conversation_state"] == "ai_active", body

print("ROUND12_SMOKE_PASSED")
