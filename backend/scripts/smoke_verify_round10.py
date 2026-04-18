from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

db_path = Path(tempfile.gettempdir()) / "helpdesk_round10_smoke.db"
if db_path.exists():
    db_path.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
os.environ["SECRET_KEY"] = "round10-secret"
os.environ["OPENCLAW_SYNC_ENABLED"] = "true"
os.environ["METRICS_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import Market, Team, User  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()
market = Market(code="PH", name="Philippines", country_code="PH", language_code="en")
team = Team(name="PH Support", team_type="support", market=market)
admin = User(username="admin", display_name="Admin", email="admin@test.local", password_hash=hash_password("pw"), role=UserRole.admin, team=team)
db.add_all([market, team, admin]); db.commit(); db.refresh(admin)

client = TestClient(app)
res = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
assert res.status_code == 200, res.text
headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

res = client.get("/api/admin/signoff-checklist", headers=headers)
assert res.status_code == 200, res.text
body = res.json()
assert body["status"] in {"ready", "not_ready"}, res.text
assert "checks" in body and "warnings" in body, res.text

res = client.get("/api/admin/production-readiness", headers=headers)
assert res.status_code == 200, res.text

res = client.get("/metrics")
assert res.status_code == 200, res.text

print("ROUND10_SMOKE_PASSED")
